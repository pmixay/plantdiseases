"""Tests for the /api/popular bundle + popular_for curation."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_wikipedia(monkeypatch):
    """Stub species image resolution so tests never hit the network, and clear
    the daily bundle cache between tests."""
    import main as server_main

    monkeypatch.setattr(
        server_main, "fetch_species_info",
        lambda name, lang="en": {
            "title": name,
            "image_url": f"https://img/{name}.jpg",
        },
    )
    monkeypatch.setattr(
        server_main,
        "community_popular_snapshot",
        lambda country, region=None, *, limit=10: {
            "total": 0, "top_species": [], "top_diseases": [],
        },
    )
    with server_main._popular_lock:
        server_main._popular_cache.clear()
    yield


def test_popular_default_country_placeholder_shape(client):
    # No country + no geo + no scans -> default country (RU) + curated
    # placeholder; there is no "worldwide" bucket any more.
    r = client.get("/api/popular?locale=en")
    assert r.status_code == 200
    body = r.json()
    assert r.headers["cache-control"] == "public, max-age=7200"
    assert body["scope"] == "country"
    assert body["country"] == "RU"
    assert body["source"] == "placeholder"
    assert body["locale"] == "en"
    assert body["items"]
    item = body["items"][0]
    assert {"scientific", "common_en", "common_ru", "image_url", "count", "percent"} <= set(item)
    assert item["image_url"].startswith("https://img/")
    assert item["percent"] == 0  # placeholder carries no percentages


def test_popular_known_country_placeholder_is_curated(client):
    from popular_plants import BY_REGION

    r = client.get("/api/popular?country=RU&locale=ru")
    assert r.status_code == 200
    body = r.json()
    assert body["scope"] == "country"
    assert body["country"] == "RU"
    assert body["source"] == "placeholder"
    served = [i["scientific"] for i in body["items"]]
    assert served == [p.scientific for p in BY_REGION["RU"]]


def test_popular_caps_at_ten_cards(client, monkeypatch):
    import main as server_main

    monkeypatch.setattr(
        server_main, "community_popular_snapshot",
        lambda country, region=None, *, limit=10: {
            "total": 100,
            "top_species": [
                {"species": f"Plant {i}", "count": 100 - i, "percent": 50}
                for i in range(20)
            ],
            "top_diseases": [
                {"disease": f"d{i}", "count": 100 - i, "percent": 50}
                for i in range(20)
            ],
        },
    )
    body = client.get("/api/popular?country=US&locale=en").json()
    assert len(body["items"]) == 10
    assert len(body["top_diseases"]) == 10


def test_popular_uses_live_country_data(client, monkeypatch):
    import main as server_main

    monkeypatch.setattr(
        server_main,
        "community_popular_snapshot",
        lambda country, region=None, *, limit=10: {
            "total": 5,
            "top_species": [{"species": "Ficus elastica", "count": 3, "percent": 60}],
            "top_diseases": [{"disease": "leaf_spot", "count": 2, "percent": 40}],
        },
    )
    r = client.get("/api/popular?country=US&locale=en")
    assert r.status_code == 200
    body = r.json()
    # No geo subdivision in tests -> country scope (not nearby).
    assert body["source"] == "country"
    assert body["country_name_en"] == "United States"
    assert body["items"][0]["scientific"] == "Ficus elastica"
    assert body["items"][0]["count"] == 3
    assert body["items"][0]["percent"] == 60
    assert body["top_diseases"][0]["percent"] == 40


def test_popular_for_known_and_unknown():
    from popular_plants import BY_REGION, GLOBAL_DEFAULT, popular_for

    assert popular_for("US") == BY_REGION["US"]
    assert popular_for("us") == BY_REGION["US"]  # case-insensitive
    assert popular_for("ZZ") == GLOBAL_DEFAULT   # unmapped
    assert popular_for(None) == GLOBAL_DEFAULT
    assert popular_for("United States") == GLOBAL_DEFAULT  # full name misses ISO map


@pytest.mark.parametrize("code,en,ru", [
    ("IN", "India", "Индия"),
    ("BR", "Brazil", "Бразилия"),
    ("CN", "China", "Китай"),
    ("CA", "Canada", "Канада"),
])
def test_popular_curated_countries_have_real_names(client, code, en, ru):
    # Every country ``popular_plants.BY_REGION`` curates (plus other
    # frequently-geoip-detected ones) must surface a localized NAME, not the
    # raw ISO code, so the Home title reads "Popular in India" not "IN".
    r = client.get(f"/api/popular?country={code}&locale=en")
    assert r.status_code == 200
    body = r.json()
    assert body["scope"] == "country"
    assert body["country_name_en"] == en
    assert body["country_name_ru"] == ru
