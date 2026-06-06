"""Tests for the 'request a second opinion' flag + strict disease scoping.

Exercises community.py directly against an isolated temp DB (no FastAPI /
network), mirroring the reload-per-test pattern used by the other
community tests.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def comm(tmp_path, monkeypatch):
    monkeypatch.setenv("COMMUNITY_DB_PATH", str(tmp_path / "community.db"))
    monkeypatch.setenv("COMMUNITY_THUMB_DIR", str(tmp_path / "thumbs"))
    monkeypatch.setenv("FEEDBACK_IP_PEPPER", "test-pepper")
    import community as c
    importlib.reload(c)
    return c


def _wants(comm, scan_id: str, country: str = "GB") -> bool:
    """Look up the wants_opinion flag for one scan via the read path."""
    rows = comm.nearby_scans(country=country, limit=50)
    for r in rows:
        if r["scan_id"] == scan_id:
            return bool(r["wants_opinion"])
    raise AssertionError(f"scan {scan_id} not found in nearby feed")


def test_flag_after_upload_materialises_on_row(comm):
    comm.record_scan(scan_id="s1", user_pseudo_id="owner", country="GB",
                     region="London", disease="leaf_spot")
    assert _wants(comm, "s1") is False
    assert comm.set_wants_opinion("s1", "owner") is True
    assert _wants(comm, "s1") is True


def test_flag_before_upload_is_applied_when_scan_lands(comm):
    # The request arrives first (the upload races behind it).
    assert comm.set_wants_opinion("s2", "owner") is True
    comm.record_scan(scan_id="s2", user_pseudo_id="owner", country="GB",
                     region="London", disease="leaf_spot")
    # The pending request is honoured on insert — no silent no-op.
    assert _wants(comm, "s2") is True


def test_flag_is_owner_scoped(comm):
    comm.record_scan(scan_id="s3", user_pseudo_id="owner", country="GB",
                     region="London", disease="leaf_spot")
    # A different install can't flag someone else's scan.
    assert comm.set_wants_opinion("s3", "attacker") is True  # intent recorded
    assert _wants(comm, "s3") is False                       # but never applied
    # The real owner can.
    comm.set_wants_opinion("s3", "owner")
    assert _wants(comm, "s3") is True


def test_record_scan_dedups_by_scan_id(comm):
    first = comm.record_scan(scan_id="dup", user_pseudo_id="owner", country="GB",
                             region="London", disease="leaf_spot")
    second = comm.record_scan(scan_id="dup", user_pseudo_id="owner", country="GB",
                              region="London", disease="leaf_spot")
    assert first == second  # same row id, no duplicate
    rows = comm.nearby_scans(country="GB", limit=50)
    assert sum(1 for r in rows if r["scan_id"] == "dup") == 1


def test_nearby_strict_disease_filter_no_other_diseases(comm):
    # One leaf_spot, several healthy — all same country/region.
    comm.record_scan(scan_id="d1", user_pseudo_id="u1", country="GB",
                     region="London", disease="leaf_spot")
    for i in range(5):
        comm.record_scan(scan_id=f"h{i}", user_pseudo_id=f"u{i}", country="GB",
                         region="London", disease="healthy")
    # A disease-scoped query must NEVER pad with other diseases, even when
    # the same-disease bucket is thin.
    rows = comm.nearby_scans(country="GB", region="London",
                             disease="leaf_spot", limit=12)
    assert [r["scan_id"] for r in rows] == ["d1"]
    assert all(r["disease"] == "leaf_spot" for r in rows)


def test_nearby_without_disease_still_returns_everything(comm):
    comm.record_scan(scan_id="a", user_pseudo_id="u1", country="GB",
                     region="London", disease="leaf_spot")
    comm.record_scan(scan_id="b", user_pseudo_id="u2", country="GB",
                     region="London", disease="healthy")
    rows = comm.nearby_scans(country="GB", region="London", limit=12)
    assert {r["scan_id"] for r in rows} == {"a", "b"}


def test_delete_for_user_clears_pending_requests(comm):
    comm.set_wants_opinion("s9", "gone")
    comm.delete_for_user("gone")
    # After deletion the request is gone, so a later upload isn't flagged.
    comm.record_scan(scan_id="s9", user_pseudo_id="gone", country="GB",
                     region="London", disease="leaf_spot")
    assert _wants(comm, "s9") is False
