"""Home-screen trending module (/api/community/trending + community.trending)."""

from __future__ import annotations

import time

import pytest

import community


@pytest.fixture
def comdb(tmp_path, monkeypatch):
    """Isolated community DB (module-global patch, same idiom as feedback)."""
    monkeypatch.setattr(community, "COMMUNITY_DB_PATH", tmp_path / "community.db")
    monkeypatch.setattr(community, "COMMUNITY_THUMB_DIR", tmp_path / "thumbs")
    monkeypatch.setattr(community, "_initialised", False)
    return community


_seq = 0


def _seed(country, region="", disease="leaf_spot", species=None, age_days=0):
    global _seq
    _seq += 1
    community.record_scan(
        scan_id=f"trend_scan_{_seq:04d}", user_pseudo_id=f"user{_seq}",
        country=country, region=region, disease=disease, species=species,
    )
    if age_days:
        with community._connect() as conn:
            conn.execute(
                "UPDATE community_scans SET created_at=? WHERE scan_id=?",
                (int(time.time()) - age_days * 86_400, f"trend_scan_{_seq:04d}"),
            )


def test_trending_region_scope_when_enough_data(comdb):
    for _ in range(3):
        _seed("Germany", "Berlin", disease="pests", species="Ficus elastica")
    out = community.trending("Germany", "Berlin")
    assert out["scope"] == "region" and out["total"] == 3
    assert out["top_species"][0] == {
        "species": "Ficus elastica",
        "count": 3,
        "percent": 100,
    }
    assert out["top_diseases"][0] == {
        "disease": "pests",
        "count": 3,
        "percent": 100,
    }


def test_trending_widens_region_to_country_to_global(comdb):
    # only 1 scan in Berlin (< min 3) but 3 in Germany → country scope
    _seed("Germany", "Berlin", species="Monstera deliciosa")
    _seed("Germany", "Munich", species="Monstera deliciosa")
    _seed("Germany", "", species="Monstera deliciosa")
    out = community.trending("Germany", "Berlin")
    assert out["scope"] == "country" and out["region"] is None
    # unknown country entirely → global (served even when thin)
    out = community.trending("Atlantis")
    assert out["scope"] == "global" and out["country"] is None
    assert out["total"] == 3


def test_trending_window_and_hidden_diseases(comdb):
    _seed("France", disease="not_a_plant", species="Ficus")  # hidden
    _seed("France", disease="healthy", species="Ficus", age_days=30)  # stale
    for _ in range(3):
        _seed("France", disease="powdery_mildew", species="Rosa")
    out = community.trending("France", days=7)
    assert out["total"] == 3
    diseases = {d["disease"] for d in out["top_diseases"]}
    assert "not_a_plant" not in diseases and "healthy" not in diseases
    assert {s["species"] for s in out["top_species"]} == {"Rosa"}


def test_trending_species_grouped_case_insensitive(comdb):
    _seed("Spain", species="ficus elastica")
    _seed("Spain", species="Ficus elastica")
    _seed("Spain", species="Ficus elastica")
    out = community.trending("Spain")
    assert len(out["top_species"]) == 1
    assert out["top_species"][0]["count"] == 3


def test_trending_endpoint_optional_country(client, comdb):
    for _ in range(3):
        _seed("Italy", disease="gray_mold_botrytis", species="Pelargonium")
    r = client.get("/api/community/trending")
    assert r.status_code == 200
    body = r.json()
    assert body["scope"] == "global" and body["total"] == 3
    r = client.get("/api/community/trending?country=Italy&days=7&limit=3")
    assert r.status_code == 200 and r.json()["scope"] == "country"
    # invalid country degrades to global instead of 422 (discovery widget)
    r = client.get("/api/community/trending?country=%3Cscript%3E")
    assert r.status_code == 200 and r.json()["scope"] == "global"


def test_trending_endpoint_keeps_global_scope_when_widening(client, comdb):
    # The requested country (Germany) has data, but fewer than the
    # _TRENDING_MIN_SCANS threshold, so trending() widens to global. The
    # endpoint must NOT re-insert the requested country and overwrite the
    # scope back to "country" — that masked the real "global" verdict.
    for _ in range(3):
        _seed("France", disease="powdery_mildew", species="Rosa")
    _seed("Germany", disease="leaf_spot", species="Ficus")  # 1 scan → widens
    r = client.get("/api/community/trending?country=Germany")
    assert r.status_code == 200
    body = r.json()
    assert body["scope"] == "global"
    assert body["country"] is None
    # Localized names are still attached (for the global label), but the
    # scope/country stay authoritative.
    assert body["country_name_en"] == "Worldwide"


def test_trending_endpoint_localizes_known_country(client, comdb):
    # A known ISO code with enough data is served as "country" scope and
    # carries a real localized name (not the raw code). Production stores the
    # bucket as the geoip-resolved ISO code, so the corpus is seeded as "ES"
    # too — the read path resolves ?country=ES to the same "ES" bucket.
    for _ in range(3):
        _seed("ES", disease="powdery_mildew", species="Rosa")
    r = client.get("/api/community/trending?country=ES")
    assert r.status_code == 200
    body = r.json()
    assert body["scope"] == "country" and body["country"] == "ES"
    assert body["country_name_en"] == "Spain"
    assert body["country_name_ru"] == "Испания"
