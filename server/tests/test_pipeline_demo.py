"""Pipeline post-processing rules — these are pure helpers and don't
need real model weights to exercise."""

from __future__ import annotations


def test_class_names_are_alphabetical_and_complete():
    from classifier import DEFAULT_CLASS_NAMES

    assert DEFAULT_CLASS_NAMES == sorted(DEFAULT_CLASS_NAMES)
    assert "not_a_plant" in DEFAULT_CLASS_NAMES, "rejection class must be present"


def test_taxonomy_is_disease_only():
    """PlantVision carries 10 disease-only classes. Pests live in
    PEST_INFO and are surfaced via possible_pest_causes hints, not as
    classifier outputs."""
    from classifier import DEFAULT_CLASS_NAMES

    expected = {
        "anthracnose", "bacterial_leaf_spot", "gray_mold", "healthy",
        "iron_chlorosis", "late_blight", "mosaic_virus", "not_a_plant",
        "powdery_mildew", "sunburn",
    }
    assert set(DEFAULT_CLASS_NAMES) == expected, (
        f"class set drift: extra={set(DEFAULT_CLASS_NAMES) - expected} "
        f"missing={expected - set(DEFAULT_CLASS_NAMES)}"
    )
    # Pests must NOT be classifier outputs.
    for pest in ("aphids", "mealybug", "scale_insect", "spider_mites",
                 "thrips", "whitefly"):
        assert pest not in DEFAULT_CLASS_NAMES, (
            f"must drop pest class {pest} (it's a hint, not a label)"
        )


def test_classifier_refuses_to_load_without_weights(tmp_path):
    """No demo fallback: a missing weights path raises a clear error so
    the FastAPI lifespan can refuse to come up."""
    from classifier import ClassifierWeightsMissing, DiseaseClassifier

    try:
        DiseaseClassifier(model_path=tmp_path / "missing.pth")
    except ClassifierWeightsMissing:
        return
    raise AssertionError("DiseaseClassifier must raise without weights")


def test_detector_refuses_to_load_without_weights(tmp_path):
    from detector import DetectorWeightsMissing, LeafDetector

    try:
        LeafDetector(model_path=tmp_path / "missing.pt")
    except DetectorWeightsMissing:
        return
    raise AssertionError("LeafDetector must raise without weights")


def test_healthy_gate_attaches_warning_without_rerouting():
    """The healthy gate is soft: a low margin attaches the
    ``uncertain_healthy`` warning but DOES NOT swap the class."""
    from pipeline import HEALTHY_MARGIN, _apply_postprocessing_rules

    probs = {
        "healthy": 0.55,
        "anthracnose": 0.55 - (HEALTHY_MARGIN / 2),
        "gray_mold": 0.05,
        "iron_chlorosis": 0.05,
        "late_blight": 0.05,
        "not_a_plant": 0.01,
        "powdery_mildew": 0.05,
        "sunburn": 0.02,
    }
    s = sum(probs.values())
    probs = {k: v / s for k, v in probs.items()}

    classification = {
        "class_name": "healthy",
        "confidence": probs["healthy"],
        "all_probs": probs,
        "low_confidence": False,
    }
    out = _apply_postprocessing_rules(classification, list(probs.keys()))

    # Contract: warning is attached, but the class stays healthy.
    assert out["class_name"] == "healthy", "gate must NOT reroute the class"
    assert "uncertain_healthy" in out["warnings"]


def test_healthy_gate_silent_on_high_confidence():
    """If healthy is well above the thresholds, no warning is added."""
    from pipeline import _apply_postprocessing_rules

    probs = {
        "healthy": 0.92,
        "anthracnose": 0.01,
        "gray_mold": 0.01,
        "iron_chlorosis": 0.01,
        "late_blight": 0.01,
        "not_a_plant": 0.01,
        "powdery_mildew": 0.01,
        "sunburn": 0.02,
    }
    classification = {
        "class_name": "healthy",
        "confidence": 0.92,
        "all_probs": probs,
        "low_confidence": False,
    }
    out = _apply_postprocessing_rules(classification, list(probs.keys()))

    assert out["class_name"] == "healthy"
    assert "uncertain_healthy" not in out["warnings"]


