"""Coverage for /api/community/* and the community.py module.

Endpoint tests isolate the DB + thumb directory per test via env-var
indirection, so no two tests can step on each other's rows.
"""

from __future__ import annotations

import io
import sqlite3

import pytest


@pytest.fixture
def isolated_community(tmp_path, monkeypatch):
    """Point the community store at a temp DB + thumb dir per test."""
    db_path = tmp_path / "community.db"
    thumb_dir = tmp_path / "thumbs"
    monkeypatch.setenv("COMMUNITY_DB_PATH", str(db_path))
    monkeypatch.setenv("COMMUNITY_THUMB_DIR", str(thumb_dir))
    monkeypatch.setenv("FEEDBACK_IP_PEPPER", "test-pepper")
    import importlib

    import community as comm
    importlib.reload(comm)
    import main as server_main
    server_main.community_record_scan = comm.record_scan
    server_main.community_nearby_scans = comm.nearby_scans
    server_main.community_region_summary = comm.region_summary
    server_main.community_stats_snapshot = comm.stats_snapshot
    server_main.community_delete_for_user = comm.delete_for_user
    server_main.community_set_wants_opinion = comm.set_wants_opinion
    server_main.community_clear_wants_opinion = comm.clear_wants_opinion
    server_main.community_wants_opinion = comm.wants_opinion
    server_main.community_owner_of = comm.owner_of
    server_main.init_community_db = comm.init_db
    server_main.COMMUNITY_THUMB_DIR = comm.COMMUNITY_THUMB_DIR
    # Community upload now requires a server-issued scan_id (same gate as
    # /api/feedback). These tests use synthetic ids, so treat every id as
    # issued; the rejection path is covered explicitly below.
    monkeypatch.setattr(server_main, "is_scan_id_issued", lambda *a, **k: True)
    yield db_path, thumb_dir


def _form(**fields):
    """Compact form-data builder; pytest-fastapi accepts a dict."""
    return {k: str(v) for k, v in fields.items() if v is not None}


def _png_bytes() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color=(60, 140, 60)).save(buf, format="PNG")
    return buf.getvalue()


# ── Happy paths ─────────────────────────────────────────────────────


def test_community_scan_upload_minimal(client, isolated_community):
    r = client.post(
        "/api/community/scan",
        data=_form(
            scan_id="scanabc123",
            user_pseudo_id="pseudoabc123",
            region="London", country="GB",
            disease="powdery_mildew",
        ),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["id"] > 0


def test_community_scan_upload_stores_client_location(client, isolated_community):
    # Location is now supplied by the (opted-in) client: country + city + the
    # rounded coarse coords. There is no server-side IP/header geo.
    db_path, _ = isolated_community
    r = client.post(
        "/api/community/scan",
        data=_form(
            scan_id="scangeo001",
            user_pseudo_id="pseudogeo01",
            country="US", region="Austin",
            lat=30.27, lon=-97.74,
            disease="powdery_mildew",
        ),
    )
    assert r.status_code == 200, r.text
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT country, region, city, lat, lon FROM community_scans WHERE scan_id = ?",
            ("scangeo001",),
        ).fetchone()
    assert row[0] == "US"
    assert row[1] == "Austin" and row[2] == "Austin"
    assert abs(row[3] - 30.27) < 1e-6 and abs(row[4] - (-97.74)) < 1e-6


def test_community_scan_upload_no_location_stores_empty_country(client, isolated_community):
    # No client location (community off / permission denied) → empty country;
    # such rows only surface in the worldwide aggregate, never "RU"/"GLOBAL".
    db_path, _ = isolated_community
    r = client.post(
        "/api/community/scan",
        data=_form(
            scan_id="scandefault1",
            user_pseudo_id="pseudodefault1",
            disease="healthy",
        ),
    )
    assert r.status_code == 200, r.text
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT country, lat FROM community_scans WHERE scan_id = ?",
            ("scandefault1",),
        ).fetchone()
    assert row[0] == ""
    assert row[1] is None


def test_community_scan_upload_rejects_unissued_scan_id(
    client, isolated_community, monkeypatch,
):
    # Integrity gate: a scan_id the server never issued at /api/analyze is
    # refused (422) so a client can't post a fabricated diagnosis into the
    # public feed under a made-up or copied id — same gate as /api/feedback.
    db_path, _ = isolated_community
    import main as server_main
    monkeypatch.setattr(server_main, "is_scan_id_issued", lambda *a, **k: False)
    r = client.post(
        "/api/community/scan",
        data=_form(
            scan_id="forgedid1234",
            user_pseudo_id="pseudoforged1",
            country="NL", region="Amsterdam",
            disease="gray_mold_botrytis",
            confidence=0.99,
        ),
    )
    assert r.status_code == 422, r.text
    # Nothing was written to the corpus — the id is refused before any DB
    # write, so a follow-up nearby query surfaces no such scan.
    feed = client.get(
        "/api/community/nearby",
        params={"country": "NL", "scope": "country", "user_pseudo_id": "pseudo-check"},
    )
    assert all(item["disease"] != "gray_mold_botrytis" for item in feed.json()["items"])


