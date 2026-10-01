"""Config-resolution + TTA-helper unit tests for the classifier.

These cover the new metadata knobs (``architecture``, ``inference.tta``)
and the horizontal-flip helper without needing a trained artefact or
onnxruntime/torch — they exercise pure functions only, so they run on a
fresh clone in CI.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

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
    # A bundle's calibration is fitted for one TTA mode. Silently switching an
    # unknown declaration to another mode invalidates temperature/conformal/OOD.
    with pytest.raises(clf.ClassifierLoadError, match="inference.tta"):
        clf._resolve_tta({"inference": {"tta": "tencrop"}})


def test_class_names_are_nonempty_unique_trimmed_strings():
    assert clf._resolve_class_names({"class_names": ["healthy", "leaf_spot"]}) == [
        "healthy", "leaf_spot",
    ]
    for names in ([], ["healthy", "healthy"], [" healthy"], [3]):
        with pytest.raises(clf.ClassifierLoadError):
            clf._resolve_class_names({"class_names": names})


def test_normalisation_is_metadata_driven_and_strict():
    mean, std = clf._resolve_normalization({
        "inference": {
            "normalize_mean": [0.1, 0.2, 0.3],
            "normalize_std": [1.0, 2.0, 3.0],
        },
    })
    np.testing.assert_allclose(mean, [0.1, 0.2, 0.3])
    np.testing.assert_allclose(std, [1.0, 2.0, 3.0])
    with pytest.raises(clf.ClassifierLoadError, match="normalize_std"):
        clf._resolve_normalization({"inference": {"normalize_std": [1.0, 0.0, 1.0]}})


def test_exact_size_input_still_gets_declared_eval_center_zoom():
    classifier = clf.DiseaseClassifier.__new__(clf.DiseaseClassifier)
    classifier.img_size = 4
    classifier.eval_resize_ratio = 1.5
    classifier.norm_mean = np.zeros(3, dtype=np.float32)
    classifier.norm_std = np.ones(3, dtype=np.float32)
    pixels = np.arange(4 * 4 * 3, dtype=np.uint8).reshape(4, 4, 3)
    image = Image.fromarray(pixels, mode="RGB")
    served = classifier._to_chw(image).transpose(1, 2, 0)
    baseline = pixels.astype(np.float32) / 255.0
    assert served.shape == baseline.shape
    assert not np.allclose(served, baseline)


def test_nonfinite_or_wrong_width_logits_fail_inference():
    classifier = clf.DiseaseClassifier.__new__(clf.DiseaseClassifier)
    classifier.NUM_CLASSES = 2
    classifier.CLASS_NAMES = ["healthy", "leaf_spot"]
    classifier._plant_logit_idx = (0, 1)
    classifier._embedding_dim = None
    with pytest.raises(RuntimeError, match="non-finite"):
        classifier._format_result_from_logits(np.array([0.0, np.nan], dtype=np.float32))
    with pytest.raises(RuntimeError, match="for 2 classes"):
        classifier._format_result_from_logits(np.array([0.0], dtype=np.float32))


def test_probabilities_keep_precision_until_the_api_boundary():
    classifier = clf.DiseaseClassifier.__new__(clf.DiseaseClassifier)
    classifier.NUM_CLASSES = 3
    classifier.CLASS_NAMES = ["healthy", "leaf_spot", "not_a_plant"]
    classifier._plant_logit_idx = (0, 1)
    classifier._embedding_dim = None
    result = classifier._format_result_from_logits(np.array([0.0, 0.0, 0.0], np.float32))
    assert sum(result["all_probs"].values()) == pytest.approx(1.0)
    assert result["all_probs"]["healthy"] != round(result["all_probs"]["healthy"], 4)


def test_hflip_mirrors_width_and_roundtrips():
    x = np.arange(2 * 3 * 4 * 5, dtype=np.float32).reshape(2, 3, 4, 5)
    flipped = clf.DiseaseClassifier._hflip(x)
    assert flipped.flags["C_CONTIGUOUS"]
    # Mirrored along the last (width) axis only.
    np.testing.assert_array_equal(flipped, x[..., ::-1])
    # Flipping twice is the identity.
    np.testing.assert_array_equal(clf.DiseaseClassifier._hflip(flipped), x)
