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

Post-processing rules applied after the classifier runs:

* ``healthy`` predictions require extra evidence. Because ``healthy`` is
  the default class when nothing obvious is wrong, we only surface it as
  a confident "green" badge when the top-1 probability is at least
  ``HEALTHY_MIN_CONFIDENCE`` and its margin over the runner-up is at
  least ``HEALTHY_MARGIN``. Otherwise we re-route the top label to the
  runner-up disease and attach a ``uncertain_healthy`` warning so the UI
  can show the alternatives.
* If the detector flagged a ``diseased_leaf`` bbox but the classifier
  still landed on ``healthy``, we attach a ``detector_classifier_mismatch``
  warning so the UI can prompt for a re-scan.
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

# ``healthy`` is the default class when nothing obvious is wrong, so we
# require it to clear a higher bar than any disease class. These match
# the README's "uncertain healthy" contract — tweaking them is the
# quickest lever for cutting false-healthy reports.
HEALTHY_MIN_CONFIDENCE = 0.85
HEALTHY_MARGIN = 0.20

# Number of ranked alternatives (including top-1) that the API surfaces
# so the Android UI can build the "why this diagnosis" card.
TOP_K_ALTERNATIVES = 3


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
            class_name          : str   — final top-1 class after rules
            confidence          : float — final top-1 probability
            all_probs           : dict  — per-class probabilities
            top_k               : list  — [{class, confidence}, ...] top-3
            warnings            : list[str] — diagnostic flags for the UI
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
            top_k = _build_top_k(probs, TOP_K_ALTERNATIVES)
            return {
                "class_name": "not_a_plant",
                "confidence": 1.0,
                "all_probs": probs,
                "top_k": top_k,
                "warnings": [],
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

        final = _apply_postprocessing_rules(
            classification, stage1, self.classifier.CLASS_NAMES,
        )

        elapsed = (time.perf_counter() - t0) * 1000
        return {
            "class_name": final["class_name"],
            "confidence": final["confidence"],
            "all_probs": final["all_probs"],
            "top_k": final["top_k"],
            "warnings": final["warnings"],
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


# ── pure helpers (exposed for tests) ─────────────────────────────────

def _build_top_k(probs: dict[str, float], k: int) -> list[dict]:
    """Return the top-k class/confidence pairs sorted by descending probability."""
    ordered = sorted(probs.items(), key=lambda item: item[1], reverse=True)[:k]
    return [
        {"class": name, "confidence": round(float(conf), 4)}
        for name, conf in ordered
    ]


def _apply_postprocessing_rules(
    classification: dict,
    stage1: dict,
    class_names: list[str],
) -> dict:
    """Refine the classifier output with healthy/margin and detector rules.

    The returned dict carries:
      * class_name, confidence     — possibly re-routed top-1
      * all_probs                   — unchanged; the UI renders this
      * top_k                       — ranked top-3 (before re-routing)
      * warnings                    — list of short flag strings
    """
    probs = classification["all_probs"]
    top_k = _build_top_k(probs, TOP_K_ALTERNATIVES)
    warnings: list[str] = []

    # Sorted snapshot so we can reason about top-1 / top-2 without
    # duplicating the sort. argmax/partition on the numpy array would
    # work too but this keeps the names visible in the warning payload.
    ordered = sorted(probs.items(), key=lambda item: item[1], reverse=True)
    top1_name, top1_prob = ordered[0]
    top2_name, top2_prob = (ordered[1] if len(ordered) > 1 else (top1_name, 0.0))

    final_name = classification["class_name"]
    final_conf = classification["confidence"]

    if top1_name == "healthy" and "healthy" in class_names:
        margin = top1_prob - top2_prob
        low_conf = top1_prob < HEALTHY_MIN_CONFIDENCE
        low_margin = margin < HEALTHY_MARGIN
        if low_conf or low_margin:
            warnings.append("uncertain_healthy")
            # Re-route to the most likely disease (i.e. any non-healthy
            # runner-up). This is what the UI should present as the
            # actionable diagnosis; the raw probability dict still shows
            # healthy so the user can see the model's hesitation.
            fallback = next(
                (name for name, _ in ordered if name != "healthy"), None,
            )
            if fallback is not None:
                final_name = fallback
                final_conf = float(probs[fallback])
            logger.info(
                "healthy gate failed  top1=%s (%.3f) top2=%s (%.3f)  low_conf=%s low_margin=%s",
                top1_name, top1_prob, top2_name, top2_prob, low_conf, low_margin,
            )

    # Detector said "diseased_leaf" but classifier stayed on healthy →
    # surface that disagreement so the user can retake the photo.
    if stage1.get("is_diseased") and final_name == "healthy":
        warnings.append("detector_classifier_mismatch")

    return {
        "class_name": final_name,
        "confidence": round(float(final_conf), 4),
        "all_probs": probs,
        "top_k": top_k,
        "warnings": warnings,
    }
