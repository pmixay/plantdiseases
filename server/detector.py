"""
Stage 1 — Plant-leaf detector.

YOLOv8n trained on **indoor leaf bboxes** (300–800 hand-labelled
indoor photos plus targeted Roboflow Universe houseplant slices, NOT
PlantDoc — agricultural close-ups bias localisation toward field
shots and mis-fire on wide indoor compositions).

The detector is single-class (``plant_leaf``). The "is it diseased?"
decision is delegated 100 % to Stage 2 (classifier).

Loading is fail-fast: if no usable artefact is found, ``__init__``
raises ``DetectorLoadError`` so the API never boots in a half-broken
state. There is no heuristic / demo fallback path.

ONNX is loaded transparently: ``YOLO('detector.onnx')`` works exactly
like the .pt path. Production deployments should ship ``detector.onnx``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

DETECTOR_IMG_SIZE = 640
DETECTOR_CLASSES = ("plant_leaf",)
# Indoor-trained detector — recall-biased so that wide-frame indoor
# compositions don't drop the only leaf bbox. The OOD gate downstream
# (detector confidence + classifier MaxLogit) cleans up false positives.
MIN_CONFIDENCE = 0.15
MAX_DETECTIONS = 10


class DetectorLoadError(RuntimeError):
    """Raised when no usable detector artefact can be loaded."""


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
        self.device = "cpu"

        resolved = _resolve_detector_path(model_path)
        if resolved is None:
            raise DetectorLoadError(
                "Cannot start without a trained detector. "
                "Expected detector.onnx or detector.pt next to the configured model path."
            )
        try:
            self._load_model(resolved)
        except Exception as e:
            raise DetectorLoadError(f"Failed to load YOLO detector {resolved}: {e}") from e

    @property
    def is_loaded(self) -> bool:
        return self.model is not None

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
        # strict=True: a length mismatch between bboxes and confidences would
        # be an upstream YOLO bug, not something to silently mask.
        for (x1, y1, x2, y2), conf in zip(boxes_xyxy, confs, strict=True):
            detections.append({
                "x": int(max(0, round(x1))),
                "y": int(max(0, round(y1))),
                "width": int(max(1, round(x2 - x1))),
                "height": int(max(1, round(y2 - y1))),
                "class": "plant_leaf",
                "confidence": round(float(conf), 4),
            })
        detections.sort(key=lambda d: -d["confidence"])
        return self._build_result(detections)

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
