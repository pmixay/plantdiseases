"""
Two-stage houseplant disease detection pipeline (v4).

Stage 1 (Detector)   — YOLOv8n single-class. Locates ``leaf`` bboxes.
Stage 2 (Classifier) — EfficientNet-B0 (ONNX). Classifies a cropped
                       leaf region into one of up to 8 houseplant
                       condition classes including ``not_a_plant``.

v4 design changes vs v3.x:

* The detector no longer makes a healthy/diseased call — it only
  localises leaves. The classifier owns the disease decision. This
  removes a v3.x bias where a skewed two-class detector reported
  ``diseased_leaf`` on most boxes and pulled the postprocessing gate
  toward "everything is sick."
* When multiple bboxes are returned the pipeline picks the one with
  the highest classifier disease score (max softmax over non-healthy
  / non-not_a_plant classes). If every box is confidently healthy we
  surface the most central bbox.
* The healthy gate is significantly less aggressive. ``HEALTHY_MIN_*``
  are calibrated for an in-distribution houseplant classifier rather
  than a defensive 0.85/0.20 fallback. We **no longer reroute** to a
  runner-up disease — we attach an ``uncertain_healthy`` warning and
  let the UI surface alternatives via ``top_k``.
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

# v4 healthy gate. Previous v3 values (0.85 / 0.20 with auto-rerouting)
# were too aggressive — any healthy plant photographed at low light or
# at an angle would trip the gate and get re-labeled as the strongest
# runner-up disease. Now the gate only attaches a soft warning.
HEALTHY_MIN_CONFIDENCE = 0.55
HEALTHY_MARGIN = 0.10

# Classes the pipeline considers "disease-like" when ranking which
# bbox to feed back to the classifier and to the UI as primary.
_NON_DISEASE = {"healthy", "not_a_plant"}

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
            "Pipeline ready  detector=%s  classifier=%s (%s backend)",
            "MODEL" if self.detector.is_loaded else "DEMO",
            "MODEL" if self.classifier.is_loaded else "DEMO",
            getattr(self.classifier, "backend", "demo"),
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
        t0 = time.perf_counter()

        stage1 = self.detector.detect(img)
        detections = stage1["detections"]

        logger.info(
            "Stage 1 → detections=%d  best_conf=%.3f",
            len(detections), stage1["confidence"],
        )

        # No leaf in frame → short-circuit to ``not_a_plant``.
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

        # Disease-aware primary-box selection: classify each candidate
        # bbox (capped to keep latency bounded), pick the one with the
        # highest non-healthy/non-not_a_plant softmax mass. If none
        # beat the threshold we fall back to the highest-confidence
        # leaf as the primary box.
        primary, classification = self._pick_primary(img, detections)

        is_diseased = (
            classification["class_name"] not in _NON_DISEASE
            and classification["confidence"] >= HEALTHY_MIN_CONFIDENCE * 0.5
        )

        logger.info(
            "Stage 2 → class=%s  confidence=%.3f  primary_box=%s",
            classification["class_name"],
            classification["confidence"],
            primary,
        )

        final = _apply_postprocessing_rules(
            classification, self.classifier.CLASS_NAMES,
        )

        elapsed = (time.perf_counter() - t0) * 1000
        return {
            "class_name": final["class_name"],
            "confidence": final["confidence"],
            "all_probs": final["all_probs"],
            "top_k": final["top_k"],
            "warnings": final["warnings"],
            "detection": {
                "is_diseased": is_diseased,
                "detector_confidence": stage1["confidence"],
                "regions": detections,
                "primary_region": primary,
            },
            "pipeline_mode": self.mode,
            "elapsed_ms": round(elapsed, 1),
        }

    # ── helpers ──────────────────────────────────────────────────────

    # Cap the number of bboxes we re-classify per request. With ONNX
    # INT8 EfficientNet-B0 each call costs ~20-30ms on CPU, so 4 boxes
    # adds ~100ms worst case which is acceptable for our 150ms budget.
    _MAX_RECLASSIFY = 4

    def _pick_primary(self, img: Image.Image, detections: list[dict]):
        """Classify the top-N leaf bboxes and pick the most disease-like.

        Returns ``(primary_box, classification_dict)``. Falls back to
        the highest-confidence detection when nothing scores high on a
        disease class.
        """
        # Sort by detector confidence and cap the count.
        ranked = sorted(detections, key=lambda d: -d["confidence"])[: self._MAX_RECLASSIFY]
        best_disease_score = -1.0
        best_box = ranked[0]
        best_classification: Optional[dict] = None
        results: list[tuple[dict, dict]] = []

        for box in ranked:
            crop = self._extract_crop(img, box)
            cls_res = self.classifier.classify(crop)
            results.append((box, cls_res))
            disease_score = max(
                (
                    p for n, p in cls_res["all_probs"].items()
                    if n not in _NON_DISEASE
                ),
                default=0.0,
            )
            if disease_score > best_disease_score:
                best_disease_score = disease_score
                best_box = box
                best_classification = cls_res

        # If nothing looked obviously diseased, prefer the most central
        # large leaf (helps avoid noisy edge crops on cluttered photos).
        if best_disease_score < 0.30 and len(ranked) > 1:
            W, H = img.size
            cx, cy = W / 2, H / 2
            def central_score(d: dict) -> float:
                bx = d["x"] + d["width"] / 2
                by = d["y"] + d["height"] / 2
                area = d["width"] * d["height"]
                dist2 = (bx - cx) ** 2 + (by - cy) ** 2
                # Larger + more central wins.
                return area - 0.4 * dist2
            best_box, best_classification = max(
                results, key=lambda rc: central_score(rc[0])
            )

        # ``best_classification`` is always set because we ran the loop
        # at least once.
        assert best_classification is not None
        return best_box, best_classification

    @staticmethod
    def _extract_crop(img: Image.Image, region: Optional[dict]) -> Image.Image:
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
    ordered = sorted(probs.items(), key=lambda item: item[1], reverse=True)[:k]
    return [
        {"class": name, "confidence": round(float(conf), 4)}
        for name, conf in ordered
    ]


def _apply_postprocessing_rules(
    classification: dict,
    class_names: list[str],
) -> dict:
    """Soft postprocessing: warnings only, no class re-routing.

    Two checks remain:
      * ``uncertain_healthy`` — top-1 is healthy but confidence/margin
        below the v4 thresholds. We surface alternatives but do NOT
        rewrite the predicted class. (v3 used to swap to the runner-up
        disease which forced false-positive disease reports.)
      * ``low_confidence`` — top-1 below ``LOW_CONFIDENCE_THRESHOLD``.
        Lets the UI tone down the badge without fighting the model.
    """
    probs = classification["all_probs"]
    top_k = _build_top_k(probs, TOP_K_ALTERNATIVES)
    warnings: list[str] = []

    ordered = sorted(probs.items(), key=lambda item: item[1], reverse=True)
    top1_name, top1_prob = ordered[0]
    top2_prob = ordered[1][1] if len(ordered) > 1 else 0.0

    if top1_name == "healthy" and "healthy" in class_names:
        margin = top1_prob - top2_prob
        if top1_prob < HEALTHY_MIN_CONFIDENCE or margin < HEALTHY_MARGIN:
            warnings.append("uncertain_healthy")
            logger.info(
                "healthy gate soft-warning  top1=%.3f  margin=%.3f",
                top1_prob, margin,
            )

    if classification.get("low_confidence"):
        warnings.append("low_confidence")

    return {
        "class_name": classification["class_name"],
        "confidence": round(float(classification["confidence"]), 4),
        "all_probs": probs,
        "top_k": top_k,
        "warnings": warnings,
    }
