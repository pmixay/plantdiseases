"""Demo-mode pipeline must be deterministic for a fixed input image."""

from __future__ import annotations

from PIL import Image

from pipeline import PlantDiseasePipeline


def _seeded_image(seed: int, size: int = 96) -> Image.Image:
    """Return an image whose pixel statistics depend only on ``seed``."""
    import numpy as np

    rng = np.random.RandomState(seed)
    arr = (rng.rand(size, size, 3) * 255).astype("uint8")
    return Image.fromarray(arr, mode="RGB")


def test_demo_pipeline_is_deterministic():
    img = _seeded_image(seed=7)
    p1 = PlantDiseasePipeline()
    p2 = PlantDiseasePipeline()
    r1 = p1.analyze(img)
    r2 = p2.analyze(img)

    assert r1["pipeline_mode"] == "demo"
    assert r1["class_name"] == r2["class_name"]
    assert r1["confidence"] == r2["confidence"]
    assert r1["all_probs"] == r2["all_probs"]


def test_demo_pipeline_result_shape():
    p = PlantDiseasePipeline()
    result = p.analyze(_seeded_image(seed=3))

    for key in ("class_name", "confidence", "all_probs", "detection", "pipeline_mode", "elapsed_ms"):
        assert key in result, f"missing key {key}"

    assert 0.0 <= result["confidence"] <= 1.0
    assert set(result["detection"].keys()) == {
        "is_diseased", "detector_confidence", "regions", "primary_region",
    }
    assert isinstance(result["detection"]["regions"], list)
    assert isinstance(result["all_probs"], dict)
    total = sum(result["all_probs"].values())
    assert 0.95 <= total <= 1.05


def test_class_names_match_default():
    from classifier import DEFAULT_CLASS_NAMES, DiseaseClassifier

    clf = DiseaseClassifier(model_path=None)
    assert clf.NUM_CLASSES == len(DEFAULT_CLASS_NAMES)
    assert clf.CLASS_NAMES == DEFAULT_CLASS_NAMES


def test_not_a_plant_class_is_present():
    """Rejection class must always exist — it's how the pipeline reports noise."""
    from classifier import DEFAULT_CLASS_NAMES

    assert "not_a_plant" in DEFAULT_CLASS_NAMES


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


def test_top_k_and_warnings_keys_in_result():
    """Pipeline should always include top_k and warnings so the UI
    never has to gate on `.get(...)` for these fields."""
    p = PlantDiseasePipeline()
    result = p.analyze(_seeded_image(seed=11))

    assert "top_k" in result, "top_k missing from pipeline result"
    assert "warnings" in result, "warnings missing from pipeline result"
    assert isinstance(result["top_k"], list)
    assert isinstance(result["warnings"], list)
    assert len(result["top_k"]) <= 3
    for entry in result["top_k"]:
        assert set(entry.keys()) == {"class", "confidence"}


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


def test_ood_reroute_to_nap_produces_consistent_distribution():
    """When the secondary OOD gate fires, _reroute_to_nap must rewrite
    the classification so that ``not_a_plant`` is the top class AND the
    published probability mass adds up to ~1, with the original
    runner-up classes preserved in proportion (so the UI's alternatives
    list still makes sense)."""
    from pipeline import PlantDiseasePipeline

    p = PlantDiseasePipeline()
    classification = {
        "class_name": "powdery_mildew",
        "confidence": 0.42,
        "all_probs": {
            "anthracnose": 0.05, "gray_mold": 0.10, "healthy": 0.20,
            "iron_chlorosis": 0.05, "late_blight": 0.05,
            "not_a_plant": 0.03, "powdery_mildew": 0.42, "sunburn": 0.10,
        },
        "low_confidence": False,
        "_voted_over": 1,
    }
    out = p._reroute_to_nap(classification)

    assert out["class_name"] == "not_a_plant"
    assert out["confidence"] >= 0.55
    # Distribution still sums to ~1 and not_a_plant is genuinely the top.
    total = sum(out["all_probs"].values())
    assert 0.99 <= total <= 1.01, f"distribution drift {total}"
    top = max(out["all_probs"], key=out["all_probs"].get)
    assert top == "not_a_plant"
    # The OOD gate flag is recorded so the postprocessor can surface
    # the corresponding warning.
    assert out["_ood_gated"] is True


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


