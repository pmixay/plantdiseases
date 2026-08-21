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
    server_main.community_recommended_scans = comm.recommended_scans
    server_main.community_encode_recommendation_cursor = comm.encode_recommendation_cursor
    server_main.community_decode_recommendation_cursor = comm.decode_recommendation_cursor
    server_main.community_region_summary = comm.region_summary
    server_main.community_stats_snapshot = comm.stats_snapshot
    server_main.community_set_wants_opinion = comm.set_wants_opinion
    server_main.community_clear_wants_opinion = comm.clear_wants_opinion
    server_main.community_wants_opinion = comm.wants_opinion
    server_main.community_owner_of = comm.owner_of
    server_main.community_record_requested_opinion = comm.record_requested_opinion
    server_main.community_opinions_for = comm.opinions_for
    server_main.community_report_content = comm.report_content
    server_main.community_block_content_author = comm.block_content_author
    server_main.community_lookup_author = comm.lookup_author
    server_main.community_resolve_author = comm.resolve_author
    server_main.community_touch_author = comm.touch_author
    server_main.community_daily_safety_writes = comm.daily_safety_count_for_ip_hash
    server_main.OpinionLockedError = comm.OpinionLockedError
    server_main.OpinionOwnerError = comm.OpinionOwnerError
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


def _request_opinion(client, scan_id: str, owner: str):
    response = client.post(
        "/api/community/request-opinion",
        json={"scan_id": scan_id, "user_pseudo_id": owner},
    )
    assert response.status_code == 200, response.text
    assert response.json()["flagged"] is True


def _adopt_author(client, key: str, pseudo: str, name: str = "Test grower"):
    response = client.post(
        "/api/community/authors/adopt",
        headers={"X-Community-Author-Key": key},
        json={"seed_display_name": name, "legacy_pseudo_id": pseudo},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _png_bytes() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color=(60, 140, 60)).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_with_exif_bytes(size=(1200, 900)) -> bytes:
    """An oversized (> the 512px thumbnail cap), EXIF-carrying JPEG — proof
    material for _save_thumbnail actually decoding and re-encoding rather
    than writing client bytes straight through."""
    from PIL import Image
    img = Image.new("RGB", size, color=(60, 140, 60))
    buf = io.BytesIO()
    exif = Image.Exif()
    exif[0x010F] = "TestMake"  # Make — stands in for the GPS tags a real
    exif[0x0131] = "TestSoftware"  # phone photo would carry alongside them.
    img.save(buf, format="JPEG", exif=exif)
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


def test_community_thumbnail_is_decoded_downscaled_and_exif_stripped(client, isolated_community):
    """_save_thumbnail used to write whatever bytes the client sent straight
    to disk — its own docstring claimed a 512px JPEG while enforcing nothing
    of the kind. A modified client could put a full-resolution, GPS-tagged
    photo behind a publicly served .jpg filename. Now it actually decodes."""
    _, thumb_dir = isolated_community
    r = client.post(
        "/api/community/scan",
        data=_form(
            scan_id="scanexif0001",
            user_pseudo_id="pseudoexif1",
            region="Berlin", country="DE",
            disease="healthy",
        ),
        files={"image": ("scan.jpg", _jpeg_with_exif_bytes((1200, 900)), "image/jpeg")},
    )
    assert r.status_code == 200, r.text

    files = list(thumb_dir.iterdir())
    assert len(files) == 1
    from PIL import Image
    with Image.open(files[0]) as saved:
        assert max(saved.size) <= 512
        assert not saved.getexif()


def test_community_thumbnail_rejects_undecodable_bytes(client, isolated_community):
    """Bytes that pass the size cap but aren't a real image (a modified
    client, or a truncated upload) must not land on disk at all — the row
    is still recorded (best-effort thumbnail), just with no thumb_path."""
    db_path, thumb_dir = isolated_community
    garbage = b"\x89PNG\r\n\x1a\n" + b"not actually a png" * 100
    r = client.post(
        "/api/community/scan",
        data=_form(
            scan_id="scangarbage1",
            user_pseudo_id="pseudogarba1",
            region="Berlin", country="DE",
            disease="healthy",
        ),
        files={"image": ("scan.png", garbage, "image/png")},
    )
    assert r.status_code == 200, r.text
    assert list(thumb_dir.iterdir()) == []
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT thumb_path FROM community_scans WHERE scan_id = ?",
            ("scangarbage1",),
        ).fetchone()
    assert row[0] is None


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

    # A non-owner cannot create the durable request that authorises writes.
    r_wrong = client.post(
        "/api/community/request-opinion",
        json={"scan_id": "wantsopinion1", "user_pseudo_id": "pseudo-not-owner"},
    )
    assert r_wrong.status_code == 200, r_wrong.text
    assert r_wrong.json() == {"ok": True, "flagged": False}
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


# ── Regression: prune sweeps companion tables ───────────────────────


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
    _request_opinion(client, "realscanid01", "pseudo-owner-1")
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


def test_opinion_write_and_push_only_when_owner_requested(client, isolated_community, monkeypatch):
    """Unrequested scans are server-locked; a live request accepts the reply
    and is the only path that can notify the owner."""
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

    locked = client.post(
        "/api/community/opinion",
        json={
            "scan_id": "nudgeoff0001",
            "user_pseudo_id": "pseudo-viewer-9",
            "suggested_disease": "powdery_mildew",
        },
    )
    assert locked.status_code == 423, locked.text
    assert locked.json()["detail"] == "opinions_locked"
    open_reply = client.post(
        "/api/community/opinion",
        json={
            "scan_id": "nudgeon00001",
            "user_pseudo_id": "pseudo-viewer-9",
            "suggested_disease": "powdery_mildew",
        },
    )
    assert open_reply.status_code == 200, open_reply.text

    # Only the requested scan triggered a push to the owner.
    assert [p[1] for p in pushes] == ["nudgeon00001"]
    assert pushes[0][0] == "pseudo-owner-9"


