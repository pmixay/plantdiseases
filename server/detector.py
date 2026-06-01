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

ONNX is loaded transparently via raw onnxruntime. Production deployments
must ship ``detector.onnx``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

DETECTOR_IMG_SIZE = 640
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
    if model_path.suffix.lower() == ".onnx" and model_path.exists():
        return model_path
    alternative = model_path.with_suffix(".onnx")
    if alternative.exists():
        return alternative
    return None


def nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> list[int]:
    """Pure NumPy Non-Maximum Suppression (NMS)."""
    if len(boxes) == 0:
        return []
    x1 = boxes[:, 0]
    y1 = boxes[:, 1]
    x2 = boxes[:, 2]
    y2 = boxes[:, 3]
    areas = (x2 - x1) * (y2 - y1)
    order = scores.argsort()[::-1]

    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(i)
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])

        w = np.maximum(0.0, xx2 - xx1)
        h = np.maximum(0.0, yy2 - yy1)
        inter = w * h
        ovr = inter / (areas[i] + areas[order[1:]] - inter)

        inds = np.where(ovr <= iou_threshold)[0]
        order = order[inds + 1]
    return keep


def letterbox(img: Image.Image, target_size: int = 640) -> tuple[np.ndarray, float, tuple[float, float]]:
    """Resize keeping aspect ratio, and pad to target_size x target_size."""
    w, h = img.size
    scale = target_size / max(w, h)
    new_w = int(round(w * scale))
    new_h = int(round(h * scale))
    resized = img.resize((new_w, new_h), Image.BILINEAR)

    # 114 is the standard YOLO grey padding
    new_img = Image.new("RGB", (target_size, target_size), (114, 114, 114))
    pad_x = (target_size - new_w) // 2
    pad_y = (target_size - new_h) // 2
    new_img.paste(resized, (pad_x, pad_y))

    arr = np.asarray(new_img, dtype=np.float32) / 255.0
    arr = arr.transpose(2, 0, 1)  # HWC to CHW
    arr = np.ascontiguousarray(arr[None])  # NCHW
    return arr, scale, (pad_x, pad_y)


class LeafDetector:
    """YOLOv8n-based single-class leaf detector running on raw ONNX Runtime."""

    def __init__(self, model_path: Path | None = None):
        self.model = None
        self.input_name = ""

        resolved = _resolve_detector_path(model_path)
        if resolved is None:
            raise DetectorLoadError(
                "Cannot start without a trained detector ONNX model. "
                "Expected detector.onnx next to the configured model path."
            )
        try:
            self._load_model(resolved)
        except Exception as e:
            raise DetectorLoadError(f"Failed to load YOLO ONNX detector {resolved}: {e}") from e

    @property
    def is_loaded(self) -> bool:
        return self.model is not None

    def _load_model(self, path: Path) -> None:
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        self.model = ort.InferenceSession(
            str(path),
            sess_options=opts,
            providers=["CPUExecutionProvider"],
        )
        self.input_name = self.model.get_inputs()[0].name

        # Warm up so the first request isn't paying JIT/init cost.
        dummy_input = np.zeros((1, 3, DETECTOR_IMG_SIZE, DETECTOR_IMG_SIZE), dtype=np.float32)
        self.model.run(None, {self.input_name: dummy_input})
        logger.info("ONNX YOLOv8 detector loaded from %s", path)

    # ── public API ───────────────────────────────────────────────────

    def detect(self, img: Image.Image) -> dict:
        """Run detection on a PIL image using ONNX Runtime.

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
        # Force 3-channel RGB. A PNG upload can arrive as RGBA/P (4-channel
        # or palette) and a grayscale JPEG as single-channel L; passing
        # those straight to the onnxruntime session changes the channel
        # count/order the detector was trained on and corrupts Stage 1.
        if img.mode != "RGB":
            img = img.convert("RGB")
        orig_w, orig_h = img.size
        input_data, scale, (pad_x, pad_y) = letterbox(img, DETECTOR_IMG_SIZE)

        outputs = self.model.run(None, {self.input_name: input_data})
        output = outputs[0][0]  # Shape (5, 8400) for single-class YOLOv8

        confs = output[4, :]
        indices = np.where(confs >= MIN_CONFIDENCE)[0]
        if len(indices) == 0:
            return self._empty_result()

        boxes = output[:4, indices].T  # Shape (N, 4)
        confs = confs[indices]

        # Convert cx, cy, w, h to x1, y1, x2, y2 (640x640 space)
        cx, cy, w, h = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
        x1 = cx - w / 2.0
        y1 = cy - h / 2.0
        x2 = cx + w / 2.0
        y2 = cy + h / 2.0
        xyxy = np.stack([x1, y1, x2, y2], axis=1)

        # Run NMS
        keep = nms(xyxy, confs, iou_threshold=0.45)
        if not keep:
            return self._empty_result()

        xyxy = xyxy[keep]
        confs = confs[keep]

        detections: list[dict] = []
        for i in range(len(xyxy)):
            box = xyxy[i]
            conf = confs[i]

            # Map back to original image space
            bx1 = (box[0] - pad_x) / scale
            by1 = (box[1] - pad_y) / scale
            bx2 = (box[2] - pad_x) / scale
            by2 = (box[3] - pad_y) / scale

            # Clip coordinates to original image bounds
            bx1 = max(0.0, min(bx1, float(orig_w)))
            by1 = max(0.0, min(by1, float(orig_h)))
            bx2 = max(0.0, min(bx2, float(orig_w)))
            by2 = max(0.0, min(by2, float(orig_h)))

            bw = bx2 - bx1
            bh = by2 - by1

            if bw >= 1 and bh >= 1:
                detections.append({
                    "x": int(round(bx1)),
                    "y": int(round(by1)),
                    "width": int(round(bw)),
                    "height": int(round(bh)),
                    "class": "plant_leaf",
                    "confidence": round(float(conf), 4),
                })

        detections.sort(key=lambda d: -d["confidence"])
        detections = detections[:MAX_DETECTIONS]

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
        primary: dict | None = detections[0]
        best_conf = max(d["confidence"] for d in detections)
        return {
            "detections": detections,
            "confidence": round(float(best_conf), 4),
            "primary_box": primary,
        }
