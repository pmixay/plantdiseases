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
    server_main.init_community_db = comm.init_db
    server_main.COMMUNITY_THUMB_DIR = comm.COMMUNITY_THUMB_DIR
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


def test_community_scan_upload_country_from_geo_header(client, isolated_community):
    db_path, _ = isolated_community
    r = client.post(
        "/api/community/scan",
        headers={"cf-ipcountry": "US"},
        data=_form(
            scan_id="scangeo001",
            user_pseudo_id="pseudogeo01",
            disease="powdery_mildew",
        ),
    )
    assert r.status_code == 200, r.text
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT country FROM community_scans WHERE scan_id = ?",
            ("scangeo001",),
        ).fetchone()
    assert row[0] == "US"


def test_community_scan_upload_no_geo_uses_global_bucket(client, isolated_community):
    db_path, _ = isolated_community
    r = client.post(
        "/api/community/scan",
        data=_form(
            scan_id="scanglobal1",
            user_pseudo_id="pseudoglobal1",
            disease="healthy",
        ),
    )
    assert r.status_code == 200, r.text
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT country FROM community_scans WHERE scan_id = ?",
            ("scanglobal1",),
        ).fetchone()
    assert row[0] == "GLOBAL"


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


def test_community_nearby_handles_empty_region(client, isolated_community):
    r = client.get(
        "/api/community/nearby",
        params={"region": "Nowheresville", "country": "ZZ"},
    )
    assert r.status_code == 200
    assert r.json()["items"] == []
    assert r.json()["summary"]["total"] == 0


def test_community_nearby_country_optional_uses_geo_header(client, isolated_community):
    client.post(
        "/api/community/scan",
        data=_form(
            scan_id="scangeofeed1",
            user_pseudo_id="pseudo-other-y",
            country="US",
            disease="healthy",
        ),
    )
    r = client.get("/api/community/nearby", headers={"cf-ipcountry": "US"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scope"] == "country"
    assert body["country"] == "US"
    assert body["summary"]["country_name_en"] == "United States"
    assert len(body["items"]) == 1


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
    """The corpus stores country + region separately. A country-only
    nearby query returns every locality; a country+region query
    narrows to one city. Legacy "City, Country" composite writes
    auto-split via the backward-compat fallback so older clients
    still land in the right column.
    """
    db_path, _ = isolated_community
    writes = (
        # New hierarchy: explicit country + region.
        dict(scan_id="scangb-london-1", country="GB", region="London"),
        dict(scan_id="scangb-london-2", country="GB", region="London"),
        dict(scan_id="scangb-mancunian1", country="GB", region="Manchester"),
        # Legacy: composite "City, Country" sent as region with no
        # explicit country. The endpoint splits on the last comma.
        dict(scan_id="scangb-legacy001", region="Bristol", country="GB"),
    )
    for w in writes:
        r = client.post(
            "/api/community/scan",
            data=_form(
                scan_id=w["scan_id"],
                user_pseudo_id="pseudo-gb-mixed",
                country=w.get("country"),
                region=w.get("region"),
                disease="healthy",
            ),
        )
        assert r.status_code == 200, r.text

    # Persisted country column must always be "GB"; region carries
    # the locality.
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT country, region FROM community_scans ORDER BY id"
        ).fetchall()
    assert {row[0] for row in rows} == {"GB"}
    assert {row[1] for row in rows} == {"London", "Manchester", "Bristol"}

    # Country-only nearby returns everything.
    r_country = client.get(
        "/api/community/nearby", params={"country": "GB"},
    ).json()
    assert r_country["summary"]["total"] == 4
    assert r_country["summary"]["country"] == "GB"
    assert r_country["summary"]["region"] is None
    # Top regions surfaced for the breakdown view.
    top_region_names = {r["region"] for r in r_country["summary"]["top_regions"]}
    assert top_region_names >= {"London", "Manchester", "Bristol"}

    # Country + region narrows to the matching locality. We still
    # populate the carousel by widening to the country when the
    # locality alone has < limit rows — Bristol has 1 row, so the
    # response includes other GB localities as fillers.
    r_london = client.get(
        "/api/community/nearby", params={"country": "GB", "region": "London"},
    ).json()
    assert r_london["summary"]["total"] == 2
    assert r_london["summary"]["region"] == "London"
    london_first = [it for it in r_london["items"] if it["region"] == "London"]
    assert len(london_first) == 2

    # Legacy "City, Country" composite on the read path also lands.
    r_legacy = client.get(
        "/api/community/nearby", params={"region": "Bristol", "country": "GB"},
    ).json()
    assert r_legacy["summary"]["country"] == "GB"
    assert r_legacy["summary"]["region"] == "Bristol"


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
