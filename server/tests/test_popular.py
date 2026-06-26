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
        lambda country: {"total": 0, "top_species": [], "top_diseases": []},
    )
    with server_main._popular_lock:
        server_main._popular_cache.clear()
    yield


def test_popular_global_shape(client):
    r = client.get("/api/popular?locale=en")
    assert r.status_code == 200
    body = r.json()
    assert r.headers["cache-control"] == "public, max-age=7200"
    assert body["scope"] == "global"
    assert body["locale"] == "en"
    assert body["items"]
    item = body["items"][0]
    assert {"scientific", "common_en", "common_ru", "image_url", "count", "percent"} <= set(item)
    assert item["image_url"].startswith("https://img/")


def test_popular_known_country_is_curated(client):
    from popular_plants import BY_REGION

    r = client.get("/api/popular?country=RU&locale=ru")
    assert r.status_code == 200
    body = r.json()
    assert body["scope"] == "country"
    assert body["country"] == "RU"
    served = [i["scientific"] for i in body["items"]]
    assert served == [p.scientific for p in BY_REGION["RU"]]


def test_popular_no_country_no_geo_is_global(client):
    # No country param and no geo header -> global scope (no network geoip in tests).
    r = client.get("/api/popular")
    assert r.status_code == 200
    assert r.json()["scope"] == "global"


def test_popular_prefers_live_community_data(client, monkeypatch):
    import main as server_main

    monkeypatch.setattr(
        server_main,
        "community_popular_snapshot",
        lambda country: {
            "total": 5,
            "top_species": [{"species": "Ficus elastica", "count": 3, "percent": 60}],
            "top_diseases": [{"disease": "leaf_spot", "count": 2, "percent": 40}],
        },
    )
    r = client.get("/api/popular?country=US&locale=en")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "community"
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
