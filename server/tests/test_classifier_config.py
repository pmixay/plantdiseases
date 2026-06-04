"""Config-resolution + TTA-helper unit tests for the classifier.

These cover the new metadata knobs (``architecture``, ``inference.tta``)
and the horizontal-flip helper without needing a trained artefact or
onnxruntime/torch — they exercise pure functions only, so they run on a
fresh clone in CI.
"""

from __future__ import annotations

import numpy as np

import classifier as clf


def test_architecture_defaults_to_dinov2():
    assert clf._resolve_architecture({}) == "dinov2_vits14+mlp_head"
    assert clf._resolve_architecture({"training": {}}) == "dinov2_vits14+mlp_head"


def test_architecture_reads_top_level_and_training():
    assert clf._resolve_architecture({"architecture": "dinov2_vits14"}) == "dinov2_vits14"
    assert (
        clf._resolve_architecture({"training": {"architecture": "dinov2_vits14"}})
        == "dinov2_vits14"
    )
    # Top-level wins when both are present.
    meta = {"architecture": "dinov2_vits14", "training": {"architecture": "other"}}
    assert clf._resolve_architecture(meta) == "dinov2_vits14"


def test_tta_defaults_off_and_validates():
    assert clf._resolve_tta({}) == "none"
    assert clf._resolve_tta({"inference": {}}) == "none"
    assert clf._resolve_tta({"inference": {"tta": "hflip"}}) == "hflip"
    # Unknown modes fall back to the safe default rather than crashing.
    assert clf._resolve_tta({"inference": {"tta": "tencrop"}}) == "none"


def test_hflip_mirrors_width_and_roundtrips():
    x = np.arange(2 * 3 * 4 * 5, dtype=np.float32).reshape(2, 3, 4, 5)
    flipped = clf.DiseaseClassifier._hflip(x)
    assert flipped.flags["C_CONTIGUOUS"]
    # Mirrored along the last (width) axis only.
    np.testing.assert_array_equal(flipped, x[..., ::-1])
    # Flipping twice is the identity.
    np.testing.assert_array_equal(clf.DiseaseClassifier._hflip(flipped), x)
