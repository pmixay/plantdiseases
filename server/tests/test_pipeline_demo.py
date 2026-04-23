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
    # All-probs dicts must match exactly.
    assert r1["all_probs"] == r2["all_probs"]


def test_demo_pipeline_result_shape():
    p = PlantDiseasePipeline()
    result = p.analyze(_seeded_image(seed=3))

    for key in ("class_name", "confidence", "all_probs", "detection", "pipeline_mode", "elapsed_ms"):
        assert key in result, f"missing key {key}"

    assert 0.0 <= result["confidence"] <= 1.0
    assert set(result["detection"].keys()) == {"is_diseased", "detector_confidence", "region"}
    assert isinstance(result["all_probs"], dict)
    # Probabilities are rounded to 4 decimals but must sum to roughly 1.
    total = sum(result["all_probs"].values())
    assert 0.95 <= total <= 1.05


def test_class_names_match_default_15():
    from classifier import DEFAULT_CLASS_NAMES, DiseaseClassifier

    clf = DiseaseClassifier(model_path=None)
    assert clf.NUM_CLASSES == 15
    assert clf.CLASS_NAMES == DEFAULT_CLASS_NAMES
