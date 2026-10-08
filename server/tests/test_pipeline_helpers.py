"""Pure-helper tests for the PlantVision pipeline.

These tests exercise deterministic helpers and model-free pipeline plumbing
(post-processing, OOD abstention, severity contract, taxonomy
constants). A plumbing regression allocates ``PlantDiseasePipeline`` via
``__new__`` with complete stubs; production ``__init__`` is never called
because startup is fail-fast and requires trained weights.
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


def test_borderline_healthy_is_not_force_converted():
    """A healthy argmax with a nearby disease must not be force-converted.

    The previous gap gate manufactured disease false positives while making
    ``class_name`` disagree with the probability argmax.  Suspicion remains
    visible in warnings and top-k without asserting the lower-probability class.
    """
    from pipeline import _apply_postprocessing_rules

    probs = {n: 0.01 for n in EXPECTED_CLASSES}
    probs["healthy"] = 0.62
    probs["leaf_spot"] = 0.46     # close behind, but healthy keeps the majority
    s = sum(probs.values())
    probs = {k: v / s for k, v in probs.items()}
    assert probs["healthy"] - probs["leaf_spot"] < 0.30  # narrow gap

    classification = {
        "class_name": "healthy",
        "confidence": probs["healthy"],
        "all_probs": probs,
        "low_confidence": False,
    }
    out = _apply_postprocessing_rules(classification, list(probs.keys()))

    assert out["class_name"] == "healthy", (
        "borderline healthy with a nearby disease must NOT be force-converted "
        "(that manufactured false positives on healthy plants)"
    )
    assert "weak_healthy_rerouted" not in out["warnings"]
    # Detection signal is NOT lost — the suspected disease is still surfaced.
    assert "uncertain_healthy" in out["warnings"]
    assert any(w == "disease_suspected:leaf_spot" for w in out["warnings"]), (
        f"suspected disease must still ride along, got {out['warnings']}"
    )


def test_submajority_healthy_preserves_argmax_and_surfaces_suspicion():
    """A diffuse softmax is uncertainty, not permission to change its argmax."""
    from pipeline import _apply_postprocessing_rules

    probs = {n: 0.0 for n in EXPECTED_CLASSES}
    probs["healthy"] = 0.44       # below the majority floor
    probs["leaf_spot"] = 0.34     # competitive disease
    leftover = 1.0 - (0.44 + 0.34)
    others = [n for n in probs if n not in ("healthy", "leaf_spot")]
    for n in others:
        probs[n] = leftover / len(others)
    classification = {
        "class_name": "healthy",
        "confidence": probs["healthy"],
        "all_probs": probs,
        "low_confidence": False,
    }
    out = _apply_postprocessing_rules(classification, list(probs.keys()))

    assert out["class_name"] == "healthy"
    assert out["confidence"] == pytest.approx(probs["healthy"])
    assert out["top_k"][0]["class"] == "healthy"
    assert "weak_healthy_rerouted" not in out["warnings"]
    assert "uncertain_healthy" in out["warnings"]
    assert "disease_suspected:leaf_spot" in out["warnings"]


def test_healthy_gate_attaches_warning_when_gap_is_wide():
    """Weak healthy remains healthy even when its nearest-disease gap is wide."""
    from pipeline import _apply_postprocessing_rules

    probs = {n: 0.01 for n in EXPECTED_CLASSES}
    # ~0.69 healthy, ~0.23 leaf_spot post-normalisation: gap ≈ 0.46,
    # a wide gap. Healthy stays below 0.75 so the uncertainty warning attaches.
    probs["healthy"] = 0.70
    probs["leaf_spot"] = 0.23
    s = sum(probs.values())
    probs = {k: v / s for k, v in probs.items()}
    gap = probs["healthy"] - probs["leaf_spot"]
    assert gap >= 0.30

    classification = {
        "class_name": "healthy",
        "confidence": probs["healthy"],
        "all_probs": probs,
        "low_confidence": False,
    }
    out = _apply_postprocessing_rules(classification, list(probs.keys()))

    assert out["class_name"] == "healthy", "postprocessing must preserve the argmax"
    assert "uncertain_healthy" in out["warnings"]
    assert "weak_healthy_rerouted" not in out["warnings"]


def test_diffuse_healthy_screenshot_case_keeps_probability_argmax():
    """The 0.29/0.24 screenshot case is ambiguous, not a disease verdict.

    The model's highest measured probability remains the reported class.  The
    suspected disease still rides in warnings and ranked alternatives.
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

    assert out["class_name"] == "healthy"
    assert out["confidence"] == pytest.approx(0.29)
    assert out["all_probs"] == probs
    assert [item["class"] for item in out["top_k"][:2]] == ["healthy", "leaf_spot"]
    assert "weak_healthy_rerouted" not in out["warnings"]
    assert "uncertain_healthy" in out["warnings"]
    assert "disease_suspected:leaf_spot" in out["warnings"]