def test_proxy_trained_resolver_reads_classes_json():
    """Pipeline must source the proxy-trained set from classes.json so
    PlantVision 1.2 picks up its extended 5-class list (incl. iron_chlorosis
    and mosaic_virus) without a code change."""
    from pipeline import _DEFAULT_PROXY_TRAINED, _resolve_proxy_trained

    plantvision_12 = {
        "proxy_trained_classes": [
            "bacterial_leaf_spot", "iron_chlorosis", "late_blight",
            "mosaic_virus", "sunburn",
        ],
    }
    assert _resolve_proxy_trained(plantvision_12) == frozenset({
        "bacterial_leaf_spot", "iron_chlorosis", "late_blight",
        "mosaic_virus", "sunburn",
    })

    # Missing or malformed → safe historical default.
    assert _resolve_proxy_trained({}) == _DEFAULT_PROXY_TRAINED
    assert _resolve_proxy_trained({"proxy_trained_classes": "oops"}) == _DEFAULT_PROXY_TRAINED


def test_msp_floor_resolver_handles_null_and_explicit_values():
    """``ood_calibration.suggested_classifier_msp_floor: null`` (the
    PlantVision 1.2 case) disables the MSP gate entirely; an explicit
    float is honoured; missing keys fall back to the historical default.
    """
    from pipeline import NAP_CLASSIFIER_MSP_FLOOR_DEFAULT, _resolve_msp_floor

    assert _resolve_msp_floor({}) == NAP_CLASSIFIER_MSP_FLOOR_DEFAULT
    assert _resolve_msp_floor({"ood_calibration": {}}) == NAP_CLASSIFIER_MSP_FLOOR_DEFAULT
    assert _resolve_msp_floor(
        {"ood_calibration": {"suggested_classifier_msp_floor": None}}
    ) == 0.0
    assert _resolve_msp_floor(
        {"ood_calibration": {"suggested_classifier_msp_floor": 0.35}}
    ) == 0.35
    # Out-of-range values are clamped, not raised.
    assert _resolve_msp_floor(
        {"ood_calibration": {"suggested_classifier_msp_floor": 1.5}}
    ) == 1.0


def test_postprocessor_uses_supplied_proxy_set():
    """``_apply_postprocessing_rules`` honours the per-pipeline proxy
    set (PlantVision 1.2 includes mosaic_virus)."""
    from pipeline import _apply_postprocessing_rules

    classification = {
        "class_name": "mosaic_virus",
        "confidence": 0.8,
        "all_probs": {
            "anthracnose": 0.02, "bacterial_leaf_spot": 0.02,
            "gray_mold": 0.02, "healthy": 0.04, "iron_chlorosis": 0.02,
            "late_blight": 0.02, "mosaic_virus": 0.80, "not_a_plant": 0.02,
            "powdery_mildew": 0.02, "sunburn": 0.02,
        },
        "low_confidence": False,
    }
    out = _apply_postprocessing_rules(
        classification,
        list(classification["all_probs"].keys()),
        proxy_trained=frozenset({
            "bacterial_leaf_spot", "iron_chlorosis", "late_blight",
            "mosaic_virus", "sunburn",
        }),
    )
    assert "proxy_trained:mosaic_virus" in out["warnings"]

    # With the historical default set (no mosaic_virus) the same
    # classification should NOT carry the proxy warning.
    out_default = _apply_postprocessing_rules(
        classification, list(classification["all_probs"].keys()),
    )
    assert all(not w.startswith("proxy_trained:") for w in out_default["warnings"])


def test_classes_json_in_repo_drives_pipeline_config():
    """End-to-end check: the committed PlantVision 1.2 classes.json
    actually produces the expected proxy set + disabled MSP gate when
    parsed through the pipeline resolvers."""
    import json
    from pathlib import Path

    from pipeline import _resolve_msp_floor, _resolve_proxy_trained

    repo_classes = Path(__file__).resolve().parents[1] / "models" / "classes.json"
    if not repo_classes.exists():  # pragma: no cover - models optional locally
        return

    meta = json.loads(repo_classes.read_text(encoding="utf-8"))
    proxy = _resolve_proxy_trained(meta)
    floor = _resolve_msp_floor(meta)

    # PlantVision 1.2 ships an extended proxy list incl. iron_chlorosis
    # and mosaic_virus (the yellow-leaf confusion pair) and explicitly
    # turns off the MSP-based OOD gate via a null suggested floor.
    assert "iron_chlorosis" in proxy
    assert "mosaic_virus" in proxy
    assert floor == 0.0


def test_healthy_fallback_keeps_top_k_consistent_with_class_name():
    """After the healthy fallback fires, the chosen class must be the
    actual top-1 in all_probs / top_k — UI cannot show ``healthy`` while
    top_k[0] is a disease."""
    from pipeline import _apply_postprocessing_rules

    # Synthetic post-fallback classification: class_name="healthy" with
    # a redistributed distribution (healthy now actually top-1).
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
