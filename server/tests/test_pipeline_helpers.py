"""Pure-helper tests for the PlantVision pipeline.

These tests only exercise the deterministic helpers (post-processing
rules, healthy fallback, OOD reroute, severity scoring, taxonomy
constants). They do not instantiate ``PlantDiseasePipeline`` directly
because production startup is fail-fast and requires trained weights.
The HTTP-level contract is covered separately in ``test_endpoints.py``
via the ``FakePipeline`` injected by ``conftest.py``.
"""

from __future__ import annotations

# Canonical alphabetical 7-class taxonomy (PlantVision 2.1).
# The four physiological classes from the v2.0 design (edema,
# leaf_scorch_underwatering, nutrient_deficiency, sunburn_or_scorch)
# are deferred until labelled real-world data is added — see
# server/models/MODEL_CARD.md.
EXPECTED_CLASSES = {
    "bacterial_leaf_spot",
    "gray_mold_botrytis",
    "healthy",
    "leaf_spot_fungal",
    "not_a_plant",
    "powdery_mildew",
    "viral_disease",
}


def _uniform_probs() -> dict[str, float]:
    """A flat distribution over the v2.1 class set."""
    n = len(EXPECTED_CLASSES)
    return {name: 1.0 / n for name in sorted(EXPECTED_CLASSES)}


def test_not_a_plant_class_is_present():
    """Rejection class must always exist — it's how the pipeline reports noise."""
    from classifier import DEFAULT_CLASS_NAMES

    assert "not_a_plant" in DEFAULT_CLASS_NAMES


def test_taxonomy_is_v21_disease_only():
    """PlantVision 2.1 carries 7 trainable classes. Pests and physiological
    conditions are deferred until labelled real-world data is added."""
    from classifier import DEFAULT_CLASS_NAMES

    assert set(DEFAULT_CLASS_NAMES) == EXPECTED_CLASSES, (
        f"class set drift: extra={set(DEFAULT_CLASS_NAMES) - EXPECTED_CLASSES} "
        f"missing={EXPECTED_CLASSES - set(DEFAULT_CLASS_NAMES)}"
    )
    # Old / removed labels must not reappear.
    for legacy in ("anthracnose", "gray_mold", "iron_chlorosis",
                   "late_blight", "mosaic_virus", "sunburn"):
        assert legacy not in DEFAULT_CLASS_NAMES, (
            f"legacy class {legacy} must not be in v2.1 taxonomy"
        )
    # Deferred — must not appear in v2.1 trainable taxonomy yet.
    for deferred in ("edema", "leaf_scorch_underwatering",
                     "nutrient_deficiency", "sunburn_or_scorch"):
        assert deferred not in DEFAULT_CLASS_NAMES, (
            f"deferred class {deferred} must not be in v2.1 trainable taxonomy"
        )
    # Pests must NOT be classifier outputs.
    for pest in ("aphids", "mealybug", "scale_insect", "spider_mites",
                 "thrips", "whitefly"):
        assert pest not in DEFAULT_CLASS_NAMES, (
            f"must not have pest class {pest}"
        )


def test_healthy_gate_attaches_warning_without_rerouting():
    """The healthy gate is soft: a low margin attaches the
    ``uncertain_healthy`` warning but DOES NOT swap the class."""
    from pipeline import HEALTHY_MARGIN, _apply_postprocessing_rules

    probs = _uniform_probs()
    probs["healthy"] = 0.55
    probs["leaf_spot_fungal"] = 0.55 - (HEALTHY_MARGIN / 2)
    s = sum(probs.values())
    probs = {k: v / s for k, v in probs.items()}

    classification = {
        "class_name": "healthy",
        "confidence": probs["healthy"],
        "all_probs": probs,
        "low_confidence": False,
    }
    out = _apply_postprocessing_rules(classification, list(probs.keys()))

    assert out["class_name"] == "healthy", "gate must NOT reroute the class"
    assert "uncertain_healthy" in out["warnings"]