def test_weak_healthy_does_not_substitute_low_mass_disease():
    """A 0.54 healthy cannot become a 0.12 disease through postprocessing."""
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
        "postprocessing cannot substitute a lower-probability disease"
    )
    assert "weak_healthy_rerouted" not in out["warnings"]
    # Soft-warning path must still surface the suspected disease.
    assert "uncertain_healthy" in out["warnings"]
    assert any(w == "disease_suspected:gray_mold_botrytis" for w in out["warnings"]), (
        f"expected disease_suspected:gray_mold_botrytis in {out['warnings']}"
    )


def test_weak_healthy_keeps_class_when_no_disease_alternative():
    """A weak but genuine healthy argmax stays healthy with soft warnings."""
    from pipeline import _apply_postprocessing_rules

    probs = {n: 0.0 for n in EXPECTED_CLASSES}
    probs["healthy"] = 0.40
    probs["not_a_plant"] = 0.35
    diseases = [n for n in probs if n not in ("healthy", "not_a_plant")]
    for name in diseases:
        probs[name] = 0.25 / len(diseases)
    classification = {
        "class_name": "healthy",
        "confidence": probs["healthy"],
        "all_probs": probs,
        "low_confidence": False,
    }
    out = _apply_postprocessing_rules(classification, list(probs.keys()))

    # No disease class hits HEALTHY_DISEASE_FLOOR and healthy is the argmax.
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
    # remaining mass uniformly so it remains the measured argmax.
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
    # The post-fallback "uncertain_healthy" branch must not fire on a
    # disease verdict, even though healthy sits in the distribution.
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


def test_ood_abstention_preserves_classifier_distribution_and_argmax():
    """OOD evidence is a rejection signal, not a NAP class probability."""
    from pipeline import _apply_postprocessing_rules

    probs = {name: 0.0 for name in EXPECTED_CLASSES}
    probs["leaf_spot"] = 0.70
    probs["not_a_plant"] = 0.20
    probs["healthy"] = 0.10
    classification = {
        "class_name": "leaf_spot",
        "confidence": probs["leaf_spot"],
        "all_probs": probs,
        "low_confidence": False,
        "_ood_gated": True,
    }
    out = _apply_postprocessing_rules(classification, list(probs.keys()))

    assert out["class_name"] == "leaf_spot"
    assert out["confidence"] == pytest.approx(0.70)
    assert out["all_probs"] == probs
    assert out["top_k"][0] == {"class": "leaf_spot", "confidence": 0.7}
    assert out["_ood_gated"] is True
    assert "nap_ood_gated" in out["warnings"]


def test_postprocessor_repairs_stale_class_and_confidence_from_probabilities():
    """Temperature-calibrated ``all_probs`` is the source of truth."""
    from pipeline import _apply_postprocessing_rules

    probs = {name: 0.0 for name in EXPECTED_CLASSES}
    probs["leaf_spot"] = 0.72
    probs["healthy"] = 0.28
    out = _apply_postprocessing_rules(
        {
            # Simulate stale pre-calibration summary fields.
            "class_name": "healthy",
            "confidence": 0.91,
            "all_probs": probs,
            "low_confidence": False,
        },
        list(probs),
    )

    assert out["class_name"] == "leaf_spot"
    assert out["confidence"] == pytest.approx(0.72)
    assert out["top_k"][0]["class"] == "leaf_spot"


def test_is_diseased_uses_disease_min_confidence():
    """The ``is_diseased`` flag must share the disease verdict's
    confidence threshold."""
    from pipeline import _NON_DISEASE, DISEASE_MIN_CONFIDENCE

    assert DISEASE_MIN_CONFIDENCE >= 0.5
    assert "healthy" in _NON_DISEASE
    assert "not_a_plant" in _NON_DISEASE


