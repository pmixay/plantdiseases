"""Receipt-only feedback endpoint and privacy-screened image coverage."""

from __future__ import annotations

import sqlite3

import pytest

import main as server_main
import training_store


@pytest.fixture
def isolated_training_store(tmp_path, monkeypatch):
    monkeypatch.setattr(training_store, "TRAINING_DB_PATH", tmp_path / "training.db")
    monkeypatch.setattr(training_store, "TRAINING_IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr(training_store, "_initialised", False)
    training_store.init_db()
    server_main._daily_write_counts.clear()
    return training_store


def _sample(store, receipt: str) -> str:
    from PIL import Image

    image = store.sanitize_training_image(Image.new("RGB", (160, 120), "green"))
    sample_id, _created = store.store_sample(
        image,
        source="consented_scan",
        model_family="gamma",
        model_version="2.6.9",
        predicted_class="healthy",
        predicted_confidence=0.91,
        top_k=[{"class": "healthy", "confidence": 0.91}],
        conformal_set=["healthy"],
        warnings=[],
        blur_bucket="sharp",
        detector_region_count=1,
        bbox_area_buckets=["large"],
        severity="low",
        review_status="auto_yes",
        consent_version="model-improvement-v1",
        receipt=receipt,
    )
    return sample_id


def test_feedback_accepts_receipt_without_a_retained_sample(
    client, isolated_training_store
):
    receipt = isolated_training_store.issue_feedback_receipt(capture_pending=False)
    response = client.post(
        "/api/feedback",
        json={"feedback_receipt": receipt, "correct": True},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True, "updated": False}

    replay = client.post(
        "/api/feedback",
        json={"feedback_receipt": receipt, "correct": True},
    )
    assert replay.status_code == 409
    assert replay.json()["detail"] == "feedback_receipt_used"


def test_feedback_updates_matching_sample_with_structured_correction(
    client, isolated_training_store
):
    receipt = isolated_training_store.issue_feedback_receipt(capture_pending=True)
    sample_id = _sample(isolated_training_store, receipt)
    response = client.post(
        "/api/feedback",
        json={
            "feedback_receipt": receipt,
            "correct": False,
            "corrected_class": "edema",
            "reason": "wrong_class",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True, "updated": True}
    with sqlite3.connect(isolated_training_store.TRAINING_DB_PATH) as connection:
        row = connection.execute(
            "SELECT user_correct, corrected_class, correction_reason, out_of_head, "
            "review_status, label_provenance FROM training_samples WHERE sample_id = ?",
            (sample_id,),
        ).fetchone()
    assert row == (0, "edema", "wrong_class", 1, "pending", "model+user")


@pytest.mark.parametrize(
    "extra",
    [
        {"scan_id": "legacy-scan"},
        {"user_pseudo_id": "legacy-install"},
        {"note": "free text must not enter the store"},
        {"predicted_class": "healthy"},
        {"image_hash": "a" * 64},
        {"platform": "android"},
    ],
)
def test_feedback_rejects_legacy_linkage_and_free_text_fields(
    client, isolated_training_store, extra
):
    receipt = isolated_training_store.issue_feedback_receipt(capture_pending=False)
    response = client.post(
        "/api/feedback",
        json={"feedback_receipt": receipt, "correct": False, **extra},
    )
    assert response.status_code == 422
    assert any(item["type"] == "extra_forbidden" for item in response.json()["detail"])


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"corrected_class": "made_up_disease"}, "corrected_class"),
        ({"reason": "long_unstructured_story"}, "reason"),
    ],
)
def test_feedback_rejects_unknown_structured_values(
    client, isolated_training_store, payload, field
):
    receipt = isolated_training_store.issue_feedback_receipt(capture_pending=False)
    response = client.post(
        "/api/feedback",
        json={"feedback_receipt": receipt, "correct": False, **payload},
    )
    assert response.status_code == 422
    assert any(item["loc"][-1] == field for item in response.json()["detail"])


