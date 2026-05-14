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
            region="London, GB",
            disease="powdery_mildew",
        ),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["id"] > 0


def test_community_scan_upload_with_thumbnail(client, isolated_community):
    _, thumb_dir = isolated_community
    r = client.post(
        "/api/community/scan",
        data=_form(
            scan_id="scanthumb01",
            user_pseudo_id="pseudothumb",
            region="Berlin, DE",
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
            region="London, GB",
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
            region="London, GB",
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
            region="London, GB",
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
                region="London, GB",
                disease=disease,
            ),
        )
    r = client.get(
        "/api/community/nearby",
        params={"region": "London, GB", "disease": "powdery_mildew", "limit": 10},
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
            region="Paris, FR",
            disease="healthy",
        ),
    )
    client.post(
        "/api/community/scan",
        data=_form(
            scan_id="scanotherid1",
            user_pseudo_id="pseudo-other-y",
            region="Paris, FR",
            disease="healthy",
        ),
    )
    r = client.get(
        "/api/community/nearby",
        params={"region": "Paris, FR", "user_pseudo_id": "pseudo-self-x"},
    )
    body = r.json()
    assert {item["scan_id"] for item in body["items"]} == {"scanotherid1"}


def test_community_nearby_handles_empty_region(client, isolated_community):
    r = client.get(
        "/api/community/nearby",
        params={"region": "Nowheresville, ZZ"},
    )
    assert r.status_code == 200
    assert r.json()["items"] == []
    assert r.json()["summary"]["total"] == 0


# ── Delete-for-user ─────────────────────────────────────────────────


def test_community_delete_user_purges_rows_and_thumbs(client, isolated_community):
    db_path, thumb_dir = isolated_community
    client.post(
        "/api/community/scan",
        data=_form(
            scan_id="scandel00001",
            user_pseudo_id="pseudo-delete-x",
            region="Tokyo, JP",
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
            region="Madrid, ES",
            disease="healthy",
        ),
    )
    m = client.get("/api/metrics").json()
    assert "community" in m
    assert m["community"]["total"] >= 1
    assert m["community"]["regions"] >= 1
