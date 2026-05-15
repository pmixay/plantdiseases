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


def test_healthy_gate_reroutes_on_narrow_gap_even_above_floor():
    """Tight-margin healthy verdicts above the absolute reroute floor
    must still reroute under the gap-based gate. Mirrors the production
    error band the user surfaced: top-1 healthy at ~0.55 with a
    disease alternative at ~0.45 is essentially a coin flip, even
    though the absolute floor (0.50) wouldn't catch it. The gap
    floor (HEALTHY_GAP_MARGIN=0.30) does.
    """
    from pipeline import HEALTHY_GAP_MARGIN, _apply_postprocessing_rules

    probs = {n: 0.01 for n in EXPECTED_CLASSES}
    probs["healthy"] = 0.60
    probs["leaf_spot_fungal"] = 0.52
    s = sum(probs.values())
    probs = {k: v / s for k, v in probs.items()}
    assert probs["healthy"] > 0.50  # absolute floor wouldn't trigger
    gap = probs["healthy"] - probs["leaf_spot_fungal"]
    assert gap < HEALTHY_GAP_MARGIN  # gap floor must trigger

    classification = {
        "class_name": "healthy",
        "confidence": probs["healthy"],
        "all_probs": probs,
        "low_confidence": False,
    }
    out = _apply_postprocessing_rules(classification, list(probs.keys()))

    assert out["class_name"] == "leaf_spot_fungal", (
        "narrow-gap healthy must reroute to the strongest disease"
    )
    assert "weak_healthy_rerouted" in out["warnings"]


def test_healthy_gate_attaches_warning_when_gap_is_wide():
    """The gap floor must NOT fire when the margin to the strongest
    disease is wide enough — even if healthy itself is in the weak
    (< 0.75) band. Verdict stays healthy with the soft
    ``uncertain_healthy`` / disease-suspected warnings.
    """
    from pipeline import HEALTHY_GAP_MARGIN, _apply_postprocessing_rules

    probs = {n: 0.01 for n in EXPECTED_CLASSES}
    # ~0.69 healthy, ~0.23 leaf_spot post-normalisation: gap ≈ 0.46,
    # safely above HEALTHY_GAP_MARGIN. Healthy stays below 0.75 so
    # the soft uncertainty warning still attaches.
    probs["healthy"] = 0.70
    probs["leaf_spot_fungal"] = 0.23
    s = sum(probs.values())
    probs = {k: v / s for k, v in probs.items()}
    gap = probs["healthy"] - probs["leaf_spot_fungal"]
    assert gap >= HEALTHY_GAP_MARGIN

    classification = {
        "class_name": "healthy",
        "confidence": probs["healthy"],
        "all_probs": probs,
        "low_confidence": False,
    }
    out = _apply_postprocessing_rules(classification, list(probs.keys()))

    assert out["class_name"] == "healthy", "gate must NOT reroute on a wide gap"
    assert "uncertain_healthy" in out["warnings"]
    assert "weak_healthy_rerouted" not in out["warnings"]


def test_weak_healthy_below_reroute_floor_swaps_to_top_disease():
    """When healthy is below 0.50 and a disease alternative crosses
    ``HEALTHY_DISEASE_FLOOR``, the verdict must swap to that disease.
    Matches the screenshot bug: 0.29 healthy / 0.24 leaf_spot_fungal /
    0.21 gray_mold_botrytis should not be reported as a healthy plant.
    """
    from pipeline import _apply_postprocessing_rules

    probs = {n: 0.0 for n in EXPECTED_CLASSES}
    probs["healthy"] = 0.29
    probs["leaf_spot_fungal"] = 0.24
    probs["gray_mold_botrytis"] = 0.21
    # Spread the remaining mass over the other classes so the dict
    # still sums to 1.0.
    leftover = 1.0 - (0.29 + 0.24 + 0.21)
    other_classes = [n for n in probs if n not in ("healthy", "leaf_spot_fungal", "gray_mold_botrytis")]
    for n in other_classes:
        probs[n] = leftover / len(other_classes)
    classification = {
        "class_name": "healthy",
        "confidence": probs["healthy"],
        "all_probs": probs,
        "low_confidence": False,
    }
    out = _apply_postprocessing_rules(classification, list(probs.keys()))

    assert out["class_name"] == "leaf_spot_fungal", (
        "weak-healthy reroute must surface the strongest disease alternative"
    )
    assert "weak_healthy_rerouted" in out["warnings"]


