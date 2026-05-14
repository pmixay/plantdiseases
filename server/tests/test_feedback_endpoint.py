"""Coverage for the new /api/feedback endpoint and feedback DB module."""

from __future__ import annotations

import pytest


@pytest.fixture
def isolated_feedback_db(tmp_path, monkeypatch):
    """Point the feedback store at a temp DB per test so writes can't leak."""
    db_path = tmp_path / "feedback.db"
    monkeypatch.setenv("FEEDBACK_DB_PATH", str(db_path))
    import importlib

    import feedback as fb
    importlib.reload(fb)
    # Re-bind the symbol main.py imported.
    import main as server_main
    server_main.record_feedback = fb.record_feedback
    server_main.init_feedback_db = fb.init_db
    server_main.feedback_counts = fb.feedback_counts
    yield db_path


def test_feedback_endpoint_accepts_minimal_payload(client, isolated_feedback_db):
    r = client.post(
        "/api/feedback",
        json={"scan_id": "scan-001", "correct": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert isinstance(body["id"], int) and body["id"] > 0


def test_feedback_endpoint_persists_and_aggregates(client, isolated_feedback_db):
    import feedback as fb

    # Mix correct + incorrect feedback signals.
    client.post("/api/feedback", json={"scan_id": "s1", "correct": True})
    client.post(
        "/api/feedback",
        json={
            "scan_id": "s2",
            "correct": False,
            "corrected_class": "powdery_mildew",
            "note": "Looks like white powder, not gray mold.",
        },
    )
    client.post("/api/feedback", json={"scan_id": "s3", "correct": False})

    counts = fb.feedback_counts()
    assert counts["total"] == 3
    assert counts["correct"] == 1
    assert counts["incorrect"] == 2


def test_feedback_endpoint_rejects_oversize_note(client, isolated_feedback_db):
    r = client.post(
        "/api/feedback",
        json={"scan_id": "s1", "correct": False, "note": "x" * 5000},
    )
    # Pydantic field validator caps `note` at 2000 chars.
    assert r.status_code == 422


def test_feedback_endpoint_requires_scan_id(client, isolated_feedback_db):
    r = client.post("/api/feedback", json={"correct": True})
    assert r.status_code == 422


def test_feedback_counts_surface_on_metrics(client, isolated_feedback_db):
    client.post("/api/feedback", json={"scan_id": "s1", "correct": True})
    m = client.get("/api/metrics").json()
    assert "feedback" in m
    assert m["feedback"]["total"] >= 1
