"""Feedback → labeling-queue wiring (mark_feedback, image-upload path)."""

from __future__ import annotations

import hashlib
import io
import time

import pytest
from PIL import Image

import feedback as feedback_mod
import labeling


def _jpeg(seed=0, size=(640, 480)) -> bytes:
    img = Image.new("RGB", size, (40, 150, 60))
    img.putpixel((1, 1), (seed % 251, (seed * 7) % 251, 99))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def labdb(tmp_path, monkeypatch):
    """Fresh labeling DB + an isolated feedback DB (module-global patch, no
    reload needed — every fn resolves its path at call time).

    The labeling DB is redirected to this test's own ``tmp_path``, like every
    other labeling test file does. It used to reuse the SESSION-scoped path from
    ``conftest`` and just unlink the ``.db`` between tests, which left the
    ``-wal`` and ``-shm`` sidecars behind, pointed the next connection at a new
    inode under an old shared-memory name, and made the fixture depend on
    whatever the rest of the session had already done to that one file. It
    passed locally and errored on CI with ``sqlite3.OperationalError: disk I/O
    error`` at ``PRAGMA journal_mode=WAL`` — same 650 tests, same order, so the
    trigger was environmental and the fixture was merely the thing thin enough
    to break. A per-test directory removes the shared state rather than timing
    the symptom.
    """
    monkeypatch.setattr(labeling, "LABELING_DB_PATH", tmp_path / "labeling.db")
    monkeypatch.setattr(labeling, "LABELING_IMG_DIR", tmp_path / "labeling_images")
    labeling._initialised = False
    labeling.init_db()
    monkeypatch.setattr(feedback_mod, "FEEDBACK_DB_PATH", tmp_path / "feedback.db")
    monkeypatch.setattr(feedback_mod, "FEEDBACK_IMG_DIR", tmp_path / "fb_images")
    monkeypatch.setattr(feedback_mod, "_initialised", False)
    return labeling


def _seed_scan(scan_id, cls="healthy", status="pending", model_pred=None, seed=1):
    body = _jpeg(seed)
    sha = hashlib.sha256(body).hexdigest()
    fn = f"{sha}.jpg"
    labeling.LABELING_IMG_DIR.mkdir(parents=True, exist_ok=True)
    (labeling.LABELING_IMG_DIR / fn).write_bytes(body)
    return labeling.insert_candidate(
        cls=cls, image_path=fn, source="user_scan", source_url=None,
        subject_plant=None, sha=sha, phash=None, status=status,
        model_pred=model_pred or cls, model_conf=0.5, scan_id=scan_id,
    )


def _row(scan_id):
    return labeling.candidate_for_scan(scan_id)


# ── mark_feedback (unit) ─────────────────────────────────────────────
def test_negative_feedback_reopens_even_auto_yes(labdb):
    _seed_scan("scanA", cls="healthy", status="auto_yes")
    assert labeling.mark_feedback("scanA", False, "leaf_spot", "looks spotty") is True
    row = _row("scanA")
    assert row["status"] == "pending"
    assert row["source"] == "user_feedback"
    assert row["class"] == "leaf_spot"            # retargeted at user's class
    assert row["feedback_note"] == "looks spotty"
    assert row["user_correct"] == 0


def test_negative_feedback_without_correction_keeps_class(labdb):
    _seed_scan("scanB", cls="pests", status="auto_yes")
    labeling.mark_feedback("scanB", False, None, None)
    row = _row("scanB")
    assert row["status"] == "pending" and row["class"] == "pests"


def test_positive_feedback_promotes_when_model_user_agree(labdb):
    _seed_scan("scanC", cls="leaf_spot", status="pending", model_pred="leaf_spot")
    labeling.mark_feedback("scanC", True)
    row = _row("scanC")
    assert row["status"] == "auto_yes" and row["user_correct"] == 1


def test_positive_feedback_no_promote_on_disagreement(labdb):
    # model_pred differs from the candidate class → stays pending
    _seed_scan("scanD", cls="leaf_spot", status="pending", model_pred="viral_disease")
    labeling.mark_feedback("scanD", True)
    row = _row("scanD")
    assert row["status"] == "pending" and row["user_correct"] == 1


def test_feedback_never_overrides_human_verdict(labdb):
    _seed_scan("scanE", cls="pests", status="yes")
    assert labeling.mark_feedback("scanE", False, "healthy", "no!") is True
    row = _row("scanE")
    assert row["status"] == "yes" and row["class"] == "pests"
    assert row["user_correct"] is None


def test_mark_feedback_unknown_scan_returns_false(labdb):
    assert labeling.mark_feedback("nope", False) is False


# ── HTTP wiring ──────────────────────────────────────────────────────
def _wait_for(predicate, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = predicate()
        if v:
            return v
        time.sleep(0.02)
    return predicate()


def _post_feedback(client, scan_id, correct, corrected=None, user=None):
    feedback_mod.record_issued_scan_id(scan_id)
    payload = {
        "scan_id": scan_id, "correct": correct,
        "predicted_class": "healthy", "predicted_confidence": 0.9,
    }
    if corrected:
        payload["corrected_class"] = corrected
    if user:
        payload["user_pseudo_id"] = user
    r = client.post("/api/feedback", json=payload)
    assert r.status_code == 200, r.text
    return r


def test_feedback_endpoint_reroutes_recorded_scan(client, labdb):
    _seed_scan("scanF1xx", cls="healthy", status="auto_yes")
    _post_feedback(client, "scanF1xx", False, corrected="powdery_mildew")
    row = _wait_for(
        lambda: (_row("scanF1xx") or {}).get("status") == "pending" and _row("scanF1xx"))
    assert row["source"] == "user_feedback"
    assert row["class"] == "powdery_mildew"


def test_feedback_image_upload_creates_candidate_for_unrecorded_scan(client, labdb):
    scan_id = "scanF2xx"
    _post_feedback(client, scan_id, False, corrected="leaf_spot")
    assert _row(scan_id) is None  # scan was never recorded
    r = client.post(
        "/api/feedback/image",
        data={"scan_id": scan_id},
        files={"image": ("leaf.jpg", _jpeg(seed=42), "image/jpeg")},
    )
    assert r.status_code == 200, r.text
    row = _wait_for(lambda: _row(scan_id))
    assert row is not None
    assert row["source"] == "user_feedback" and row["status"] == "pending"
    assert row["class"] == "leaf_spot"
    # the stored image went through the anonymize helper (re-encoded jpg)
    stored = labeling.LABELING_IMG_DIR / row["image_path"]
    assert stored.is_file()
    assert not Image.open(stored).getexif()
