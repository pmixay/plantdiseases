"""
Two-stage houseplant disease detection pipeline.

Stage 1 (Detector)   — YOLOv8n. Locates leaves and flags diseased regions.
                       Produces a list of bounding boxes in the original
                       image's pixel space.
Stage 2 (Classifier) — EfficientNetV2-S. Classifies the highest-confidence
                       disease bbox into one of 9 houseplant-focused
                       disease classes (including ``not_a_plant``).

The pipeline is deliberately tolerant to real-world noise: when Stage 1
finds no plant in the frame, the final label is set to ``not_a_plant``
and the user is asked to retake the photo.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

from PIL import Image

from classifier import DiseaseClassifier
from detector import LeafDetector

logger = logging.getLogger(__name__)

MIN_CROP_SIZE = 32
CROP_PADDING_RATIO = 0.10


class PlantDiseasePipeline:
    """Orchestrates the two-stage detection → classification pipeline."""

    def __init__(
        self,
        detector_path: Path | None = None,
        classifier_path: Path | None = None,
    ):
        self.detector = LeafDetector(detector_path)
        self.classifier = DiseaseClassifier(classifier_path)

        logger.info(
            "Pipeline ready  detector=%s  classifier=%s",
            "MODEL" if self.detector.is_loaded else "DEMO",
            "MODEL" if self.classifier.is_loaded else "DEMO",
        )

    @property
    def is_loaded(self) -> bool:
        return self.detector.is_loaded or self.classifier.is_loaded

    @property
    def mode(self) -> str:
        if self.detector.is_loaded and self.classifier.is_loaded:
            return "full"
        if self.detector.is_loaded or self.classifier.is_loaded:
            return "partial"
        return "demo"

    # ── main entry point ─────────────────────────────────────────────

    def analyze(self, img: Image.Image) -> dict:
        """Run the full two-stage pipeline on a PIL image.

        Returns
        -------
        dict with keys:
            class_name          : str   — predicted Stage 2 class
            confidence          : float — Stage 2 top-1 probability
            all_probs           : dict  — per-class probabilities
            detection           : dict  — Stage 1 output
                is_diseased         : bool
                detector_confidence : float
                regions             : list[dict]  (bbox + class + confidence)
                primary_region      : dict | None  (bbox used by Stage 2)
            pipeline_mode       : str   — "full" | "partial" | "demo"
            elapsed_ms          : float
        """
        t0 = time.perf_counter()

        stage1 = self.detector.detect(img)
        detections = stage1["detections"]
        primary = stage1["primary_box"]

        logger.info(
            "Stage 1 → detections=%d  diseased=%s  best_conf=%.3f",
            len(detections),
            stage1["is_diseased"],
            stage1["confidence"],
        )

        # No plant at all in frame — short-circuit to "not_a_plant"
        if not detections:
            elapsed = (time.perf_counter() - t0) * 1000
            probs = {n: 0.0 for n in self.classifier.CLASS_NAMES}
            if "not_a_plant" in probs:
                probs["not_a_plant"] = 1.0
            return {
                "class_name": "not_a_plant",
                "confidence": 1.0,
                "all_probs": probs,
                "detection": {
                    "is_diseased": False,
                    "detector_confidence": 0.0,
                    "regions": [],
                    "primary_region": None,
                },
                "pipeline_mode": self.mode,
                "elapsed_ms": round(elapsed, 1),
            }

        crop = self._extract_crop(img, primary)
        classification = self.classifier.classify(crop)

        logger.info(
            "Stage 2 → class=%s  confidence=%.3f  low_conf=%s",
            classification["class_name"],
            classification["confidence"],
            classification.get("low_confidence"),
        )

        elapsed = (time.perf_counter() - t0) * 1000
        return {
            "class_name": classification["class_name"],
            "confidence": classification["confidence"],
            "all_probs": classification["all_probs"],
            "detection": {
                "is_diseased": stage1["is_diseased"],
                "detector_confidence": stage1["confidence"],
                "regions": detections,
                "primary_region": primary,
            },
            "pipeline_mode": self.mode,
            "elapsed_ms": round(elapsed, 1),
        }

    # ── helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _extract_crop(img: Image.Image, region: Optional[dict]) -> Image.Image:
        """Crop the image to ``region`` with a small padding margin.

        Falls back to the full image when the region is missing or too small.
        """
        if region is None:
            return img

        x = int(region.get("x", 0))
        y = int(region.get("y", 0))
        w = int(region.get("width", 0))
        h = int(region.get("height", 0))

        if w < MIN_CROP_SIZE or h < MIN_CROP_SIZE:
            return img

        pad_x = int(w * CROP_PADDING_RATIO)
        pad_y = int(h * CROP_PADDING_RATIO)
        x1 = max(0, x - pad_x)
        y1 = max(0, y - pad_y)
        x2 = min(img.width, x + w + pad_x)
        y2 = min(img.height, y + h + pad_y)
        if x2 <= x1 or y2 <= y1:
            return img
        return img.crop((x1, y1, x2, y2))
