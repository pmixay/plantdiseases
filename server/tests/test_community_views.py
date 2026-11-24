"""Tests for the per-viewer-deduped 'views' counter on shared scans.

Exercises community.py directly against an isolated temp DB (no FastAPI /
network), mirroring the reload-per-test pattern used by the other community
tests.
"""
from __future__ import annotations

import pytest

import community_scans
import community_views


@pytest.fixture
def comm(community_store):
    return community_store


def _scan(comm, scan_id="s1", owner="owner"):
    community_scans.record_scan(scan_id=scan_id, user_pseudo_id=owner, country="GB",
                     region="London", disease="leaf_spot")


def test_view_is_recorded_once_per_viewer(comm):
    _scan(comm)
    assert community_views.record_views(["s1"], "viewer-a") == 1   # new
    assert community_views.record_views(["s1"], "viewer-a") == 0  # dedup, no growth
    assert community_views.view_count_for("s1") == 1
    assert community_views.record_views(["s1"], "viewer-b") == 1
    assert community_views.view_count_for("s1") == 2


def test_owner_self_view_never_counts(comm):
    _scan(comm, owner="owner")
    assert community_views.record_views(["s1"], "owner") == 0
    assert community_views.view_count_for("s1") == 0


def test_view_on_unknown_scan_is_ignored(comm):
    assert community_views.record_views(["does-not-exist"], "viewer-a") == 0
    assert community_views.view_count_for("does-not-exist") == 0


def test_blank_args_are_ignored(comm):
    _scan(comm)
    assert community_views.record_views([""], "viewer-a") == 0
    assert community_views.record_views(["s1"], "  ") == 0
    assert community_views.view_count_for("s1") == 0


def test_view_counts_batch(comm):
    _scan(comm, scan_id="s1")
    _scan(comm, scan_id="s2")
    community_views.record_views(["s1"], "v1")
    community_views.record_views(["s1"], "v2")
    community_views.record_views(["s2"], "v1")

    # Scans without views (and unknown ids) are omitted.
    assert community_views.view_counts(["s1", "s2", "s3"]) == {"s1": 2, "s2": 1}