def test_is_diseased_is_false_for_ood_abstention():
    """A rejected frame must not enter downstream disease workflows."""
    import pipeline as pipeline_module

    real = pipeline_module._RealPipelineForTests
    assert real._is_diseased(
        {"class_name": "leaf_spot", "confidence": 0.99, "_ood_gated": True}
    ) is False


def test_analyze_computes_disease_flag_after_probability_canonicalisation(monkeypatch):
    """Regression: response class and ``is_diseased`` must share one verdict.

    This is model-free plumbing: the stub intentionally supplies stale summary
    fields while its probability vector has a confident disease argmax.
    """
    from PIL import Image

    import pipeline as pipeline_module

    real = pipeline_module._RealPipelineForTests
    pipe = real.__new__(real)

    class StubClassifier:
        CLASS_NAMES = ["healthy", "leaf_spot", "not_a_plant"]

    class StubDetector:
        @staticmethod
        def detect(_img):
            box = {"x": 0, "y": 0, "width": 64, "height": 64, "confidence": 0.9}
            return {"detections": [box], "confidence": 0.9}

    pipe.classifier = StubClassifier()
    pipe.detector = StubDetector()
    pipe.conformal = None
    pipe._pick_primary = lambda _img, detections: (
        detections[0],
        {
            "class_name": "healthy",
            "confidence": 0.9,
            "all_probs": {"healthy": 0.15, "leaf_spot": 0.80, "not_a_plant": 0.05},
            "low_confidence": False,
            "_voted_over": 1,
        },
    )
    pipe._should_route_to_nap = lambda _classification, _det_conf: False
    monkeypatch.setattr(pipeline_module, "_variance_of_laplacian", lambda _img: 999.0)

    out = pipe.analyze(Image.new("RGB", (64, 64)))

    assert out["class_name"] == "leaf_spot"
    assert out["confidence"] == pytest.approx(0.80)
    assert out["detection"]["is_diseased"] is True
    assert out["severity"] == "low"
    assert out["abstained"] is False
    assert out["abstention_reason"] is None
    assert out["verdict_status"] == "accepted"


def test_ood_abstention_suppresses_plant_conformal_claim():
    """RAPS coverage does not apply once the runtime rejects the frame."""
    import pipeline as pipeline_module
    from conformal import ConformalConfig

    real = pipeline_module._RealPipelineForTests
    pipe = real.__new__(real)
    pipe.conformal = ConformalConfig(method="aps", alpha=0.1, q_hat=0.9)
    probs = {"healthy": 0.1, "leaf_spot": 0.8, "not_a_plant": 0.1}

    assert pipe._build_prediction_set(
        probs, "leaf_spot", rejected=True
    ) is None


def test_public_abstention_contract_distinguishes_ood_quality_and_natural_nap():
    from pipeline import _derive_abstention

    assert _derive_abstention({
        "class_name": "leaf_spot", "warnings": ["nap_ood_gated"],
    }) == (True, "ood")
    assert _derive_abstention({
        "class_name": "unknown", "warnings": ["blurry_image"],
    }) == (True, "image_quality")
    assert _derive_abstention({
        "class_name": "not_a_plant", "warnings": [],
    }) == (True, "not_a_plant")
    assert _derive_abstention({
        "class_name": "healthy", "warnings": ["uncertain_healthy"],
    }) == (False, None)
    assert _derive_abstention({
        "class_name": "leaf_spot",
        "abstention_reason": "ood",
        "warnings": [],
    }) == (True, "ood")
    assert _derive_abstention({
        "class_name": "healthy",
        "abstained": True,
        "verdict_status": "abstained",
        "warnings": [],
    }) == (True, "unknown")


def test_blur_response_does_not_fabricate_nap_probability(monkeypatch):
    """The pre-classifier quality gate has no softmax evidence to report."""
    from PIL import Image

    import pipeline as pipeline_module

    real = pipeline_module._RealPipelineForTests
    pipe = real.__new__(real)

    class StubClassifier:
        CLASS_NAMES = ["healthy", "leaf_spot", "not_a_plant"]

    pipe.classifier = StubClassifier()
    pipe.conformal = None
    monkeypatch.setattr(pipeline_module, "_variance_of_laplacian", lambda _img: 0.0)

    out = pipe.analyze(Image.new("RGB", (64, 64)))

    assert out["class_name"] == "unknown"
    assert out["confidence"] == 0.0
    assert out["all_probs"] == {}
    assert out["top_k"] == []
    assert out["abstained"] is True
    assert out["abstention_reason"] == "image_quality"
    assert out["detection"]["is_diseased"] is False


