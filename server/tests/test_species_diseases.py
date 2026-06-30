"""Tests for the AI species common-diseases helper + endpoint.

``species_common_diseases`` asks the chat model for the conditions a species
is commonly affected by indoors and returns a small sanitised list, each item
optionally tagged with a guide ``doc_key`` the Android client can deep-link.
It must degrade gracefully (``available=False``) with no API key or on any
failure so the client always falls back to its offline disease guide. The
OpenRouter call is fully mocked — no network.
"""
from __future__ import annotations

import json

import pytest

import ai_services


@pytest.fixture(autouse=True)
def _clear_cache(tmp_path, monkeypatch):
    ai_services._SPECIES_DISEASES_CACHE._d.clear()
    # Isolate the persistent disk layer per test (point it at a tmp dir) so a
    # real result from one test never satisfies another and the repo's
    # ``server/ai_cache/`` stays clean.
    monkeypatch.setattr(
        ai_services._SPECIES_DISEASES_DISK, "dir", tmp_path / "species_diseases",
    )
    yield
    ai_services._SPECIES_DISEASES_CACHE._d.clear()


# ── parser ──────────────────────────────────────────────────────────

def test_parser_plain_json():
    text = json.dumps({"diseases": [
        {"name": "Powdery mildew", "doc_key": "powdery_mildew", "note": "White dust on leaves; improve airflow."},
    ]})
    items = ai_services._parse_species_diseases(text)
    assert items == [
        {"name": "Powdery mildew", "doc_key": "powdery_mildew",
         "note": "White dust on leaves; improve airflow."},
    ]


def test_parser_tolerates_code_fence_and_prose():
    text = "Sure! Here it is:\n```json\n" + json.dumps(
        {"diseases": [{"name": "Root rot", "doc_key": None, "note": "Soggy soil."}]}
    ) + "\n```"
    items = ai_services._parse_species_diseases(text)
    assert len(items) == 1 and items[0]["name"] == "Root rot"


def test_parser_drops_unknown_doc_key_and_caps():
    text = json.dumps({"diseases": [
        {"name": "X", "doc_key": "not_a_real_key", "note": "n"},
        {"name": "", "doc_key": "leaf_spot", "note": "skip me — no name"},
        {"name": "L" * 200, "note": "too long a name"},  # dropped (>80)
        {"name": "Long note", "doc_key": "pests", "note": "z" * 500},
    ]})
    items = ai_services._parse_species_diseases(text)
    assert len(items) == 2
    assert items[0] == {"name": "X", "doc_key": None, "note": "n"}
    assert items[1]["doc_key"] == "pests"
    assert len(items[1]["note"]) <= 240


def test_parser_limits_to_five_items():
    text = json.dumps({"diseases": [
        {"name": f"d{i}", "note": ""} for i in range(9)
    ]})
    assert len(ai_services._parse_species_diseases(text)) == 5


@pytest.mark.parametrize("text", ["", "not json at all", "{}", json.dumps({"diseases": "x"})])
def test_parser_returns_empty_on_junk(text):
    assert ai_services._parse_species_diseases(text) == []


# ── helper: graceful + structured paths ─────────────────────────────

def test_no_api_key_is_graceful(monkeypatch):
    monkeypatch.setattr(ai_services, "OPENROUTER_API_KEY", "")
    out = ai_services.species_common_diseases("Ficus elastica", lang="en")
    assert out["available"] is False
    assert out["diseases"] == []
    assert out["source"] == "no_api_key"


def test_blank_name_is_graceful():
    out = ai_services.species_common_diseases("   ", lang="en")
    assert out["available"] is False and out["source"] == "no_species"


