"""Tests for the per-viewer-deduped 'views' counter on shared scans.

Exercises community.py directly against an isolated temp DB (no FastAPI /
network), mirroring the reload-per-test pattern used by the other community
tests.
"""
from __future__ import annotations

import importlib
import uuid

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
    assert comm.record_views(["s1"], "viewer-a") == 1   # new
    assert comm.record_views(["s1"], "viewer-a") == 0  # dedup, no growth
    assert comm.view_count_for("s1") == 1
    assert comm.record_views(["s1"], "viewer-b") == 1
    assert comm.view_count_for("s1") == 2


def test_owner_self_view_never_counts(comm):
    _scan(comm, owner="owner")
    assert comm.record_views(["s1"], "owner") == 0
    assert comm.view_count_for("s1") == 0


def test_view_on_unknown_scan_is_ignored(comm):
    assert comm.record_views(["does-not-exist"], "viewer-a") == 0
    assert comm.view_count_for("does-not-exist") == 0


def test_blank_args_are_ignored(comm):
    _scan(comm)
    assert comm.record_views([""], "viewer-a") == 0
    assert comm.record_views(["s1"], "  ") == 0
    assert comm.view_count_for("s1") == 0


def test_view_counts_and_scan_counts_batch(comm):
    _scan(comm, scan_id="s1")
    _scan(comm, scan_id="s2")
    comm.record_views(["s1"], "v1")
    comm.record_views(["s1"], "v2")
    comm.record_views(["s2"], "v1")
    comm.record_opinion(scan_id="s1", author_pseudo_id="v1",
                        suggested_disease="leaf_spot")

    assert comm.view_counts(["s1", "s2", "s3"]) == {"s1": 2, "s2": 1}

    counts = comm.scan_counts(["s1", "s2", "s3"])
    assert counts["s1"] == {"opinions": 1, "views": 2}
    assert counts["s2"] == {"opinions": 0, "views": 1}
    assert "s3" not in counts  # zero/unknown omitted


def test_opinions_for_includes_view_count(comm):
    _scan(comm)
    comm.record_views(["s1"], "v1")
    comm.record_views(["s1"], "v2")
    out = comm.opinions_for("s1")
    assert out["views"] == 2
    assert out["opinions"] == []


def test_opinion_counts_for_actor_carries_views(comm):
    owner, other, viewer_one, viewer_two = (str(uuid.uuid4()) for _ in range(4))
    comm.record_scan(scan_id="s1", account_actor_id=owner, country="GB",
                     region="London", disease="leaf_spot")
    # The poll only surfaces scans the owner asked the community about.
    comm.set_wants_opinion("s1", account_actor_id=owner)
    # An opinion from someone else is what makes the scan show up here.
    comm.record_opinion(scan_id="s1", account_actor_id=other,
                        suggested_disease="leaf_spot")
    comm.record_views(["s1"], account_actor_id=viewer_one)
    comm.record_views(["s1"], account_actor_id=viewer_two)
    comm.record_views(["s1"], account_actor_id=viewer_two)  # dedup

    payload = comm.opinion_counts_for_actor(owner)
    assert payload["total_opinions"] == 1
    assert payload["total_views"] == 2
    scan = next(s for s in payload["scans"] if s["scan_id"] == "s1")
    assert scan["opinion_count"] == 1
    assert scan["view_count"] == 2
