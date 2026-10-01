"""Consent-gated automatic training capture coverage."""

from __future__ import annotations

import copy

import pytest
from PIL import Image

import main as server_main
import training_store


@pytest.fixture
def capture_store(tmp_path, monkeypatch):
    monkeypatch.setattr(training_store, "TRAINING_DB_PATH", tmp_path / "training.db")
    monkeypatch.setattr(training_store, "TRAINING_IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr(training_store, "_initialised", False)
    monkeypatch.setattr(server_main, "TRAINING_CAPTURE_ENABLED", True)
    monkeypatch.setattr(server_main, "TRAINING_HEALTHY_KEEP_RATE", 1.0)
    monkeypatch.setattr(server_main, "TRAINING_DAILY_CAP_PER_CLASS", 50)
    training_store.init_db()
    with server_main._analyze_cache_lock:
        server_main._analyze_cache.clear()
    return training_store


def _result(
    class_name: str = "leaf_spot",
    *,
    confidence: float = 0.72,
    regions: list[dict] | None = None,
    prediction_set: list[str] | None = None,
) -> dict:
    regions = regions if regions is not None else [
        {"x": 10, "y": 8, "width": 80, "height": 70, "confidence": 0.9}
    ]
    return {
        "class_name": class_name,
        "confidence": confidence,
        "all_probs": {class_name: confidence, "healthy": 1.0 - confidence},
        "top_k": [
            {"class": class_name, "confidence": confidence},
            {"class": "healthy", "confidence": 1.0 - confidence},
        ],
        "warnings": ["uncertain_prediction"] if confidence < 0.85 else [],
        "severity": "medium" if class_name != "healthy" else "low",
        "prediction_set": {
            "classes": prediction_set or [class_name, "healthy"],
            "coverage": 0.9,
        },
        "detection": {
            "is_diseased": class_name != "healthy",
            "detector_confidence": 0.9,
            "regions": regions,
            "primary_region": regions[0] if regions else None,
        },
        "elapsed_ms": 1.0,
    }


def _capture(store, result: dict, *, colour="green") -> str:
    receipt = store.issue_feedback_receipt(capture_pending=True)
    server_main._record_training_candidate(
        Image.new("RGB", (120, 100), colour),
        receipt,
        family="gamma",
        model_version="2.6.9",
        result=result,
        consent_version="model-improvement-v1",
    )
    return receipt


def test_disease_and_uncertain_samples_are_retained(capture_store, monkeypatch):
    monkeypatch.setattr(server_main, "TRAINING_HEALTHY_KEEP_RATE", 0.0)
    disease_receipt = _capture(capture_store, _result("leaf_spot"))
    uncertain_receipt = _capture(
        capture_store,
        _result("healthy", confidence=0.51, prediction_set=["healthy", "leaf_spot"]),
        colour="yellow",
    )
    assert capture_store.sample_by_receipt(disease_receipt)["predicted_class"] == "leaf_spot"
    assert capture_store.sample_by_receipt(uncertain_receipt)["review_status"] == "pending"


def test_confident_healthy_sampling_is_deterministic(capture_store, monkeypatch):
    monkeypatch.setattr(server_main, "TRAINING_HEALTHY_KEEP_RATE", 0.0)
    dropped = _capture(
        capture_store,
        _result("healthy", confidence=0.95, prediction_set=["healthy"]),
    )
    assert capture_store.sample_by_receipt(dropped) is None
    assert capture_store.deterministically_sample("00000001" + "0" * 56, 0.1)
    assert not capture_store.deterministically_sample("ffffffff" + "0" * 56, 0.1)


@pytest.mark.parametrize(
    "result",
    [
        _result("not_a_plant"),
        _result("leaf_spot", regions=[]),
    ],
)
def test_not_a_plant_and_missing_crop_retain_nothing(capture_store, result):
    receipt = _capture(capture_store, copy.deepcopy(result))
    assert capture_store.sample_by_receipt(receipt) is None
    assert capture_store.apply_feedback(receipt, correct=False).updated is False
    assert capture_store.training_counts()["total"] == 0


def test_daily_class_cap_is_enforced(capture_store, monkeypatch):
    monkeypatch.setattr(server_main, "TRAINING_DAILY_CAP_PER_CLASS", 1)
    first = _capture(capture_store, _result("leaf_spot"), colour="green")
    second = _capture(capture_store, _result("leaf_spot"), colour="red")
    assert capture_store.sample_by_receipt(first) is not None
    assert capture_store.sample_by_receipt(second) is None
    assert capture_store.training_counts()["total"] == 1


def _ood_rejected_result() -> dict:
    """Raw disease argmax rejected by the secondary OOD verdict."""
    result = _result("leaf_spot", confidence=0.7)
    result["warnings"] = ["nap_ood_gated"]
    return result


def test_ood_abstained_argmax_is_not_ingested_as_training_label(capture_store):
    receipt = _capture(capture_store, _ood_rejected_result())
    assert capture_store.sample_by_receipt(receipt) is None
    assert capture_store.training_counts()["total"] == 0


def test_analyze_ood_abstention_skips_capture_and_binds_hidden_community_claim(
    client, capture_store, png_bytes, monkeypatch
):
    claims: list[str] = []

    def _record_token(_token, **kwargs):
        claims.append(kwargs["disease"])

    monkeypatch.setattr(server_main, "community_record_analysis_token", _record_token)
    monkeypatch.setattr(
        server_main.pipeline, "analyze", lambda _image: _ood_rejected_result()
    )
    path = (
        "/api/analyze?training_consent=true&"
        f"training_consent_version={server_main.TRAINING_CONSENT_VERSION}"
    )
    first = client.post(path, files={"image": ("scan.png", png_bytes, "image/png")})
    second = client.post(path, files={"image": ("scan.png", png_bytes, "image/png")})
    assert first.status_code == second.status_code == 200
    body = first.json()
    assert body["class_name"] == "leaf_spot"  # diagnostic remains available
    assert body["abstained"] is True
    assert body["abstention_reason"] == "ood"
    assert second.json()["is_repeat"] is True
    assert capture_store.training_counts()["total"] == 0
    # Neither the fresh nor the cached response can publish the raw argmax.
    assert claims == ["not_a_plant", "not_a_plant"]


def test_analyze_without_training_consent_writes_no_sample(
    client, capture_store, png_bytes, monkeypatch
):
    monkeypatch.setattr(server_main.pipeline, "analyze", lambda _image: _result())
    response = client.post(
        "/api/analyze", files={"image": ("scan.png", png_bytes, "image/png")}
    )
    assert response.status_code == 200, response.text
    assert capture_store.training_counts()["total"] == 0
    assert capture_store.sample_by_receipt(response.json()["feedback_receipt"]) is None


def test_analyze_requires_current_consent_disclosure(
    client, capture_store, png_bytes
):
    response = client.post(
        "/api/analyze?training_consent=true&training_consent_version=stale-v0",
        files={"image": ("scan.png", png_bytes, "image/png")},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "training_consent_version_invalid"
    assert capture_store.training_counts()["total"] == 0


def test_analyze_with_consent_stores_crop_and_cache_issues_fresh_receipt(
    client, capture_store, png_bytes, monkeypatch
):
    monkeypatch.setattr(server_main.pipeline, "analyze", lambda _image: _result())
    path = (
        "/api/analyze?training_consent=true&"
        f"training_consent_version={server_main.TRAINING_CONSENT_VERSION}"
    )
    first = client.post(path, files={"image": ("scan.png", png_bytes, "image/png")})
    second = client.post(path, files={"image": ("scan.png", png_bytes, "image/png")})
    assert first.status_code == second.status_code == 200
    assert first.json()["feedback_receipt"] != second.json()["feedback_receipt"]
    assert first.json()["scan_id"] != second.json()["scan_id"]
    assert second.json()["is_repeat"] is True
    assert capture_store.training_counts()["total"] == 1
    assert capture_store.sample_by_receipt(first.json()["feedback_receipt"]) is not None
    assert capture_store.sample_by_receipt(second.json()["feedback_receipt"]) is not None