def test_community_scan_upload_with_thumbnail(client, isolated_community):
    _, thumb_dir = isolated_community
    r = client.post(
        "/api/community/scan",
        data=_form(
            scan_id="scanthumb01",
            user_pseudo_id="pseudothumb",
            region="Berlin", country="DE",
            disease="gray_mold_botrytis",
            confidence=0.71,
            species="Begonia",
            species_score=0.92,
            locale="en",
        ),
        files={"image": ("scan.png", _png_bytes(), "image/png")},
    )
    assert r.status_code == 200
    # Thumbnail file was written.
    assert any(thumb_dir.iterdir()), "thumb dir should contain at least one file"


# ── Validation guard-rails ──────────────────────────────────────────


def test_community_scan_rejects_unknown_disease(client, isolated_community):
    r = client.post(
        "/api/community/scan",
        data=_form(
            scan_id="scanunknown1",
            user_pseudo_id="pseudoabc123",
            region="London", country="GB",
            disease="alien_invasion",
        ),
    )
    assert r.status_code == 422


def test_community_scan_rejects_bad_region(client, isolated_community):
    r = client.post(
        "/api/community/scan",
        data=_form(
            scan_id="scanregion1",
            user_pseudo_id="pseudoabc123",
            region="bad\nregion;DROP TABLE",
            disease="healthy",
        ),
    )
    assert r.status_code == 422


def test_community_scan_accepts_unicode_region(client, isolated_community):
    """Region validation is Unicode-aware: Cyrillic / accented place names
    pass (it filters control chars + injection punctuation, not non-Latin
    letters). The country name additionally canonicalizes to its ISO code
    ("Россия" -> "RU") while the locality is preserved verbatim."""
    db_path, _ = isolated_community
    r = client.post(
        "/api/community/scan",
        data=_form(
            scan_id="scanru000001",
            user_pseudo_id="pseudoabc123",
            country="Россия", region="Москва",
            disease="healthy",
        ),
    )
    assert r.status_code == 200, r.text
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT country, region FROM community_scans WHERE scan_id = ?",
            ("scanru000001",),
        ).fetchone()
    assert row == ("RU", "Москва")


def test_community_scan_rejects_short_scan_id(client, isolated_community):
    r = client.post(
        "/api/community/scan",
        data=_form(
            scan_id="short",
            user_pseudo_id="pseudoabc123",
            region="London", country="GB",
            disease="healthy",
        ),
    )
    assert r.status_code == 422


def test_community_scan_thumbnail_size_limit(client, isolated_community):
    # 1 MB of bytes triggers the COMMUNITY_MAX_THUMB_BYTES guard inside
    # _save_thumbnail; the row is still recorded but with NULL
    # thumb_path. The endpoint succeeds.
    big = b"\x89PNG\r\n\x1a\n" + b"0" * (1 * 1024 * 1024)
    r = client.post(
        "/api/community/scan",
        data=_form(
            scan_id="scanbig00001",
            user_pseudo_id="pseudoabc123",
            region="London", country="GB",
            disease="healthy",
        ),
        files={"image": ("big.png", big, "image/png")},
    )
    assert r.status_code == 200


# ── Nearby + region summary ─────────────────────────────────────────


