"""Community 'second opinion' store tests.

Exercises community.py's opinion helpers directly against an isolated temp
DB (no FastAPI / network), mirroring the reload-per-test pattern used by
test_community_endpoint.py.
"""
from __future__ import annotations

import sqlite3
import time
import uuid
from contextlib import closing

import pytest


@pytest.fixture
def comm(community_store):
    return community_store


def _scan(comm, scan_id: str) -> None:
    comm.record_scan(
        scan_id=scan_id,
        user_pseudo_id="owner",
        country="GB",
        region="London",
        disease="leaf_spot",
    )


def test_record_and_list_opinions_with_consensus(comm):
    _scan(comm, "s1")
    comm.record_opinion(scan_id="s1", author_pseudo_id="u1",
                        suggested_disease="leaf_spot", note="undersides spotted")
    comm.record_opinion(scan_id="s1", author_pseudo_id="u2",
                        suggested_disease="leaf_spot")
    comm.record_opinion(scan_id="s1", author_pseudo_id="u3",
                        suggested_disease="powdery_mildew", note="looks dusty")

    res = comm.opinions_for("s1")
    assert len(res["opinions"]) == 3
    # Most-voted disease leads the consensus tally.
    assert res["consensus"][0] == {"disease": "leaf_spot", "count": 2}
    assert {"disease": "powdery_mildew", "count": 1} in res["consensus"]
    # Newest opinion first; an absent note normalises to "".
    assert res["opinions"][0]["suggested_disease"] == "powdery_mildew"
    assert res["opinions"][1]["note"] == ""


def test_opinions_for_unknown_scan_is_empty(comm):
    assert comm.opinions_for("does-not-exist") == {
        "opinions": [], "consensus": [], "my_opinion": None, "views": 0,
        "wants_opinion": False, "opinion_requested_at": None,
        "like_count": 0, "liked_by_me": False,
    }


def test_opinions_for_orphaned_companion_rows_fails_closed(comm):
    comm.init_db()
    now = int(time.time())
    with closing(sqlite3.connect(comm.COMMUNITY_DB_PATH)) as connection, connection:
        connection.execute(
            "INSERT INTO community_opinions "
            "(scan_id, author_pseudo_id, suggested_disease, created_at) "
            "VALUES ('orphan', 'author', 'leaf_spot', ?)",
            (now,),
        )
        connection.execute(
            "INSERT INTO community_opinion_requests "
            "(scan_id, user_pseudo_id, created_at) VALUES ('orphan', 'owner', ?)",
            (now,),
        )
        connection.execute(
            "INSERT INTO community_scan_views "
            "(scan_id, viewer_pseudo_id, created_at) VALUES ('orphan', 'viewer', ?)",
            (now,),
        )

    assert comm.opinions_for("orphan") == {
        "opinions": [], "consensus": [], "my_opinion": None, "views": 0,
        "wants_opinion": False, "opinion_requested_at": None,
        "like_count": 0, "liked_by_me": False,
    }


@pytest.mark.parametrize("target_state", ["absent", "deleted", "stale"])
def test_record_opinion_rejects_unavailable_target_in_write_transaction(
    comm, target_state,
):
    owner = str(uuid.uuid4())
    if target_state != "absent":
        comm.record_scan(
            scan_id="closed-target",
            account_actor_id=owner,
            nickname="Owner",
            country="GB",
            region="London",
            disease="leaf_spot",
        )
    if target_state == "deleted":
        comm.request_actor_deletion(owner)
    elif target_state == "stale":
        stale = int(time.time()) - (comm.IDENTITY_ROTATION_DAYS + 1) * 86_400
        with comm._connect() as connection:
            connection.execute(
                "UPDATE community_scans SET created_at = ? WHERE scan_id = ?",
                (stale, "closed-target"),
            )

    with pytest.raises(comm.OpinionTargetUnavailableError):
        comm.record_opinion(
            scan_id="closed-target",
            author_pseudo_id="responder",
            suggested_disease="healthy",
        )
    with closing(sqlite3.connect(comm.COMMUNITY_DB_PATH)) as connection, connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM community_opinions WHERE scan_id = ?",
            ("closed-target",),
        ).fetchone()[0] == 0


def test_opinion_open_respects_30_day_cutoff(comm):
    """A freshly shared scan accepts opinions; after 30 days it goes
    read-only while its public handle remains stable."""
    comm.record_scan(
        scan_id="fresh", user_pseudo_id="owner", country="PL",
        disease="leaf_spot",
    )
    assert comm.opinion_open("fresh") is True

    # Backdate the row past the rotation window and confirm it closes.
    import time as _t
    stale = int(_t.time()) - (comm.IDENTITY_ROTATION_DAYS + 1) * 86_400
    with comm._connect() as conn:
        conn.execute(
            "UPDATE community_scans SET created_at = ? WHERE scan_id = ?",
            (stale, "fresh"),
        )
    assert comm.opinion_open("fresh") is False
    # The scan row itself still exists — only new opinions are blocked.
    assert comm.scan_exists("fresh") is True


def test_opinion_open_unknown_scan_is_false(comm):
    assert comm.opinion_open("nope") is False
    assert comm.opinion_open("") is False


def test_author_token_rotates_with_identity_epoch(comm):
    """The per-feed author token is stable within a 30-day window but differs
    across windows, so activity can't be linked across rotations."""
    import time as _t

    real_time = _t.time
    base = real_time()
    try:
        _t.time = lambda: base
        tok_now = comm._author_token("user-xyz")
        # Same window → same token.
        assert comm._author_token("user-xyz") == tok_now
        # A window later → different token for the same user.
        _t.time = lambda: base + (comm.IDENTITY_ROTATION_DAYS + 1) * 86_400
        assert comm._author_token("user-xyz") != tok_now
    finally:
        _t.time = real_time


def test_repeat_author_keeps_every_comment_but_consensus_takes_latest(comm):
    """Community v2: comments are a thread, not a single vote slot. The same
    author posting twice keeps BOTH rows (Community v2 unlocked repeat
    commenting), but the consensus tally counts their latest pick only."""
    _scan(comm, "s9")
    id1 = comm.record_opinion(
        scan_id="s9", author_pseudo_id="bob", suggested_disease="leaf_spot",
    )
    id2 = comm.record_opinion(
        scan_id="s9", author_pseudo_id="bob",
        suggested_disease="powdery_mildew", note="changed my mind",
    )
    assert id1 != id2  # a new row, not a dedupe

    res = comm.opinions_for("s9")
    assert len(res["opinions"]) == 2  # both comments kept, newest first
    assert res["opinions"][0]["suggested_disease"] == "powdery_mildew"
    assert res["opinions"][1]["suggested_disease"] == "leaf_spot"
    # Consensus counts bob once, for his latest pick.
    assert res["consensus"] == [{"disease": "powdery_mildew", "count": 1}]

    # A different author's own opinion adds a second, independent vote.
    comm.record_opinion(
        scan_id="s9", author_pseudo_id="carol", suggested_disease="viral_disease",
    )
    res = comm.opinions_for("s9")
    assert len(res["opinions"]) == 3
    assert sum(c["count"] for c in res["consensus"]) == 2