def test_ood_gate_checks_healthy_but_bypasses_natural_nap_top1():
    """Sharp junk predicted healthy must not evade otherwise-valid OOD checks."""
    from pipeline import _decide_nap_route

    common = {
        "class_names": ["healthy", "leaf_spot", "not_a_plant"],
        "det_conf": 0.1,
        "ood_score": 0.9,
        "ood_threshold": 0.5,
        "ml_floor": 0.0,
        "max_logit": 0.0,
        "head_nap": 0.10,
        "head_mass_floor": 0.05,
    }
    healthy, _ = _decide_nap_route(class_name="healthy", **common)
    natural_nap, _ = _decide_nap_route(class_name="not_a_plant", **common)

    assert healthy is True
    assert natural_nap is False


def test_served_ood_gate_requires_exact_pipeline_calibration_contract():
    import pipeline as pipeline_module

    valid = {
        "ood": {
            "served_gate": {
                "schema_version": 1,
                "calibration_mode": "full_served_pipeline",
                "embedding_aggregation": "min_member_distance",
                "max_logit_aggregation": "max_member_logit",
                "detector_confidence_floor": pipeline_module.NAP_DETECTOR_CONF_FLOOR,
                "fold_whole_frame_vote": pipeline_module.FOLD_WHOLE_FRAME_VOTE,
            }
        }
    }
    assert pipeline_module._served_ood_gate_contract(valid)[0] is True
    assert pipeline_module._served_ood_gate_contract({})[0] is False

    stale = {
        "ood": {"served_gate": dict(valid["ood"]["served_gate"])}
    }
    stale["ood"]["served_gate"]["embedding_aggregation"] = "single_image"
    matched, reason = pipeline_module._served_ood_gate_contract(stale)
    assert matched is False
    assert "embedding_aggregation" in reason


def test_pipeline_disables_secondary_ood_without_served_contract():
    """Even firing detector/MaxLogit evidence stays advisory without binding."""
    import pipeline as pipeline_module

    real = pipeline_module._RealPipelineForTests
    pipe = real.__new__(real)

    class StubClassifier:
        CLASS_NAMES = ["healthy", "leaf_spot", "not_a_plant"]

    pipe.classifier = StubClassifier()
    pipe._secondary_ood_gate_enabled = False
    pipe.embedding_ood = None
    pipe.nap_max_logit_floor = 4.0
    pipe.nap_max_logit_scope = "plant_only"
    pipe._max_logit_key = "plant_max_logit"
    pipe.nap_head_mass_floor = 0.05
    classification = {
        "class_name": "leaf_spot",
        "all_probs": {"healthy": 0.1, "leaf_spot": 0.8, "not_a_plant": 0.1},
        "plant_max_logit": 1.0,
    }
    assert pipe._should_route_to_nap(classification, det_conf=0.0) is False


@pytest.mark.parametrize(
    "payload",
    [
        "[]",
        "{}",
        '{"temperature": true}',
        '{"temperature": "1.2"}',
        '{"temperature": Infinity}',
        '{"temperature": 0.01}',
        '{"temperature": 12.0}',
    ],
)
def test_present_invalid_temperature_sidecar_fails_readiness(tmp_path, payload):
    from pipeline import _load_temperature

    model_path = tmp_path / "classifier.onnx"
    (tmp_path / "temperature.json").write_text(payload, encoding="utf-8")
    with pytest.raises(RuntimeError, match="temperature sidecar"):
        _load_temperature(model_path)


def test_temperature_sidecar_missing_is_noop_but_tta_mismatch_fails(tmp_path):
    from pipeline import _load_temperature

    model_path = tmp_path / "classifier.onnx"
    assert _load_temperature(model_path, expected_tta="none") == 1.0

    (tmp_path / "temperature.json").write_text(
        '{"temperature": 1.25, "tta": "hflip"}', encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="serving uses tta"):
        _load_temperature(model_path, expected_tta="none")

    assert _load_temperature(model_path, expected_tta="hflip") == pytest.approx(1.25)


def test_temperature_binding_warning_only_for_unbound_sidecars(tmp_path, caplog):
    """A sidecar the verified bundle manifest already binds is not 'advisory';
    the caveat is for loose trees only."""
    import logging

    from pipeline import _load_temperature

    model_path = tmp_path / "classifier.onnx"
    (tmp_path / "temperature.json").write_text('{"temperature": 0.83}', encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="pipeline"):
        assert _load_temperature(model_path, manifest_bound=True) == pytest.approx(0.83)
    assert "calibration claim is advisory" not in caplog.text
    with caplog.at_level(logging.WARNING, logger="pipeline"):
        _load_temperature(model_path)
    assert "calibration claim is advisory" in caplog.text


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