def test_healthy_gate_silent_on_high_confidence():
    """If healthy is well above the thresholds, no warning is added."""
    from pipeline import _apply_postprocessing_rules

    probs = {n: 0.008 for n in EXPECTED_CLASSES}
    probs["healthy"] = 0.92
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

    probs = _uniform_probs()
    classification = {
        "class_name": "leaf_spot_fungal",
        "confidence": 1.0 / len(probs),
        "all_probs": probs,
        "low_confidence": True,
    }
    out = _apply_postprocessing_rules(classification, list(probs.keys()))

    assert "low_confidence" in out["warnings"]


def test_healthy_fallback_emits_disease_uncertain_warning():
    """When the healthy fallback fires, the postprocessor must surface
    a ``disease_uncertain:<class>`` warning."""
    from pipeline import _apply_postprocessing_rules

    probs = _uniform_probs()
    probs["healthy"] = 0.55
    probs["powdery_mildew"] = 0.15
    s = sum(probs.values())
    probs = {k: v / s for k, v in probs.items()}

    classification = {
        "class_name": "healthy",
        "confidence": probs["healthy"],
        "all_probs": probs,
        "low_confidence": False,
        "_suspected_disease": "powdery_mildew",
    }
    out = _apply_postprocessing_rules(classification, list(probs.keys()))

    assert out["class_name"] == "healthy"
    assert "disease_uncertain:powdery_mildew" in out["warnings"]


def test_postprocessor_emits_nap_ood_warning():
    """The pipeline tags an OOD-rerouted result so the UI can explain it
    differently from a "no leaves found" NAP."""
    from pipeline import _apply_postprocessing_rules

    probs = _uniform_probs()
    probs["not_a_plant"] = 0.7
    s = sum(probs.values())
    probs = {k: v / s for k, v in probs.items()}
    classification = {
        "class_name": "not_a_plant",
        "confidence": probs["not_a_plant"],
        "all_probs": probs,
        "low_confidence": False,
        "_ood_gated": True,
    }
    out = _apply_postprocessing_rules(classification, list(probs.keys()))
    assert "nap_ood_gated" in out["warnings"]


def test_is_diseased_uses_disease_min_confidence():
    """The ``is_diseased`` flag must share the disease verdict's
    confidence threshold."""
    from pipeline import _NON_DISEASE, DISEASE_MIN_CONFIDENCE

    assert DISEASE_MIN_CONFIDENCE >= 0.5
    assert "healthy" in _NON_DISEASE
    assert "not_a_plant" in _NON_DISEASE


def test_max_logit_floor_resolver_handles_null_and_explicit_values():
    """`ood.max_logit_floor: null` disables the MaxLogit gate; an
    explicit float is honoured; missing keys fall back to default."""
    from pipeline import NAP_MAX_LOGIT_FLOOR_DEFAULT, _resolve_max_logit_floor

    assert _resolve_max_logit_floor({}) == NAP_MAX_LOGIT_FLOOR_DEFAULT
    assert _resolve_max_logit_floor({"ood": {}}) == NAP_MAX_LOGIT_FLOOR_DEFAULT
    assert _resolve_max_logit_floor(
        {"ood": {"max_logit_floor": None}}
    ) == 0.0
    assert _resolve_max_logit_floor(
        {"ood": {"max_logit_floor": 3.5}}
    ) == 3.5
    # Negative values are clamped to 0.0 (gate off).
    assert _resolve_max_logit_floor(
        {"ood": {"max_logit_floor": -2.0}}
    ) == 0.0


def test_classes_json_in_repo_drives_pipeline_config():
    """End-to-end check: the committed classes.json parses cleanly and
    matches the v2.1 trainable taxonomy."""
    import json
    from pathlib import Path

    repo_classes = Path(__file__).resolve().parents[1] / "models" / "classes.json"
    if not repo_classes.exists():  # pragma: no cover - models optional locally
        return

    meta = json.loads(repo_classes.read_text(encoding="utf-8"))
    assert set(meta["class_names"]) == EXPECTED_CLASSES
    assert len(meta["class_names"]) == len(EXPECTED_CLASSES)
    assert meta["detector_classes"] == ["plant_leaf"]
    assert meta.get("model_version", "").startswith("2.")


