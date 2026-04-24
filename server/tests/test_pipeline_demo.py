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


def test_top_k_and_warnings_keys_in_result():
    """Pipeline should always include top_k and warnings so the UI
    never has to gate on `.get(...)` for these fields."""
    p = PlantDiseasePipeline()
    result = p.analyze(_seeded_image(seed=11))

    assert "top_k" in result, "top_k missing from pipeline result"
    assert "warnings" in result, "warnings missing from pipeline result"
    assert isinstance(result["top_k"], list)
    assert isinstance(result["warnings"], list)
    # top_k at most 3 entries, each has class + confidence.
    assert len(result["top_k"]) <= 3
    for entry in result["top_k"]:
        assert set(entry.keys()) == {"class", "confidence"}


def test_healthy_gate_reroutes_when_margin_too_small():
    """If healthy wins but the runner-up is within the margin, the final
    class should be re-routed to the runner-up and `uncertain_healthy`
    should appear in warnings."""
    from pipeline import _apply_postprocessing_rules, HEALTHY_MARGIN

    # Synthetic probabilities where healthy wins narrowly — margin under
    # the threshold must trigger the re-route.
    probs = {
        "blight": 0.05,
        "healthy": 0.55,
        "leaf_mold": 0.05,
        "leaf_spot": 0.55 - (HEALTHY_MARGIN / 2),
        "mosaic_virus": 0.02,
        "not_a_plant": 0.01,
        "powdery_mildew": 0.01,
        "rust": 0.01,
    }
    # Normalise so the dict sums to ~1 (the rule logic doesn't require
    # it but this keeps the test honest).
    s = sum(probs.values())
    probs = {k: v / s for k, v in probs.items()}

    classification = {
        "class_name": "healthy",
        "confidence": probs["healthy"],
        "all_probs": probs,
        "low_confidence": False,
    }
    stage1 = {"is_diseased": False, "confidence": 0.7}
    out = _apply_postprocessing_rules(classification, stage1, list(probs.keys()))

    assert out["class_name"] != "healthy", "re-route must trigger on low margin"
    assert "uncertain_healthy" in out["warnings"]


def test_detector_classifier_mismatch_warning():
    """Detector says 'diseased_leaf' but classifier confidently says
    'healthy' → UI should get a mismatch warning."""
    from pipeline import _apply_postprocessing_rules

    probs = {
        "blight": 0.01, "healthy": 0.95, "leaf_mold": 0.01,
        "leaf_spot": 0.01, "mosaic_virus": 0.01, "not_a_plant": 0.0,
        "powdery_mildew": 0.005, "rust": 0.005,
    }
    classification = {
        "class_name": "healthy",
        "confidence": 0.95,
        "all_probs": probs,
        "low_confidence": False,
    }
    stage1 = {"is_diseased": True, "confidence": 0.8}
    out = _apply_postprocessing_rules(classification, stage1, list(probs.keys()))

    assert "detector_classifier_mismatch" in out["warnings"]
    # Healthy was confident enough that we don't ALSO flag uncertain_healthy.
    assert "uncertain_healthy" not in out["warnings"]
