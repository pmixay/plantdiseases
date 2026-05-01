"""
Stage 1 — Leaf detector.

YOLOv8n trained on PlantDoc to localise plant leaves in real photos.
**v4 is single-class:** the detector outputs ``leaf`` bboxes only — the
"is it diseased?" decision is delegated to Stage 2 (classifier). This
fixes a v3.x bias where a skewed two-class detector ('leaf' /
'diseased_leaf') flagged almost every leaf as diseased and propagated
that signal into the postprocessing gate.

When no trained weights are present, the detector falls back to a
deterministic HSV colour heuristic so the server is usable in demo
mode. The heuristic also returns a single ``leaf`` bbox.

ONNX is loaded transparently: ``YOLO('detector.onnx')`` works exactly
like the .pt path. Production deployments should ship ``detector.onnx``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

DETECTOR_IMG_SIZE = 640
DETECTOR_CLASSES = ("leaf",)
# Detector training data (PlantDoc) is heavy on close-up agricultural
# leaves and short on indoor wide-frame compositions, so we bias toward
# higher recall at inference. The OOD gate downstream (detector
# confidence + classifier MSP) cleans up the extra false positives.
MIN_CONFIDENCE = 0.15
MAX_DETECTIONS = 10


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
    """YOLOv8n-based single-class leaf detector."""

    def __init__(self, model_path: Path | None = None):
        self.model = None
        self.is_loaded = False
        self.device = "cpu"
        self._model_path = model_path

        resolved = _resolve_detector_path(model_path)
        if resolved is not None:
            try:
                self._load_model(resolved)
            except Exception as e:
                logger.error("Failed to load YOLO detector %s: %s", resolved, e)

        if not self.is_loaded:
            logger.warning("Detector: no YOLO weights found, running in DEMO mode")

    def _load_model(self, path: Path) -> None:
        from ultralytics import YOLO  # type: ignore

        self.model = YOLO(str(path))
        # Warm up so the first request isn't paying JIT/init cost.
        self.model.predict(
            np.zeros((DETECTOR_IMG_SIZE, DETECTOR_IMG_SIZE, 3), dtype=np.uint8),
            imgsz=DETECTOR_IMG_SIZE, verbose=False, device=self.device,
        )
        self.is_loaded = True
        logger.info("YOLOv8 detector loaded from %s", path)

    # ── public API ───────────────────────────────────────────────────

    def detect(self, img: Image.Image) -> dict:
        """Run detection on a PIL image.

        Returns
        -------
        dict with:
            detections : list of {x, y, width, height, class, confidence}
                         (bboxes in original-image pixel coordinates,
                          sorted by confidence descending)
            confidence : float — best confidence across detections
            primary_box : dict | None — highest-confidence bbox; the
                          pipeline may override this after consulting
                          the classifier (disease-aware selection).
        """
        if self.is_loaded:
            return self._detect_real(img)
        return self._detect_demo(img)

    # ── real inference (YOLOv8 / ONNX via Ultralytics) ───────────────

    def _detect_real(self, img: Image.Image) -> dict:
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
            return self._empty_result()
        r = results[0]
        if r.boxes is None or len(r.boxes) == 0:
            return self._empty_result()

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
        return self._build_result(detections)

    # ── demo mode (HSV heuristic, single leaf bbox) ──────────────────

    def _detect_demo(self, img: Image.Image) -> dict:
        arr = np.array(img.resize((320, 320)))
        hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)
        green_mask = cv2.inRange(hsv, (25, 30, 30), (95, 255, 255))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        green_mask = cv2.morphologyEx(green_mask, cv2.MORPH_OPEN, kernel)

        green_ratio = float(green_mask.sum()) / 255.0 / green_mask.size
        if green_ratio < 0.05:
            return self._empty_result()

        sx = img.width / 320.0
        sy = img.height / 320.0
        contours, _ = cv2.findContours(green_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return self._empty_result()

        largest = max(contours, key=cv2.contourArea)
        if cv2.contourArea(largest) <= 500:
            return self._empty_result()

        gx, gy, gw, gh = cv2.boundingRect(largest)
        det = {
            "x": int(gx * sx),
            "y": int(gy * sy),
            "width": int(max(1, gw * sx)),
            "height": int(max(1, gh * sy)),
            "class": "leaf",
            "confidence": round(float(min(0.95, 0.60 + green_ratio)), 4),
        }
        return self._build_result([det])

    # ── helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _empty_result() -> dict:
        return {
            "detections": [],
            "confidence": 0.0,
            "primary_box": None,
        }

    @staticmethod
    def _build_result(detections: list[dict]) -> dict:
        if not detections:
            return LeafDetector._empty_result()
        primary: Optional[dict] = detections[0]
        best_conf = max(d["confidence"] for d in detections)
        return {
            "detections": detections,
            "confidence": round(float(best_conf), 4),
            "primary_box": primary,
        }