def test_cancelled_request_locks_new_replies_but_keeps_thread_readable(
    client, isolated_community,
):
    client.post(
        "/api/community/scan",
        data=_form(
            scan_id="lockedcancel1", user_pseudo_id="pseudo-owner-lock",
            region="Paris", country="FR", disease="leaf_spot",
        ),
    )
    _request_opinion(client, "lockedcancel1", "pseudo-owner-lock")
    first = client.post(
        "/api/community/opinion",
        json={
            "scan_id": "lockedcancel1",
            "user_pseudo_id": "pseudo-viewer-one",
            "suggested_disease": "healthy",
        },
    )
    assert first.status_code == 200, first.text
    cancelled = client.post(
        "/api/community/request-opinion/cancel",
        json={"scan_id": "lockedcancel1", "user_pseudo_id": "pseudo-owner-lock"},
    )
    assert cancelled.json()["cleared"] is True

    locked = client.post(
        "/api/community/opinion",
        json={
            "scan_id": "lockedcancel1",
            "user_pseudo_id": "pseudo-viewer-two",
            "suggested_disease": "powdery_mildew",
        },
    )
    assert locked.status_code == 423, locked.text
    thread = client.get("/api/community/opinions/lockedcancel1").json()
    assert thread["wants_opinion"] is False
    assert [row["suggested_disease"] for row in thread["opinions"]] == ["healthy"]


def test_expired_request_is_locked_at_write_time(client, isolated_community):
    import time as _time

    import community as comm

    db_path, _ = isolated_community
    client.post(
        "/api/community/scan",
        data=_form(
            scan_id="lockedexpire1", user_pseudo_id="pseudo-owner-exp",
            region="Paris", country="FR", disease="leaf_spot",
        ),
    )
    _request_opinion(client, "lockedexpire1", "pseudo-owner-exp")
    expired = int(_time.time()) - (comm.OPINION_REQUEST_TTL_HOURS * 3600) - 1
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE community_opinion_requests SET created_at = ? WHERE scan_id = ?",
            (expired, "lockedexpire1"),
        )
    reply = client.post(
        "/api/community/opinion",
        json={
            "scan_id": "lockedexpire1",
            "user_pseudo_id": "pseudo-viewer-exp",
            "suggested_disease": "healthy",
        },
    )
    assert reply.status_code == 423, reply.text
    assert client.get("/api/community/opinions/lockedexpire1").json()["wants_opinion"] is False


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
    _request_opinion(client, "staleshared01", "pseudo-owner-1")
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


def test_opinion_consensus_dedupes_on_author_key_not_body_pseudo_id(client, isolated_community):
    """The roadmap's "forgeable second opinions" finding: author_pseudo_id
    is a plain client-chosen body field, so a caller varying it per request
    used to vote as many times as it liked. With a real X-Community-Author-Key
    attached, the consensus tally must dedupe on THAT instead — two posts
    under the same header count as one vote even with two different bodies."""
    client.post(
        "/api/community/scan",
        data=_form(
            scan_id="dedupeauthor1", user_pseudo_id="pseudo-owner-1",
            region="Berlin", country="DE", disease="leaf_spot",
        ),
    )
    _request_opinion(client, "dedupeauthor1", "pseudo-owner-1")
    key = "a" * 64
    _adopt_author(client, key, "pseudo-forger-1")
    first = client.post(
        "/api/community/opinion",
        headers={"X-Community-Author-Key": key},
        json={
            "scan_id": "dedupeauthor1",
            "user_pseudo_id": "pseudo-forger-1",
            "suggested_disease": "leaf_spot",
        },
    )
    assert first.status_code == 200, first.text
    second = client.post(
        "/api/community/opinion",
        headers={"X-Community-Author-Key": key},
        json={
            "scan_id": "dedupeauthor1",
            "user_pseudo_id": "pseudo-forger-2",
            "suggested_disease": "leaf_spot",
        },
    )
    assert second.status_code == 200, second.text

    result = client.get("/api/community/opinions/dedupeauthor1").json()
    # Two comments were posted (the thread keeps every row) but they resolve
    # to the same author, so the consensus tally counts one vote, not two.
    assert len(result["opinions"]) == 2
    assert result["consensus"] == [{"disease": "leaf_spot", "count": 1}]


def test_scan_owner_cannot_bypass_self_opinion_with_a_different_body_pseudo(
    client, isolated_community,
):
    key = "c" * 64
    _adopt_author(client, key, "pseudo-owner-original", "Leaf Keeper")
    uploaded = client.post(
        "/api/community/scan",
        headers={"X-Community-Author-Key": key},
        data=_form(
            scan_id="durableowner1", user_pseudo_id="pseudo-owner-original",
            region="Berlin", country="DE", disease="leaf_spot",
            nickname="Leaf Keeper",
        ),
    )
    assert uploaded.status_code == 200, uploaded.text
    _request_opinion(client, "durableowner1", "pseudo-owner-original")

    forged_pseudo = client.post(
        "/api/community/opinion",
        headers={"X-Community-Author-Key": key},
        json={
            "scan_id": "durableowner1",
            "user_pseudo_id": "pseudo-owner-changed",
            "suggested_disease": "healthy",
        },
    )
    assert forged_pseudo.status_code == 403, forged_pseudo.text
    assert forged_pseudo.json()["detail"] == "owner_cannot_opine"


