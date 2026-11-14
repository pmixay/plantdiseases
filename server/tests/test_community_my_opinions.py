"""Opinion counts on a signed actor's own scans — drives the second-opinion
notification poll. The per-scan view already exists; this is the batch the
client polls to know which of *its* scans got new opinions.

These run against ``opinion_counts_for_actor``, the function
``/api/community/my-scan-opinions`` calls. They used to exercise a
pseudo-id twin of it that nothing in production called."""

from __future__ import annotations

import importlib
import uuid

import pytest

OWNER, SOMEONE, ALICE, BOB = (str(uuid.uuid4()) for _ in range(4))
_EMPTY = {"scans": [], "total_opinions": 0, "total_views": 0}


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
    comm.record_scan(scan_id="mine1", account_actor_id=OWNER, country="GB",
                     region="London", disease="leaf_spot")
    comm.record_scan(scan_id="mine2", account_actor_id=OWNER, country="GB",
                     region="London", disease="powdery_mildew")
    comm.record_scan(scan_id="mine3", account_actor_id=OWNER, country="GB",
                     region="London", disease="rust")
    comm.record_scan(scan_id="theirs", account_actor_id=SOMEONE, country="GB",
                     region="London", disease="rust")

    # Owner only asked the community to weigh in on mine1 and mine2. mine3 was
    # shared but never flagged, so opinions on it must NOT drive a notification.
    comm.set_wants_opinion("mine1", account_actor_id=OWNER)
    comm.set_wants_opinion("mine2", account_actor_id=OWNER)

    # two outside opinions on mine1, one on mine2, one on the un-requested mine3,
    # one on theirs, and one self-opinion on mine1 (must NOT count).
    comm.record_opinion(scan_id="mine1", account_actor_id=ALICE, suggested_disease="blight")
    comm.record_opinion(scan_id="mine1", account_actor_id=BOB, suggested_disease="leaf_spot")
    comm.record_opinion(scan_id="mine1", account_actor_id=OWNER, suggested_disease="leaf_spot")
    comm.record_opinion(scan_id="mine2", account_actor_id=ALICE, suggested_disease="powdery_mildew")
    comm.record_opinion(scan_id="mine3", account_actor_id=ALICE, suggested_disease="rust")
    comm.record_opinion(scan_id="theirs", account_actor_id=OWNER, suggested_disease="rust")

    res = comm.opinion_counts_for_actor(OWNER)
    by_id = {s["scan_id"]: s for s in res["scans"]}
    # only my scans, only those I asked the community about, only those with opinions
    assert set(by_id) == {"mine1", "mine2"}
    assert by_id["mine1"]["opinion_count"] == 2      # self-opinion excluded
    assert by_id["mine1"]["disease"] == "leaf_spot"
    assert by_id["mine2"]["opinion_count"] == 1
    assert res["total_opinions"] == 3
    assert isinstance(by_id["mine1"]["latest_at"], int)


def test_latest_nickname_is_returned_for_poll_notification(comm):
    """The poll surfaces the most-recent outside responder's nickname so the
    fallback notification can name who weighed in (matching the FCM push)."""
    comm.record_scan(scan_id="mine1", account_actor_id=OWNER, country="GB",
                     region="London", disease="leaf_spot")
    comm.set_wants_opinion("mine1", account_actor_id=OWNER)
    comm.record_opinion(scan_id="mine1", account_actor_id=ALICE,
                        suggested_disease="blight", nickname="Leafy Sage 7")
    comm.record_opinion(scan_id="mine1", account_actor_id=BOB,
                        suggested_disease="leaf_spot", nickname="Mossy Baron 3")

    scan = comm.opinion_counts_for_actor(OWNER)["scans"][0]
    # Newest opinion (bob) wins the latest_nickname slot.
    assert scan["latest_nickname"] == "Mossy Baron 3"


def test_empty_for_actor_with_no_opinions_or_a_malformed_id(comm):
    comm.record_scan(scan_id="x", account_actor_id=OWNER, country="GB",
                     region="London", disease="healthy")
    assert comm.opinion_counts_for_actor(OWNER) == _EMPTY
    # Anything that is not an actor id fails closed rather than matching rows.
    assert comm.opinion_counts_for_actor("") == _EMPTY
    assert comm.opinion_counts_for_actor("owner") == _EMPTY


def test_endpoint_returns_counts(client):
    # Ownership is derived from the access token; no stable id rides in the URL.
    r = client.get("/api/community/my-scan-opinions")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "scans" in body and "total_opinions" in body