def test_community_nearby_lists_recent_rows(client, isolated_community):
    for i, disease in enumerate([
        "powdery_mildew", "powdery_mildew", "gray_mold_botrytis", "healthy",
    ]):
        client.post(
            "/api/community/scan",
            data=_form(
                scan_id=f"scanrow{i:04d}",
                user_pseudo_id="pseudo-london-x",
                region="London", country="GB",
                disease=disease,
            ),
        )
    r = client.get(
        "/api/community/nearby",
        params={"region": "London", "country": "GB", "disease": "powdery_mildew", "limit": 10},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    diseases = [item["disease"] for item in body["items"]]
    # Same-disease rows are listed first.
    assert diseases[:2] == ["powdery_mildew", "powdery_mildew"]
    # Region summary reports total + last_7d.
    assert body["summary"]["total"] == 4
    assert body["summary"]["last_7d"] == 4


def test_community_nearby_excludes_requesting_user(client, isolated_community):
    client.post(
        "/api/community/scan",
        data=_form(
            scan_id="scanselfid01",
            user_pseudo_id="pseudo-self-x",
            region="Paris", country="FR",
            disease="healthy",
        ),
    )
    client.post(
        "/api/community/scan",
        data=_form(
            scan_id="scanotherid1",
            user_pseudo_id="pseudo-other-y",
            region="Paris", country="FR",
            disease="healthy",
        ),
    )
    r = client.get(
        "/api/community/nearby",
        params={"region": "Paris", "country": "FR", "user_pseudo_id": "pseudo-self-x"},
    )
    body = r.json()
    # The feed exposes the PUBLIC token in the scan_id field — never the
    # real (feedback-authorising) scan_id.
    import community as comm
    assert {item["scan_id"] for item in body["items"]} \
        == {comm.public_scan_token("scanotherid1")}
    # The summary block must apply the same exclusion. Otherwise the
    # chip row on Android shows a phantom disease — the user's own
    # scan they just uploaded — alongside the carousel that already
    # filters them out, which was confusing in practice.
    summary = body["summary"]
    assert summary["total"] == 1
    assert summary["last_7d"] == 1
    assert [d["disease"] for d in summary["top_diseases"]] == ["healthy"]
    assert summary["top_diseases"][0]["count"] == 1


def test_community_nearby_hides_not_a_plant(client, isolated_community):
    """``not_a_plant`` is noise in the community feed — typically faces
    or random objects — and must not surface in either the carousel
    rows or the summary aggregates, even if it's the freshest row in
    the bucket.
    """
    # One healthy row + two not_a_plant rows, none from the requesting
    # user so the user-exclusion filter can't account for the result.
    client.post(
        "/api/community/scan",
        data=_form(
            scan_id="hidehealthy1",
            user_pseudo_id="pseudo-feed-x",
            region="Berlin", country="DE",
            disease="healthy",
        ),
    )
    for sid in ("hidenotplant1", "hidenotplant2"):
        client.post(
            "/api/community/scan",
            data=_form(
                scan_id=sid,
                user_pseudo_id="pseudo-feed-x",
                region="Berlin", country="DE",
                disease="not_a_plant",
            ),
        )
    r = client.get(
        "/api/community/nearby",
        params={"region": "Berlin", "country": "DE"},
    )
    body = r.json()
    assert {item["disease"] for item in body["items"]} == {"healthy"}
    summary = body["summary"]
    assert summary["total"] == 1
    assert summary["last_7d"] == 1
    assert [d["disease"] for d in summary["top_diseases"]] == ["healthy"]

    # Even an explicit ``disease=not_a_plant`` query must not surface
    # any hidden rows. The filler stage may return other diagnoses,
    # but no item in the carousel can carry the hidden disease.
    r2 = client.get(
        "/api/community/nearby",
        params={"region": "Berlin", "country": "DE", "disease": "not_a_plant"},
    )
    assert all(item["disease"] != "not_a_plant" for item in r2.json()["items"])


def test_community_nearby_disease_filter_is_strict(client, isolated_community):
    """A disease-scoped query must return ONLY that disease — never the
    region/country fillers of other diseases. This is the "similar scans
    near you" contract: same-disease or nothing, so the user is never
    shown an unrelated verdict dressed up as a 'similar' scan.
    """
    for i, disease in enumerate([
        "powdery_mildew", "gray_mold_botrytis", "healthy", "leaf_spot",
    ]):
        client.post(
            "/api/community/scan",
            data=_form(
                scan_id=f"strict{i:05d}",
                user_pseudo_id="pseudo-strict-x",
                region="Lyon", country="FR",
                disease=disease,
            ),
        )
    r = client.get(
        "/api/community/nearby",
        params={"region": "Lyon", "country": "FR", "disease": "powdery_mildew", "limit": 10},
    )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    # Only the single powdery_mildew row — no gray_mold / healthy / leaf_spot
    # fillers padding the carousel.
    assert [item["disease"] for item in items] == ["powdery_mildew"]


def test_community_nearby_worldwide_ignores_location(client, isolated_community):
    """``scope=worldwide`` returns scans from every country, newest first,
    regardless of the requester's country/coords — and needs no location, so
    ``location_required`` is never set even when the caller sends nothing."""
    for i, country in enumerate(["PL", "US", "JP", "BR"]):
        client.post(
            "/api/community/scan",
            data=_form(
                scan_id=f"world{i:05d}",
                user_pseudo_id="pseudo-world-up",
                region="Somewhere", country=country,
                disease="leaf_spot",
            ),
        )
    # No country, no coords, no region — a geo-scoped feed would be empty and
    # flag location_required; worldwide fills regardless.
    r = client.get(
        "/api/community/nearby",
        params={"scope": "worldwide", "user_pseudo_id": "pseudo-viewer-w", "limit": 10},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scope"] == "worldwide"
    assert body["location_required"] is False
    assert len(body["items"]) == 4  # all four countries surfaced
    # Backward compat: the nested summary.country must never serialize as null
    # (older installed builds decode it as a non-null String and crash on null).
    assert body["summary"]["country"] == ""

    # Country scope with no anchor still flags location_required (contrast).
    r_country = client.get(
        "/api/community/nearby",
        params={"scope": "country", "user_pseudo_id": "pseudo-viewer-w"},
    )
    assert r_country.json()["location_required"] is True


def test_community_request_opinion_flags_owner_scan(client, isolated_community):
    """The scanner can flag THEIR OWN scan as wanting a second opinion;
    the flag then surfaces on the nearby feed (``wants_opinion: true``)
    for other users to act on. A non-owner can't flag the scan."""
    client.post(
        "/api/community/scan",
        data=_form(
            scan_id="wantsopinion1",
            user_pseudo_id="pseudo-owner-x",
            region="Oslo", country="NO",
            disease="leaf_spot",
        ),
    )
    def feed_wants_opinion() -> bool:
        items = client.get(
            "/api/community/nearby",
            params={"region": "Oslo", "country": "NO",
                    "user_pseudo_id": "pseudo-viewer-z"},
        ).json()["items"]
        import community as comm
        assert len(items) == 1 \
            and items[0]["scan_id"] == comm.public_scan_token("wantsopinion1")
        return items[0]["wants_opinion"]

    # A non-owner's request is recorded (the endpoint never errors), but
    # owner-scoping means it is NEVER applied to the scan — the feed flag
    # stays false.
    r_wrong = client.post(
        "/api/community/request-opinion",
        json={"scan_id": "wantsopinion1", "user_pseudo_id": "pseudo-not-owner"},
    )
    assert r_wrong.status_code == 200, r_wrong.text
    assert r_wrong.json() == {"ok": True, "flagged": True}
    assert feed_wants_opinion() is False

    # The owner flags it → the flag surfaces to other users on the feed.
    r_owner = client.post(
        "/api/community/request-opinion",
        json={"scan_id": "wantsopinion1", "user_pseudo_id": "pseudo-owner-x"},
    )
    assert r_owner.status_code == 200, r_owner.text
    assert r_owner.json() == {"ok": True, "flagged": True}
    assert feed_wants_opinion() is True


def test_community_request_opinion_missing_row_records_intent(client, isolated_community):
    """Flagging a scan that hasn't made it into the corpus yet (the upload
    raced behind the tap) is recorded durably — ``flagged: true`` — and
    applied when the scan later lands, instead of being a silent no-op."""
    r = client.post(
        "/api/community/request-opinion",
        json={"scan_id": "racedupload", "user_pseudo_id": "pseudo-owner-x"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "flagged": True}

    # When the upload finally arrives, the pending request materialises.
    client.post(
        "/api/community/scan",
        data=_form(
            scan_id="racedupload",
            user_pseudo_id="pseudo-owner-x",
            region="Oslo", country="NO",
            disease="leaf_spot",
        ),
    )
    items = client.get(
        "/api/community/nearby",
        params={"region": "Oslo", "country": "NO", "user_pseudo_id": "pseudo-viewer-z"},
    ).json()["items"]
    import community as comm
    assert len(items) == 1 \
        and items[0]["scan_id"] == comm.public_scan_token("racedupload")
    assert items[0]["wants_opinion"] is True


def test_community_request_opinion_cancel(client, isolated_community):
    """The owner can withdraw an open request; the feed badge drops and the
    endpoint is owner-scoped + idempotent."""
    client.post(
        "/api/community/scan",
        data=_form(
            scan_id="cancelme1",
            user_pseudo_id="pseudo-owner-x",
            region="Oslo", country="NO",
            disease="leaf_spot",
        ),
    )
    client.post(
        "/api/community/request-opinion",
        json={"scan_id": "cancelme1", "user_pseudo_id": "pseudo-owner-x"},
    )

    def feed_wants_opinion() -> bool:
        items = client.get(
            "/api/community/nearby",
            params={"region": "Oslo", "country": "NO",
                    "user_pseudo_id": "pseudo-viewer-z"},
        ).json()["items"]
        return items[0]["wants_opinion"]

    assert feed_wants_opinion() is True

    # A non-owner can't withdraw someone else's request.
    r_wrong = client.post(
        "/api/community/request-opinion/cancel",
        json={"scan_id": "cancelme1", "user_pseudo_id": "pseudo-not-owner"},
    )
    assert r_wrong.status_code == 200, r_wrong.text
    assert r_wrong.json() == {"ok": True, "cleared": False}
    assert feed_wants_opinion() is True

    # The owner cancels → badge drops; a repeat cancel is a clean no-op.
    r_owner = client.post(
        "/api/community/request-opinion/cancel",
        json={"scan_id": "cancelme1", "user_pseudo_id": "pseudo-owner-x"},
    )
    assert r_owner.status_code == 200, r_owner.text
    assert r_owner.json() == {"ok": True, "cleared": True}
    assert feed_wants_opinion() is False
    r_again = client.post(
        "/api/community/request-opinion/cancel",
        json={"scan_id": "cancelme1", "user_pseudo_id": "pseudo-owner-x"},
    )
    assert r_again.json() == {"ok": True, "cleared": False}


def test_community_nearby_handles_empty_region(client, isolated_community):
    r = client.get(
        "/api/community/nearby",
        params={"region": "Nowheresville", "country": "ZZ"},
    )
    assert r.status_code == 200
    assert r.json()["items"] == []
    assert r.json()["summary"]["total"] == 0


def test_community_nearby_no_location_flags_location_required(client, isolated_community):
    # Neither coords nor country → the feed can't be geo-scoped. The response
    # must say so explicitly so the client prompts "turn on location" instead
    # of a misleading "no scans near you".
    r = client.get(
        "/api/community/nearby",
        params={"user_pseudo_id": "pseudo-no-geo"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["items"] == []
    assert body["location_required"] is True


def test_community_nearby_with_location_not_flagged(client, isolated_community):
    # A country-scoped request has a geo anchor, so location_required is False.
    r = client.get(
        "/api/community/nearby",
        params={"country": "US", "scope": "country", "user_pseudo_id": "pseudo-geo"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["location_required"] is False


def test_community_nearby_country_param(client, isolated_community):
    # Location is client-supplied now (no header geo); a country-scoped query
    # passes country explicitly.
    client.post(
        "/api/community/scan",
        data=_form(
            scan_id="scangeofeed1",
            user_pseudo_id="pseudo-other-y",
            country="US",
            disease="healthy",
        ),
    )
    r = client.get(
        "/api/community/nearby",
        params={"country": "US", "scope": "country", "user_pseudo_id": "pseudo-viewer-q"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scope"] == "country"
    assert body["country"] == "US"
    assert body["summary"]["country_name_en"] == "United States"
    assert len(body["items"]) == 1


# ── nearby (30 km radius) vs country filter toggle ─────────────────


def test_community_nearby_scope_toggle(client, isolated_community):
    """`scope=nearby` restricts the feed to a ~30 km radius of the requester's
    coords; `scope=country` widens to the whole country. Coords ride in on the
    upload's lat/lon and the query's lat/lon."""
    # Two scans ~3 km apart in Warsaw, one ~300 km away in Gdańsk — all PL.
    client.post("/api/community/scan", data=_form(
        scan_id="nearwarsaw01", user_pseudo_id="pseudo-near-a",
        country="PL", region="Warsaw", lat=52.23, lon=21.01, disease="leaf_spot"))
    client.post("/api/community/scan", data=_form(
        scan_id="nearwarsaw02", user_pseudo_id="pseudo-near-a",
        country="PL", region="Warsaw", lat=52.25, lon=21.03, disease="leaf_spot"))
    client.post("/api/community/scan", data=_form(
        scan_id="fargdansk001", user_pseudo_id="pseudo-near-a",
        country="PL", region="Gdansk", lat=54.35, lon=18.65, disease="leaf_spot"))

    import community as comm
    # nearby (within 30 km of Warsaw): only the two Warsaw scans.
    near = client.get("/api/community/nearby", params={
        "country": "PL", "lat": 52.24, "lon": 21.02, "scope": "nearby",
        "user_pseudo_id": "pseudo-viewer-z"}).json()
    assert near["scope"] == "nearby"
    assert {it["scan_id"] for it in near["items"]} == {
        comm.public_scan_token("nearwarsaw01"),
        comm.public_scan_token("nearwarsaw02"),
    }

    # country: all three PL scans regardless of distance.
    whole = client.get("/api/community/nearby", params={
        "country": "PL", "scope": "country",
        "user_pseudo_id": "pseudo-viewer-z"}).json()
    assert whole["scope"] == "country"
    assert {it["scan_id"] for it in whole["items"]} == {
        comm.public_scan_token("nearwarsaw01"),
        comm.public_scan_token("nearwarsaw02"),
        comm.public_scan_token("fargdansk001"),
    }


def test_community_nearby_attaches_distance_and_hides_coords(client, isolated_community):
    """Nearby feed rows carry an approximate distance from the requester
    ('~5 km near you') but NEVER the other user's raw coords."""
    client.post("/api/community/scan", data=_form(
        scan_id="distnearwarsaw", user_pseudo_id="pseudo-near-a",
        country="PL", region="Warsaw", lat=52.23, lon=21.01, disease="leaf_spot"))
    items = client.get("/api/community/nearby", params={
        "country": "PL", "lat": 52.24, "lon": 21.02, "scope": "nearby",
        "user_pseudo_id": "pseudo-viewer-z"}).json()["items"]
    assert len(items) == 1
    item = items[0]
    # Distance present, within the radius, and coords stripped.
    assert item["distance_km"] is not None
    assert 0 <= item["distance_km"] <= 30
    assert "lat" not in item and "lon" not in item


def test_community_nearby_no_viewer_coords_omits_distance(client, isolated_community):
    """Country scope (no requester coords) → distance is null, coords stripped."""
    client.post("/api/community/scan", data=_form(
        scan_id="distcountry001", user_pseudo_id="pseudo-other-y",
        country="US", disease="healthy"))
    item = client.get("/api/community/nearby", params={
        "country": "US", "scope": "country",
        "user_pseudo_id": "pseudo-viewer-q"}).json()["items"][0]
    assert item["distance_km"] is None
    assert "lat" not in item and "lon" not in item


# ── Localized per-item country names (no raw "PL" codes) ────────────


def test_community_nearby_localizes_item_country(client, isolated_community):
    """Feed rows carry localized country NAMES so cards render 'Poland' /
    'Польша' instead of the raw 'PL' ISO code."""
    client.post("/api/community/scan", data=_form(
        scan_id="localename01", user_pseudo_id="pseudo-pl-b",
        country="PL", region="Warsaw", disease="leaf_spot"))
    item = client.get(
        "/api/community/nearby",
        params={"country": "PL", "scope": "country",
                "user_pseudo_id": "pseudo-viewer-x"},
    ).json()["items"][0]
    assert item["country"] == "PL"
    assert item["country_name_en"] == "Poland"
    assert item["country_name_ru"] == "Польша"


def test_community_scan_nickname_round_trips(client, isolated_community):
    """The author's generated plant-vibe handle rides along on the upload and
    comes back on the feed row (incl. a Cyrillic handle)."""
    client.post("/api/community/scan", data=_form(
        scan_id="nicktest0001", user_pseudo_id="pseudo-nick-a",
        country="PL", region="Warsaw", lat=52.23, lon=21.01,
        disease="leaf_spot", nickname="Томатный Король 42"))
    item = client.get(
        "/api/community/nearby",
        params={"country": "PL", "scope": "country", "user_pseudo_id": "pseudo-viewer"},
    ).json()["items"][0]
    assert item["nickname"] == "Томатный Король 42"


# ── Delete-for-user ─────────────────────────────────────────────────


def test_community_delete_user_purges_rows_and_thumbs(client, isolated_community):
    db_path, thumb_dir = isolated_community
    client.post(
        "/api/community/scan",
        data=_form(
            scan_id="scandel00001",
            user_pseudo_id="pseudo-delete-x",
            region="Tokyo", country="JP",
            disease="healthy",
        ),
        files={"image": ("scan.png", _png_bytes(), "image/png")},
    )
    assert any(thumb_dir.iterdir())
    r = client.delete("/api/community/user/pseudo-delete-x")
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] >= 1
    # Thumb file gone, row gone.
    assert not any(thumb_dir.iterdir())
    with sqlite3.connect(str(db_path)) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM community_scans WHERE user_pseudo_id = ?",
            ("pseudo-delete-x",),
        ).fetchone()[0]
    assert n == 0


def test_community_thumb_rejects_path_traversal(client, isolated_community):
    # Starlette decodes `%2F` to `/` before matching, so a traversal
    # attempt either hits the explicit-guard 400 (if URL-decode keeps
    # `..` in the filename slot) OR misses the route entirely (404
    # from the router). Both outcomes are safe; we just assert it
    # never reaches the read.
    r = client.get("/api/community/thumb/..%2Fetc%2Fpasswd")
    assert r.status_code in (400, 404)
    r2 = client.get("/api/community/thumb/..%5Cetc%5Cpasswd")  # backslash variant
    assert r2.status_code in (400, 404)


def test_community_thumb_404_for_missing(client, isolated_community):
    r = client.get("/api/community/thumb/does-not-exist.jpg")
    assert r.status_code == 404


# ── /api/metrics surfaces the corpus ────────────────────────────────


def test_metrics_surfaces_community_block(client, isolated_community):
    client.post(
        "/api/community/scan",
        data=_form(
            scan_id="scanmetric01",
            user_pseudo_id="pseudo-metric",
            region="Madrid", country="ES",
            disease="healthy",
        ),
    )
    m = client.get("/api/metrics").json()
    assert "community" in m
    assert m["community"]["total"] >= 1
    assert m["community"]["regions"] >= 1


def test_country_groups_with_region_breakdown(client, isolated_community):
    """The corpus stores country + city (in the ``region`` column). A
    country-scoped query returns every city and surfaces a top-cities
    breakdown in the summary."""
    db_path, _ = isolated_community
    writes = (
        dict(scan_id="scangb-london-1", country="GB", region="London"),
        dict(scan_id="scangb-london-2", country="GB", region="London"),
        dict(scan_id="scangb-mancunian1", country="GB", region="Manchester"),
        dict(scan_id="scangb-bristol001", country="GB", region="Bristol"),
    )
    for w in writes:
        r = client.post(
            "/api/community/scan",
            data=_form(
                scan_id=w["scan_id"],
                user_pseudo_id="pseudo-gb-mixed",
                country=w["country"],
                region=w["region"],
                disease="healthy",
            ),
        )
        assert r.status_code == 200, r.text

    # Persisted country is "GB"; the region column carries the city.
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT country, region FROM community_scans ORDER BY id"
        ).fetchall()
    assert {row[0] for row in rows} == {"GB"}
    assert {row[1] for row in rows} == {"London", "Manchester", "Bristol"}

    # Country-scoped query returns everything + a top-cities breakdown.
    r_country = client.get(
        "/api/community/nearby", params={"country": "GB", "scope": "country"},
    ).json()
    assert r_country["summary"]["total"] == 4
    assert r_country["summary"]["country"] == "GB"
    top_region_names = {r["region"] for r in r_country["summary"]["top_regions"]}
    assert top_region_names >= {"London", "Manchester", "Bristol"}


# ── Regression: thumbnail magic-byte validation ─────────────────────


def test_community_scan_rejects_non_image_thumbnail_bytes(client, isolated_community):
    """Bytes that aren't a real JPEG/PNG/WebP are refused even when the
    multipart Content-Type claims image/png — the stored thumbnail is later
    re-served as an image and must never carry arbitrary attacker bytes."""
    _, thumb_dir = isolated_community
    r = client.post(
        "/api/community/scan",
        data=_form(
            scan_id="scanbadbytes1",
            user_pseudo_id="pseudobadbyte",
            region="London", country="GB",
            disease="powdery_mildew",
        ),
        files={"image": ("scan.png", b"<html>not an image</html>", "image/png")},
    )
    assert r.status_code == 400
    assert not thumb_dir.exists() or not any(thumb_dir.iterdir())


# ── Regression: duplicate upload leaves no orphan thumbnail ─────────


def test_duplicate_upload_does_not_orphan_thumbnail(client, isolated_community):
    _, thumb_dir = isolated_community
    form = _form(
        scan_id="scandup00001",
        user_pseudo_id="pseudodup001",
        region="Berlin", country="DE",
        disease="leaf_spot",
    )
    r1 = client.post(
        "/api/community/scan", data=form,
        files={"image": ("a.png", _png_bytes(), "image/png")},
    )
    assert r1.status_code == 200
    files_after_first = sorted(p.name for p in thumb_dir.iterdir())
    # Second upload of the same scan_id (different bytes) dedups on the
    # row AND must not write a new, never-referenced thumbnail file.
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color=(200, 30, 30)).save(buf, format="PNG")
    r2 = client.post(
        "/api/community/scan", data=form,
        files={"image": ("b.png", buf.getvalue(), "image/png")},
    )
    assert r2.status_code == 200
    assert r2.json()["id"] == r1.json()["id"]
    assert sorted(p.name for p in thumb_dir.iterdir()) == files_after_first


# ── Regression: delete_for_user / prune sweep companion tables ──────


def test_delete_for_user_sweeps_opinions_and_views_on_own_scans(isolated_community):
    import community as comm

    comm.record_scan(
        scan_id="scanowned001", user_pseudo_id="owner0000001",
        country="GB", region="London", disease="leaf_spot",
    )
    comm.record_opinion(
        scan_id="scanowned001", author_pseudo_id="viewer000001",
        suggested_disease="powdery_mildew",
    )
    comm.record_view("scanowned001", "viewer000001")
    assert comm.view_count_for("scanowned001") == 1

    deleted = comm.delete_for_user("owner0000001")
    assert deleted == 1
    db_path, _ = isolated_community
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM community_opinions WHERE scan_id='scanowned001'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM community_scan_views WHERE scan_id='scanowned001'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_prune_sweeps_orphaned_companion_rows(isolated_community):
    import time as _time

    import community as comm

    comm.init_db()
    old = int(_time.time()) - 200 * 86_400
    db_path, _ = isolated_community
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO community_scans (scan_id, user_pseudo_id, country, "
            "region, disease, created_at) VALUES ('scanold00001','u0000000001',"
            "'GB','','leaf_spot',?)", (old,),
        )
        conn.execute(
            "INSERT INTO community_opinions (scan_id, author_pseudo_id, "
            "suggested_disease, created_at) VALUES ('scanold00001',"
            "'u0000000002','healthy',?)", (old,),
        )
        conn.execute(
            "INSERT INTO community_scan_views (scan_id, viewer_pseudo_id, "
            "created_at) VALUES ('scanold00001','u0000000002',?)", (old,),
        )
        conn.commit()
    finally:
        conn.close()

    assert comm.prune_older_than(90) == 1
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM community_opinions"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM community_scan_views"
        ).fetchone()[0] == 0
    finally:
        conn.close()


# ── Public scan tokens (feedback-gate leak closed) ──────────────────


def test_feed_token_round_trip_for_opinions_and_views(client, isolated_community):
    """Viewers only ever see the public token; opinions and views posted
    with it must resolve to the real scan_id so the owner (who uses the
    real id) sees them."""
    import community as comm

    client.post(
        "/api/community/scan",
        data=_form(
            scan_id="realscanid01",
            user_pseudo_id="pseudo-owner-1",
            region="Paris", country="FR",
            disease="leaf_spot",
        ),
    )
    feed = client.get(
        "/api/community/nearby",
        params={"country": "FR", "user_pseudo_id": "pseudo-viewer-1"},
    ).json()["items"]
    token = feed[0]["scan_id"]
    assert token == comm.public_scan_token("realscanid01")
    assert token != "realscanid01"
    # Thumb filenames must not leak the real id either.
    assert "realscanid01" not in (feed[0].get("thumb_path") or "")

    # Viewer posts an opinion + view against the token.
    r = client.post(
        "/api/community/opinion",
        json={
            "scan_id": token,
            "user_pseudo_id": "pseudo-viewer-1",
            "suggested_disease": "powdery_mildew",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 1
    r = client.post(
        "/api/community/view",
        json={"scan_id": token, "user_pseudo_id": "pseudo-viewer-1"},
    )
    assert r.json()["counted"] is True

    # Owner reads activity under the REAL id and sees both.
    own = client.get("/api/community/opinions/realscanid01").json()
    assert len(own["opinions"]) == 1
    assert own["opinions"][0]["suggested_disease"] == "powdery_mildew"
    assert own["views"] == 1
    # Viewer reads under the token and sees the same.
    via_token = client.get(f"/api/community/opinions/{token}").json()
    assert len(via_token["opinions"]) == 1


def test_opinion_push_only_when_owner_requested(client, isolated_community, monkeypatch):
    """The owner is only pushed about a new opinion when they explicitly asked
    for a second opinion. Sharing a scan to the corpus is not such a request."""
    import main as server_main

    pushes: list[tuple] = []
    monkeypatch.setattr(
        server_main, "push_send_opinion",
        lambda owner, sid, disease, note, nickname: pushes.append((owner, sid, disease)),
    )

    # Two shared scans by the same owner; only the second is flagged for input.
    for sid in ("nudgeoff0001", "nudgeon00001"):
        client.post(
            "/api/community/scan",
            data=_form(
                scan_id=sid, user_pseudo_id="pseudo-owner-9",
                region="Paris", country="FR", disease="leaf_spot",
            ),
        )
    client.post(
        "/api/community/request-opinion",
        json={"scan_id": "nudgeon00001", "user_pseudo_id": "pseudo-owner-9"},
    )

    # A viewer weighs in on both scans.
    for sid in ("nudgeoff0001", "nudgeon00001"):
        r = client.post(
            "/api/community/opinion",
            json={
                "scan_id": sid,
                "user_pseudo_id": "pseudo-viewer-9",
                "suggested_disease": "powdery_mildew",
            },
        )
        assert r.status_code == 200, r.text

    # Only the requested scan triggered a push to the owner.
    assert [p[1] for p in pushes] == ["nudgeon00001"]
    assert pushes[0][0] == "pseudo-owner-9"


def test_opinion_on_unknown_scan_rejected(client, isolated_community):
    r = client.post(
        "/api/community/opinion",
        json={
            "scan_id": "neverssharedscan1",
            "user_pseudo_id": "pseudo-viewer-1",
            "suggested_disease": "healthy",
        },
    )
    assert r.status_code == 422


def test_opinion_on_rotated_scan_rejected_410(client, isolated_community):
    """Public handles rotate every 30 days; a scan older than that goes
    read-only and a new opinion against it is refused with 410 Gone."""
    import time as _time

    import community as comm

    db_path, _ = isolated_community
    client.post(
        "/api/community/scan",
        data=_form(
            scan_id="staleshared01",
            user_pseudo_id="pseudo-owner-1",
            region="Paris", country="FR",
            disease="leaf_spot",
        ),
    )
    token = comm.public_scan_token("staleshared01")
    # A fresh opinion lands fine.
    fresh = client.post(
        "/api/community/opinion",
        json={
            "scan_id": token,
            "user_pseudo_id": "pseudo-viewer-1",
            "suggested_disease": "powdery_mildew",
        },
    )
    assert fresh.status_code == 200, fresh.text

    # Backdate the scan past the rotation window → new opinions refused.
    stale = int(_time.time()) - (comm.IDENTITY_ROTATION_DAYS + 1) * 86_400
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE community_scans SET created_at = ? WHERE scan_id = ?",
            (stale, "staleshared01"),
        )
    r = client.post(
        "/api/community/opinion",
        json={
            "scan_id": token,
            "user_pseudo_id": "pseudo-viewer-2",
            "suggested_disease": "healthy",
        },
    )
    assert r.status_code == 410, r.text


def test_legacy_rows_backfilled_with_public_id_and_thumb_rename(tmp_path, monkeypatch):
    """Rows + thumbnails created before the public_id column existed get
    migrated on init: token assigned, thumbnail renamed off the real id."""
    import importlib
    import time as _time

    monkeypatch.setenv("COMMUNITY_DB_PATH", str(tmp_path / "community.db"))
    monkeypatch.setenv("COMMUNITY_THUMB_DIR", str(tmp_path / "thumbs"))
    import community as comm
    importlib.reload(comm)

    # Simulate a legacy DB: table without public_id, plus a legacy thumb.
    (tmp_path / "thumbs").mkdir()
    (tmp_path / "thumbs" / "legacyscan01_abc.jpg").write_bytes(b"\xff\xd8\xffthumb")
    conn = sqlite3.connect(tmp_path / "community.db")
    conn.executescript(
        "CREATE TABLE community_scans ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, scan_id TEXT NOT NULL,"
        " user_pseudo_id TEXT NOT NULL, country TEXT, region TEXT NOT NULL,"
        " disease TEXT NOT NULL, confidence REAL, species TEXT,"
        " species_lower TEXT, species_score REAL, thumb_path TEXT,"
        " locale TEXT, client_ip_hash TEXT,"
        " wants_opinion INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL)"
    )
    conn.execute(
        "INSERT INTO community_scans (scan_id, user_pseudo_id, country, region,"
        " disease, thumb_path, created_at) VALUES (?,?,?,?,?,?,?)",
        ("legacyscan01", "pseudo-legacy", "FR", "Paris", "leaf_spot",
         "legacyscan01_abc.jpg", int(_time.time())),
    )
    conn.commit()
    conn.close()

    comm.init_db()
    token = comm.public_scan_token("legacyscan01")
    assert comm.resolve_scan_token(token) == "legacyscan01"
    rows = comm.nearby_scans(country="FR")
    assert rows[0]["public_id"] == token
    assert rows[0]["thumb_path"] == f"{token}_abc.jpg"
    assert (tmp_path / "thumbs" / f"{token}_abc.jpg").exists()
    assert not (tmp_path / "thumbs" / "legacyscan01_abc.jpg").exists()
