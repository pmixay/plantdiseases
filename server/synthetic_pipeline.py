"""Deterministic weight-free pipeline for the isolated local beta stack.

This module is never a production fallback. ``main.py`` constructs it only
after ``beta_runtime.allow_synthetic_pipeline`` proves the explicit test-mode
guard. It exists so a clean checkout can exercise API/process boundaries
without downloading model artefacts or training anything locally.
"""

from __future__ import annotations

from PIL import Image

SYNTHETIC_CLASS_NAMES = [
    "gray_mold_botrytis",
    "healthy",
    "leaf_scorch_underwatering",
    "leaf_spot",
    "not_a_plant",
    "nutrient_deficiency",
    "pests",
    "powdery_mildew",
    "viral_disease",
]


class _SyntheticClassifier:
    backend = "synthetic-test-only"
    architecture = "synthetic-test-only"
    model_family = "gamma"
    img_size = 224
    CLASS_NAMES = SYNTHETIC_CLASS_NAMES
    NUM_CLASSES = len(SYNTHETIC_CLASS_NAMES)


class _SyntheticDetector:
    is_loaded = True


class SyntheticPipeline:
    """Mirrors the serving response shape with a fixed low-confidence result."""

    def __init__(self):
        self.classifier = _SyntheticClassifier()
        self.detector = _SyntheticDetector()

    def analyze(self, _image: Image.Image) -> dict:
        names = self.classifier.CLASS_NAMES
        base_probability = 1.0 / len(names)
        probabilities = {name: base_probability for name in names}
        probabilities["healthy"] += 0.1
        total = sum(probabilities.values())
        probabilities = {name: round(value / total, 6) for name, value in probabilities.items()}
        top_k = sorted(probabilities.items(), key=lambda item: -item[1])[:3]
        return {
            "class_name": "healthy",
            "confidence": probabilities["healthy"],
            "all_probs": probabilities,
            "top_k": [{"class": name, "confidence": value} for name, value in top_k],
            "warnings": ["synthetic_test_result"],
            "severity": "low",
            "prediction_set": None,
            "detection": {
                "is_diseased": False,
                "detector_confidence": 0.0,
                "regions": [],
                "primary_region": None,
            },
            "elapsed_ms": 0.0,
        }
