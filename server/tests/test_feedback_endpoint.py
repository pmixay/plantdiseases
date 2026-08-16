"""Coverage for the /api/feedback endpoint and feedback DB module.

Tests run against an isolated per-test SQLite file so writes from one
test never leak into the next. The endpoint surface and the DB module
are exercised separately:

  * Endpoint tests cover request shape, validation rules, and the
    response contract.
  * DB-module tests cover the metrics aggregation surface and the
    forward-only schema migration.
"""

from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture
def isolated_feedback_db(tmp_path, monkeypatch):
    """Point the feedback store at a temp DB per test so writes can't leak.

    Bypasses the issued-scan-id gate by default — most contract tests
    don't care about the gate and would otherwise need to register
    every scan_id they POST. The dedicated gate tests
    (``test_feedback_endpoint_rejects_unissued_scan_id`` and friends)
    undo this patch.
    """
    db_path = tmp_path / "feedback.db"
    monkeypatch.setenv("FEEDBACK_DB_PATH", str(db_path))
    monkeypatch.setenv("FEEDBACK_IP_PEPPER", "test-pepper")
    import importlib

    import feedback as fb
    importlib.reload(fb)
    # Re-bind the symbols main.py imported at module-load time so the
    # endpoint hits the freshly-reloaded module (which sees the temp DB).
    import main as server_main
    server_main.record_feedback = fb.record_feedback
    server_main.init_feedback_db = fb.init_db
    server_main.feedback_counts = fb.feedback_counts
    server_main.record_issued_scan_id = fb.record_issued_scan_id
    monkeypatch.setattr(server_main, "is_scan_id_issued", lambda *a, **k: True)
    yield db_path


# ── Happy-path contract ─────────────────────────────────────────────


