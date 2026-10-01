"""Manifest claims must equal the live pipeline's serving behavior."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

import pipeline
from bundle import BundleLoadError


def _contract():
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
            "fold_whole_frame_vote": pipeline.FOLD_WHOLE_FRAME_VOTE,
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
            "variance_laplacian_floor": pipeline.BLUR_LAPLACIAN_FLOOR,
            "downscale_max_side": pipeline.BLUR_DOWNSCALE,
        },
        "ood": {
            "policy": "detector_and_classifier_signal_with_nap_head_veto",
            "detector_confidence_floor": pipeline.NAP_DETECTOR_CONF_FLOOR,
            "classifier_signal_priority": "embedding_knn_else_max_logit",
            "embedding_aggregation": "min_member_distance",
            "max_logit_aggregation": "max_member_logit",
            "nap_head_veto": "veto_when_probability_below_configured_floor",
            "served_gate_enabled": False,
            "served_gate": None,
            "config_source": "classes.json#ood",
        },
    }


def _pipeline(contract):
    cls = getattr(pipeline, "_RealPipelineForTests", pipeline.PlantDiseasePipeline)
    instance = cls.__new__(cls)
    instance.detector = SimpleNamespace(
        class_name="plant_leaf",
        img_size=640,
        min_confidence=0.15,
        nms_iou=0.45,
        max_detections=10,
    )
    instance.classifier = SimpleNamespace(
        img_size=224,
        eval_resize_ratio=1.05,
        norm_mean=np.array([0.485, 0.456, 0.406], dtype=np.float32),
        norm_std=np.array([0.229, 0.224, 0.225], dtype=np.float32),
        tta="hflip",
    )
    instance._secondary_ood_gate_enabled = False
    instance.verified_bundle = SimpleNamespace(
        classifier_path=SimpleNamespace(name="classifier_int8.onnx"),
        manifest={"serving_contract": contract},
        manifest_sha256="a" * 64,
        serving_contract_sha256="b" * 64,
    )
    return instance


def test_runtime_contract_accepts_exact_live_configuration():
    _pipeline(_contract())._validate_bundle_serving_contract({"ood": {}})


def test_runtime_contract_rejects_one_changed_lever():
    contract = _contract()
    contract["crop"]["padding_ratio"] = 0.2
    with pytest.raises(BundleLoadError, match="padding_ratio"):
        _pipeline(contract)._validate_bundle_serving_contract({"ood": {}})
