"""Tests for the anonymized scan + feedback collection store (server/labeling.py).

The human admin review queue (claim/verdict/auth/endpoints) was removed — the
store is now export-only (tools/export_data.py), judged offline. These tests
cover the surviving store API: idempotent inserts, scan lookup, the feedback
fold-back and the daily-cap counter."""

from __future__ import annotations

import hashlib
import io

import pytest
from PIL import Image

import labeling

_seed_counter = 0


def _unique_jpeg() -> bytes:
    """Distinct bytes each call so the SHA-unique index doesn't collapse seeds."""
    global _seed_counter
    _seed_counter += 1
    c = _seed_counter
    img = Image.new("RGB", (64, 64), (c * 37 % 256, c * 101 % 256, c * 53 % 256))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


@pytest.fixture
def labdb(monkeypatch, tmp_path):
    """Fresh per-test labeling DB + image dir (WAL connections leak across paths)."""
    monkeypatch.setattr(labeling, "LABELING_DB_PATH", tmp_path / "labeling.db")
    monkeypatch.setattr(labeling, "LABELING_IMG_DIR", tmp_path / "images")
    labeling._initialised = False
    labeling.init_db()
    return labeling


def _seed(cls, status="pending", source="user_scan", scan_id=None):
    body = _unique_jpeg()
    sha = hashlib.sha256(body).hexdigest()
    fn = f"{sha}.jpg"
    labeling.LABELING_IMG_DIR.mkdir(parents=True, exist_ok=True)
    (labeling.LABELING_IMG_DIR / fn).write_bytes(body)
    rid = labeling.insert_candidate(
        cls=cls, image_path=fn, source=source, source_url=None,
        subject_plant=None, sha=sha, phash=None, status=status,
        model_pred=cls, scan_id=scan_id or sha,
    )
    return rid, sha


def test_insert_is_sha_idempotent(labdb):
    body = _unique_jpeg()
    sha = hashlib.sha256(body).hexdigest()
    a = labeling.insert_candidate(
        cls="nutrient_deficiency", image_path=f"{sha}.jpg", source="user_scan",
        source_url=None, subject_plant=None, sha=sha, phash=None, scan_id="a1",
    )
    dup = labeling.insert_candidate(
        cls="nutrient_deficiency", image_path=f"{sha}.jpg", source="user_scan",
        source_url=None, subject_plant=None, sha=sha, phash=None, scan_id="a2",
    )
    assert a is not None and dup is None


def test_candidate_for_scan_roundtrip(labdb):
    rid, sha = _seed("gray_mold_botrytis", scan_id="scan-xyz")
    row = labeling.candidate_for_scan("scan-xyz")
    assert row is not None
    assert row["id"] == rid and row["class"] == "gray_mold_botrytis"
    assert labeling.candidate_for_scan("missing") is None


def test_mark_feedback_negative_reopens_as_user_feedback(labdb):
    _seed("healthy", status="auto_yes", scan_id="s1")
    assert labeling.mark_feedback("s1", correct=False, corrected_class="leaf_spot") is True
    row = labeling.candidate_for_scan("s1")
    assert row["status"] == "pending"
    assert row["source"] == "user_feedback"
    assert row["class"] == "leaf_spot"       # retargeted at the user's class
    assert row["user_correct"] == 0


def test_mark_feedback_positive_promotes_pending_to_auto_yes(labdb):
    _seed("pests", status="pending", scan_id="s2")  # model_pred == class
    assert labeling.mark_feedback("s2", correct=True) is True
    row = labeling.candidate_for_scan("s2")
    assert row["status"] == "auto_yes" and row["user_correct"] == 1


def test_count_user_scans_today(labdb):
    _seed("leaf_spot")
    _seed("leaf_spot")
    _seed("powdery_mildew")
    assert labeling.count_user_scans_today("leaf_spot") == 2
    assert labeling.count_user_scans_today("powdery_mildew") == 1