def test_max_logit_scope_resolver_defaults_to_legacy_all_classes():
    """Bundles exported before ``ood.max_logit_scope`` existed must keep their
    historical all-class numerics; only an explicit ``plant_only`` switches the
    gate onto the NAP-excluded max. An unknown value degrades to the default
    rather than failing boot — the floor is only the *fallback* OOD signal."""
    from pipeline import MAX_LOGIT_SCOPE_DEFAULT, _resolve_max_logit_scope

    assert MAX_LOGIT_SCOPE_DEFAULT == "all_classes"
    assert _resolve_max_logit_scope({}) == "all_classes"
    assert _resolve_max_logit_scope({"ood": {}}) == "all_classes"
    assert _resolve_max_logit_scope({"ood": {"max_logit_scope": "plant_only"}}) == (
        "plant_only"
    )
    assert _resolve_max_logit_scope({"ood": {"max_logit_scope": "nonsense"}}) == (
        "all_classes"
    )


def test_plant_only_max_logit_separates_nap_where_all_class_max_cannot():
    """The reason the scope key exists.

    ``not_a_plant`` is a TRAINED class, so on a NAP photo the all-class max is
    the (high) NAP logit — which is why every export auto-disabled the floor
    (``separation_p50`` < 0 in the run logs). Excluding the NAP logit makes the
    score mean "does this look like any plant condition we know?", which
    separates: a NAP photo scores LOW and a plant photo scores HIGH.
    """
    import numpy as np

    from classifier import DiseaseClassifier

    names = ["healthy", "leaf_spot", "not_a_plant"]
    stub = object.__new__(DiseaseClassifier)
    stub.CLASS_NAMES = names
    stub.NUM_CLASSES = len(names)
    stub._plant_logit_idx = tuple(i for i, n in enumerate(names) if n != "not_a_plant")

    nap = stub._format_result_from_logits(np.array([0.4, 0.2, 8.0], dtype=np.float32))
    plant = stub._format_result_from_logits(np.array([7.5, 0.9, 0.1], dtype=np.float32))

    # All-class max is HIGHER for the NAP photo — the dead signal.
    assert nap["max_logit"] > plant["max_logit"]
    # Plant-only max flips the ordering, which is what the floor needs.
    assert nap["plant_max_logit"] < plant["plant_max_logit"]

    # A floor between the two now fires on NAP and stays quiet on the plant.
    from pipeline import _decide_nap_route

    floor = 4.0
    for cls_res, expect in ((nap, True), (plant, False)):
        triggered, _ = _decide_nap_route(
            class_name="leaf_spot",
            class_names=names,
            det_conf=0.1,               # detector unsure — the AND-gate's other half
            ood_score=None,
            ood_threshold=None,
            ml_floor=floor,
            max_logit=cls_res["plant_max_logit"],
            ml_scope="plant_only",
        )
        assert triggered is expect


def test_taxonomy_without_nap_keeps_plant_max_logit_equal_to_max_logit():
    """No ``not_a_plant`` class ⇒ nothing to exclude ⇒ identical scores, so a
    non-NAP taxonomy behaves exactly as it did before the scope key."""
    import numpy as np

    from classifier import DiseaseClassifier

    names = ["healthy", "leaf_spot"]
    stub = object.__new__(DiseaseClassifier)
    stub.CLASS_NAMES = names
    stub.NUM_CLASSES = len(names)
    stub._plant_logit_idx = tuple(i for i, n in enumerate(names) if n != "not_a_plant")

    res = stub._format_result_from_logits(np.array([3.0, 5.0], dtype=np.float32))
    assert res["plant_max_logit"] == res["max_logit"] == 5.0


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


def test_severity_does_not_treat_leaf_box_area_as_lesion_extent():
    """Leaf localisation and class confidence cannot measure severity."""
    from pipeline import compute_severity

    big_box = {"width": 400, "height": 400}  # >35 % of a 640×640 frame
    small_box = {"width": 30, "height": 30}

    large_leaf = compute_severity(
        class_name="leaf_spot", confidence=0.92, healthy_prob=0.05,
        primary_box=big_box, image_size=(640, 640), is_diseased=True,
    )
    small_leaf = compute_severity(
        class_name="leaf_spot", confidence=0.66, healthy_prob=0.40,
        primary_box=small_box, image_size=(640, 640), is_diseased=True,
    )
    assert large_leaf == "low"
    assert small_leaf == "low"


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