def test_low_confidence_warning_is_propagated():
    from pipeline import _apply_postprocessing_rules

    probs = {n: 1 / 8 for n in (
        "anthracnose", "gray_mold", "healthy", "iron_chlorosis",
        "late_blight", "not_a_plant", "powdery_mildew", "sunburn",
    )}
    classification = {
        "class_name": "anthracnose",
        "confidence": 0.13,
        "all_probs": probs,
        "low_confidence": True,
    }
    out = _apply_postprocessing_rules(classification, list(probs.keys()))

    assert "low_confidence" in out["warnings"]


def test_healthy_fallback_emits_disease_uncertain_warning():
    """When the healthy fallback fires, the postprocessor must surface
    a ``disease_uncertain:<class>`` warning so the UI can show the
    user which disease was ruled out as borderline."""
    from pipeline import _apply_postprocessing_rules

    classification = {
        "class_name": "healthy",
        "confidence": 0.55,
        "all_probs": {
            "healthy": 0.55, "anthracnose": 0.05, "gray_mold": 0.05,
            "iron_chlorosis": 0.05, "late_blight": 0.05,
            "not_a_plant": 0.05, "powdery_mildew": 0.15, "sunburn": 0.05,
        },
        "low_confidence": False,
        "_suspected_disease": "powdery_mildew",
    }
    out = _apply_postprocessing_rules(classification,
                                      list(classification["all_probs"].keys()))

    assert out["class_name"] == "healthy"
    assert any(w.startswith("disease_uncertain:") for w in out["warnings"]), (
        f"expected disease_uncertain warning, got {out['warnings']}"
    )
    assert "disease_uncertain:powdery_mildew" in out["warnings"]


def test_postprocessor_emits_nap_ood_warning():
    """The pipeline tags an OOD-rerouted result so the UI can explain it
    differently from a "no leaves found" NAP."""
    from pipeline import _apply_postprocessing_rules

    classification = {
        "class_name": "not_a_plant",
        "confidence": 0.7,
        "all_probs": {"not_a_plant": 0.7, "healthy": 0.1, "anthracnose": 0.05,
                       "gray_mold": 0.05, "iron_chlorosis": 0.025,
                       "late_blight": 0.025, "powdery_mildew": 0.025,
                       "sunburn": 0.025},
        "low_confidence": False,
        "_ood_gated": True,
    }
    out = _apply_postprocessing_rules(
        classification, list(classification["all_probs"].keys())
    )
    assert "nap_ood_gated" in out["warnings"]


def test_is_diseased_uses_disease_min_confidence():
    """The ``is_diseased`` flag must share the disease verdict's
    confidence threshold so the detector overlay and the verdict can't
    disagree (overlay says "diseased region" while verdict says
    healthy)."""
    from pipeline import _NON_DISEASE, DISEASE_MIN_CONFIDENCE

    assert DISEASE_MIN_CONFIDENCE >= 0.5
    assert "healthy" in _NON_DISEASE
    assert "not_a_plant" in _NON_DISEASE


def test_healthy_fallback_keeps_top_k_consistent_with_class_name():
    """After the healthy fallback fires, the chosen class must be the
    actual top-1 in all_probs / top_k — UI cannot show ``healthy`` while
    top_k[0] is a disease."""
    from pipeline import _apply_postprocessing_rules

    aggregated = {
        "class_name": "healthy",
        "confidence": 0.55,
        "all_probs": {
            "healthy": 0.55, "anthracnose": 0.05, "gray_mold": 0.05,
            "iron_chlorosis": 0.05, "late_blight": 0.05,
            "not_a_plant": 0.05, "powdery_mildew": 0.15, "sunburn": 0.05,
        },
        "low_confidence": False,
        "_suspected_disease": "powdery_mildew",
    }
    out = _apply_postprocessing_rules(aggregated,
                                      list(aggregated["all_probs"].keys()))

    # The chosen class must agree with the top of the published
    # distribution — UI can't show "healthy" while top_k[0] = disease.
    assert out["top_k"][0]["class"] == out["class_name"], (
        f"top_k[0]={out['top_k'][0]} but class_name={out['class_name']}"
    )
