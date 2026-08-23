"""Checksummed immutable serving-bundle contract tests (no model execution)."""

from __future__ import annotations

import hashlib
import json

import pytest

from bundle import BundleLoadError, verify_bundle
from tools import verify_bundle as verify_bundle_cli


def _record(path):
    body = path.read_bytes()
    return {
        "path": path.name,
        "sha256": hashlib.sha256(body).hexdigest(),
        "size_bytes": len(body),
    }


def _write_manifest(tmp_path, manifest):
    identity = dict(manifest)
    identity.pop("bundle_id", None)
    canonical = json.dumps(
        identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()
    manifest["bundle_id"] = hashlib.sha256(canonical).hexdigest()
    (tmp_path / "bundle_manifest.json").write_text(json.dumps(manifest))


def _serving_contract():
    return {
        "schema_version": 1,
        "detector": {
            "path": "detector.onnx",
            "class_names": ["plant_leaf"],
            "img_size": 640,
            "min_confidence": 0.15,
            "nms_iou": 0.45,
            "max_detections": 10,
        },
        "crop": {
            "max_crops": 4,
            "min_size_px": 32,
            "min_area_fraction": 0.005,
            "padding_ratio": 0.10,
            "fold_whole_frame_vote": True,
            "member_selection": "detector_confidence_descending",
            "vote_aggregation": "equal_mean_probabilities",
            "whole_frame_vote_weight": 1.0,
        },
        "classifier": {
            "path": "classifier_int8.onnx",
            "img_size": 224,
            "eval_resize_ratio": 1.05,
            "resize": "shorter_side",
            "crop": "center",
            "normalize_mean": [0.485, 0.456, 0.406],
            "normalize_std": [0.229, 0.224, 0.225],
            "tta": "hflip",
            "tta_aggregation": "mean_logits",
            "output_semantics": "logits",
        },
        "blur": {
            "enabled": True,
            "policy": "abstain",
            "variance_laplacian_floor": 50.0,
            "downscale_max_side": 320,
        },
        "ood": {
            "policy": "detector_and_classifier_signal_with_nap_head_veto",
            "detector_confidence_floor": 0.40,
            "classifier_signal_priority": "embedding_knn_else_max_logit",
            "embedding_aggregation": "min_member_distance",
            "max_logit_aggregation": "max_member_logit",
            "nap_head_veto": "veto_when_probability_below_configured_floor",
            "served_gate_enabled": False,
            "served_gate": None,
            "config_source": "classes.json#ood",
        },
    }


def _bundle(tmp_path):
    (tmp_path / "classifier_int8.onnx").write_bytes(b"classifier")
    (tmp_path / "detector.onnx").write_bytes(b"detector")
    (tmp_path / "classes.json").write_text(json.dumps({
        "model_version": "9.0.0",
        "model_family": "gamma",
        "run_id": "9.0.0-gamma-test",
        "data_manifest_sha256": "d" * 64,
        "architecture": "test-backbone+head",
        "class_names": ["healthy", "leaf_spot"],
        "detector_classes": ["plant_leaf"],
        "training": {
            "architecture": "test-backbone+head",
            "img_size": 224,
            "eval_resize_ratio": 1.05,
        },
        "inference": {
            "layout": "NCHW",
            "normalize_mean": [0.485, 0.456, 0.406],
            "normalize_std": [0.229, 0.224, 0.225],
            "tta": "none",
            "output_semantics": "logits",
        },
        "ood": {
            "max_logit_floor": None,
            "max_logit_scope": "plant_only",
            "nap_head_mass_floor": 0.05,
            "embedding": None,
        },
    }))
    (tmp_path / "temperature.json").write_text(json.dumps({
        "temperature": 1.2, "source": "int8",
    }))
    (tmp_path / "conformal.json").write_text(json.dumps({
        "method": "raps", "alpha": 0.1, "q_hat": 0.8, "fit_source": "int8",
    }))
    dataset_sha = "d" * 64
    (tmp_path / "data_manifest.json").write_text(json.dumps({
        "schema_version": 1,
        "classification_sha256": dataset_sha,
        "n_images": 123,
    }))
    (tmp_path / "run_manifest.json").write_text(json.dumps({
        "schema_version": 1,
        "run_id": "9.0.0-gamma-test",
        "model_version": "9.0.0",
        "train_mode": "gamma",
        "model_family": "gamma",
        "data_identity": dataset_sha,
    }))
    artifacts = {
        role: _record(tmp_path / name)
        for role, name in {
            "classifier": "classifier_int8.onnx",
            "detector": "detector.onnx",
            "classes": "classes.json",
            "temperature": "temperature.json",
            "conformal": "conformal.json",
            "data_manifest": "data_manifest.json",
            "run_manifest": "run_manifest.json",
        }.items()
    }
    manifest = {
        "schema_version": 1,
        "bundle_id_algorithm": "sha256_canonical_json_without_bundle_id",
        "manifest_file": "bundle_manifest.json",
        "run_id": "9.0.0-gamma-test",
        "model_version": "9.0.0",
        "train_mode": "gamma",
        "model_family": "gamma",
        "data_manifest": {
            "path": "data_manifest.json",
            "manifest_sha256": artifacts["data_manifest"]["sha256"],
            "dataset_sha256": dataset_sha,
            "n_images": 123,
        },
        "classifier_selection": {
            "path": "classifier_int8.onnx",
            "fallback_allowed": False,
            "calibration_source": "int8",
        },
        "artifacts": artifacts,
        "serving_contract": _serving_contract(),
    }
    _write_manifest(tmp_path, manifest)
    return manifest


def test_verify_bundle_returns_only_manifest_selected_graphs(tmp_path):
    _bundle(tmp_path)
    verified = verify_bundle(tmp_path)
    assert verified.classifier_path.name == "classifier_int8.onnx"
    assert verified.detector_path.name == "detector.onnx"
    assert len(verified.manifest_sha256) == 64
    assert len(verified.serving_contract_sha256) == 64


def test_verify_bundle_rejects_tamper_and_stale_sibling(tmp_path):
    _bundle(tmp_path)
    (tmp_path / "classifier_int8.onnx").write_bytes(b"changed")
    with pytest.raises(BundleLoadError, match="size mismatch|SHA-256 mismatch"):
        verify_bundle(tmp_path)

    _bundle(tmp_path)
    (tmp_path / "classifier.onnx").write_bytes(b"stale fallback")
    with pytest.raises(BundleLoadError, match="unbound"):
        verify_bundle(tmp_path)


def test_verify_bundle_rejects_cross_run_calibration(tmp_path):
    manifest = _bundle(tmp_path)
    (tmp_path / "temperature.json").write_text(json.dumps({
        "temperature": 1.2, "source": "fp32_fallback",
    }))
    manifest["artifacts"]["temperature"] = _record(tmp_path / "temperature.json")
    _write_manifest(tmp_path, manifest)
    with pytest.raises(BundleLoadError, match="not fit"):
        verify_bundle(tmp_path)


def test_verify_bundle_rejects_missing_or_escaping_manifest(tmp_path):
    with pytest.raises(BundleLoadError, match="manifest is missing"):
        verify_bundle(tmp_path)

    manifest = _bundle(tmp_path)
    manifest["artifacts"]["classifier"]["path"] = "../classifier_int8.onnx"
    manifest["classifier_selection"]["path"] = "../classifier_int8.onnx"
    _write_manifest(tmp_path, manifest)
    with pytest.raises(BundleLoadError, match="unsafe path"):
        verify_bundle(tmp_path)


def test_verify_bundle_requires_explicit_optional_artifact_roles(tmp_path):
    manifest = _bundle(tmp_path)
    del manifest["artifacts"]["conformal"]
    _write_manifest(tmp_path, manifest)
    with pytest.raises(BundleLoadError, match="explicitly declare"):
        verify_bundle(tmp_path)


def test_verify_bundle_rejects_metadata_that_would_use_runtime_defaults(tmp_path):
    manifest = _bundle(tmp_path)
    classes_path = tmp_path / "classes.json"
    classes = json.loads(classes_path.read_text())
    del classes["inference"]["normalize_mean"]
    classes_path.write_text(json.dumps(classes))
    manifest["artifacts"]["classes"] = _record(classes_path)
    _write_manifest(tmp_path, manifest)
    with pytest.raises(BundleLoadError, match="normalize_mean"):
        verify_bundle(tmp_path)


def test_verify_bundle_cli_is_inference_free_preflight(tmp_path, capsys):
    manifest = _bundle(tmp_path)
    assert verify_bundle_cli.main([str(tmp_path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "valid"
    assert payload["bundle_id"] == manifest["bundle_id"]
    assert payload["classifier"] == "classifier_int8.onnx"

    (tmp_path / "classes.json").write_text("{}")
    assert verify_bundle_cli.main([str(tmp_path)]) == 1
    assert "INVALID:" in capsys.readouterr().err


def test_verify_bundle_rejects_stub_serving_contract(tmp_path):
    manifest = _bundle(tmp_path)
    manifest["serving_contract"] = {"preprocessing": {"output_semantics": "logits"}}
    _write_manifest(tmp_path, manifest)
    with pytest.raises(BundleLoadError, match="schema_version|missing sections"):
        verify_bundle(tmp_path)
