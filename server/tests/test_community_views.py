"""Tests for the per-viewer-deduped 'views' counter on shared scans.

Exercises community.py directly against an isolated temp DB (no FastAPI /
network), mirroring the reload-per-test pattern used by the other community
tests.
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


def _scan(comm, scan_id="s1", owner="owner"):
    comm.record_scan(scan_id=scan_id, user_pseudo_id=owner, country="GB",
                     region="London", disease="leaf_spot")


def test_view_is_recorded_once_per_viewer(comm):
    _scan(comm)
    assert comm.record_view("s1", "viewer-a") is True   # new
    assert comm.record_view("s1", "viewer-a") is False  # dedup, no growth
    assert comm.view_count_for("s1") == 1
    assert comm.record_view("s1", "viewer-b") is True
    assert comm.view_count_for("s1") == 2


def test_owner_self_view_never_counts(comm):
    _scan(comm, owner="owner")
    assert comm.record_view("s1", "owner") is False
    assert comm.view_count_for("s1") == 0


def test_view_on_unknown_scan_is_ignored(comm):
    assert comm.record_view("does-not-exist", "viewer-a") is False
    assert comm.view_count_for("does-not-exist") == 0


def test_blank_args_are_ignored(comm):
    _scan(comm)
    assert comm.record_view("", "viewer-a") is False
    assert comm.record_view("s1", "  ") is False
    assert comm.view_count_for("s1") == 0


def test_view_counts_and_scan_counts_batch(comm):
    _scan(comm, scan_id="s1")
    _scan(comm, scan_id="s2")
    comm.record_view("s1", "v1")
    comm.record_view("s1", "v2")
    comm.record_view("s2", "v1")
    comm.record_opinion(scan_id="s1", author_pseudo_id="v1",
                        suggested_disease="leaf_spot")

    assert comm.view_counts(["s1", "s2", "s3"]) == {"s1": 2, "s2": 1}

    counts = comm.scan_counts(["s1", "s2", "s3"])
    assert counts["s1"] == {"opinions": 1, "views": 2}
    assert counts["s2"] == {"opinions": 0, "views": 1}
    assert "s3" not in counts  # zero/unknown omitted


def test_opinions_for_includes_view_count(comm):
    _scan(comm)
    comm.record_view("s1", "v1")
    comm.record_view("s1", "v2")
    out = comm.opinions_for("s1")
    assert out["views"] == 2
    assert out["opinions"] == []


def test_opinion_counts_for_user_carries_views(comm):
    _scan(comm, scan_id="s1", owner="owner")
    # An opinion from someone else is what makes the scan show up here.
    comm.record_opinion(scan_id="s1", author_pseudo_id="other",
                        suggested_disease="leaf_spot")
    comm.record_view("s1", "v1")
    comm.record_view("s1", "v2")
    comm.record_view("s1", "v2")  # dedup

    payload = comm.opinion_counts_for_user("owner")
    assert payload["total_opinions"] == 1
    assert payload["total_views"] == 2
    scan = next(s for s in payload["scans"] if s["scan_id"] == "s1")
    assert scan["opinion_count"] == 1
    assert scan["view_count"] == 2