def test_served_gate_applies_served_thresholds_not_the_single_image_ones():
    """The overlay must reject on ``ood.served_gate.embedding_threshold`` (fitted
    on min-over-members) and never on ``ood.embedding.threshold`` (fitted one
    image at a time). A served min distance between the two thresholds is the
    exact case that used to be mis-gated."""
    import pipeline as pipeline_module

    real = pipeline_module._RealPipelineForTests
    pipe = real.__new__(real)

    class StubClassifier:
        CLASS_NAMES = ["healthy", "leaf_spot", "not_a_plant"]

    class StubBank:
        threshold = 0.55   # single-image threshold: provenance only

    pipe.classifier = StubClassifier()
    pipe._secondary_ood_gate_enabled = True
    pipe.embedding_ood = StubBank()
    pipe._served_embedding_threshold = 0.70
    pipe.nap_max_logit_floor = 0.0
    pipe.nap_max_logit_scope = "plant_only"
    pipe._max_logit_key = "plant_max_logit"
    pipe.nap_head_mass_floor = 0.05
    base = {
        "class_name": "leaf_spot",
        "all_probs": {"healthy": 0.2, "leaf_spot": 0.6, "not_a_plant": 0.2},
        "max_logit": 1.0, "plant_max_logit": 1.0,
    }
    between = {**base, "ood_score": 0.62}   # > single-image 0.55, < served 0.70
    beyond = {**base, "ood_score": 0.75}    # > served 0.70
    assert pipe._should_route_to_nap(between, det_conf=0.1) is False
    assert pipe._should_route_to_nap(beyond, det_conf=0.1) is True
    # No served embedding threshold -> the bank is not a signal at all.
    pipe._served_embedding_threshold = None
    assert pipe._should_route_to_nap(beyond, det_conf=0.1) is False


def test_served_gate_thresholds_are_resolved_strictly():
    import pipeline as pipeline_module

    resolve = pipeline_module._resolve_served_gate_thresholds
    assert resolve({}) == (None, None)
    assert resolve({"ood": {"served_gate": None}}) == (None, None)
    both = {"ood": {"served_gate": {"embedding_threshold": 0.63, "max_logit_floor": 1.75}}}
    assert resolve(both) == (0.63, 1.75)
    only_ml = {"ood": {"served_gate": {"embedding_threshold": None, "max_logit_floor": 1.75}}}
    assert resolve(only_ml) == (None, 1.75)
    for bad in (True, "0.6", float("nan"), -0.1, 2.5):
        with pytest.raises(RuntimeError):
            resolve({"ood": {"served_gate": {"embedding_threshold": bad}}})
    with pytest.raises(RuntimeError):
        resolve({"ood": {"served_gate": {"max_logit_floor": -1.0}}})


def test_pipeline_init_adopts_served_thresholds_when_contract_matches():
    """``__init__`` must replace the single-image MaxLogit floor with the served
    one and keep the served embedding threshold beside the bank."""
    import pipeline as pipeline_module

    meta = {
        "ood": {
            "max_logit_floor": 2.01,          # single-image: provenance only
            "max_logit_scope": "plant_only",
            "nap_head_mass_floor": 0.05,
            "served_gate": {
                "schema_version": 1,
                "calibration_mode": "full_served_pipeline",
                "embedding_aggregation": "min_member_distance",
                "max_logit_aggregation": "max_member_logit",
                "detector_confidence_floor": pipeline_module.NAP_DETECTOR_CONF_FLOOR,
                "fold_whole_frame_vote": pipeline_module.FOLD_WHOLE_FRAME_VOTE,
                "embedding_threshold": 0.68,
                "max_logit_floor": 1.4,
            },
        }
    }
    enabled, _ = pipeline_module._served_ood_gate_contract(meta)
    assert enabled is True
    served_emb, served_ml = pipeline_module._resolve_served_gate_thresholds(meta)
    assert served_emb == 0.68 and served_ml == 1.4
    # Single-image resolver still reads the provenance value untouched.
    assert pipeline_module._resolve_max_logit_floor(meta) == 2.01