def test_opinions_list_returns_the_callers_current_pick(client, isolated_community):
    client.post(
        "/api/community/scan",
        data=_form(
            scan_id="myopinionscan1", user_pseudo_id="pseudo-owner-2",
            region="Berlin", country="DE", disease="leaf_spot",
        ),
    )
    _request_opinion(client, "myopinionscan1", "pseudo-owner-2")
    key = "b" * 64
    _adopt_author(client, key, "pseudo-viewer-9")
    client.post(
        "/api/community/opinion",
        headers={"X-Community-Author-Key": key},
        json={
            "scan_id": "myopinionscan1",
            "user_pseudo_id": "pseudo-viewer-9",
            "suggested_disease": "powdery_mildew",
        },
    )

    mine = client.get(
        "/api/community/opinions/myopinionscan1",
        headers={"X-Community-Author-Key": key},
    ).json()
    assert mine["my_opinion"] == "powdery_mildew"

    # No identity attached at all — reads as "haven't voted", not an error.
    anonymous = client.get("/api/community/opinions/myopinionscan1").json()
    assert anonymous["my_opinion"] is None


def test_nearby_before_cursor_pages_without_overlap(client, isolated_community):
    """Keyset pagination: page 1 (limit=2) then page 2 (before=next_before)
    together cover all 3 rows with no repeats, and the short second page
    reports next_before=null."""
    db_path, _ = isolated_community
    for sid in ("pagescan0001", "pagescan0002", "pagescan0003"):
        client.post(
            "/api/community/scan",
            data=_form(
                scan_id=sid, user_pseudo_id="pseudo-pager-1",
                region="Lyon", country="FR", disease="leaf_spot",
            ),
        )
    # Give each row a distinct, ordered created_at — same-second inserts in a
    # tight loop would otherwise tie and make DESC order undefined.
    with sqlite3.connect(str(db_path)) as conn:
        base = 1_700_000_000
        for i, sid in enumerate(("pagescan0001", "pagescan0002", "pagescan0003")):
            conn.execute(
                "UPDATE community_scans SET created_at = ? WHERE scan_id = ?",
                (base + i, sid),
            )

    page1 = client.get(
        "/api/community/nearby",
        params={"country": "FR", "scope": "country", "limit": 2, "user_pseudo_id": "x"},
    ).json()
    assert len(page1["items"]) == 2
    assert page1["next_before"] is not None

    page2 = client.get(
        "/api/community/nearby",
        params={
            "country": "FR", "scope": "country", "limit": 2,
            "user_pseudo_id": "x", "before": page1["next_before"],
        },
    ).json()
    assert len(page2["items"]) == 1
    assert page2["next_before"] is None

    ids1 = {i["scan_id"] for i in page1["items"]}
    ids2 = {i["scan_id"] for i in page2["items"]}
    assert not (ids1 & ids2)
    assert len(ids1) + len(ids2) == 3


