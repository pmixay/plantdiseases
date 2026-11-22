"""Tests for the per-viewer-deduped 'views' counter on shared scans.

Exercises community.py directly against an isolated temp DB (no FastAPI /
network), mirroring the reload-per-test pattern used by the other community
tests.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def comm(community_store):
    return community_store


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


def test_view_counts_batch(comm):
    _scan(comm, scan_id="s1")
    _scan(comm, scan_id="s2")
    comm.record_views(["s1"], "v1")
    comm.record_views(["s1"], "v2")
    comm.record_views(["s2"], "v1")

    # Scans without views (and unknown ids) are omitted.
    assert comm.view_counts(["s1", "s2", "s3"]) == {"s1": 2, "s2": 1}


