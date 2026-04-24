"""
Stage 1 — Leaf / Diseased-Region Detector.

YOLOv8-nano detector trained to localise plant leaves in real-world photos
and flag the ones that look diseased. Outputs bounding boxes with class and
confidence; the pipeline then crops the highest-confidence disease box for
Stage 2.

Two detector classes:
    0 → "leaf"           — visibly healthy leaf
    1 → "diseased_leaf"  — leaf with visible disease symptoms

When no trained weights are present, the detector falls back to a
deterministic HSV colour heuristic that still produces a plausible bbox so
the server is usable in demo mode.
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
DETECTOR_CLASSES = ("leaf", "diseased_leaf")
MIN_CONFIDENCE = 0.25
MAX_DETECTIONS = 10


class LeafDetector:
    """YOLOv8n-based leaf / diseased-leaf detector."""

    def __init__(self, model_path: Path | None = None):
        self.model = None
        self.is_loaded = False
        self.device = "cpu"
        self._model_path = model_path

        if model_path and model_path.exists():
            try:
                self._load_model(model_path)
            except Exception as e:
                logger.error("Failed to load YOLO detector: %s", e)

        if not self.is_loaded:
            logger.warning("Detector: no YOLO weights found, running in DEMO mode")

    def _load_model(self, path: Path) -> None:
        # Lazy import keeps the ultralytics dependency optional at runtime.
        from ultralytics import YOLO  # type: ignore

        self.model = YOLO(str(path))
        # Warm up so the first request isn't paying JIT cost.
        self.model.predict(
            np.zeros((DETECTOR_IMG_SIZE, DETECTOR_IMG_SIZE, 3), dtype=np.uint8),
            imgsz=DETECTOR_IMG_SIZE,
            verbose=False,
            device=self.device,
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
                         in original-image pixel coordinates. Sorted by
                         (diseased first, then confidence desc).
            is_diseased : bool   — any "diseased_leaf" detection above threshold
            confidence  : float  — best confidence across all detections
            primary_box : dict | None — the bbox the classifier should crop
        """
        if self.is_loaded:
            return self._detect_real(img)
        return self._detect_demo(img)

    # ── real inference (YOLOv8) ──────────────────────────────────────

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
        cls_ids = r.boxes.cls.cpu().numpy().astype(int)

        detections = []
        for (x1, y1, x2, y2), conf, cls_id in zip(boxes_xyxy, confs, cls_ids):
            cls_id = int(cls_id)
            class_name = (
                DETECTOR_CLASSES[cls_id]
                if 0 <= cls_id < len(DETECTOR_CLASSES)
                else f"class_{cls_id}"
            )
            detections.append({
                "x": int(max(0, round(x1))),
                "y": int(max(0, round(y1))),
                "width": int(max(1, round(x2 - x1))),
                "height": int(max(1, round(y2 - y1))),
                "class": class_name,
                "confidence": round(float(conf), 4),
            })

        # Prefer diseased boxes, then highest confidence.
        detections.sort(
            key=lambda d: (0 if d["class"] == "diseased_leaf" else 1, -d["confidence"])
        )
        return self._build_result(detections)

    # ── demo mode (HSV heuristics) ───────────────────────────────────

    def _detect_demo(self, img: Image.Image) -> dict:
        arr = np.array(img.resize((320, 320)))
        hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)

        # Plant-ish pixels (any green hue, reasonable saturation & value)
        green_mask = cv2.inRange(hsv, (25, 30, 30), (95, 255, 255))
        # Disease-ish pixels (brown / yellow / white / dark lesions)
        brown = cv2.inRange(hsv, (8, 40, 40), (25, 210, 190))
        yellow = cv2.inRange(hsv, (20, 80, 100), (35, 255, 255))
        white = cv2.inRange(hsv, (0, 0, 180), (180, 50, 255))
        dark = cv2.inRange(hsv, (0, 0, 0), (180, 255, 50))
        disease_mask = cv2.bitwise_or(cv2.bitwise_or(brown, yellow), cv2.bitwise_or(white, dark))
        disease_mask = cv2.bitwise_and(disease_mask, green_mask)  # only within leaf area

        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        green_mask = cv2.morphologyEx(green_mask, cv2.MORPH_OPEN, kernel)
        disease_mask = cv2.morphologyEx(disease_mask, cv2.MORPH_CLOSE, kernel)

        green_ratio = float(green_mask.sum()) / 255.0 / green_mask.size
        disease_ratio = float(disease_mask.sum()) / 255.0 / disease_mask.size

        # No plant → probably "not_a_plant" scenario
        if green_ratio < 0.05:
            return self._empty_result()

        sx = img.width / 320.0
        sy = img.height / 320.0
        detections: list[dict] = []

        # Largest leaf contour
        contours, _ = cv2.findContours(green_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if contours:
            largest = max(contours, key=cv2.contourArea)
            if cv2.contourArea(largest) > 500:
                gx, gy, gw, gh = cv2.boundingRect(largest)
                detections.append({
                    "x": int(gx * sx),
                    "y": int(gy * sy),
                    "width": int(max(1, gw * sx)),
                    "height": int(max(1, gh * sy)),
                    "class": "leaf",
                    "confidence": round(float(min(0.95, 0.60 + green_ratio)), 4),
                })

        # Diseased-region contour (if any)
        if disease_ratio > 0.02:
            d_contours, _ = cv2.findContours(disease_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if d_contours:
                d_largest = max(d_contours, key=cv2.contourArea)
                dx, dy, dw, dh = cv2.boundingRect(d_largest)
                # pad out a bit so the classifier sees some healthy context
                pad = 0.2
                dx = max(0, int((dx - dw * pad) * sx))
                dy = max(0, int((dy - dh * pad) * sy))
                dw = int(min(img.width - dx, dw * (1 + 2 * pad) * sx))
                dh = int(min(img.height - dy, dh * (1 + 2 * pad) * sy))
                detections.append({
                    "x": dx,
                    "y": dy,
                    "width": max(1, dw),
                    "height": max(1, dh),
                    "class": "diseased_leaf",
                    "confidence": round(float(min(0.90, 0.50 + disease_ratio * 3)), 4),
                })

        detections.sort(
            key=lambda d: (0 if d["class"] == "diseased_leaf" else 1, -d["confidence"])
        )
        return self._build_result(detections)

    # ── helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _empty_result() -> dict:
        return {
            "detections": [],
            "is_diseased": False,
            "confidence": 0.0,
            "primary_box": None,
        }

    @staticmethod
    def _build_result(detections: list[dict]) -> dict:
        if not detections:
            return LeafDetector._empty_result()

        diseased = [d for d in detections if d["class"] == "diseased_leaf"]
        primary: Optional[dict] = diseased[0] if diseased else detections[0]
        best_conf = max(d["confidence"] for d in detections)

        return {
            "detections": detections,
            "is_diseased": bool(diseased),
            "confidence": round(float(best_conf), 4),
            "primary_box": primary,
        }
