"""Tests for the community scans map: /api/community/map (individual scan
markers, zoomed-in) and /api/community/heatmap (k-anonymized density, zoomed-out).

Covers bbox filtering, the marker cap/`truncated` flag, that markers KEEP their
~1 km coords + only the public token (never the real scan_id), the heatmap's
k-anonymity (a 2-scan cell is dropped, a 3-scan cell appears) + no-raw-coord
leak, plus disease/time-window filters and the hidden-disease exclusion.
"""
from __future__ import annotations

import importlib
import time

import pytest


@pytest.fixture
def mapenv(tmp_path, monkeypatch):
    monkeypatch.setenv("COMMUNITY_DB_PATH", str(tmp_path / "community.db"))
    monkeypatch.setenv("COMMUNITY_THUMB_DIR", str(tmp_path / "thumbs"))
    monkeypatch.setenv("FEEDBACK_IP_PEPPER", "test-pepper")
    import community as comm
    importlib.reload(comm)
    import main as server_main
    server_main.community_record_scan = comm.record_scan

    def legacy_analysis_write(**kwargs):
        return comm.record_scan(
            scan_id=kwargs["scan_id"],
            account_actor_id=kwargs["account_actor_id"],
            country=kwargs["country"],
            region=kwargs.get("region", ""),
            city=kwargs.get("city"),
            lat=kwargs.get("lat"),
            lon=kwargs.get("lon"),
            disease=kwargs.get("client_disease") or "healthy",
            confidence=kwargs.get("client_confidence"),
            species=kwargs.get("client_species"),
            species_score=kwargs.get("client_species_score"),
            image_bytes=kwargs.get("image_bytes"),
            locale=kwargs.get("locale"),
            nickname=kwargs.get("nickname"),
        )

    server_main.community_record_analysis_scan = legacy_analysis_write
    server_main.community_map_scans = comm.map_scans
    server_main.community_density_grid = comm.density_grid
    server_main.community_opinion_counts = comm.opinion_counts
    server_main.init_community_db = comm.init_db
    server_main.COMMUNITY_THUMB_DIR = comm.COMMUNITY_THUMB_DIR
    server_main._daily_write_counts.clear()
    # /api/community/scan now requires a server-issued scan_id; these tests
    # post synthetic ids, so treat them all as issued.
    monkeypatch.setattr(
        server_main, "community_analysis_token_issued", lambda *a, **k: True
    )
    return comm


def _scan(comm, sid, lat, lon, disease="leaf_spot", user="u1"):
    comm.record_scan(
        scan_id=sid, user_pseudo_id=user, country="PL",
        lat=lat, lon=lon, disease=disease,
    )


# ── /community/map (individual markers) ─────────────────────────────


def test_map_scans_bbox_filters_and_keeps_coords(mapenv):
    comm = mapenv
    _scan(comm, "inbox0000001", 52.23, 21.01)
    _scan(comm, "outbox000001", 10.0, 10.0)  # outside the bbox
    res = comm.map_scans(52.0, 52.5, 20.5, 21.5)
    assert {it["scan_id"] for it in res["items"]} == {"inbox0000001"}
    item = res["items"][0]
    # The map KEEPS coords (the feed strips them to distance_km).
    assert item["lat"] == 52.23 and item["lon"] == 21.01
    assert res["truncated"] is False


def test_map_scans_excludes_not_a_plant(mapenv):
    comm = mapenv
    _scan(comm, "plantrow0001", 52.23, 21.01, disease="leaf_spot")
    _scan(comm, "notplant0001", 52.24, 21.02, disease="not_a_plant")
    ids = {it["scan_id"] for it in comm.map_scans(52.0, 52.5, 20.5, 21.5)["items"]}
    assert ids == {"plantrow0001"}


def test_map_scans_disease_filter(mapenv):
    comm = mapenv
    _scan(comm, "lsrow0000001", 52.23, 21.01, disease="leaf_spot")
    _scan(comm, "pmrow0000001", 52.24, 21.02, disease="powdery_mildew")
    ids = {
        it["scan_id"]
        for it in comm.map_scans(52.0, 52.5, 20.5, 21.5, disease="leaf_spot")["items"]
    }
    assert ids == {"lsrow0000001"}


def test_map_scans_truncated_flag(mapenv):
    comm = mapenv
    for i in range(5):
        _scan(comm, f"trunc{i:07d}", 52.20 + i * 0.001, 21.0)
    res = comm.map_scans(52.0, 52.5, 20.5, 21.5, limit=3)
    assert len(res["items"]) == 3
    assert res["truncated"] is True


