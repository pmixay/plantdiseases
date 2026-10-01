"""Pure contract checks for detector metadata and YOLO postprocessing."""

from __future__ import annotations

import numpy as np
import pytest

import detector


def test_detector_settings_default_to_the_shipped_graph_contract():
    assert detector._resolve_detector_settings({}) == (640, 0.15, 0.45, 10, "plant_leaf")


def test_detector_settings_are_bundle_driven_and_strict():
    settings = detector._resolve_detector_settings({
        "detector_classes": ["leaf"],
        "detector": {
            "img_size": 320,
            "min_confidence": 0.2,
            "nms_iou": 0.5,
            "max_detections": 7,
        },
    })
    assert settings == (320, 0.2, 0.5, 7, "leaf")
    for bad in ("0.2", float("nan"), True, -0.1, 0.0, 1.1):
        with pytest.raises(detector.DetectorLoadError, match="min_confidence"):
            detector._resolve_detector_settings({"detector": {"min_confidence": bad}})
    with pytest.raises(detector.DetectorLoadError, match="exactly one"):
        detector._resolve_detector_settings({"detector_classes": ["leaf", "stem"]})


def test_yolo_output_accepts_only_the_two_single_class_layouts():
    channel_first = np.arange(50, dtype=np.float32).reshape(1, 5, 10)
    np.testing.assert_array_equal(
        detector._normalise_yolo_output(channel_first), channel_first[0],
    )
    anchor_first = channel_first.transpose(0, 2, 1)
    np.testing.assert_array_equal(
        detector._normalise_yolo_output(anchor_first), channel_first[0],
    )
    for bad in (np.zeros((2, 5, 10)), np.zeros((1, 6, 10)), np.zeros((5, 0))):
        with pytest.raises(RuntimeError):
            detector._normalise_yolo_output(bad)


def test_nms_drops_nonfinite_and_degenerate_boxes_and_preserves_indices():
    boxes = np.array([
        [0, 0, 10, 10],
        [1, 1, 9, 9],       # suppressed by box 0
        [20, 20, 20, 30],   # zero width
        [30, 30, 40, 40],
        [0, 0, np.nan, 1],
    ], dtype=np.float32)
    scores = np.array([0.9, 0.8, 0.99, 0.7, 0.6], dtype=np.float32)
    assert detector.nms(boxes, scores, 0.5) == [0, 3]


def test_nms_rejects_misaligned_inputs():
    with pytest.raises(ValueError, match="NMS expects"):
        detector.nms(np.zeros((2, 4)), np.zeros(1), 0.5)
