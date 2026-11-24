"""A complete, servable bundle on disk for tests (no real graphs).

``write_bundle`` writes every artefact ``bundle.verify_bundle`` requires, with
placeholder graph bytes, and returns the manifest it wrote. Tests that need a
broken bundle edit a file and call ``write_manifest`` (or ``record`` for one
artifact entry) to re-bind it.
"""

from __future__ import annotations

import hashlib
import json


def record(path):
    body = path.read_bytes()
    return {
        "path": path.name,
        "sha256": hashlib.sha256(body).hexdigest(),
        "size_bytes": len(body),
    }


def write_manifest(tmp_path, manifest):
    identity = dict(manifest)
    identity.pop("bundle_id", None)
    canonical = json.dumps(
        identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()
    manifest["bundle_id"] = hashlib.sha256(canonical).hexdigest()
    (tmp_path / "bundle_manifest.json").write_text(json.dumps(manifest))


def serving_contract():
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


def write_bundle(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
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
        role: record(tmp_path / name)
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
        "serving_contract": serving_contract(),
    }
    write_manifest(tmp_path, manifest)
    return manifest