def test_healthy_fallback_keeps_top_k_consistent_with_class_name():
    """After the healthy fallback fires, the chosen class must be the
    actual top-1 in all_probs / top_k."""
    from pipeline import _apply_postprocessing_rules

    probs = _uniform_probs()
    probs["healthy"] = 0.55
    probs["powdery_mildew"] = 0.15
    s = sum(probs.values())
    probs = {k: v / s for k, v in probs.items()}
    aggregated = {
        "class_name": "healthy",
        "confidence": probs["healthy"],
        "all_probs": probs,
        "low_confidence": False,
        "_suspected_disease": "powdery_mildew",
    }
    out = _apply_postprocessing_rules(aggregated, list(probs.keys()))

    assert out["top_k"][0]["class"] == out["class_name"], (
        f"top_k[0]={out['top_k'][0]} but class_name={out['class_name']}"
    )


def test_severity_low_for_non_disease():
    """compute_severity must short-circuit to "low" for healthy / NAP."""
    from pipeline import compute_severity

    assert compute_severity(
        class_name="healthy", confidence=0.95, healthy_prob=0.95,
        primary_box=None, image_size=(640, 640), is_diseased=False,
    ) == "low"
    assert compute_severity(
        class_name="not_a_plant", confidence=0.80, healthy_prob=0.10,
        primary_box=None, image_size=(640, 640), is_diseased=False,
    ) == "low"


def test_severity_escalates_with_area_and_margin():
    """A widespread, confident disease must score high; a tiny faint
    bbox must stay low."""
    from pipeline import compute_severity

    big_box = {"width": 400, "height": 400}  # >35 % of a 640×640 frame
    small_box = {"width": 30, "height": 30}

    high = compute_severity(
        class_name="leaf_spot_fungal", confidence=0.92, healthy_prob=0.05,
        primary_box=big_box, image_size=(640, 640), is_diseased=True,
    )
    low = compute_severity(
        class_name="leaf_spot_fungal", confidence=0.66, healthy_prob=0.40,
        primary_box=small_box, image_size=(640, 640), is_diseased=True,
    )
    assert high == "high"
    assert low == "low"


def test_healthy_fallback_helper_fires_on_weak_margin():
    """`_maybe_apply_healthy_fallback` must downgrade a weak disease
    verdict — the suspected class is reported back so the postprocessor
    can surface a `disease_uncertain:<class>` warning."""
    from pipeline import HEALTHY_FALLBACK_MARGIN, _maybe_apply_healthy_fallback

    probs = {n: 0.01 for n in EXPECTED_CLASSES}
    probs["powdery_mildew"] = 0.40
    probs["healthy"] = 0.40 - (HEALTHY_FALLBACK_MARGIN / 2)
    s = sum(probs.values())
    probs = {k: v / s for k, v in probs.items()}
    out = _maybe_apply_healthy_fallback(probs, "powdery_mildew", probs["powdery_mildew"])

    assert out is not None
    assert out["suspected"] == "powdery_mildew"
    assert 0.0 < out["top1_conf"] <= 1.0


def test_healthy_fallback_helper_skips_strong_disease_verdict():
    """A confident disease verdict with healthy well below must be left alone."""
    from pipeline import _maybe_apply_healthy_fallback

    strong_probs = {n: 0.01 for n in EXPECTED_CLASSES}
    strong_probs["powdery_mildew"] = 0.85
    strong_probs["healthy"] = 0.05
    s = sum(strong_probs.values())
    strong_probs = {k: v / s for k, v in strong_probs.items()}
    assert _maybe_apply_healthy_fallback(
        strong_probs, "powdery_mildew", strong_probs["powdery_mildew"],
    ) is None
