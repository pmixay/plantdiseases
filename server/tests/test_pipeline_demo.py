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


def test_v4_taxonomy_replaces_field_crop_classes():
    """v4 dropped field-crop-only classes (blight, leaf_mold, mosaic_virus, rust)
    and added houseplant-specific ones (mealybug, scale_insect, nutrient_deficiency)."""
    from classifier import DEFAULT_CLASS_NAMES

    for added in ("mealybug", "scale_insect", "nutrient_deficiency"):
        assert added in DEFAULT_CLASS_NAMES, f"v4 must include {added}"
    for removed in ("blight", "leaf_mold", "mosaic_virus", "rust"):
        assert removed not in DEFAULT_CLASS_NAMES, f"v4 must drop {removed}"


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
    """v4 healthy gate is soft: a low margin attaches the
    ``uncertain_healthy`` warning but DOES NOT swap the class. This is
    the explicit fix for v3.x's "every plant is sick" regression."""
    from pipeline import HEALTHY_MARGIN, _apply_postprocessing_rules

    probs = {
        "healthy": 0.55,
        "leaf_spot": 0.55 - (HEALTHY_MARGIN / 2),
        "mealybug": 0.05,
        "not_a_plant": 0.01,
        "nutrient_deficiency": 0.05,
        "powdery_mildew": 0.05,
        "scale_insect": 0.02,
        "spider_mites": 0.02,
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

    # v4 contract: warning is attached, but the class stays healthy.
    assert out["class_name"] == "healthy", "v4 gate must NOT reroute the class"
    assert "uncertain_healthy" in out["warnings"]


def test_healthy_gate_silent_on_high_confidence():
    """If healthy is well above the v4 thresholds, no warning is added."""
    from pipeline import _apply_postprocessing_rules

    probs = {
        "healthy": 0.92,
        "leaf_spot": 0.02,
        "mealybug": 0.01,
        "not_a_plant": 0.01,
        "nutrient_deficiency": 0.01,
        "powdery_mildew": 0.01,
        "scale_insect": 0.01,
        "spider_mites": 0.01,
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
        "healthy", "leaf_spot", "mealybug", "not_a_plant",
        "nutrient_deficiency", "powdery_mildew", "scale_insect", "spider_mites",
    )}
    classification = {
        "class_name": "leaf_spot",
        "confidence": 0.13,
        "all_probs": probs,
        "low_confidence": True,
    }
    out = _apply_postprocessing_rules(classification, list(probs.keys()))

    assert "low_confidence" in out["warnings"]
