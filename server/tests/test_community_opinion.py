"""Community 'second opinion' store tests.

Exercises community.py's opinion helpers directly against an isolated temp
DB (no FastAPI / network), mirroring the reload-per-test pattern used by
test_community_endpoint.py.
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


def test_record_and_list_opinions_with_consensus(comm):
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
        "opinions": [], "consensus": [], "views": 0,
        "wants_opinion": False, "opinion_requested_at": None,
        "like_count": 0, "liked_by_me": False,
    }


def test_daily_opinion_count_guards_none(comm):
    assert comm.daily_opinion_count_for_ip_hash(None) == 0


def test_opinion_open_respects_30_day_cutoff(comm):
    """A freshly shared scan accepts opinions; once its public handle has
    rotated (older than 30 days) it goes read-only."""
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


# ── Durable forum identity (resolve_author) ───────────────────────────

def test_resolve_author_creates_one_row_per_key_hash(comm):
    a = comm.resolve_author(key_hash="k" * 64, seed_display_name="Leafy Sage 7")
    assert a["author_id"].startswith("au_")
    assert a["display_name"] == "Leafy Sage 7"
    assert a["account_id"] is None
    assert a["status"] == "active"


def test_resolve_author_is_idempotent_and_does_not_overwrite_the_seed(comm):
    first = comm.resolve_author(
        key_hash="a" * 64, seed_display_name="Leafy Sage 7", legacy_pseudo_id="pseudo-1",
    )
    # Same key, different (later) seed values -- a re-adopt call must not
    # rename the author or swap which legacy pseudo id it backfills from.
    second = comm.resolve_author(
        key_hash="a" * 64, seed_display_name="Renamed Later", legacy_pseudo_id="pseudo-2",
    )
    assert second["author_id"] == first["author_id"]
    assert second["display_name"] == "Leafy Sage 7"
    assert second["last_seen_at"] >= first["last_seen_at"]

    with comm._connect() as conn:
        stored = conn.execute(
            "SELECT legacy_pseudo_id FROM community_authors WHERE author_id = ?",
            (first["author_id"],),
        ).fetchone()
    assert stored[0] == "pseudo-1"


def test_resolve_author_different_keys_never_collide(comm):
    a = comm.resolve_author(key_hash="b" * 64, seed_display_name="Author A")
    b = comm.resolve_author(key_hash="c" * 64, seed_display_name="Author B")
    assert a["author_id"] != b["author_id"]


def test_resolve_author_blank_key_hash_rejected(comm):
    with pytest.raises(ValueError):
        comm.resolve_author(key_hash="", seed_display_name="Someone")


def test_resolve_author_blank_display_name_falls_back(comm):
    a = comm.resolve_author(key_hash="d" * 64, seed_display_name="   ")
    assert a["display_name"] == "Plant grower"