def test_map_scans_time_window(mapenv):
    comm = mapenv
    _scan(comm, "freshrow0001", 52.23, 21.01)
    _scan(comm, "stalerow0001", 52.24, 21.02)
    old = int(time.time()) - 40 * 86_400
    with comm._connect() as conn:
        conn.execute(
            "UPDATE community_scans SET created_at = ? WHERE scan_id = ?",
            (old, "stalerow0001"),
        )
    ids = {it["scan_id"] for it in comm.map_scans(52.0, 52.5, 20.5, 21.5, days=30)["items"]}
    assert ids == {"freshrow0001"}


def test_endpoint_map_uses_public_token_and_keeps_coords(client, mapenv):
    client.post("/api/community/scan", data={
        "scan_id": "mapscanid001", "user_pseudo_id": "pseudo-map-a",
        "country": "PL", "region": "Warsaw", "lat": "52.23", "lon": "21.01",
        "disease": "leaf_spot",
    })
    body = client.get("/api/community/map", params={
        "min_lat": 52.0, "min_lon": 20.5, "max_lat": 52.5, "max_lon": 21.5,
    }).json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["scan_id"] == mapenv.public_scan_token("mapscanid001")
    assert item["scan_id"] != "mapscanid001"  # never the real (feedback) id
    assert item["lat"] == 52.23 and item["lon"] == 21.01
    assert body["truncated"] is False


def test_endpoint_map_rejects_degenerate_bbox(client, mapenv):
    r = client.get("/api/community/map", params={
        "min_lat": 50, "min_lon": 20, "max_lat": 50, "max_lon": 21,  # zero height
    })
    assert r.status_code == 422


# ── /community/heatmap (k-anonymized density) ───────────────────────


def test_density_k_anonymity(mapenv):
    comm = mapenv
    # Two scans in one ~5 km cell → dropped (k=3).
    _scan(comm, "kc1aaaaaaaa1", 52.231, 21.011)
    _scan(comm, "kc2aaaaaaaa2", 52.234, 21.013)
    assert comm.density_grid(52.0, 52.5, 20.5, 21.5)["cells"] == []
    # A third in the same cell clears k → the cell appears with its count.
    _scan(comm, "kc3aaaaaaaa3", 52.236, 21.012)
    res = comm.density_grid(52.0, 52.5, 20.5, 21.5)
    assert len(res["cells"]) == 1
    assert res["cells"][0]["count"] == 3
    assert res["max_count"] == 3
    # No per-scan coords leak — cells carry only a center + count.
    assert set(res["cells"][0].keys()) == {"lat", "lon", "count"}


def test_density_excludes_not_a_plant(mapenv):
    comm = mapenv
    for i in range(3):
        _scan(comm, f"napcell{i:05d}", 52.231 + i * 0.001, 21.011, disease="not_a_plant")
    assert comm.density_grid(52.0, 52.5, 20.5, 21.5)["cells"] == []


def test_density_disease_filter(mapenv):
    comm = mapenv
    for i in range(3):
        _scan(comm, f"lscell{i:06d}", 52.231 + i * 0.001, 21.011, disease="leaf_spot")
    for i in range(3):
        _scan(comm, f"pmcell{i:06d}", 52.231 + i * 0.001, 21.011, disease="powdery_mildew")
    # All 6 share a cell → unfiltered count 6; leaf_spot-only count 3.
    assert comm.density_grid(52.0, 52.5, 20.5, 21.5)["cells"][0]["count"] == 6
    only_ls = comm.density_grid(52.0, 52.5, 20.5, 21.5, disease="leaf_spot")
    assert only_ls["cells"][0]["count"] == 3


def test_density_empty_bbox(mapenv):
    res = mapenv.density_grid(0.0, 1.0, 0.0, 1.0)
    assert res["cells"] == [] and res["max_count"] == 0 and res["total"] == 0


def test_endpoint_heatmap_shape(client, mapenv):
    for i in range(3):
        client.post("/api/community/scan", data={
            "scan_id": f"heatrow{i:05d}", "user_pseudo_id": "pseudo-heat-a",
            "country": "PL", "lat": str(52.231 + i * 0.001), "lon": "21.011",
            "disease": "leaf_spot",
        })
    body = client.get("/api/community/heatmap", params={
        "min_lat": 52.0, "min_lon": 20.5, "max_lat": 52.5, "max_lon": 21.5,
    }).json()
    assert {"cells", "cell_size_deg", "max_count", "total", "k", "days"} <= set(body)
    assert body["k"] == 3
    assert body["cells"] and body["cells"][0]["count"] == 3


def test_endpoint_heatmap_rejects_bad_bbox(client, mapenv):
    r = client.get("/api/community/heatmap", params={
        "min_lat": 50, "min_lon": 20, "max_lat": 50, "max_lon": 21,
    })
    assert r.status_code == 422
