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


# Canonical alphabetical 11-class taxonomy (PlantVision 2.0).
EXPECTED_CLASSES = {
    "bacterial_leaf_spot",
    "edema",
    "gray_mold_botrytis",
    "healthy",
    "leaf_scorch_underwatering",
    "leaf_spot_fungal",
    "not_a_plant",
    "nutrient_deficiency",
    "powdery_mildew",
    "sunburn_or_scorch",
    "viral_disease",
}


def _uniform_probs() -> dict[str, float]:
    """A flat distribution over the v2.0 class set."""
    n = len(EXPECTED_CLASSES)
    return {name: 1.0 / n for name in sorted(EXPECTED_CLASSES)}


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

    for key in ("class_name", "confidence", "all_probs", "detection",
                "pipeline_mode", "elapsed_ms"):
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


def test_taxonomy_is_v2_disease_only():
    """PlantVision 2.0 carries 11 classes. Pests are out of the taxonomy."""
    from classifier import DEFAULT_CLASS_NAMES

    assert set(DEFAULT_CLASS_NAMES) == EXPECTED_CLASSES, (
        f"class set drift: extra={set(DEFAULT_CLASS_NAMES) - EXPECTED_CLASSES} "
        f"missing={EXPECTED_CLASSES - set(DEFAULT_CLASS_NAMES)}"
    )
    # Old / removed labels must not reappear.
    for legacy in ("anthracnose", "gray_mold", "iron_chlorosis",
                   "late_blight", "mosaic_virus", "sunburn"):
        assert legacy not in DEFAULT_CLASS_NAMES, (
            f"legacy class {legacy} must not be in v2.0 taxonomy"
        )
    # Pests must NOT be classifier outputs.
    for pest in ("aphids", "mealybug", "scale_insect", "spider_mites",
                 "thrips", "whitefly"):
        assert pest not in DEFAULT_CLASS_NAMES, (
            f"must not have pest class {pest}"
        )


def test_top_k_and_warnings_keys_in_result():
    """Pipeline should always include top_k and warnings."""
    p = PlantDiseasePipeline()
    result = p.analyze(_seeded_image(seed=11))

    assert "top_k" in result
    assert "warnings" in result
    assert isinstance(result["top_k"], list)
    assert isinstance(result["warnings"], list)
    assert len(result["top_k"]) <= 3
    for entry in result["top_k"]:
        assert set(entry.keys()) == {"class", "confidence"}


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
    s = sum(probs.values()); probs = {k: v / s for k, v in probs.items()}

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


def test_ood_reroute_to_nap_produces_consistent_distribution():
    """When the secondary OOD gate fires, _reroute_to_nap must rewrite
    the classification so that ``not_a_plant`` is the top class AND the
    published probability mass adds up to ~1."""
    from pipeline import PlantDiseasePipeline

    p = PlantDiseasePipeline()
    probs = _uniform_probs()
    probs["powdery_mildew"] = 0.42
    probs["healthy"] = 0.20
    probs["not_a_plant"] = 0.03
    s = sum(probs.values()); probs = {k: v / s for k, v in probs.items()}

    classification = {
        "class_name": "powdery_mildew",
        "confidence": probs["powdery_mildew"],
        "all_probs": probs,
        "max_logit": 1.5,
        "low_confidence": False,
        "_voted_over": 1,
    }
    out = p._reroute_to_nap(classification)

    assert out["class_name"] == "not_a_plant"
    assert out["confidence"] >= 0.55
    total = sum(out["all_probs"].values())
    assert 0.99 <= total <= 1.01, f"distribution drift {total}"
    top = max(out["all_probs"], key=out["all_probs"].get)
    assert top == "not_a_plant"
    assert out["_ood_gated"] is True


def test_postprocessor_emits_nap_ood_warning():
    """The pipeline tags an OOD-rerouted result so the UI can explain it
    differently from a "no leaves found" NAP."""
    from pipeline import _apply_postprocessing_rules

    probs = _uniform_probs()
    probs["not_a_plant"] = 0.7
    s = sum(probs.values()); probs = {k: v / s for k, v in probs.items()}
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
    """End-to-end check: the committed classes.json parses cleanly."""
    import json
    from pathlib import Path

    repo_classes = Path(__file__).resolve().parents[1] / "models" / "classes.json"
    if not repo_classes.exists():  # pragma: no cover - models optional locally
        return

    meta = json.loads(repo_classes.read_text(encoding="utf-8"))
    assert meta["num_classes"] == 11
    assert set(meta["class_names"]) == EXPECTED_CLASSES
    assert meta["detector_classes"] == ["plant_leaf"]


def test_healthy_fallback_keeps_top_k_consistent_with_class_name():
    """After the healthy fallback fires, the chosen class must be the
    actual top-1 in all_probs / top_k."""
    from pipeline import _apply_postprocessing_rules

    probs = _uniform_probs()
    probs["healthy"] = 0.55
    probs["powdery_mildew"] = 0.15
    s = sum(probs.values()); probs = {k: v / s for k, v in probs.items()}
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