def test_weak_healthy_keeps_class_when_no_disease_alternative():
    """If healthy is weak but every disease class is also tiny, we
    leave the verdict alone — there's nothing better to swap to. The
    soft ``uncertain_healthy`` / ``low_confidence`` warnings still
    fire so the UI can demote the confidence in the card.
    """
    from pipeline import _apply_postprocessing_rules

    probs = {n: 0.01 for n in EXPECTED_CLASSES}
    probs["healthy"] = 0.40   # below the 0.50 reroute floor
    probs["not_a_plant"] = 0.55
    s = sum(probs.values())
    probs = {k: v / s for k, v in probs.items()}
    classification = {
        "class_name": "healthy",
        "confidence": probs["healthy"],
        "all_probs": probs,
        "low_confidence": False,
    }
    out = _apply_postprocessing_rules(classification, list(probs.keys()))

    # No disease class hits HEALTHY_DISEASE_FLOOR — keep healthy verdict.
    assert out["class_name"] == "healthy"
    assert "weak_healthy_rerouted" not in out["warnings"]


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


def test_weak_disease_verdict_stays_disease_not_synthesised_healthy():
    """Regression for the dominant bug in the post-launch feedback
    corpus: ``_maybe_apply_healthy_fallback`` used to flip a weak
    disease verdict to a synthesised ``healthy`` (transferring the
    rejected disease's mass), then the ``_suspected_disease`` guard
    gated off the weak-healthy reroute, and two contradictory disease
    names landed in the warnings (the killed-by-fallback class and the
    worst-after-fallback class). The fallback has been removed — a
    weak disease verdict must stay disease, with ``low_confidence``
    carrying the uncertainty.
    """
    from pipeline import _apply_postprocessing_rules

    probs = _uniform_probs()
    probs["viral_disease"] = 0.32     # would have been the rejected top-1
    probs["leaf_spot_fungal"] = 0.30  # the band the old reroute couldn't catch
    probs["healthy"] = 0.22
    s = sum(probs.values())
    probs = {k: v / s for k, v in probs.items()}

    classification = {
        "class_name": "viral_disease",
        "confidence": probs["viral_disease"],
        "all_probs": probs,
        "low_confidence": True,
    }
    out = _apply_postprocessing_rules(classification, list(probs.keys()))

    assert out["class_name"] == "viral_disease", (
        "weak disease must not be downgraded to healthy"
    )
    assert "low_confidence" in out["warnings"]
    # The contradictory-warnings bug: neither of these strings may
    # appear, even though both diseases sit in the distribution.
    assert not any(w.startswith("disease_uncertain:") for w in out["warnings"])
    # The post-fallback "uncertain_healthy" branch must not fire on a
    # disease verdict either.
    assert "uncertain_healthy" not in out["warnings"]


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


def test_blur_gate_rejects_uniform_image():
    """Solid-colour PNGs have ~zero edge variance — must trip the gate."""
    from PIL import Image

    from pipeline import BLUR_LAPLACIAN_FLOOR, _variance_of_laplacian

    flat = Image.new("RGB", (640, 480), color=(120, 160, 80))
    score = _variance_of_laplacian(flat)
    assert score < 1.0
    assert score < BLUR_LAPLACIAN_FLOOR


def test_blur_gate_accepts_textured_image():
    """High-frequency noise produces a wide Laplacian spread → passes."""
    import numpy as np
    from PIL import Image

    from pipeline import BLUR_LAPLACIAN_FLOOR, _variance_of_laplacian

    rng = np.random.default_rng(seed=42)
    noisy = Image.fromarray(rng.integers(0, 255, (480, 640, 3), dtype=np.uint8))
    assert _variance_of_laplacian(noisy) > BLUR_LAPLACIAN_FLOOR * 10
