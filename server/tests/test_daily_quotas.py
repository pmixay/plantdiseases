"""Per-IP daily allowances on the endpoints that cost money or PlantNet quota.

Each test lowers one ``_DailyQuota.limit`` and checks what the caller sees
past it: a 429 on the inference routes, a graceful ``available: false`` on the
best-effort AI surfaces, and the rules-based plan without LLM polish on
/api/care-plan. ``conftest._reset_abuse_counters`` empties the counters
before every test.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image


def _png(color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def m():
    import main as server_main

    return server_main


def test_daily_quota_counts_per_key_and_resets_each_day(m):
    q = m._DailyQuota("t", 2)
    assert q.try_consume("a") and q.try_consume("a")
    assert not q.try_consume("a")
    assert q.try_consume("b")
    assert q.rejections == 1

    q._day = "2000-01-01"  # yesterday's counts
    assert q.try_consume("a")


def test_daily_quota_zero_limit_and_key_ceiling(m):
    assert all(m._DailyQuota("off", 0).try_consume("a") for _ in range(10))

    q = m._DailyQuota("t", 1, max_keys=1)
    assert q.try_consume("a")
    # The map is full: a new client passes untracked instead of growing it.
    assert q.try_consume("b") and q.try_consume("b")
    assert not q.try_consume("a")


@pytest.mark.parametrize(
    ("route", "quota_name"),
    [("/api/analyze", "_analyze_quota"), ("/api/identify", "_identify_quota")],
)
def test_inference_routes_answer_429_past_the_daily_quota(
    client, m, monkeypatch, route, quota_name,
):
    monkeypatch.setattr(getattr(m, quota_name), "limit", 2)
    for i in range(2):
        r = client.post(route, files={"image": ("l.png", _png((40, 150 + i, 60)), "image/png")})
        assert r.status_code == 200, r.text
    r = client.post(route, files={"image": ("l.png", _png((40, 160, 60)), "image/png")})
    assert r.status_code == 429, r.text
    assert "Retry-After" not in r.headers


def test_plantnet_allowance_is_shared_by_analyze_and_identify(client, m, monkeypatch):
    calls: list[str] = []

    def fake_identify(image_bytes, *, organ="leaf", lang="en"):
        calls.append(organ)
        return {"available": False, "results": [], "source": "no_api_key"}

    monkeypatch.setattr(m, "identify_species", fake_identify)
    monkeypatch.setattr(m._plantnet_quota, "limit", 1)

    first = client.post("/api/analyze", files={"image": ("a.png", _png((40, 150, 60)), "image/png")})
    assert first.status_code == 200, first.text
    assert len(calls) == 1

    # A new photo still gets its diagnosis, without the PlantNet call.
    second = client.post("/api/analyze", files={"image": ("b.png", _png((50, 150, 60)), "image/png")})
    assert second.status_code == 200, second.text
    assert second.json()["species"]["source"] == "quota"

    ident = client.post("/api/identify", files={"image": ("c.png", _png((60, 150, 60)), "image/png")})
    assert ident.status_code == 200, ident.text
    assert ident.json()["source"] == "quota"
    assert len(calls) == 1


def test_chat_daily_quota_survives_new_session_ids(client, m, monkeypatch):
    monkeypatch.setattr(m._chat_quota, "limit", 1)
    client.post("/api/chat", json={"session_id": "s1", "message": "hi"})

    r = client.post("/api/chat", json={"session_id": "s2", "message": "hi", "locale": "ru"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is False
    assert body["reason"] == "daily_quota"
    assert body["fallback"] == m._QUOTA_UNAVAILABLE_TEXT["ru"]


def test_care_plan_past_quota_drops_only_the_llm_polish(client, m, monkeypatch):
    seen: list[bool] = []

    def fake_plan(**kwargs):
        seen.append(kwargs["polish"])
        return {"summary_md": "rules"}

    monkeypatch.setattr(m, "generate_care_plan", fake_plan)
    monkeypatch.setattr(m._care_plan_quota, "limit", 1)
    for _ in range(2):
        r = client.post("/api/care-plan", json={"disease": "leaf_spot"})
        assert r.status_code == 200, r.text
    assert seen == [True, False]


def test_species_ai_quota_is_shared_and_graceful(client, m, monkeypatch):
    llm_calls: list[str] = []

    def fake_diseases(name, *, common_name=None, lang="en"):
        llm_calls.append(name)
        return {"available": True, "diseases": []}

    monkeypatch.setattr(m, "species_common_diseases", fake_diseases)
    monkeypatch.setattr(m._species_ai_quota, "limit", 1)

    ok = client.get("/api/species-diseases", params={"name": "Ficus elastica"})
    assert ok.status_code == 200 and ok.json()["available"] is True

    profile = client.get("/api/species-profile", params={"name": "Ficus elastica"})
    assert profile.status_code == 200
    assert profile.json() == {"available": False, "reason": "daily_quota"}
    assert profile.headers["Cache-Control"] == "no-store"

    again = client.get("/api/species-diseases", params={"name": "Monstera deliciosa"})
    assert again.json() == {"available": False, "diseases": [], "reason": "daily_quota"}
    assert llm_calls == ["Ficus elastica"]


def test_quota_rejections_reach_metrics(client, m, monkeypatch):
    monkeypatch.setattr(m._species_ai_quota, "limit", 1)
    for _ in range(3):
        client.get("/api/species-profile", params={"name": "Ficus elastica"})
    body = client.get("/api/metrics").json()
    assert body["daily_quota_rejections"]["species_ai"] == 2


def test_a_quota_blanked_species_block_is_not_cached_for_everyone(client, m, monkeypatch):
    """The analyze cache is shared by everyone uploading the same bytes; a
    species block blanked by one caller's PlantNet allowance must not be
    replayed to the next."""
    calls: list[str] = []

    def fake_identify(image_bytes, *, organ="leaf", lang="en"):
        calls.append(organ)
        return {"available": False, "results": [], "source": "no_api_key"}

    monkeypatch.setattr(m, "identify_species", fake_identify)
    monkeypatch.setattr(m._plantnet_quota, "limit", 1)
    first_photo = _png((40, 150, 60))
    client.post("/api/analyze", files={"image": ("a.png", _png((90, 90, 90)), "image/png")})

    blanked = client.post("/api/analyze", files={"image": ("b.png", first_photo, "image/png")})
    assert blanked.json()["species"]["source"] == "quota"

    m._plantnet_quota.reset()
    again = client.post("/api/analyze", files={"image": ("b.png", first_photo, "image/png")})
    assert again.json()["is_repeat"] is False
    assert again.json()["species"]["source"] != "quota"
    assert len(calls) == 2
