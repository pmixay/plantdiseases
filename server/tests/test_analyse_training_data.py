"""Read-only training inventory, calibration, and overconfidence analysis."""

from __future__ import annotations

import hashlib

import pytest
from PIL import Image

import training_store
from tools import analyse_training_data


@pytest.fixture
def analysis_db(tmp_path, monkeypatch):
    monkeypatch.setattr(training_store, "TRAINING_DB_PATH", tmp_path / "training.db")
    monkeypatch.setattr(training_store, "TRAINING_IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr(training_store, "_initialised", False)
    training_store.init_db()
    return training_store


def _row(
    store,
    colour,
    *,
    predicted,
    confidence,
    correct,
    corrected=None,
    out_of_head=None,
    top_k=None,
    conformal=None,
):
    image = store.sanitize_training_image(Image.new("RGB", (100, 100), colour))
    store.store_sample(
        image,
        source="consented_scan",
        model_family="gamma",
        model_version="2.6.9",
        predicted_class=predicted,
        predicted_confidence=confidence,
        top_k=top_k or [{"class": predicted, "confidence": confidence}],
        conformal_set=conformal or [predicted],
        warnings=[],
        blur_bucket="sharp",
        detector_region_count=1,
        bbox_area_buckets=["large"],
        severity="medium",
        review_status="pending" if not correct else "auto_yes",
        consent_version="model-improvement-v1",
        user_correct=correct,
        corrected_class=corrected,
        correction_reason="wrong_class" if not correct else None,
        out_of_head=out_of_head,
        label_provenance="model+user",
    )


def test_analysis_reports_calibration_coverage_and_overconfidence(analysis_db):
    _row(
        analysis_db,
        "green",
        predicted="leaf_spot",
        confidence=0.9,
        correct=True,
    )
    _row(
        analysis_db,
        "yellow",
        predicted="healthy",
        confidence=0.95,
        correct=False,
        corrected="leaf_spot",
        top_k=[{"class": "healthy", "confidence": 0.95}],
        conformal=["healthy"],
    )
    _row(
        analysis_db,
        "red",
        predicted="leaf_spot",
        confidence=0.6,
        correct=False,
        corrected="edema",
        out_of_head=True,
        top_k=[
            {"class": "leaf_spot", "confidence": 0.6},
            {"class": "edema", "confidence": 0.3},
        ],
        conformal=["leaf_spot", "edema"],
    )
    before = hashlib.sha256(analysis_db.TRAINING_DB_PATH.read_bytes()).hexdigest()

    report = analyse_training_data.analyse(analysis_db.TRAINING_DB_PATH)

    assert report["inventory"]["total"] == 3
    assert report["inventory"]["by_source"] == {"consented_scan": 3}
    assert report["feedback_quality"]["labelled_samples"] == 3
    assert report["feedback_quality"]["per_predicted_class"]["leaf_spot"] == {
        "count": 2,
        "accuracy": 0.5,
    }
    assert report["feedback_quality"]["top_k"] == {
        "count": 3,
        "coverage": pytest.approx(2 / 3, abs=1e-6),
    }
    assert report["feedback_quality"]["conformal"] == {
        "count": 3,
        "empirical_coverage": pytest.approx(2 / 3, abs=1e-6),
    }
    assert report["feedback_quality"]["out_of_head"] == {"edema": 1}
    assert report["overconfident_errors"] == [
        {
            "predicted_class": "healthy",
            "corrected_class": "leaf_spot",
            "count": 1,
        }
    ]
    assert report["calibration"]["expected_calibration_error"] > 0
    after = hashlib.sha256(analysis_db.TRAINING_DB_PATH.read_bytes()).hexdigest()
    assert after == before


def test_analysis_refuses_missing_database(tmp_path):
    with pytest.raises(
        analyse_training_data.AnalysisError, match="database does not exist"
    ):
        analyse_training_data.analyse(tmp_path / "missing.db")