def test_recommended_feed_is_deterministic_and_composite_cursor_has_no_overlap(
    client, isolated_community,
):
    db_path, _ = isolated_community
    scan_ids = [f"recommend{i:04d}" for i in range(5)]
    for index, scan_id in enumerate(scan_ids):
        client.post(
            "/api/community/scan",
            data=_form(
                scan_id=scan_id,
                user_pseudo_id=f"pseudo-author-{index}",
                region="Lyon",
                country="FR",
                disease="leaf_spot",
            ),
        )
    # Force the timestamp tie that a created_at-only cursor cannot page.
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE community_scans SET created_at = ?", (1_800_000_000,))
    _request_opinion(client, scan_ids[0], "pseudo-author-0")

    params = {
        "country": "FR",
        "scope": "country",
        "ranking": "recommended",
        "user_pseudo_id": "pseudo-reader",
        "limit": 2,
    }
    first = client.get("/api/community/nearby", params=params)
    assert first.status_code == 200, first.text
    page1 = first.json()
    assert page1["ranking"] == "recommended"
    assert page1["next_before"] is None
    assert page1["next_cursor"].startswith("r1.")
    assert page1["items"][0]["recommendation_reason"] == "needs_opinion"

    # Same snapshot + request produces the same order.
    repeat = client.get("/api/community/nearby", params=params).json()
    assert [row["scan_id"] for row in repeat["items"]] == [
        row["scan_id"] for row in page1["items"]
    ]

    seen: list[str] = []
    page = page1
    while True:
        seen.extend(row["scan_id"] for row in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
        page = client.get(
            "/api/community/nearby",
            params={**params, "cursor": cursor},
        ).json()
    assert len(seen) == 5
    assert len(set(seen)) == 5

    assert client.get(
        "/api/community/nearby",
        params={**params, "cursor": "not-a-cursor"},
    ).status_code == 422
    tampered = page1["next_cursor"][:-1] + (
        "0" if page1["next_cursor"][-1] != "0" else "1"
    )
    assert client.get(
        "/api/community/nearby",
        params={**params, "cursor": tampered},
    ).status_code == 422
    assert client.get(
        "/api/community/nearby",
        params={**params, "before": 1_800_000_000},
    ).status_code == 422


def test_recent_composite_cursor_keeps_same_second_rows(client, isolated_community):
    db_path, _ = isolated_community
    for index in range(5):
        client.post(
            "/api/community/scan",
            data=_form(
                scan_id=f"recenttie{index:03d}",
                user_pseudo_id=f"recent-owner-{index}",
                country="FR",
                region="Lyon",
                disease="healthy",
            ),
        )
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE community_scans SET created_at = ?", (1_800_000_000,))

    params = {
        "country": "FR",
        "scope": "country",
        "ranking": "recent",
        "user_pseudo_id": "recent-reader",
        "limit": 2,
    }
    seen: list[str] = []
    response = client.get("/api/community/nearby", params=params)
    assert response.status_code == 200, response.text
    page = response.json()
    while True:
        seen.extend(row["scan_id"] for row in page["items"])
        if page["next_cursor"] is None:
            break
        response = client.get(
            "/api/community/nearby",
            params={**params, "cursor": page["next_cursor"]},
        )
        assert response.status_code == 200, response.text
        page = response.json()
    assert len(seen) == 5
    assert len(set(seen)) == 5


def test_consensus_counts_latest_pick_per_author(client, isolated_community):
    """One author posting twice contributes ONE vote to the consensus tally
    (their latest pick), even though both comments stay in the thread."""
    client.post(
        "/api/community/scan",
        data=_form(
            scan_id="consensusscan1", user_pseudo_id="pseudo-owner-c",
            region="Turin", country="IT", disease="leaf_spot",
        ),
    )
    _request_opinion(client, "consensusscan1", "pseudo-owner-c")
    for disease in ("powdery_mildew", "healthy"):
        r = client.post(
            "/api/community/opinion",
            json={
                "scan_id": "consensusscan1",
                "user_pseudo_id": "pseudo-flip-flop",
                "suggested_disease": disease,
            },
        )
        assert r.status_code == 200, r.text

    result = client.get("/api/community/opinions/consensusscan1").json()
    # Both comments are kept in the thread...
    assert len(result["opinions"]) == 2
    # ...but the consensus tally counts one vote, for the latest pick.
    assert sum(c["count"] for c in result["consensus"]) == 1
    assert result["consensus"] == [{"disease": "healthy", "count": 1}]


def test_like_toggle_and_self_like_rejected(client, isolated_community):
    client.post(
        "/api/community/scan",
        data=_form(
            scan_id="likescan00001", user_pseudo_id="pseudo-owner-l",
            region="Turin", country="IT", disease="leaf_spot",
        ),
    )
    liked = client.post(
        "/api/community/like",
        json={"scan_id": "likescan00001", "user_pseudo_id": "pseudo-liker-1", "liked": True},
    )
    assert liked.status_code == 200, liked.text
    assert liked.json() == {"ok": True, "liked": True, "count": 1}

    unliked = client.post(
        "/api/community/like",
        json={"scan_id": "likescan00001", "user_pseudo_id": "pseudo-liker-1", "liked": False},
    )
    assert unliked.json() == {"ok": True, "liked": False, "count": 0}

    self_like = client.post(
        "/api/community/like",
        json={"scan_id": "likescan00001", "user_pseudo_id": "pseudo-owner-l", "liked": True},
    )
    assert self_like.json() == {"ok": True, "liked": False, "count": 0}


def test_durable_identity_dedupes_changed_pseudos_for_views_and_likes(
    client, isolated_community,
):
    client.post(
        "/api/community/scan",
        data=_form(
            scan_id="durablemetric1",
            user_pseudo_id="metric-owner",
            country="IT",
            region="Turin",
            disease="healthy",
        ),
    )
    headers = {"X-Community-Author-Key": "d" * 64}
    _adopt_author(client, "d" * 64, "viewer-one")
    first_view = client.post(
        "/api/community/view",
        headers=headers,
        json={"scan_id": "durablemetric1", "user_pseudo_id": "viewer-one"},
    )
    second_view = client.post(
        "/api/community/view",
        headers=headers,
        json={"scan_id": "durablemetric1", "user_pseudo_id": "viewer-two"},
    )
    assert first_view.json()["counted_n"] == 1
    assert second_view.json()["counted_n"] == 0

    first_like = client.post(
        "/api/community/like",
        headers=headers,
        json={
            "scan_id": "durablemetric1",
            "user_pseudo_id": "liker-one",
            "liked": True,
        },
    )
    second_like = client.post(
        "/api/community/like",
        headers=headers,
        json={
            "scan_id": "durablemetric1",
            "user_pseudo_id": "liker-two",
            "liked": True,
        },
    )
    assert first_like.json()["count"] == 1
    assert second_like.json()["count"] == 1
    state = client.get(
        "/api/community/opinions/durablemetric1",
        headers=headers,
        params={"user_pseudo_id": "liker-two"},
    ).json()
    assert state["views"] == 1
    assert state["like_count"] == 1
    assert state["liked_by_me"] is True
    client.post(
        "/api/community/scan",
        headers=headers,
        data=_form(
            scan_id="durableown001",
            user_pseudo_id="new-owner-pseudo",
            country="IT",
            region="Turin",
            disease="leaf_spot",
        ),
    )
    feed = client.get(
        "/api/community/nearby",
        headers=headers,
        params={
            "scope": "worldwide",
            "ranking": "recommended",
            "user_pseudo_id": "rotated-reader-pseudo",
        },
    ).json()["items"]
    by_disease = {row["disease"]: row for row in feed}
    assert by_disease["healthy"]["liked_by_me"] is True
    assert all(row["disease"] != "leaf_spot" for row in feed)


def test_report_dedupes_and_block_hides_author_scans_and_replies(
    client, isolated_community,
):
    client.post(
        "/api/community/scan",
        data=_form(
            scan_id="safetytarget1", user_pseudo_id="pseudo-owner-safe",
            region="Turin", country="IT", disease="leaf_spot",
        ),
    )
    client.post(
        "/api/community/scan",
        data=_form(
            scan_id="safetyauthor1", user_pseudo_id="pseudo-reply-author",
            region="Turin", country="IT", disease="healthy",
        ),
    )
    _request_opinion(client, "safetytarget1", "pseudo-owner-safe")
    reply = client.post(
        "/api/community/opinion",
        json={
            "scan_id": "safetytarget1",
            "user_pseudo_id": "pseudo-reply-author",
            "suggested_disease": "healthy",
            "nickname": "Leaf Sage",
        },
    )
    assert reply.status_code == 200, reply.text
    opinion_id = reply.json()["opinions"][0]["content_id"]
    assert opinion_id.startswith("co")

    report_payload = {
        "user_pseudo_id": "pseudo-owner-safe",
        "target_type": "opinion",
        "target_id": opinion_id,
        "reason": "spam_or_misleading",
    }
    first = client.post("/api/community/safety/report", json=report_payload)
    second = client.post("/api/community/safety/report", json=report_payload)
    assert first.json() == {"ok": True, "created": True}
    assert second.json() == {"ok": True, "created": False}

    blocked = client.post(
        "/api/community/safety/block",
        json={
            "user_pseudo_id": "pseudo-owner-safe",
            "target_type": "opinion",
            "target_id": opinion_id,
        },
    )
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["blocked"] is True
    assert "blocked_pseudo_id" not in blocked.json()

    thread = client.get(
        "/api/community/opinions/safetytarget1",
        params={"user_pseudo_id": "pseudo-owner-safe"},
    ).json()
    assert thread["opinions"] == []
    assert thread["consensus"] == []
    feed = client.get(
        "/api/community/nearby",
        params={
            "country": "IT", "scope": "country",
            "user_pseudo_id": "pseudo-owner-safe",
        },
    ).json()
    assert all(row["disease"] != "healthy" for row in feed["items"])


def test_durable_block_rejects_cached_token_write_after_pseudo_rotation(
    client, isolated_community,
):
    owner_headers = {"X-Community-Author-Key": "a" * 64}
    author_headers = {"X-Community-Author-Key": "b" * 64}
    _adopt_author(client, "a" * 64, "block-owner")
    _adopt_author(client, "b" * 64, "blocked-author-one")
    client.post(
        "/api/community/scan",
        headers=owner_headers,
        data=_form(
            scan_id="durableblock1",
            user_pseudo_id="block-owner",
            country="IT",
            region="Turin",
            disease="leaf_spot",
        ),
    )
    _request_opinion(client, "durableblock1", "block-owner")
    first = client.post(
        "/api/community/opinion",
        headers=author_headers,
        json={
            "scan_id": "durableblock1",
            "user_pseudo_id": "blocked-author-one",
            "suggested_disease": "healthy",
        },
    )
    opinion_id = first.json()["opinions"][0]["content_id"]
    block = client.post(
        "/api/community/safety/block",
        headers=owner_headers,
        json={
            "user_pseudo_id": "block-owner",
            "target_type": "opinion",
            "target_id": opinion_id,
        },
    )
    assert block.status_code == 200, block.text
    retry = client.post(
        "/api/community/opinion",
        headers=author_headers,
        json={
            "scan_id": "durableblock1",
            "user_pseudo_id": "blocked-author-two",
            "suggested_disease": "healthy",
        },
    )
    assert retry.status_code == 423, retry.text

    report_headers = {"X-Community-Author-Key": "c" * 64}
    _adopt_author(client, "c" * 64, "reporter-one")
    target_token = client.get(
        "/api/community/nearby",
        params={"scope": "worldwide", "user_pseudo_id": "reporter"},
    ).json()["items"][0]["scan_id"]
    payload = {
        "target_type": "scan",
        "target_id": target_token,
        "reason": "spam_or_misleading",
    }
    report1 = client.post(
        "/api/community/safety/report",
        headers=report_headers,
        json={**payload, "user_pseudo_id": "reporter-one"},
    )
    report2 = client.post(
        "/api/community/safety/report",
        headers=report_headers,
        json={**payload, "user_pseudo_id": "reporter-two"},
    )
    assert report1.json()["created"] is True
    assert report2.json()["created"] is False


def test_adoption_backfills_legacy_block_and_report_identities(
    client, isolated_community,
):
    db_path, _ = isolated_community
    client.post(
        "/api/community/scan",
        data=_form(
            scan_id="legacyblock001",
            user_pseudo_id="legacy-owner",
            country="IT",
            region="Turin",
            disease="leaf_spot",
        ),
    )
    _request_opinion(client, "legacyblock001", "legacy-owner")
    reply = client.post(
        "/api/community/opinion",
        json={
            "scan_id": "legacyblock001",
            "user_pseudo_id": "legacy-author",
            "suggested_disease": "healthy",
        },
    ).json()
    opinion_id = reply["opinions"][0]["content_id"]
    client.post(
        "/api/community/safety/report",
        json={
            "user_pseudo_id": "legacy-owner",
            "target_type": "opinion",
            "target_id": opinion_id,
            "reason": "spam_or_misleading",
        },
    )
    client.post(
        "/api/community/safety/block",
        json={
            "user_pseudo_id": "legacy-owner",
            "target_type": "opinion",
            "target_id": opinion_id,
        },
    )
    owner_key = "e" * 64
    author_key = "f" * 64
    for key, pseudo in ((owner_key, "legacy-owner"), (author_key, "legacy-author")):
        adopted = client.post(
            "/api/community/authors/adopt",
            headers={"X-Community-Author-Key": key},
            json={"seed_display_name": pseudo, "legacy_pseudo_id": pseudo},
        )
        assert adopted.status_code == 200, adopted.text

    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute(
            "SELECT reporter_author_id FROM community_reports"
        ).fetchone()[0]
        blocker_id, blocked_id = conn.execute(
            "SELECT blocker_author_id, blocked_author_id FROM community_blocks"
        ).fetchone()
        assert blocker_id and blocked_id
    retry = client.post(
        "/api/community/opinion",
        headers={"X-Community-Author-Key": author_key},
        json={
            "scan_id": "legacyblock001",
            "user_pseudo_id": "rotated-author",
            "suggested_disease": "healthy",
        },
    )
    assert retry.status_code == 423, retry.text


def test_author_adopt_creates_row_and_is_idempotent(client, isolated_community):
    key = "e" * 64
    first = client.post(
        "/api/community/authors/adopt",
        headers={"X-Community-Author-Key": key},
        json={"seed_display_name": "Leafy Sage 7", "legacy_pseudo_id": "pseudo-adopt-1"},
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["ok"] is True
    assert body["author_id"].startswith("au_")
    assert body["display_name"] == "Leafy Sage 7"

    second = client.post(
        "/api/community/authors/adopt",
        headers={"X-Community-Author-Key": key},
        json={"seed_display_name": "Ignored On Repeat", "legacy_pseudo_id": "pseudo-adopt-2"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["author_id"] == body["author_id"]
    assert second.json()["display_name"] == "Leafy Sage 7"


def test_keyed_read_does_not_preempt_adoption_or_legacy_backfill(
    client, isolated_community,
):
    """A feed GET must be lookup-only: explicit adoption owns the first write
    so it can seed the nickname and attach this install's pre-V2 rows."""
    db_path, _ = isolated_community
    import community as comm

    comm.record_scan(
        scan_id="readbeforeadopt1",
        user_pseudo_id="legacy-reader",
        country="DE",
        region="Berlin",
        disease="leaf_spot",
    )
    key = "9" * 64
    feed = client.get(
        "/api/community/nearby",
        params={
            "scope": "worldwide",
            "ranking": "recommended",
            "user_pseudo_id": "legacy-reader",
        },
        headers={"X-Community-Author-Key": key},
    )
    assert feed.status_code == 200, feed.text
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM community_authors").fetchone()[0] == 0

    adopted = client.post(
        "/api/community/authors/adopt",
        headers={"X-Community-Author-Key": key},
        json={
            "seed_display_name": "Fern Keeper",
            "legacy_pseudo_id": "legacy-reader",
        },
    )
    assert adopted.status_code == 200, adopted.text
    with sqlite3.connect(db_path) as conn:
        scan_author = conn.execute(
            "SELECT author_id FROM community_scans WHERE scan_id = ?",
            ("readbeforeadopt1",),
        ).fetchone()[0]
    assert scan_author == adopted.json()["author_id"]
    assert adopted.json()["display_name"] == "Fern Keeper"


def test_adopt_fills_null_legacy_after_earlier_keyed_write(
    client, isolated_community,
):
    """A failed/old client ordering cannot permanently suppress migration."""
    db_path, _ = isolated_community
    import community as comm

    comm.record_scan(
        scan_id="latelegacyfill01",
        user_pseudo_id="legacy-late",
        country="DE",
        region="Berlin",
        disease="healthy",
    )
    key = "8" * 64
    first = comm.resolve_author(key, "Original name", None)
    adopted = client.post(
        "/api/community/authors/adopt",
        headers={"X-Community-Author-Key": key},
        json={
            "seed_display_name": "Must not overwrite",
            "legacy_pseudo_id": "legacy-late",
        },
    )
    assert adopted.status_code == 200, adopted.text
    assert adopted.json()["author_id"] == first["author_id"]
    assert adopted.json()["display_name"] == "Original name"
    with sqlite3.connect(db_path) as conn:
        author = conn.execute(
            "SELECT legacy_pseudo_id FROM community_authors WHERE author_id = ?",
            (first["author_id"],),
        ).fetchone()[0]
        scan_author = conn.execute(
            "SELECT author_id FROM community_scans WHERE scan_id = ?",
            ("latelegacyfill01",),
        ).fetchone()[0]
    assert author == "legacy-late"
    assert scan_author == first["author_id"]


def test_keyed_write_only_touches_author_and_backfill_stays_one_time(
    client, isolated_community, monkeypatch,
):
    """Normal keyed actions must not call resolve_author or sweep pseudo rows.

    A row arriving after the explicit adoption marker remains legacy on both a
    keyed like and an idempotent second adoption, proving the attachment suite
    is not repeated.
    """
    db_path, _ = isolated_community
    import community as comm
    import main as server_main

    comm.record_scan(
        scan_id="touchonlytarget1",
        user_pseudo_id="different-owner",
        country="DE",
        region="Berlin",
        disease="healthy",
    )
    key = "7" * 64
    first = client.post(
        "/api/community/authors/adopt",
        headers={"X-Community-Author-Key": key},
        json={
            "seed_display_name": "Touch Tester",
            "legacy_pseudo_id": "legacy-touch",
        },
    )
    assert first.status_code == 200, first.text

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO community_scans "
            "(scan_id, user_pseudo_id, country, region, disease, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("latelegacyscan1", "legacy-touch", "DE", "Berlin", "healthy", 1),
        )

    def _unexpected_resolve(*_args, **_kwargs):
        raise AssertionError("ordinary keyed write called adoption resolver")

    monkeypatch.setattr(server_main, "community_resolve_author", _unexpected_resolve)
    liked = client.post(
        "/api/community/like",
        headers={"X-Community-Author-Key": key},
        json={
            "scan_id": "touchonlytarget1",
            "user_pseudo_id": "rotated-touch",
            "liked": True,
        },
    )
    assert liked.status_code == 200, liked.text

    # Restore only for the explicit retry; it should see the committed marker
    # and skip the attachment statements too.
    monkeypatch.setattr(server_main, "community_resolve_author", comm.resolve_author)
    retry = client.post(
        "/api/community/authors/adopt",
        headers={"X-Community-Author-Key": key},
        json={
            "seed_display_name": "Ignored",
            "legacy_pseudo_id": "legacy-touch",
        },
    )
    assert retry.status_code == 200, retry.text
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT author_id FROM community_scans WHERE scan_id = ?",
            ("latelegacyscan1",),
        ).fetchone()
        marker = conn.execute(
            "SELECT legacy_backfilled_at FROM community_authors WHERE author_id = ?",
            (first.json()["author_id"],),
        ).fetchone()[0]
    assert row[0] is None
    assert marker is not None


def test_author_adopt_rejects_malformed_key(client, isolated_community):
    r = client.post(
        "/api/community/authors/adopt",
        headers={"X-Community-Author-Key": "not-a-sha256-hash"},
        json={"seed_display_name": "Someone"},
    )
    assert r.status_code == 422, r.text


def test_author_adopt_requires_the_header(client, isolated_community):
    r = client.post(
        "/api/community/authors/adopt",
        json={"seed_display_name": "Someone"},
    )
    assert r.status_code == 422, r.text


def test_batch_impression_views_counted_once(client, isolated_community):
    for sid in ("viewbatch0001", "viewbatch0002"):
        client.post(
            "/api/community/scan",
            data=_form(
                scan_id=sid, user_pseudo_id="pseudo-owner-v",
                region="Turin", country="IT", disease="leaf_spot",
            ),
        )
    first = client.post(
        "/api/community/view",
        json={
            "scan_ids": ["viewbatch0001", "viewbatch0002"],
            "user_pseudo_id": "pseudo-scroller-1",
        },
    )
    assert first.status_code == 200, first.text
    assert first.json()["counted_n"] == 2

    # Same two ids again — both are already-seen, so nothing new is counted.
    second = client.post(
        "/api/community/view",
        json={
            "scan_ids": ["viewbatch0001", "viewbatch0002"],
            "user_pseudo_id": "pseudo-scroller-1",
        },
    )
    assert second.json()["counted_n"] == 0
    counts = client.get("/api/community/opinions/viewbatch0001").json()
    assert counts["views"] == 1


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


def test_nearby_before_rejected_with_disease_or_species(client, isolated_community):
    """``before`` is a single-pass cursor. A disease/species-scoped call is
    several concatenated passes with no global created_at order, so the
    combination is refused rather than paged wrongly (duplicated rows on the
    next page, and a whole pass silently dropped)."""
    base = {
        "country": "FR", "scope": "country", "limit": 2,
        "user_pseudo_id": "x", "before": 1_700_000_000,
    }
    for extra in ({"disease": "leaf_spot"}, {"species": "Ficus elastica"}):
        r = client.get("/api/community/nearby", params={**base, **extra})
        assert r.status_code == 422, r.text

        keyed = client.get(
            "/api/community/nearby",
            params={
                **{k: v for k, v in base.items() if k != "before"},
                "cursor": "r1.opaque.signature",
                **extra,
            },
        )
        assert keyed.status_code == 422, keyed.text
        assert keyed.json()["detail"] == "cursor cannot be combined with disease or species."

    # The filters are still fine on their own — only the cursor is refused.
    r = client.get(
        "/api/community/nearby",
        params={
            "country": "FR", "scope": "country", "limit": 2,
            "user_pseudo_id": "x", "disease": "leaf_spot",
        },
    )
    assert r.status_code == 200, r.text


def test_scan_id_index_exists_and_is_not_unique(isolated_community):
    """``community_scans.scan_id`` is the hot lookup key (token resolution,
    views, likes, ownership) and must be indexed — but NOT uniquely: legacy
    rows may repeat a scan_id, and a UNIQUE index would fail to build and
    take boot down with it."""
    db_path, _ = isolated_community
    import community as comm
    comm.init_db()
    with sqlite3.connect(str(db_path)) as conn:
        idx = {
            row[1]: bool(row[2])
            for row in conn.execute("PRAGMA index_list(community_scans)").fetchall()
        }
    assert "ix_community_scan_id" in idx
    assert idx["ix_community_scan_id"] is False


def test_legacy_backfill_pseudo_columns_are_indexed(isolated_community):
    db_path, _ = isolated_community
    import community as comm
    comm.init_db()
    with sqlite3.connect(str(db_path)) as conn:
        view_indexes = {
            row[1] for row in conn.execute(
                "PRAGMA index_list(community_scan_views)",
            ).fetchall()
        }
        like_indexes = {
            row[1] for row in conn.execute(
                "PRAGMA index_list(community_likes)",
            ).fetchall()
        }
    assert "ix_views_viewer_pseudo" in view_indexes
    assert "ix_likes_user_pseudo" in like_indexes


def test_batch_view_mixes_token_forms_and_drops_self_views(client, isolated_community):
    """One batch can carry public tokens and real scan_ids for the same rows;
    both resolve to one view each, unknown ids are ignored, and the owner's
    own impressions never count."""
    import community as comm

    for sid in ("viewmix000001", "viewmix000002"):
        client.post(
            "/api/community/scan",
            data=_form(
                scan_id=sid, user_pseudo_id="pseudo-owner-m",
                region="Turin", country="IT", disease="leaf_spot",
            ),
        )
    token = comm.public_scan_token("viewmix000001")
    r = client.post(
        "/api/community/view",
        json={
            "scan_ids": [token, "viewmix000001", "viewmix000002", "neverssharedx"],
            "user_pseudo_id": "pseudo-scroller-m",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["counted_n"] == 2

    own = client.post(
        "/api/community/view",
        json={
            "scan_ids": ["viewmix000001", "viewmix000002"],
            "user_pseudo_id": "pseudo-owner-m",
        },
    )
    assert own.json()["counted_n"] == 0


def test_unlike_soft_deletes_so_the_daily_like_quota_holds(client, isolated_community):
    """Unliking must not hand the daily quota slot back: the row survives as
    ``liked = 0``, and re-liking keeps its ORIGINAL ``created_at`` instead of
    sliding out of the 24h window."""
    db_path, _ = isolated_community
    client.post(
        "/api/community/scan",
        data=_form(
            scan_id="likesoft00001", user_pseudo_id="pseudo-owner-s",
            region="Turin", country="IT", disease="leaf_spot",
        ),
    )
    body = {
        "scan_id": "likesoft00001",
        "user_pseudo_id": "pseudo-liker-s",
        "liked": True,
    }
    assert client.post("/api/community/like", json=body).json()["count"] == 1
    with sqlite3.connect(str(db_path)) as conn:
        (created_at,) = conn.execute(
            "SELECT created_at FROM community_likes",
        ).fetchone()

    unliked = client.post("/api/community/like", json={**body, "liked": False})
    assert unliked.json() == {"ok": True, "liked": False, "count": 0}
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT liked, created_at FROM community_likes",
        ).fetchall()
    assert rows == [(0, created_at)]

    assert client.post("/api/community/like", json=body).json()["count"] == 1
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT liked, created_at FROM community_likes",
        ).fetchall()
    assert rows == [(1, created_at)]


def test_prune_reaps_stale_authors_but_keeps_linked_ones(client, isolated_community):
    """``community_authors`` is on the same 90-day clock as everything else
    here — except a row whose legacy pseudo-id still has live community
    activity, which a later backfill needs."""
    import time as _time

    import community as comm

    db_path, _ = isolated_community
    client.post(
        "/api/community/scan",
        data=_form(
            scan_id="authorlive001", user_pseudo_id="pseudo-live-1",
            region="Turin", country="IT", disease="leaf_spot",
        ),
    )
    keys = {"a" * 64: "pseudo-dead-1", "b" * 64: "pseudo-live-1", "c" * 64: None}
    for key, legacy in keys.items():
        r = client.post(
            "/api/community/authors/adopt",
            headers={"X-Community-Author-Key": key},
            json={"seed_display_name": "Someone", "legacy_pseudo_id": legacy},
        )
        assert r.status_code == 200, r.text

    stale = int(_time.time()) - 200 * 86_400
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE community_authors SET last_seen_at = ?", (stale,))

    comm.prune_older_than(90)
    with sqlite3.connect(str(db_path)) as conn:
        survivors = {
            str(r[0]) for r in conn.execute("SELECT key_hash FROM community_authors")
        }
    # The linked one survives; the unlinked one and the legacy-less one go.
    assert survivors == {comm._stored_author_key("b" * 64)}


def test_author_adopt_is_atomic_under_concurrency(isolated_community, monkeypatch):
    """Simultaneous adopts of the SAME key must all return the one row — a
    check-then-insert raced into "UNIQUE constraint failed", i.e. a 500 on an
    endpoint documented as idempotent.

    The write lock is disabled for the duration so this pins the SQL itself
    rather than the lock: a second uvicorn worker is a second process, and no
    in-process lock spans it. Eight barrier-synchronised keys at 16 threads
    each — the pre-fix code loses this race on roughly 40% of keys, so the
    run as a whole fails it essentially always."""
    import contextlib
    import threading

    import community as comm

    comm.init_db()
    monkeypatch.setattr(comm, "_write_lock", contextlib.nullcontext())
    threads_per_key = 16

    for n in range(8):
        key = f"{n:02x}" * 32
        barrier = threading.Barrier(threads_per_key)
        ids: list[str] = []
        errors: list[Exception] = []

        def _adopt(key: str = key, barrier: threading.Barrier = barrier,
                   ids: list = ids, errors: list = errors) -> None:
            barrier.wait()
            try:
                ids.append(comm.resolve_author(key, "Racer", "pseudo-race")["author_id"])
            except Exception as exc:  # noqa: BLE001 — the assertion is "none of these"
                errors.append(exc)

        threads = [threading.Thread(target=_adopt) for _ in range(threads_per_key)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, errors
        assert len(ids) == threads_per_key
        assert len(set(ids)) == 1