def test_feedback_rejects_correction_on_positive_signal(
    client, isolated_training_store
):
    receipt = isolated_training_store.issue_feedback_receipt(capture_pending=False)
    response = client.post(
        "/api/feedback",
        json={
            "feedback_receipt": receipt,
            "correct": True,
            "corrected_class": "leaf_spot",
        },
    )
    assert response.status_code == 422


def test_feedback_pending_capture_fails_closed(client, isolated_training_store):
    receipt = isolated_training_store.issue_feedback_receipt(capture_pending=True)
    response = client.post(
        "/api/feedback",
        json={"feedback_receipt": receipt, "correct": False},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "feedback_not_ready"


def test_feedback_image_rejects_full_frame_without_safe_crop(
    client, isolated_training_store, png_bytes, monkeypatch
):
    receipt = isolated_training_store.issue_feedback_receipt(capture_pending=False)
    monkeypatch.setattr(
        server_main.pipeline.detector,
        "detect",
        lambda _image: {"detections": [], "confidence": 0.0},
    )
    response = client.post(
        "/api/feedback/image",
        data={
            "feedback_receipt": receipt,
            "correct": "false",
            "corrected_class": "leaf_spot",
            "reason": "wrong_class",
        },
        files={"image": ("private-original-name.png", png_bytes, "image/png")},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "unsafe_training_image"
    assert isolated_training_store.training_counts()["total"] == 0
    assert list(isolated_training_store.TRAINING_IMAGE_DIR.iterdir()) == []


def test_feedback_image_stores_only_sanitized_detector_crop(
    client, isolated_training_store, png_bytes, monkeypatch
):
    receipt = isolated_training_store.issue_feedback_receipt(capture_pending=False)
    monkeypatch.setattr(
        server_main.pipeline.detector,
        "detect",
        lambda _image: {
            "detections": [
                {"x": 4, "y": 5, "width": 52, "height": 48, "confidence": 0.9}
            ],
            "confidence": 0.9,
        },
    )
    response = client.post(
        "/api/feedback/image",
        data={
            "feedback_receipt": receipt,
            "correct": "false",
            "corrected_class": "leaf_spot",
            "reason": "wrong_class",
        },
        files={"image": ("SENTINEL-ORIGINAL-PATH.png", png_bytes, "image/png")},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True, "updated": True, "stored_image": True}
    with sqlite3.connect(isolated_training_store.TRAINING_DB_PATH) as connection:
        row = connection.execute(
            "SELECT image_key, source, predicted_class, corrected_class, "
            "label_provenance FROM training_samples"
        ).fetchone()
        database_bytes = isolated_training_store.TRAINING_DB_PATH.read_bytes()
    assert row[1:] == (
        "feedback_upload",
        None,
        "leaf_spot",
        "model+user",
    )
    stored = (isolated_training_store.TRAINING_IMAGE_DIR / row[0]).read_bytes()
    assert isolated_training_store.metadata_is_absent(stored)
    assert b"SENTINEL-ORIGINAL-PATH" not in database_bytes


def test_feedback_daily_quota_is_memory_only(
    client, isolated_training_store, monkeypatch
):
    monkeypatch.setattr(server_main, "FEEDBACK_DAILY_QUOTA", 1)
    server_main._daily_write_counts.clear()
    first = isolated_training_store.issue_feedback_receipt(capture_pending=False)
    second = isolated_training_store.issue_feedback_receipt(capture_pending=False)
    assert client.post(
        "/api/feedback", json={"feedback_receipt": first, "correct": True}
    ).status_code == 200
    response = client.post(
        "/api/feedback", json={"feedback_receipt": second, "correct": True}
    )
    assert response.status_code == 429
    schema = isolated_training_store.schema_sql().lower()
    assert "client_ip" not in schema


def test_metrics_surface_deidentified_training_inventory(
    client, isolated_training_store
):
    receipt = isolated_training_store.issue_feedback_receipt(capture_pending=True)
    _sample(isolated_training_store, receipt)
    metrics = client.get("/api/metrics").json()
    assert metrics["training"]["total"] == 1
    assert "feedback" not in metrics
