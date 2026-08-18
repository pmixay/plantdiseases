from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path

import pytest
from PIL import Image

import training_store


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(training_store, "TRAINING_DB_PATH", tmp_path / "training.db")
    monkeypatch.setattr(training_store, "TRAINING_IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr(training_store, "_initialised", False)
    training_store.init_db()
    return training_store


def _image(colour=(32, 160, 64), *, size=(180, 140)) -> Image.Image:
    return Image.new("RGB", size, colour)


def _sample(store, receipt: str | None, *, image=None, **overrides):
    sanitized = store.sanitize_training_image(image or _image())
    values = {
        "source": "consented_scan",
        "model_family": "gamma",
        "model_version": "2.6.9",
        "predicted_class": "leaf_spot",
        "predicted_confidence": 0.72,
        "top_k": [{"class": "leaf_spot", "confidence": 0.72}],
        "conformal_set": ["leaf_spot", "viral_disease"],
        "warnings": ["uncertain_prediction"],
        "blur_bucket": "sharp",
        "detector_region_count": 2,
        "bbox_area_buckets": ["medium", "small"],
        "severity": "medium",
        "review_status": "pending",
        "consent_version": "model-improvement-v1",
        "receipt": receipt,
    }
    values.update(overrides)
    return store.store_sample(sanitized, **values)


def test_schema_contains_no_legacy_linkage_fields(store):
    schema = store.schema_sql().lower()
    forbidden = {
        "user_pseudo_id",
        "account_id",
        "community_actor_id",
        "installation_id",
        "client_ip",
        "scan_id",
        "device_model",
        "manufacturer",
        "source_url",
        "original_filename",
        "claimed_by_session",
        "claimed_at",
        "feedback_note",
    }
    assert all(value not in schema for value in forbidden)
    with sqlite3.connect(store.TRAINING_DB_PATH) as connection:
        columns = {
            row[1]
            for table in ("training_samples", "feedback_receipts")
            for row in connection.execute(f"PRAGMA table_info({table})")
        }
    assert forbidden.isdisjoint(columns)


def test_sanitizer_applies_orientation_and_removes_metadata(store):
    source = Image.new("RGB", (80, 120), (20, 80, 140))
    exif = Image.Exif()
    exif[274] = 6
    exif[271] = "SENTINEL-CAMERA-MAKE"
    exif[272] = "SENTINEL-CAMERA-MODEL"
    exif[306] = "2035:01:02 03:04:05"
    raw = io.BytesIO()
    source.save(
        raw,
        format="JPEG",
        exif=exif,
        icc_profile=b"SENTINEL-ICC-PROFILE",
        comment=b"SENTINEL-XMP-IPTC",
    )

    sanitized = store.sanitize_training_image(raw.getvalue())

    assert (sanitized.width, sanitized.height) == (120, 80)
    assert store.metadata_is_absent(sanitized.body)
    for sentinel in (
        b"SENTINEL-CAMERA-MAKE",
        b"SENTINEL-CAMERA-MODEL",
        b"SENTINEL-ICC-PROFILE",
        b"SENTINEL-XMP-IPTC",
    ):
        assert sentinel not in sanitized.body


def test_safe_crop_never_falls_back_to_a_full_frame(store):
    image = _image(size=(200, 120))
    assert store.extract_safe_leaf_crop(image, {"regions": []}) is None
    crop = store.extract_safe_leaf_crop(
        image,
        {
            "regions": [
                {"x": 20, "y": 10, "width": 80, "height": 60},
                {"x": 120, "y": 20, "width": 40, "height": 40},
            ]
        },
    )
    assert crop is not None
    pixels, buckets = crop
    assert pixels.size == (80, 60)
    assert buckets == ["medium", "small"]


def test_receipt_associates_anonymous_sample_and_structured_feedback(store):
    receipt = store.issue_feedback_receipt(capture_pending=True)
    sample_id, created = _sample(store, receipt)
    assert created is True
    assert sample_id not in receipt

    positive = store.apply_feedback(receipt, correct=True)
    assert positive.updated is True
    row = store.sample_by_receipt(receipt)
    assert row is not None
    assert row["user_correct"] == 1
    assert row["review_status"] == "auto_yes"
    assert row["label_provenance"] == "model+user"

    with pytest.raises(training_store.TrainingStoreError, match="feedback_receipt_used"):
        store.apply_feedback(receipt, correct=False)

    correction_receipt = store.issue_feedback_receipt(capture_pending=True)
    duplicate_id, created = _sample(store, correction_receipt)
    assert created is False
    assert duplicate_id == sample_id
    negative = store.apply_feedback(
        correction_receipt,
        correct=False,
        corrected_class="edema",
        correction_reason="wrong_class",
        out_of_head=True,
    )
    assert negative.updated is True
    row = store.sample_by_receipt(correction_receipt)
    assert row is not None
    assert row["user_correct"] == 0
    assert row["corrected_class"] == "edema"
    assert row["correction_reason"] == "wrong_class"
    assert row["out_of_head"] == 1
    assert row["review_status"] == "pending"


def test_receipt_pending_expiry_and_consumption_fail_closed(store):
    pending = store.issue_feedback_receipt(capture_pending=True, now=2_000)
    with pytest.raises(training_store.TrainingStoreError, match="feedback_not_ready"):
        store.apply_feedback(pending, correct=False, now=2_001)
    store.complete_receipt_without_sample(pending, now=2_002)
    assert store.apply_feedback(pending, correct=False, now=2_003).updated is False

    expired = store.issue_feedback_receipt(capture_pending=False, now=3_000)
    with pytest.raises(training_store.TrainingStoreError, match="feedback_receipt_invalid"):
        store.apply_feedback(
            expired,
            correct=True,
            now=3_000 + store.RECEIPT_TTL_SECONDS,
        )

    upload_receipt = store.issue_feedback_receipt(capture_pending=False, now=4_000)
    result = store.store_feedback_upload(
        upload_receipt,
        store.sanitize_training_image(_image((170, 30, 20))),
        correct=False,
        corrected_class="sunburn_or_scorch",
        correction_reason="wrong_class",
        out_of_head=True,
        consent_version="model-improvement-v1",
        now=4_001,
    )
    assert result == store.FeedbackResult(updated=True, stored_image=True)
    with pytest.raises(training_store.TrainingStoreError, match="feedback_receipt_used"):
        store.apply_feedback(upload_receipt, correct=False, now=4_002)


def test_post_sanitization_sha_is_idempotent(store):
    first_receipt = store.issue_feedback_receipt(capture_pending=True)
    second_receipt = store.issue_feedback_receipt(capture_pending=True)
    first_id, first_created = _sample(store, first_receipt)
    second_id, second_created = _sample(store, second_receipt)
    assert first_created is True
    assert second_created is False
    assert second_id == first_id
    assert store.training_counts() == {
        "total": 1,
        "by_source": {"consented_scan": 1},
        "by_review_status": {"pending": 1},
    }
    with sqlite3.connect(store.TRAINING_DB_PATH) as connection:
        row = connection.execute(
            "SELECT top_k_json, conformal_set_json, warnings_json "
            "FROM training_samples"
        ).fetchone()
    assert json.loads(row[0]) == [{"class": "leaf_spot", "score": 0.72}]
    assert json.loads(row[1]) == ["leaf_spot", "viral_disease"]
    assert json.loads(row[2]) == ["uncertain_prediction"]


def test_prune_removes_old_low_value_rows_but_keeps_negative_feedback(store):
    positive_id, _ = _sample(
        store,
        None,
        image=_image((10, 120, 40)),
        created_day="2020-01-01",
        user_correct=True,
        review_status="auto_yes",
        label_provenance="model+user",
    )
    negative_id, _ = _sample(
        store,
        None,
        image=_image((150, 40, 20)),
        created_day="2020-01-01",
        user_correct=False,
        corrected_class="edema",
        review_status="pending",
        label_provenance="model+user",
    )
    result = store.prune_training_data(90, 100, now=1_800_000_000)
    assert result["samples"] == result["images"] == 1
    with sqlite3.connect(store.TRAINING_DB_PATH) as connection:
        ids = {row[0] for row in connection.execute("SELECT sample_id FROM training_samples")}
    assert positive_id not in ids
    assert negative_id in ids


def test_prune_retains_row_when_image_deletion_fails(store, monkeypatch):
    sample_id, _ = _sample(
        store,
        None,
        created_day="2020-01-01",
        user_correct=True,
        review_status="auto_yes",
        label_provenance="model+user",
    )
    original_unlink = Path.unlink

    def refuse_jpeg(path, *args, **kwargs):
        if path.suffix == ".jpg":
            raise OSError("synthetic unlink failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refuse_jpeg)
    result = store.prune_training_data(90, 100, now=1_800_000_000)
    assert result["samples"] == result["images"] == 0
    with sqlite3.connect(store.TRAINING_DB_PATH) as connection:
        row = connection.execute(
            "SELECT image_key, prune_status FROM training_samples WHERE sample_id = ?",
            (sample_id,),
        ).fetchone()
    assert row is not None and row[1] == "retained"
    assert (store.TRAINING_IMAGE_DIR / row[0]).is_file()