def test_feedback_endpoint_accepts_minimal_payload(client, isolated_feedback_db):
    r = client.post(
        "/api/feedback",
        json={"scan_id": "scan-0001", "correct": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert isinstance(body["id"], int) and body["id"] > 0


def test_feedback_endpoint_accepts_full_metadata(client, isolated_feedback_db):
    r = client.post(
        "/api/feedback",
        json={
            "scan_id": "abcdef0123",
            "correct": False,
            "corrected_class": "powdery_mildew",
            "note": "white powder on the upper side of the leaf",
            "reason": "wrong_class",
            "predicted_class": "gray_mold_botrytis",
            "predicted_confidence": 0.42,
            "top3": [
                {"class": "gray_mold_botrytis", "prob": 0.42},
                {"class": "powdery_mildew", "prob": 0.31},
                {"class": "healthy", "prob": 0.18},
            ],
            "model_version": "2.1",
            "app_version": "2.1.0",
            "locale": "en",
            "platform": "android",
            "user_pseudo_id": "pseudo-abcdef12",
            "region_count": 3,
            "primary_region_index": 1,
            "feedback_latency_ms": 4_500,
            "image_hash": "a" * 64,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


# ── Validation guard-rails ──────────────────────────────────────────


def test_feedback_endpoint_rejects_oversize_note(client, isolated_feedback_db):
    r = client.post(
        "/api/feedback",
        json={"scan_id": "scan-0001", "correct": False, "note": "x" * 5000},
    )
    assert r.status_code == 422


def test_feedback_endpoint_requires_scan_id(client, isolated_feedback_db):
    r = client.post("/api/feedback", json={"correct": True})
    assert r.status_code == 422


def test_feedback_endpoint_rejects_short_scan_id(client, isolated_feedback_db):
    r = client.post("/api/feedback", json={"scan_id": "abc", "correct": True})
    assert r.status_code == 422


def test_feedback_endpoint_rejects_bad_scan_id_chars(client, isolated_feedback_db):
    r = client.post(
        "/api/feedback",
        json={"scan_id": "scan id with spaces", "correct": True},
    )
    assert r.status_code == 422


def test_feedback_endpoint_rejects_unknown_corrected_class(client, isolated_feedback_db):
    r = client.post(
        "/api/feedback",
        json={
            "scan_id": "scan-0001",
            "correct": False,
            "corrected_class": "alien_invasion",
        },
    )
    assert r.status_code == 422


def test_feedback_endpoint_accepts_deferred_class(client, isolated_feedback_db):
    # 'edema' is deferred until v2.x but should still be accepted as
    # a correction signal for the retraining loop.
    r = client.post(
        "/api/feedback",
        json={
            "scan_id": "scan-0001",
            "correct": False,
            "corrected_class": "edema",
        },
    )
    assert r.status_code == 200


def test_feedback_endpoint_tags_out_of_head_correction(client, isolated_feedback_db):
    """Out-of-head corrections (still-deferred physiological classes,
    ``other``) must be persisted with ``out_of_head=1`` so retraining
    can prioritise growing the label set. ``edema`` is one of the two
    classes still deferred after the v2.2 head expansion.
    """
    client.post(
        "/api/feedback",
        json={
            "scan_id": "scan-oohead1",
            "correct": False,
            "corrected_class": "edema",
        },
    )
    with sqlite3.connect(isolated_feedback_db) as conn:
        row = conn.execute(
            "SELECT corrected_class, out_of_head FROM feedback "
            "WHERE scan_id = ?",
            ("scan-oohead1",),
        ).fetchone()
    assert row == ("edema", 1)


def test_feedback_endpoint_marks_in_head_correction(client, isolated_feedback_db):
    """A correction whose class IS in the production head must be
    flagged ``out_of_head=0``. Distinct from the NULL we leave for
    rows without a correction at all.
    """
    client.post(
        "/api/feedback",
        json={
            "scan_id": "scan-inhead1",
            "correct": False,
            "corrected_class": "leaf_spot",
        },
    )
    with sqlite3.connect(isolated_feedback_db) as conn:
        row = conn.execute(
            "SELECT out_of_head FROM feedback WHERE scan_id = ?",
            ("scan-inhead1",),
        ).fetchone()
    assert row == (0,)


def test_feedback_image_upload_stores_bytes_and_links_row(
    client, isolated_feedback_db, tmp_path, monkeypatch,
):
    """``/api/feedback/image`` should:
      * land the bytes under FEEDBACK_IMG_DIR with the sha256 as
        filename
      * backfill ``image_path`` on the most recent matching feedback row
    """
    img_dir = tmp_path / "feedback_imgs"
    monkeypatch.setenv("FEEDBACK_IMG_DIR", str(img_dir))
    import importlib

    import feedback as fb
    importlib.reload(fb)
    import main as server_main
    server_main.save_feedback_image = fb.save_feedback_image
    server_main.feedback_attach_image_path = fb.attach_image_path

    # Drop the feedback row first so attach_image_path has something
    # to update.
    client.post(
        "/api/feedback",
        json={
            "scan_id": "scan-imgupload1",
            "correct": False,
            "corrected_class": "leaf_spot",
        },
    )
    img_bytes = b"\xff\xd8\xff\xe0fake-jpeg-bytes-for-test"
    r = client.post(
        "/api/feedback/image",
        data={"scan_id": "scan-imgupload1"},
        files={"image": ("plant.jpg", img_bytes, "image/jpeg")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["filename"].endswith(".jpg")
    # File on disk with the same bytes.
    stored = img_dir / body["filename"]
    assert stored.read_bytes() == img_bytes
    # Feedback row updated with image_path.
    with sqlite3.connect(isolated_feedback_db) as conn:
        (image_path,) = conn.execute(
            "SELECT image_path FROM feedback WHERE scan_id = ?",
            ("scan-imgupload1",),
        ).fetchone()
    assert image_path == body["filename"]


def test_feedback_image_rejects_non_image_bytes(
    client, isolated_feedback_db, tmp_path, monkeypatch,
):
    """Anything that is not a JPEG/PNG/WebP/BMP is refused.

    The extension used to come from the client's Content-Type with a `.bin`
    fallback, and nothing decoded the upload — so a valid scan_id was enough
    to write arbitrary bytes into the retraining corpus.
    """
    img_dir = tmp_path / "feedback_imgs_reject"
    monkeypatch.setenv("FEEDBACK_IMG_DIR", str(img_dir))
    import importlib

    import feedback as fb
    importlib.reload(fb)
    import main as server_main
    server_main.save_feedback_image = fb.save_feedback_image
    server_main.feedback_attach_image_path = fb.attach_image_path

    client.post(
        "/api/feedback",
        json={
            "scan_id": "scan-notimage1",
            "correct": False,
            "corrected_class": "leaf_spot",
        },
    )
    r = client.post(
        "/api/feedback/image",
        data={"scan_id": "scan-notimage1"},
        # Content-Type says JPEG; the bytes are a zip. The bytes decide.
        files={"image": ("payload.jpg", b"PK\x03\x04not-an-image", "image/jpeg")},
    )
    assert r.status_code == 400, r.text
    assert not list(img_dir.glob("*")) if img_dir.exists() else True


def test_feedback_image_is_one_per_row_but_retry_is_idempotent(
    client, isolated_feedback_db, tmp_path, monkeypatch,
):
    """A second, DIFFERENT image for the same scan_id is refused.

    The per-IP daily quota counts feedback ROWS, which this endpoint never
    creates — so without this bound one issued scan_id could store unlimited
    distinct images. Re-uploading the SAME bytes must still succeed, because
    clients retry after a timeout and the artefact is content-addressed.
    """
    img_dir = tmp_path / "feedback_imgs_once"
    monkeypatch.setenv("FEEDBACK_IMG_DIR", str(img_dir))
    import importlib

    import feedback as fb
    importlib.reload(fb)
    import main as server_main
    server_main.save_feedback_image = fb.save_feedback_image
    server_main.feedback_attach_image_path = fb.attach_image_path

    client.post(
        "/api/feedback",
        json={
            "scan_id": "scan-imgonce1",
            "correct": False,
            "corrected_class": "leaf_spot",
        },
    )
    first = b"\xff\xd8\xff\xe0first-image-bytes"
    r1 = client.post(
        "/api/feedback/image",
        data={"scan_id": "scan-imgonce1"},
        files={"image": ("a.jpg", first, "image/jpeg")},
    )
    assert r1.status_code == 200, r1.text

    # Same bytes again — an idempotent retry, still fine.
    r2 = client.post(
        "/api/feedback/image",
        data={"scan_id": "scan-imgonce1"},
        files={"image": ("a.jpg", first, "image/jpeg")},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["filename"] == r1.json()["filename"]

    # Different bytes — this is the disk-fill vector, and it is refused.
    r3 = client.post(
        "/api/feedback/image",
        data={"scan_id": "scan-imgonce1"},
        files={"image": ("b.jpg", b"\xff\xd8\xff\xe0second-image-bytes", "image/jpeg")},
    )
    assert r3.status_code == 409, r3.text
    assert len(list(img_dir.glob("*"))) == 1


def test_feedback_endpoint_persists_prediction_set(client, isolated_feedback_db):
    """The conformal set should land on the feedback row as compact
    JSON. Used by the offline retraining loop to answer
    "was the right answer inside the 90 % set?" — the diagnostic
    that separates calibration drift from point-estimate errors.
    """
    client.post(
        "/api/feedback",
        json={
            "scan_id": "scan-predset",
            "correct": False,
            "corrected_class": "leaf_spot",
            "prediction_set": [
                "healthy", "leaf_spot", "gray_mold_botrytis",
            ],
        },
    )
    with sqlite3.connect(isolated_feedback_db) as conn:
        (raw,) = conn.execute(
            "SELECT prediction_set_json FROM feedback WHERE scan_id = ?",
            ("scan-predset",),
        ).fetchone()
    import json
    assert json.loads(raw) == [
        "healthy", "leaf_spot", "gray_mold_botrytis",
    ]


def test_feedback_endpoint_rejects_unknown_reason(client, isolated_feedback_db):
    r = client.post(
        "/api/feedback",
        json={"scan_id": "scan-0001", "correct": False, "reason": "I felt like it"},
    )
    assert r.status_code == 422


def test_feedback_endpoint_normalises_unknown_platform(client, isolated_feedback_db):
    # An unknown platform tag should NOT 422 — we'd rather collect the
    # signal than bounce a client with a typo in BuildConfig.
    r = client.post(
        "/api/feedback",
        json={
            "scan_id": "scan-0001",
            "correct": True,
            "platform": "android-tablet",
        },
    )
    assert r.status_code == 200


def test_feedback_endpoint_rejects_oversize_top3(client, isolated_feedback_db):
    r = client.post(
        "/api/feedback",
        json={
            "scan_id": "scan-0001",
            "correct": True,
            "top3": [
                {"class": "a", "prob": 0.1},
                {"class": "b", "prob": 0.1},
                {"class": "c", "prob": 0.1},
                {"class": "d", "prob": 0.1},
            ],
        },
    )
    assert r.status_code == 422


def test_feedback_endpoint_rejects_non_hex_image_hash(client, isolated_feedback_db):
    r = client.post(
        "/api/feedback",
        json={
            "scan_id": "scan-0001",
            "correct": True,
            "image_hash": "z" * 64,
        },
    )
    assert r.status_code == 422


def test_feedback_endpoint_rejects_negative_latency(client, isolated_feedback_db):
    r = client.post(
        "/api/feedback",
        json={
            "scan_id": "scan-0001",
            "correct": True,
            "feedback_latency_ms": -1,
        },
    )
    assert r.status_code == 422


# ── Privacy: raw IP never lands on disk ─────────────────────────────


def test_feedback_endpoint_hashes_client_ip(client, isolated_feedback_db):
    client.post("/api/feedback", json={"scan_id": "scan-0001", "correct": True})
    with sqlite3.connect(str(isolated_feedback_db)) as conn:
        row = conn.execute(
            "SELECT client_ip, client_ip_hash FROM feedback ORDER BY id DESC LIMIT 1"
        ).fetchone()
    raw_ip, ip_hash = row
    assert raw_ip is None, "raw client IP must not be persisted"
    assert ip_hash and len(ip_hash) == 16, "hashed IP must be a short opaque token"


# ── Aggregation surface ─────────────────────────────────────────────


def test_feedback_endpoint_persists_and_aggregates(client, isolated_feedback_db):
    import feedback as fb

    client.post("/api/feedback", json={"scan_id": "scan-0001", "correct": True})
    client.post(
        "/api/feedback",
        json={
            "scan_id": "scan-0002",
            "correct": False,
            "corrected_class": "powdery_mildew",
            "note": "Looks like white powder, not gray mold.",
            "predicted_class": "gray_mold_botrytis",
            "reason": "wrong_class",
        },
    )
    client.post("/api/feedback", json={"scan_id": "scan-0003", "correct": False})

    counts = fb.feedback_counts()
    assert counts["total"] == 3
    assert counts["correct"] == 1
    assert counts["incorrect"] == 2
    assert counts["last_24h"] == 3
    assert counts["last_7d"] == 3
    # per-class accuracy is only computed when predicted_class is sent.
    assert "gray_mold_botrytis" in counts["per_class"]
    assert counts["per_class"]["gray_mold_botrytis"]["total"] == 1
    assert counts["per_class"]["gray_mold_botrytis"]["accuracy"] == 0.0
    assert counts["top_corrections"]["gray_mold_botrytis"]["powdery_mildew"] == 1
    assert counts["reasons"]["wrong_class"] == 1


def test_feedback_counts_surface_on_metrics(client, isolated_feedback_db):
    client.post("/api/feedback", json={"scan_id": "scan-0001", "correct": True})
    m = client.get("/api/metrics").json()
    assert "feedback" in m
    assert m["feedback"]["total"] >= 1
    assert "per_class" in m["feedback"]
    assert "top_corrections" in m["feedback"]


def test_metrics_aggregation_handles_empty_db(client, isolated_feedback_db):
    import feedback as fb

    counts = fb.feedback_counts()
    assert counts["total"] == 0
    assert counts["per_class"] == {}
    assert counts["top_corrections"] == {}
    assert counts["reasons"] == {}
    assert counts["avg_latency_ms"] is None


# ── Issued-scan-id gate ─────────────────────────────────────────────


def test_feedback_endpoint_rejects_unissued_scan_id(client, isolated_feedback_db, monkeypatch):
    # Undo the fixture's permissive patch so the real gate runs.
    import feedback as fb
    import main as server_main
    monkeypatch.setattr(server_main, "is_scan_id_issued", fb.is_scan_id_issued)

    r = client.post(
        "/api/feedback",
        json={"scan_id": "definitelynevermintedabcdef0123", "correct": False,
              "corrected_class": "healthy"},
    )
    assert r.status_code == 422, r.text
    assert "scan_id" in r.json()["detail"].lower()


def test_feedback_endpoint_accepts_issued_scan_id(client, isolated_feedback_db, monkeypatch):
    import feedback as fb
    import main as server_main
    monkeypatch.setattr(server_main, "is_scan_id_issued", fb.is_scan_id_issued)
    fb.record_issued_scan_id("scan-issued-001")

    r = client.post(
        "/api/feedback",
        json={
            "scan_id": "scan-issued-001",
            "correct": False,
            "corrected_class": "powdery_mildew",
            "image_hash": "b" * 64,
        },
    )
    assert r.status_code == 200, r.text
    with sqlite3.connect(str(isolated_feedback_db)) as conn:
        row = conn.execute(
            "SELECT scan_id, image_hash FROM feedback ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row == ("scan-issued-001", "b" * 64)


