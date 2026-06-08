"""Pure-helper tests for the PlantVision pipeline.

These tests only exercise the deterministic helpers (post-processing
rules, healthy fallback, OOD reroute, severity scoring, taxonomy
constants). They do not instantiate ``PlantDiseasePipeline`` directly
because production startup is fail-fast and requires trained weights.
The HTTP-level contract is covered separately in ``test_endpoints.py``
via the ``FakePipeline`` injected by ``conftest.py``.
"""

from __future__ import annotations

import pytest

# Canonical alphabetical 8-class taxonomy (PlantVision 2.x);
# leaf_spot = merged bacterial + fungal leaf spot.
# leaf_scorch_underwatering and nutrient_deficiency joined the head
# per train_notebook.ipynb §1; edema and sunburn_or_scorch stay deferred.
EXPECTED_CLASSES = {
    "gray_mold_botrytis",
    "healthy",
    "leaf_scorch_underwatering",
    "leaf_spot",
    "not_a_plant",
    "nutrient_deficiency",
    "pests",
    "powdery_mildew",
    "viral_disease",
}


def _uniform_probs() -> dict[str, float]:
    """A flat distribution over the v2.2 class set."""
    n = len(EXPECTED_CLASSES)
    return {name: 1.0 / n for name in sorted(EXPECTED_CLASSES)}


def test_not_a_plant_class_is_present():
    """Rejection class must always exist — it's how the pipeline reports noise."""
    from classifier import DEFAULT_CLASS_NAMES

    assert "not_a_plant" in DEFAULT_CLASS_NAMES


