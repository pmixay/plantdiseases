"""
Stage 1 — Leaf detector.

YOLOv8n trained on PlantDoc to localise plant leaves in real photos.
**v4 is single-class:** the detector outputs ``leaf`` bboxes only — the
"is it diseased?" decision is delegated to Stage 2 (classifier). This
fixes a v3.x bias where a skewed two-class detector ('leaf' /
'diseased_leaf') flagged almost every leaf as diseased and propagated
that signal into the postprocessing gate.

The detector REQUIRES trained weights — there is no demo / heuristic
fallback. Production deployments should ship ``detector.onnx``;
``detector.pt`` is loaded transparently when ONNX is not present.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

DETECTOR_IMG_SIZE = 640
DETECTOR_CLASSES = ("leaf",)
MIN_CONFIDENCE = 0.25
MAX_DETECTIONS = 10


class DetectorWeightsMissing(RuntimeError):
    """Raised when no usable detector weights are found at startup."""


def _resolve_detector_path(model_path: Path | None) -> Path | None:
    """Prefer detector.onnx (production) over detector.pt when both exist."""
    if model_path is None:
        return None
    parent = model_path.parent
    onnx = parent / "detector.onnx"
    if onnx.exists():
        return onnx
    if model_path.exists():
        return model_path
    return None


class LeafDetector:
    """YOLOv8n-based single-class leaf detector. Strict — needs weights."""

    def __init__(self, model_path: Path | None = None):
        self.model = None
        self.device = "cpu"
        self._model_path = model_path

        resolved = _resolve_detector_path(model_path)
        if resolved is None:
            raise DetectorWeightsMissing(
                "No detector weights found. Place detector.pt or detector.onnx "
                "in models/ before starting the server."
            )
        self._load_model(resolved)

    def _load_model(self, path: Path) -> None:
        from ultralytics import YOLO  # type: ignore

        self.model = YOLO(str(path))
        # Warm up so the first request isn't paying JIT/init cost.
        self.model.predict(
            np.zeros((DETECTOR_IMG_SIZE, DETECTOR_IMG_SIZE, 3), dtype=np.uint8),
            imgsz=DETECTOR_IMG_SIZE, verbose=False, device=self.device,
        )
        logger.info("YOLOv8 detector loaded from %s", path)

    # ── public API ───────────────────────────────────────────────────

    def detect(self, img: Image.Image) -> dict:
        """Run detection on a PIL image.

        Returns a dict with the detections list (sorted by confidence
        descending), the top confidence, and the highest-confidence
        bbox the pipeline can promote to the primary region.
        """
        arr = np.array(img)
        results = self.model.predict(
            arr,
            imgsz=DETECTOR_IMG_SIZE,
            conf=MIN_CONFIDENCE,
            iou=0.45,
            max_det=MAX_DETECTIONS,
            verbose=False,
            device=self.device,
        )
        if not results:
            return _empty_result()
        r = results[0]
        if r.boxes is None or len(r.boxes) == 0:
            return _empty_result()

        boxes_xyxy = r.boxes.xyxy.cpu().numpy()
        confs = r.boxes.conf.cpu().numpy()

        detections: list[dict] = []
        for (x1, y1, x2, y2), conf in zip(boxes_xyxy, confs, strict=False):
            detections.append({
                "x": int(max(0, round(x1))),
                "y": int(max(0, round(y1))),
                "width": int(max(1, round(x2 - x1))),
                "height": int(max(1, round(y2 - y1))),
                "class": "leaf",
                "confidence": round(float(conf), 4),
            })
        detections.sort(key=lambda d: -d["confidence"])
        return _build_result(detections)


def _empty_result() -> dict:
    return {
        "detections": [],
        "confidence": 0.0,
        "primary_box": None,
    }


def _build_result(detections: list[dict]) -> dict:
    if not detections:
        return _empty_result()
    primary: Optional[dict] = detections[0]
    best_conf = max(d["confidence"] for d in detections)
    return {
        "detections": detections,
        "confidence": round(float(best_conf), 4),
        "primary_box": primary,
    }
