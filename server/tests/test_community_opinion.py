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
    assert comm.opinions_for("does-not-exist") == {"opinions": [], "consensus": []}


def test_delete_for_user_removes_their_opinions(comm):
    comm.record_opinion(scan_id="s4", author_pseudo_id="gone",
                        suggested_disease="leaf_spot")
    comm.record_opinion(scan_id="s4", author_pseudo_id="stay",
                        suggested_disease="healthy")
    comm.delete_for_user("gone")

    res = comm.opinions_for("s4")
    assert len(res["opinions"]) == 1
    assert res["opinions"][0]["suggested_disease"] == "healthy"


def test_daily_opinion_count_guards_none(comm):
    assert comm.daily_opinion_count_for_ip_hash(None) == 0
