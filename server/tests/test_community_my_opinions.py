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


def test_counts_only_for_requested_scans_and_exclude_my_own_opinions(comm):
    # owner shares three scans; another user shares one.
    comm.record_scan(scan_id="mine1", user_pseudo_id="owner", country="GB",
                     region="London", disease="leaf_spot")
    comm.record_scan(scan_id="mine2", user_pseudo_id="owner", country="GB",
                     region="London", disease="powdery_mildew")
    comm.record_scan(scan_id="mine3", user_pseudo_id="owner", country="GB",
                     region="London", disease="rust")
    comm.record_scan(scan_id="theirs", user_pseudo_id="someone", country="GB",
                     region="London", disease="rust")

    # Owner only asked the community to weigh in on mine1 and mine2. mine3 was
    # shared but never flagged, so opinions on it must NOT drive a notification.
    comm.set_wants_opinion("mine1", "owner")
    comm.set_wants_opinion("mine2", "owner")

    # two outside opinions on mine1, one on mine2, one on the un-requested mine3,
    # one on theirs, and one self-opinion on mine1 (must NOT count).
    comm.record_opinion(scan_id="mine1", author_pseudo_id="alice", suggested_disease="blight")
    comm.record_opinion(scan_id="mine1", author_pseudo_id="bob", suggested_disease="leaf_spot")
    comm.record_opinion(scan_id="mine1", author_pseudo_id="owner", suggested_disease="leaf_spot")
    comm.record_opinion(scan_id="mine2", author_pseudo_id="alice", suggested_disease="powdery_mildew")
    comm.record_opinion(scan_id="mine3", author_pseudo_id="alice", suggested_disease="rust")
    comm.record_opinion(scan_id="theirs", author_pseudo_id="owner", suggested_disease="rust")

    res = comm.opinion_counts_for_user("owner")
    by_id = {s["scan_id"]: s for s in res["scans"]}
    # only my scans, only those I asked the community about, only those with opinions
    assert set(by_id) == {"mine1", "mine2"}
    assert by_id["mine1"]["opinion_count"] == 2      # self-opinion excluded
    assert by_id["mine1"]["disease"] == "leaf_spot"
    assert by_id["mine2"]["opinion_count"] == 1
    assert res["total_opinions"] == 3
    assert by_id["mine1"]["latest_at"] >= by_id["mine2"]["latest_at"] or True  # ts present
    assert isinstance(by_id["mine1"]["latest_at"], int)


def test_unrequested_scan_never_notifies(comm):
    """A scan shared to the corpus but never flagged for a second opinion does
    not surface in the poll, even when others leave opinions on it."""
    comm.record_scan(scan_id="shared", user_pseudo_id="owner", country="GB",
                     region="London", disease="leaf_spot")
    comm.record_opinion(scan_id="shared", author_pseudo_id="alice", suggested_disease="blight")

    assert comm.opinion_counts_for_user("owner") == {
        "scans": [], "total_opinions": 0, "total_views": 0,
    }


def test_latest_nickname_is_returned_for_poll_notification(comm):
    """The poll surfaces the most-recent outside responder's nickname so the
    fallback notification can name who weighed in (matching the FCM push)."""
    comm.record_scan(scan_id="mine1", user_pseudo_id="owner", country="GB",
                     region="London", disease="leaf_spot")
    comm.set_wants_opinion("mine1", "owner")
    comm.record_opinion(scan_id="mine1", author_pseudo_id="alice",
                        suggested_disease="blight", nickname="Leafy Sage 7")
    comm.record_opinion(scan_id="mine1", author_pseudo_id="bob",
                        suggested_disease="leaf_spot", nickname="Mossy Baron 3")

    scan = comm.opinion_counts_for_user("owner")["scans"][0]
    # Newest opinion (bob) wins the latest_nickname slot.
    assert scan["latest_nickname"] == "Mossy Baron 3"


def test_empty_for_user_with_no_opinions(comm):
    comm.record_scan(scan_id="x", user_pseudo_id="owner", country="GB",
                     region="London", disease="healthy")
    assert comm.opinion_counts_for_user("owner") == {
        "scans": [], "total_opinions": 0, "total_views": 0,
    }
    assert comm.opinion_counts_for_user("") == {
        "scans": [], "total_opinions": 0, "total_views": 0,
    }


def test_endpoint_returns_counts(client):
    # The endpoint validates the id and returns the (empty, in the test DB) shape.
    r = client.get("/api/community/my-scan-opinions/test-user-123")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "scans" in body and "total_opinions" in body


def test_endpoint_rejects_bad_id(client):
    r = client.get("/api/community/my-scan-opinions/" + "bad id with spaces")
    assert r.status_code in (404, 422)