def test_structured_success_and_cache(monkeypatch):
    monkeypatch.setattr(ai_services, "OPENROUTER_API_KEY", "test-key")
    calls = {"n": 0}

    def fake_call(system_text, history, *, max_tokens=None):
        calls["n"] += 1
        assert "JSON" in system_text
        return (json.dumps({"diseases": [
            {"name": "Spider mites", "doc_key": "pests", "note": "Fine webbing; rinse + neem."},
            {"name": "Edema", "doc_key": "edema", "note": "Blisters; water less."},
        ]}), 10, 20)

    monkeypatch.setattr(ai_services, "_call_openrouter", fake_call)

    out = ai_services.species_common_diseases("Ficus elastica", common_name="Rubber plant", lang="en")
    assert out["available"] is True
    assert out["species"] == "Ficus elastica"
    assert [d["name"] for d in out["diseases"]] == ["Spider mites", "Edema"]
    assert out["diseases"][0]["doc_key"] == "pests"

    # Second call for the same (species, lang) is served from cache (no API hit).
    cached = ai_services.species_common_diseases("Ficus elastica", lang="en")
    assert cached.get("cached") is True
    assert calls["n"] == 1


def test_disk_cache_survives_inmemory_wipe(monkeypatch):
    """A real result persists on disk, so after a server restart / fresh worker
    (in-memory LRU empty) it's reused without paying for the LLM again — this is
    why reopening a species no longer waits ~20s."""
    monkeypatch.setattr(ai_services, "OPENROUTER_API_KEY", "test-key")
    calls = {"n": 0}

    def fake_call(system_text, history, *, max_tokens=None):
        calls["n"] += 1
        return (json.dumps({"diseases": [
            {"name": "Root rot", "note": "Soggy soil; let it dry."},
        ]}), 5, 5)

    monkeypatch.setattr(ai_services, "_call_openrouter", fake_call)

    first = ai_services.species_common_diseases("Calathea orbifolia", lang="en")
    assert first["available"] is True and calls["n"] == 1

    # Simulate a restart: drop the in-memory layer; the disk layer remains.
    ai_services._SPECIES_DISEASES_CACHE._d.clear()
    again = ai_services.species_common_diseases("Calathea orbifolia", lang="en")
    assert again["available"] is True
    assert again.get("cached") is True
    assert [d["name"] for d in again["diseases"]] == ["Root rot"]
    assert calls["n"] == 1  # disk hit — the LLM was NOT called a second time


def test_disk_cache_concurrent_writes_dont_corrupt(tmp_path):
    """Concurrent puts to the SAME key must never publish a corrupt file.

    A shared "<key>.tmp" let two writers (a cold-cache stampede on one species)
    interleave their JSON into one file; the published .json was then unreadable
    and every future read missed and re-paid the ~20 s LLM call.
    """
    import threading

    cache = ai_services._DiskCache("concurrent", ttl_seconds=None)
    cache.dir = tmp_path / "concurrent"
    payloads = [{"available": True, "n": i, "blob": "z" * 500} for i in range(20)]
    barrier = threading.Barrier(len(payloads))

    def writer(p):
        barrier.wait()  # release all writers at once to maximise overlap
        cache.put("same-key", p)

    threads = [threading.Thread(target=writer, args=(p,)) for p in payloads]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    got = cache.get("same-key")
    assert got is not None             # a complete, valid file was published
    assert got in payloads             # exactly one writer's payload, not a mash-up
    assert list(cache.dir.glob("*.tmp")) == []  # no leaked temp files


def test_api_error_is_graceful(monkeypatch):
    monkeypatch.setattr(ai_services, "OPENROUTER_API_KEY", "test-key")

    def boom(*a, **k):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(ai_services, "_call_openrouter", boom)
    out = ai_services.species_common_diseases("Monstera deliciosa", lang="en")
    assert out["available"] is False and out["source"] == "api_error"


# ── endpoint ────────────────────────────────────────────────────────

def test_endpoint_graceful_without_key(client):
    """With no OpenRouter key configured in the test env, the endpoint still
    returns 200 with a graceful unavailable payload (never an error)."""
    r = client.get("/api/species-diseases", params={"name": "Ficus elastica"})
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["diseases"] == []


def test_endpoint_rejects_blank_name(client):
    r = client.get("/api/species-diseases", params={"name": "   "})
    assert r.status_code == 422