def test_taxonomy_is_v2x_disease_only():
    """PlantVision v2.x carries 9 trainable classes (incl. the combined
    ``pests`` head). The remaining physiological conditions (edema,
    sunburn_or_scorch) are deferred until labelled real-world data is
    added."""
    from classifier import DEFAULT_CLASS_NAMES

    assert set(DEFAULT_CLASS_NAMES) == EXPECTED_CLASSES, (
        f"class set drift: extra={set(DEFAULT_CLASS_NAMES) - EXPECTED_CLASSES} "
        f"missing={EXPECTED_CLASSES - set(DEFAULT_CLASS_NAMES)}"
    )
    # Old / removed labels must not reappear.
    for legacy in ("anthracnose", "gray_mold", "iron_chlorosis",
                   "late_blight", "mosaic_virus", "sunburn"):
        assert legacy not in DEFAULT_CLASS_NAMES, (
            f"legacy class {legacy} must not be in v2.2 taxonomy"
        )
    # Still-deferred physiological classes must not appear in the
    # trainable taxonomy yet.
    for deferred in ("edema", "sunburn_or_scorch"):
        assert deferred not in DEFAULT_CLASS_NAMES, (
            f"deferred class {deferred} must not be in v2.2 trainable taxonomy"
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
    probs["leaf_spot"] = 0.52
    s = sum(probs.values())
    probs = {k: v / s for k, v in probs.items()}
    assert probs["healthy"] > 0.50  # absolute floor wouldn't trigger
    gap = probs["healthy"] - probs["leaf_spot"]
    assert gap < HEALTHY_GAP_MARGIN  # gap floor must trigger

    classification = {
        "class_name": "healthy",
        "confidence": probs["healthy"],
        "all_probs": probs,
        "low_confidence": False,
    }
    out = _apply_postprocessing_rules(classification, list(probs.keys()))

    assert out["class_name"] == "leaf_spot", (
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
    probs["leaf_spot"] = 0.23
    s = sum(probs.values())
    probs = {k: v / s for k, v in probs.items()}
    gap = probs["healthy"] - probs["leaf_spot"]
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
    Matches the screenshot bug: 0.29 healthy / 0.24 leaf_spot /
    0.21 gray_mold_botrytis should not be reported as a healthy plant.
    """
    from pipeline import _apply_postprocessing_rules

    probs = {n: 0.0 for n in EXPECTED_CLASSES}
    probs["healthy"] = 0.29
    probs["leaf_spot"] = 0.24
    probs["gray_mold_botrytis"] = 0.21
    # Spread the remaining mass over the other classes so the dict
    # still sums to 1.0.
    leftover = 1.0 - (0.29 + 0.24 + 0.21)
    other_classes = [n for n in probs if n not in ("healthy", "leaf_spot", "gray_mold_botrytis")]
    for n in other_classes:
        probs[n] = leftover / len(other_classes)
    classification = {
        "class_name": "healthy",
        "confidence": probs["healthy"],
        "all_probs": probs,
        "low_confidence": False,
    }
    out = _apply_postprocessing_rules(classification, list(probs.keys()))

    assert out["class_name"] == "leaf_spot", (
        "weak-healthy reroute must surface the strongest disease alternative"
    )
    assert "weak_healthy_rerouted" in out["warnings"]


def test_weak_healthy_does_not_reroute_onto_low_mass_disease():
    """Regression: when healthy is below HEALTHY_REROUTE_FLOOR (0.65) but
    the strongest disease has only the soft-warning floor of mass
    (HEALTHY_DISEASE_FLOOR = 0.12, well below
    HEALTHY_REROUTE_DISEASE_FLOOR = 0.25), the absolute_hit path must
    NOT fire. Otherwise a 0.54 healthy gets flipped to a 0.12 disease
    verdict the UI then has to demote with very_low_confidence — the
    user sees `gray_mold_botrytis 12%` instead of `healthy 54%` with
    a disease_suspected warning, which is the wrong UX. The gap_hit
    path also stays silent here because the 0.42 gap is wider than
    HEALTHY_GAP_MARGIN.
    """
    from pipeline import _apply_postprocessing_rules

    probs = {n: 0.0 for n in EXPECTED_CLASSES}
    probs["healthy"] = 0.54
    probs["gray_mold_botrytis"] = 0.12
    leftover = 1.0 - (0.54 + 0.12)
    others = [n for n in probs if n not in ("healthy", "gray_mold_botrytis")]
    for n in others:
        probs[n] = leftover / len(others)
    classification = {
        "class_name": "healthy",
        "confidence": probs["healthy"],
        "all_probs": probs,
        "low_confidence": False,
    }
    out = _apply_postprocessing_rules(classification, list(probs.keys()))

    assert out["class_name"] == "healthy", (
        "must not reroute a 0.54 healthy onto a 0.12 disease — the rerouted "
        "verdict would itself fall below VERY_LOW_CONFIDENCE_FLOOR"
    )
    assert "weak_healthy_rerouted" not in out["warnings"]
    # Soft-warning path must still surface the suspected disease.
    assert "uncertain_healthy" in out["warnings"]
    assert any(w == "disease_suspected:gray_mold_botrytis" for w in out["warnings"]), (
        f"expected disease_suspected:gray_mold_botrytis in {out['warnings']}"
    )


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
        "class_name": "leaf_spot",
        "confidence": 1.0 / len(probs),
        "all_probs": probs,
        "low_confidence": True,
    }
    out = _apply_postprocessing_rules(classification, list(probs.keys()))

    assert "low_confidence" in out["warnings"]


def test_very_low_confidence_warning_fires_on_disease_under_floor():
    """Regression for the meme-image screenshot: top-1 was
    ``gray_mold_botrytis`` at 0.31 and the UI happily rendered
    "Disease detected, Gray Mold, 31%". Any verdict that commits
    below ``VERY_LOW_CONFIDENCE_FLOOR`` (0.40) must carry the
    ``very_low_confidence`` warning so the result screen can render
    the "retake the photo" banner over the verdict card.
    """
    from pipeline import VERY_LOW_CONFIDENCE_FLOOR, _apply_postprocessing_rules

    # 0.31 < 0.40 — clearly below the floor. Other classes share the
    # remaining mass uniformly so the gap-reroute can't kick in.
    probs = _uniform_probs()
    probs["gray_mold_botrytis"] = 0.31
    leftover = 1.0 - 0.31
    others = [k for k in probs if k != "gray_mold_botrytis"]
    for k in others:
        probs[k] = leftover / len(others)
    classification = {
        "class_name": "gray_mold_botrytis",
        "confidence": 0.31,
        "all_probs": probs,
        "low_confidence": True,
    }
    out = _apply_postprocessing_rules(classification, list(probs.keys()))

    assert VERY_LOW_CONFIDENCE_FLOOR == 0.40
    assert "very_low_confidence" in out["warnings"]
    assert out["class_name"] == "gray_mold_botrytis"  # verdict not swapped


def test_very_low_confidence_warning_silent_above_floor():
    """Same shape but at 0.45 — comfortably above the floor — must
    NOT trigger the very-low-confidence banner. The soft
    ``low_confidence`` warning still fires (top-1 < 0.75) but that
    one is intended for the inline demotion, not the prominent banner.
    """
    from pipeline import _apply_postprocessing_rules

    probs = _uniform_probs()
    probs["powdery_mildew"] = 0.45
    leftover = 1.0 - 0.45
    others = [k for k in probs if k != "powdery_mildew"]
    for k in others:
        probs[k] = leftover / len(others)
    classification = {
        "class_name": "powdery_mildew",
        "confidence": 0.45,
        "all_probs": probs,
        "low_confidence": True,
    }
    out = _apply_postprocessing_rules(classification, list(probs.keys()))

    assert "very_low_confidence" not in out["warnings"]


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
    probs["leaf_spot"] = 0.30  # the band the old reroute couldn't catch
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


def test_fold_votes_averages_members_uniformly():
    """The vote folder averages each member's mass equally and keeps the
    full class set even when a member omits a class."""
    from pipeline import _fold_votes

    class_names = sorted(EXPECTED_CLASSES)
    a = {n: 0.0 for n in class_names}
    a["leaf_spot"] = 1.0
    # Second member omits most classes entirely — missing keys count as 0.
    b = {"healthy": 1.0}
    out = _fold_votes([a, b], class_names)

    assert set(out.keys()) == set(class_names)
    assert out["leaf_spot"] == pytest.approx(0.5)
    assert out["healthy"] == pytest.approx(0.5)
    assert sum(out.values()) == pytest.approx(1.0)


def test_fold_whole_frame_recovers_overcropped_leaf_verdict():
    """Detector-crop arbitration: when the leaf crop is over-tight and
    hedges (top-1 leaf_spot but only 0.40) while the WHOLE FRAME sees the
    leaf clearly (leaf_spot 0.90), folding the full frame into the vote
    must pull the aggregate's top-1 up toward the whole-frame verdict.
    This is the -0.093 leaf-centric recovery in pure-helper form.
    """
    from pipeline import _fold_votes

    class_names = sorted(EXPECTED_CLASSES)
    crop = {n: 0.0 for n in class_names}
    crop["leaf_spot"] = 0.40
    crop["healthy"] = 0.45  # the over-cropped frame is nearly a coin flip
    crop["powdery_mildew"] = 0.15
    whole = {n: 0.0 for n in class_names}
    whole["leaf_spot"] = 0.90
    whole["healthy"] = 0.10

    crop_only = _fold_votes([crop], class_names)
    with_whole = _fold_votes([crop, whole], class_names)

    # Crop alone would have called it healthy (0.45 > 0.40); folding the
    # confident whole-frame leaf_spot vote flips the aggregate to leaf_spot.
    assert max(crop_only, key=crop_only.get) == "healthy"
    assert max(with_whole, key=with_whole.get) == "leaf_spot"
    assert with_whole["leaf_spot"] > crop_only["leaf_spot"]


def test_fold_whole_frame_does_not_override_a_strong_crop_majority():
    """The wide-indoor guard: a single (background-ish) whole-frame vote
    must NOT outvote several confident leaf crops. Three crops agreeing on
    leaf_spot keep the verdict even when the full frame says healthy."""
    from pipeline import _fold_votes

    class_names = sorted(EXPECTED_CLASSES)

    def one(cls, conf):
        d = {n: 0.0 for n in class_names}
        d[cls] = conf
        d["healthy"] = 1.0 - conf
        return d

    crops = [one("leaf_spot", 0.80), one("leaf_spot", 0.78), one("leaf_spot", 0.82)]
    whole = one("healthy", 0.70)
    out = _fold_votes(crops + [whole], class_names)
    assert max(out, key=out.get) == "leaf_spot"


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
    declares a sensible plantvision taxonomy. The exact class set may
    lag DEFAULT_CLASS_NAMES whenever the artefact is shipped from an
    older training pass than the current code (e.g. v2.1 artefact in
    tree while DEFAULT_CLASS_NAMES has already been bumped to v2.2)."""
    import json
    from pathlib import Path

    repo_classes = Path(__file__).resolve().parents[1] / "models" / "classes.json"
    if not repo_classes.exists():  # pragma: no cover - models optional locally
        return

    meta = json.loads(repo_classes.read_text(encoding="utf-8"))
    class_set = set(meta["class_names"])
    assert "healthy" in class_set and "not_a_plant" in class_set
    # The current code targets the merged 8-class set (EXPECTED_CLASSES),
    # but the shipped artefact lags until the next A100 retrain regenerates
    # classes.json + classifier.onnx together. The artefact in tree today is
    # the v2.1 7-class set (pre-merge, pre-physiological), still valid on
    # disk, so accept either. Anything else is genuine taxonomy drift.
    LEGACY_V21_CLASSES = {
        "bacterial_leaf_spot", "gray_mold_botrytis", "healthy",
        "leaf_spot_fungal", "not_a_plant", "powdery_mildew", "viral_disease",
    }
    assert class_set in (LEGACY_V21_CLASSES, EXPECTED_CLASSES), (
        f"class set drift: {class_set ^ EXPECTED_CLASSES}"
    )
    assert len(meta["class_names"]) == len(class_set)  # no duplicates
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
        class_name="leaf_spot", confidence=0.92, healthy_prob=0.05,
        primary_box=big_box, image_size=(640, 640), is_diseased=True,
    )
    low = compute_severity(
        class_name="leaf_spot", confidence=0.66, healthy_prob=0.40,
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


def test_viral_low_confidence_gets_caveat_and_demotion():
    """A mid-confidence viral verdict (the variegated-ornamental false
    positive) earns a natural-variegation caveat AND a low-confidence
    demotion so the UI presents it tentatively."""
    from pipeline import VIRAL_MIN_CONFIDENCE, _apply_postprocessing_rules

    probs = {n: 0.01 for n in EXPECTED_CLASSES}
    probs["viral_disease"] = 0.53
    probs["healthy"] = 0.21
    classification = {
        "class_name": "viral_disease",
        "confidence": 0.53,
        "all_probs": probs,
        "low_confidence": False,
    }
    assert 0.53 < VIRAL_MIN_CONFIDENCE
    out = _apply_postprocessing_rules(classification, list(probs.keys()))

    assert out["class_name"] == "viral_disease"  # verdict unchanged
    assert "viral_variegation_caveat" in out["warnings"]
    assert "low_confidence" in out["warnings"]


def test_viral_high_confidence_keeps_caveat_without_demotion():
    """A confident viral verdict still carries the caveat (mosaic ↔
    variegation is inherently confusable) but is NOT demoted."""
    from pipeline import _apply_postprocessing_rules

    probs = {n: 0.01 for n in EXPECTED_CLASSES}
    probs["viral_disease"] = 0.85
    classification = {
        "class_name": "viral_disease",
        "confidence": 0.85,
        "all_probs": probs,
        "low_confidence": False,
    }
    out = _apply_postprocessing_rules(classification, list(probs.keys()))

    assert "viral_variegation_caveat" in out["warnings"]
    assert "low_confidence" not in out["warnings"]
