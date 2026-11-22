"""Tests for the /api/popular bundle + popular_for curation."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_wikipedia(monkeypatch):
    """Stub species image resolution so tests never hit the network, and clear
    the daily bundle cache between tests."""
    import main as server_main

    # Use a Wikimedia host so the bundle's image proxy (plant_photos.proxy_path,
    # which only rewrites allowed hosts) produces a real proxy path.
    monkeypatch.setattr(
        server_main, "fetch_species_info",
        lambda name, lang="en": {
            "title": name,
            "image_url": f"https://upload.wikimedia.org/{name}.jpg",
        },
    )
    monkeypatch.setattr(
        server_main,
        "community_popular_snapshot",
        lambda country=None, *, lat=None, lon=None, radius_km=25.0, limit=10: {
            "total": 0, "top_species": [], "top_diseases": [],
        },
    )
    with server_main._popular_lock:
        server_main._popular_cache.clear()
        server_main._popular_refreshing.clear()
    yield


def test_popular_no_location_worldwide_placeholder(client):
    # No country + no coords + no scans -> worldwide scope, curated placeholder.
    r = client.get("/api/popular?locale=en")
    assert r.status_code == 200
    body = r.json()
    # Placeholder is cached only briefly so it flips to real data fast.
    assert r.headers["cache-control"] == "public, max-age=300"
    assert body["scope"] == "global"
    assert body["country"] is None
    assert body["source"] == "placeholder"
    assert body["locale"] == "en"
    assert body["items"]
    item = body["items"][0]
    assert {"scientific", "common_en", "common_ru", "image_url", "count", "percent"} <= set(item)
    # Lead images resolve through the by-name server proxy (api/plant-photo?name=…),
    # not raw Wikimedia URLs the app would load directly.
    assert item["image_url"].startswith("api/plant-photo?name=")
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
        lambda country=None, *, lat=None, lon=None, radius_km=25.0, limit=10: {
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
        lambda country=None, *, lat=None, lon=None, radius_km=25.0, limit=10: {
            "total": 5,
            "top_species": [{"species": "Ficus elastica", "count": 3, "percent": 60}],
            "top_diseases": [{"disease": "leaf_spot", "count": 2, "percent": 40}],
        },
    )
    r = client.get("/api/popular?country=US&locale=en")
    assert r.status_code == 200
    body = r.json()
    # No coords in the request -> country scope (not nearby).
    assert body["source"] == "country"
    assert body["country_name_en"] == "United States"
    assert body["items"][0]["scientific"] == "Ficus elastica"
    assert body["items"][0]["count"] == 3
    assert body["items"][0]["percent"] == 60
    assert body["top_diseases"][0]["percent"] == 40


def test_popular_nearby_scope_when_coords(client, monkeypatch):
    import main as server_main

    # Data only for the coords (nearby) scope; country/worldwide stay empty.
    monkeypatch.setattr(
        server_main, "community_popular_snapshot",
        lambda country=None, *, lat=None, lon=None, radius_km=25.0, limit=10: (
            {
                "total": 4,
                "top_species": [{"species": "Monstera deliciosa", "count": 4, "percent": 100}],
                "top_diseases": [{"disease": "leaf_spot", "count": 4, "percent": 100}],
            }
            if lat is not None
            else {"total": 0, "top_species": [], "top_diseases": []}
        ),
    )
    body = client.get(
        "/api/popular?country=RU&region=Moscow&lat=55.75&lon=37.62&locale=en",
    ).json()
    assert body["source"] == "nearby"
    assert body["region"] == "Moscow"
    assert body["items"][0]["scientific"] == "Monstera deliciosa"
    assert body["items"][0]["percent"] == 100


def test_popular_nearby_without_place_falls_back_to_worldwide(client, monkeypatch):
    """A nearby snapshot we can't attach a real place to (coords but no
    resolvable country and no city) must NOT be labeled "Worldwide" over a
    single local scan — it flips to the true worldwide aggregate so the label
    matches the data."""
    import main as server_main

    monkeypatch.setattr(
        server_main, "community_popular_snapshot",
        # Same data whether called for the nearby (lat set) or worldwide
        # (lat None) scope, so the fallback recompute finds real rows.
        lambda country=None, *, lat=None, lon=None, radius_km=25.0, limit=10: {
            "total": 1,
            "top_species": [{"species": "Colutea arborescens", "count": 1, "percent": 100}],
            "top_diseases": [],
        },
    )
    # Coords only — no country, no region/city (the Abkhazia-style case).
    body = client.get("/api/popular?lat=43.0&lon=41.0&locale=en").json()
    assert body["source"] == "worldwide"
    assert body["scope"] == "global"
    assert body["country"] is None
    assert body["region"] is None
    assert body["items"][0]["scientific"] == "Colutea arborescens"


def test_popular_images_are_english_and_titles_disambiguate(client, monkeypatch):
    """Lead images resolve through the EN proxy regardless of UI locale, and a
    localized title shared by two species falls back to the scientific name so
    the cards stay distinct (the RU 'two identical Spathiphyllum' bug)."""
    import main as server_main

    monkeypatch.setattr(
        server_main, "community_popular_snapshot",
        lambda country=None, *, lat=None, lon=None, radius_km=25.0, limit=10: {
            "total": 2,
            "top_species": [
                {"species": "Spathiphyllum", "count": 5, "percent": 60},
                {"species": "Spathiphyllum floribundum", "count": 3, "percent": 40},
            ],
            "top_diseases": [],
        },
    )
    # Both species collapse to the same localized title in RU.
    monkeypatch.setattr(
        server_main, "fetch_species_info",
        lambda name, lang="en": {"title": "Спатифиллюм",
                                 "image_url": f"https://upload.wikimedia.org/{name}.jpg"},
    )
    body = client.get("/api/popular?country=US&locale=ru").json()
    items = body["items"]
    # Image proxy pinned to English even though locale=ru.
    assert all("locale=en" in it["image_url"] for it in items)
    # First keeps the localized title; the colliding second falls back to sci.
    assert items[0]["common_ru"] == "Спатифиллюм"
    assert items[1]["common_ru"] == "Spathiphyllum floribundum"


def test_popular_for_known_and_unknown():
    from popular_plants import BY_REGION, GLOBAL_DEFAULT, popular_for

    assert popular_for("US") == BY_REGION["US"]
    assert popular_for("us") == BY_REGION["US"]  # case-insensitive
    assert popular_for("ZZ") == GLOBAL_DEFAULT   # unmapped
    assert popular_for(None) == GLOBAL_DEFAULT
    assert popular_for("United States") == GLOBAL_DEFAULT  # full name misses ISO map


def test_popular_curated_countries_have_real_names(client):
    # Every country ``popular_plants.BY_REGION`` curates (plus other
    # frequently-geoip-detected ones) must surface a localized NAME, not the
    # raw ISO code, so the Home title reads "Popular in India" not "IN".
    for code, en, ru in [
        ("IN", "India", "Индия"),
        ("BR", "Brazil", "Бразилия"),
        ("CN", "China", "Китай"),
        ("CA", "Canada", "Канада"),
    ]:
        r = client.get(f"/api/popular?country={code}&locale=en")
        assert r.status_code == 200, code
        body = r.json()
        assert body["scope"] == "country", code
        assert body["country_name_en"] == en
        assert body["country_name_ru"] == ru


def test_thin_community_data_is_topped_up_with_curated_picks(client, monkeypatch):
    """One real scan worldwide must not leave a one-card carousel: real data
    first (with its percentage), then curated picks without counts."""
    import main as server_main
    from popular_plants import GLOBAL_DEFAULT

    monkeypatch.setattr(
        server_main,
        "community_popular_snapshot",
        lambda country=None, *, lat=None, lon=None, radius_km=25.0, limit=10: {
            "total": 1,
            "top_species": [{"species": "Rosa abietina", "count": 1, "percent": 100}],
            "top_diseases": [{"disease": "leaf_spot", "count": 1, "percent": 100}],
        },
    )
    body = client.get("/api/popular?locale=en").json()
    assert body["source"] == "worldwide"
    items = body["items"]
    assert len(items) == min(10, 1 + len(GLOBAL_DEFAULT))
    assert items[0]["scientific"] == "Rosa abietina"
    assert items[0]["percent"] == 100
    assert all(it["count"] == 0 and it["percent"] == 0 for it in items[1:])
    names = [it["scientific"].lower() for it in items]
    assert len(names) == len(set(names))
    assert all(it["image_url"].startswith("api/plant-photo?name=") for it in items)


def test_anonymous_tally_counts_species_and_diagnoses_without_identifiers():
    import community

    community.record_anonymous_scan("Monstera deliciosa", "healthy", day=100)
    community.record_anonymous_scan("monstera  deliciosa", "leaf_spot", day=100)
    community.record_anonymous_scan(None, "leaf_spot", day=101)
    community.record_anonymous_scan("Ficus lyrata", "not_a_plant", day=101)  # hidden class
    snap = community.anonymous_popular_snapshot(limit=10, today=101)
    assert snap["total"] == 3
    assert snap["top_species"] == [{"species": "Monstera deliciosa", "count": 2, "percent": 67}]
    assert snap["top_diseases"][0] == {"disease": "leaf_spot", "count": 2, "percent": 67}
    # Outside the window nothing counts.
    assert community.anonymous_popular_snapshot(today=101 + community.ANON_POPULAR_WINDOW_DAYS + 1)["total"] == 0
    with community._connect() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(popular_scan_counts)")]
    assert cols == ["day", "species_lower", "species", "disease", "n"]


def test_worldwide_popular_comes_from_the_anonymous_tally(client, monkeypatch):
    """Users without location (not in the community) still shape the worldwide
    list: it is built from the anonymous tally of every accepted diagnosis."""
    import community
    import main as server_main

    def _no_community(country=None, *, lat=None, lon=None, radius_km=25.0, limit=10):
        return {"total": 0, "top_species": [], "top_diseases": []}

    monkeypatch.setattr(server_main, "community_popular_snapshot", _no_community)
    for _ in range(3):
        community.record_anonymous_scan("Ficus elastica", "healthy")
    community.record_anonymous_scan("Aloe vera", "leaf_spot")
    with server_main._popular_lock:
        server_main._popular_cache.clear()
    body = client.get("/api/popular?locale=en").json()
    assert body["source"] == "worldwide"
    assert [it["scientific"] for it in body["items"][:2]] == ["Ficus elastica", "Aloe vera"]
    assert body["items"][0]["count"] == 3
    assert body["total"] == 4
