"""
Two-stage plant disease detection pipeline.

Stage 1 (Detector)   — MobileNetV3-Small binary head + Grad-CAM.
Stage 2 (Classifier) — EfficientNet-B0 fine-grained classifier, run on
                       the ROI produced by Stage 1 (or the full image as
                       a fallback).
"""

import logging
import time
from pathlib import Path

from PIL import Image

from classifier import DiseaseClassifier
from detector import DiseaseDetector

logger = logging.getLogger(__name__)

# Classifier receives the ROI unless it is smaller than this.
MIN_CROP_SIZE = 32

# Early-exit threshold for the "healthy" branch of Stage 1.
HEALTHY_SKIP_THRESHOLD = 0.65


class PlantDiseasePipeline:
    """Orchestrates the two-stage detection → classification pipeline."""

    def __init__(
        self,
        detector_path: Path | None = None,
        classifier_path: Path | None = None,
    ):
        self.detector = DiseaseDetector(detector_path)
        self.classifier = DiseaseClassifier(classifier_path)

        logger.info(
            "Pipeline ready  detector=%s  classifier=%s",
            "MODEL" if self.detector.is_loaded else "DEMO",
            "MODEL" if self.classifier.is_loaded else "DEMO",
        )

    @property
    def is_loaded(self) -> bool:
        """True when at least one real model is loaded."""
        return self.detector.is_loaded or self.classifier.is_loaded

    @property
    def mode(self) -> str:
        if self.detector.is_loaded and self.classifier.is_loaded:
            return "full"
        if self.detector.is_loaded or self.classifier.is_loaded:
            return "partial"
        return "demo"

    # ── Main entry point ─────────────────────────────────────────────

    def analyze(self, img: Image.Image) -> dict:
        """Run the full two-stage pipeline on a PIL image.

        Returns
        -------
        dict with keys:
            class_name          : str   – predicted disease (or "healthy")
            confidence          : float
            all_probs           : dict  – per-class probabilities
            detection           : dict  – Stage 1 output
                is_diseased         : bool
                detector_confidence : float
                region              : dict | None  (x, y, width, height)
            pipeline_mode       : str   – "full" | "partial" | "demo"
            elapsed_ms          : float
        """
        t0 = time.perf_counter()

        # ── Stage 1: detect ──────────────────────────────────────────
        detection = self.detector.detect(img)
        logger.info(
            "Stage 1 → diseased=%s  confidence=%.3f  region=%s",
            detection["is_diseased"],
            detection["confidence"],
            "yes" if detection["region"] else "no",
        )

        # Early exit — plant looks healthy
        if not detection["is_diseased"] and detection["confidence"] >= HEALTHY_SKIP_THRESHOLD:
            elapsed = (time.perf_counter() - t0) * 1000
            return {
                "class_name": "healthy",
                "confidence": detection["confidence"],
                "all_probs": {n: 0.0 for n in self.classifier.CLASS_NAMES}
                | {"healthy": detection["confidence"]},
                "detection": {
                    "is_diseased": False,
                    "detector_confidence": detection["confidence"],
                    "region": None,
                },
                "pipeline_mode": self.mode,
                "elapsed_ms": round(elapsed, 1),
            }

        # ── Crop to ROI (or keep full image) ─────────────────────────
        crop = self._extract_crop(img, detection.get("region"))

        # ── Stage 2: classify ────────────────────────────────────────
        classification = self.classifier.classify(crop)
        logger.info(
            "Stage 2 → class=%s  confidence=%.3f",
            classification["class_name"],
            classification["confidence"],
        )

        elapsed = (time.perf_counter() - t0) * 1000
        return {
            "class_name": classification["class_name"],
            "confidence": classification["confidence"],
            "all_probs": classification["all_probs"],
            "detection": {
                "is_diseased": detection["is_diseased"],
                "detector_confidence": detection["confidence"],
                "region": detection["region"],
            },
            "pipeline_mode": self.mode,
            "elapsed_ms": round(elapsed, 1),
        }

    # ── helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _extract_crop(img: Image.Image, region: dict | None) -> Image.Image:
        """Crop the image to the detected region, with safety checks."""
        if region is None:
            return img

        x, y = region["x"], region["y"]
        w, h = region["width"], region["height"]

        if w < MIN_CROP_SIZE or h < MIN_CROP_SIZE:
            return img

        x2 = max(x + 1, min(img.width, x + w))
        y2 = max(y + 1, min(img.height, y + h))
        x = max(0, min(x, img.width - 1))
        y = max(0, min(y, img.height - 1))
        return img.crop((x, y, x2, y2))
