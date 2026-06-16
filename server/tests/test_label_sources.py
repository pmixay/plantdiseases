"""Prioritised labeling queue (user feedback + recorded user scans) and the
stats per-source block. Dataset ingestion/export was removed — the admin tool
processes only user-sourced candidates now."""

from __future__ import annotations

import hashlib
import io

import pytest
from PIL import Image

import labeling
import main as server_main

_seed_counter = 0


@pytest.fixture
def labdb(monkeypatch, tmp_path):
    # Unique per-test DB + image dir: labeling leaks its WAL connections, so a
    # shared path lets one test's rows survive into the next. tmp_path is fresh
    # per test and monkeypatch reverts the module globals afterwards.
    monkeypatch.setattr(labeling, "LABELING_DB_PATH", tmp_path / "labeling.db")
    monkeypatch.setattr(labeling, "LABELING_IMG_DIR", tmp_path / "images")
    labeling._initialised = False
    labeling.init_db()
    monkeypatch.setattr(labeling, "_admin_password", lambda: "s3cret")
    monkeypatch.setattr(server_main, "LABELING_ENABLED", True)
    labeling._login_attempts.clear()
    return labeling


def _unique_jpeg() -> bytes:
    # Distinct SOLID colour per seed: a 1-pixel tweak gets quantised away by
    # JPEG, collapsing to identical bytes (same sha → ignored insert).
    global _seed_counter
    _seed_counter += 1
    c = _seed_counter
    img = Image.new("RGB", (64, 64), (c * 37 % 256, c * 101 % 256, c * 53 % 256))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _seed(cls, source="user_scan", status="pending", scan_id=None,
          created_at=None, phash=None, sha=None, user_correct=None,
          feedback_note=None):
    body = _unique_jpeg()
    sha = sha or hashlib.sha256(body).hexdigest()
    fn = f"{sha}.jpg"
    labeling.LABELING_IMG_DIR.mkdir(parents=True, exist_ok=True)
    (labeling.LABELING_IMG_DIR / fn).write_bytes(body)
    rid = labeling.insert_candidate(
        cls=cls, image_path=fn, source=source, source_url=None,
        subject_plant=None, sha=sha, phash=phash, scan_id=scan_id,
        feedback_note=feedback_note,
    )
    with labeling._connect() as conn:
        if status != "pending":
            conn.execute("UPDATE candidates SET status=? WHERE id=?", (status, rid))
        if created_at is not None:
            conn.execute(
                "UPDATE candidates SET created_at=? WHERE id=?", (created_at, rid))
        if user_correct is not None:
            conn.execute(
                "UPDATE candidates SET user_correct=? WHERE id=?", (user_correct, rid))
    return rid


# ── claim priority + source filter ───────────────────────────────────
def test_default_priority_feedback_then_scans(labdb):
    scan = _seed("leaf_spot", source="user_scan", scan_id="s1", created_at=100)
    fb = _seed("leaf_spot", source="user_feedback", scan_id="s2", created_at=300)
    cards = labeling.claim_next("sess", 10)
    assert [c["id"] for c in cards] == [fb, scan]


def test_source_filter_restricts_group(labdb):
    _seed("leaf_spot", source="user_scan", scan_id="s1")
    fb = _seed("leaf_spot", source="user_feedback", scan_id="s2")
    cards = labeling.claim_next("sess", 10, source="feedback")
    assert [c["id"] for c in cards] == [fb]


def test_feedback_context_surfaces_on_card(labdb):
    fb = _seed("healthy", source="user_feedback", scan_id="s1",
               feedback_note="model said pests", user_correct=0)
    scan = _seed("healthy", source="user_scan", scan_id="s2")
    cards = labeling.claim_next("sess", 10)
    assert [c["id"] for c in cards] == [fb, scan]
    fb_card = cards[0]
    assert fb_card["scan_feedback"] is False
    assert fb_card["feedback_note"] == "model said pests"


def test_label_next_source_param_and_422(client, labdb, monkeypatch):
    _seed("leaf_spot", source="user_feedback", scan_id="s9")
    r = client.post("/api/label/login", json={"password": "s3cret"})
    tok = {"X-Label-Token": r.json()["token"]}
    ok = client.get("/api/label/next?source=feedback", headers=tok)
    assert ok.status_code == 200 and len(ok.json()["cards"]) == 1
    empty = client.get("/api/label/next?source=scans", headers=tok)
    assert empty.status_code == 200 and empty.json()["cards"] == []
    # 'dataset' is no longer a valid source group → 422.
    assert client.get("/api/label/next?source=dataset", headers=tok).status_code == 422
    assert client.get("/api/label/next?source=bogus", headers=tok).status_code == 422


def test_stats_sources_block(labdb):
    _seed("leaf_spot", source="user_feedback", scan_id="s1")
    _seed("leaf_spot", source="user_scan", scan_id="s2", status="auto_yes")
    st = labeling.stats({"leaf_spot": 600})
    assert st["sources"]["feedback"]["pending"] == 1
    assert st["sources"]["scans"]["resolved"] == 1
    assert st["feedback_pending"] == 1
    assert st["scan_pending"] == 0
    assert "dataset" not in st["sources"]
    assert "dataset_pending" not in st
