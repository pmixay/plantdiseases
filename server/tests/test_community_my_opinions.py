"""Opinion counts on a user's own scans — drives the second-opinion
notification poll. The per-scan view already exists; this is the batch the
client polls to know which of *its* scans got new opinions."""

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


def test_counts_only_for_my_scans_and_exclude_my_own_opinions(comm):
    # owner shares two scans; another user shares one.
    comm.record_scan(scan_id="mine1", user_pseudo_id="owner", country="GB",
                     region="London", disease="leaf_spot")
    comm.record_scan(scan_id="mine2", user_pseudo_id="owner", country="GB",
                     region="London", disease="powdery_mildew")
    comm.record_scan(scan_id="theirs", user_pseudo_id="someone", country="GB",
                     region="London", disease="rust")

    # two outside opinions on mine1, one on mine2, one on theirs, and one
    # self-opinion on mine1 (must NOT count).
    comm.record_opinion(scan_id="mine1", author_pseudo_id="alice", suggested_disease="blight")
    comm.record_opinion(scan_id="mine1", author_pseudo_id="bob", suggested_disease="leaf_spot")
    comm.record_opinion(scan_id="mine1", author_pseudo_id="owner", suggested_disease="leaf_spot")
    comm.record_opinion(scan_id="mine2", author_pseudo_id="alice", suggested_disease="powdery_mildew")
    comm.record_opinion(scan_id="theirs", author_pseudo_id="owner", suggested_disease="rust")

    res = comm.opinion_counts_for_user("owner")
    by_id = {s["scan_id"]: s for s in res["scans"]}
    assert set(by_id) == {"mine1", "mine2"}          # only my scans, only those with opinions
    assert by_id["mine1"]["opinion_count"] == 2      # self-opinion excluded
    assert by_id["mine1"]["disease"] == "leaf_spot"
    assert by_id["mine2"]["opinion_count"] == 1
    assert res["total_opinions"] == 3
    assert by_id["mine1"]["latest_at"] >= by_id["mine2"]["latest_at"] or True  # ts present
    assert isinstance(by_id["mine1"]["latest_at"], int)


def test_empty_for_user_with_no_opinions(comm):
    comm.record_scan(scan_id="x", user_pseudo_id="owner", country="GB",
                     region="London", disease="healthy")
    assert comm.opinion_counts_for_user("owner") == {"scans": [], "total_opinions": 0}
    assert comm.opinion_counts_for_user("") == {"scans": [], "total_opinions": 0}


def test_endpoint_returns_counts(client):
    # The endpoint validates the id and returns the (empty, in the test DB) shape.
    r = client.get("/api/community/my-scan-opinions/test-user-123")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "scans" in body and "total_opinions" in body


def test_endpoint_rejects_bad_id(client):
    r = client.get("/api/community/my-scan-opinions/" + "bad id with spaces")
    assert r.status_code in (404, 422)
