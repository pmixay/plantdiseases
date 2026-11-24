"""
Stage 1 — Plant-leaf detector.

YOLOv8n single-class (``plant_leaf``) leaf-localisation model. Its
training corpus is determined by ``train_notebook.ipynb`` §3:

* **Default:** PlantDoc detection bboxes collapsed to one class
  (``build_yolo_detection_dataset_from_plantdoc``), with ``not_a_plant``
  frames added as explicit background negatives so the detector learns
  "no leaf here" and doesn't fire on pure noise.
* **Preferred for production:** drop a hand-labelled *indoor* YOLO
  ``data.yaml`` under ``data_v2/`` (e.g. Roboflow Universe houseplant
  slices) — §3 picks it up automatically. Indoor bboxes localise wide
  indoor compositions better than agricultural close-ups, so an indoor
  set is the recommended Stage-1 corpus when available.

Either way the "is it diseased?" decision is delegated 100 % to Stage 2
(classifier); Stage 1 only crops.

Loading is fail-fast: if no usable artefact is found, ``__init__``
raises ``DetectorLoadError`` so the API never boots in a half-broken
state. There is no heuristic / demo fallback path.

ONNX is loaded transparently via raw onnxruntime. Production deployments
must ship ``detector.onnx``.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

try:
    from .config import onnx_intra_op_threads
except ImportError:  # pragma: no cover — flat layout (run from server/)
    from config import onnx_intra_op_threads

logger = logging.getLogger(__name__)

DETECTOR_IMG_SIZE = 640
# Indoor-trained detector — recall-biased so that wide-frame indoor
# compositions don't drop the only leaf bbox. The three-of-three OOD gate
# downstream (low detector confidence AND a firing classifier-side signal —
# an embedding-space k-NN distance, or the MaxLogit floor when the bundle
# ships no bank — AND no veto from the not_a_plant head) cleans up false
# positives.
MIN_CONFIDENCE = 0.15
MAX_DETECTIONS = 10
# Resource-safety ceiling before quadratic NumPy NMS. This is deliberately far
# above the number of plausible leaf candidates; a corrupt graph that assigns
# every one of 8,400 anchors a positive score must not pin a CPU worker.
MAX_PRE_NMS_CANDIDATES = 1000


class DetectorLoadError(RuntimeError):
    """Raised when no usable detector artefact can be loaded."""


def _load_detector_metadata(model_dir: Path) -> dict:
    path = model_dir / "classes.json"
    if not path.exists():
        return {}
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise DetectorLoadError(f"Failed to read detector metadata {path}: {e}") from e
    if not isinstance(meta, dict):
        raise DetectorLoadError(f"Detector metadata {path} must contain a JSON object")
    return meta


def _resolve_detector_settings(meta: dict) -> tuple[int, float, float, int, str]:
    section = meta.get("detector")
    if section is None:
        section = {}
    if not isinstance(section, dict):
        raise DetectorLoadError("classes.json -> detector must be an object")

    def _integer(key: str, fallback: int, low: int, high: int) -> int:
        if key not in section:
            return fallback
        value = section[key]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise DetectorLoadError(
                f"classes.json -> detector.{key} must be an integer in [{low}, {high}]"
            )
        return value

    def _ratio(key: str, fallback: float, *, include_zero: bool) -> float:
        if key not in section:
            return fallback
        value = section[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
        ):
            bracket = "[0, 1]" if include_zero else "(0, 1]"
            raise DetectorLoadError(
                f"classes.json -> detector.{key} must be finite and in {bracket}"
            )
        number = float(value)
        lower_ok = number >= 0.0 if include_zero else number > 0.0
        if not math.isfinite(number) or not lower_ok or number > 1.0:
            bracket = "[0, 1]" if include_zero else "(0, 1]"
            raise DetectorLoadError(
                f"classes.json -> detector.{key} must be finite and in {bracket}"
            )
        return number

    class_names = meta.get("detector_classes", ["plant_leaf"])
    if (
        not isinstance(class_names, list)
        or len(class_names) != 1
        or not isinstance(class_names[0], str)
        or not class_names[0]
        or class_names[0] != class_names[0].strip()
    ):
        raise DetectorLoadError(
            "This runtime requires exactly one non-empty detector class in "
            "classes.json -> detector_classes"
        )
    return (
        _integer("img_size", DETECTOR_IMG_SIZE, 32, 4096),
        _ratio("min_confidence", MIN_CONFIDENCE, include_zero=False),
        _ratio("nms_iou", 0.45, include_zero=False),
        _integer("max_detections", MAX_DETECTIONS, 1, 1000),
        class_names[0],
    )


def _resolve_detector_path(model_path: Path | None) -> Path | None:
    """Resolve ``detector.onnx`` in the bundle ``model_path`` points into.

    Serving is ONNX-only: a ``.pt`` is NEVER loaded, whatever ``model_path``
    names (legacy callers may pass ``models/detector.pt`` and still resolve
    here to ``models/detector.onnx``). Only the bundle directory is taken from
    ``model_path``; the filename is fixed. Returns ``None`` when the bundle
    ships no ``detector.onnx``, which ``LeafDetector.__init__`` turns into a
    boot-aborting :class:`DetectorLoadError`.
    """
    if model_path is None:
        return None
    onnx = model_path.parent / "detector.onnx"
    return onnx if onnx.exists() else None


def nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> list[int]:
    """Pure NumPy Non-Maximum Suppression (NMS)."""
    if not isinstance(iou_threshold, (int, float)) or not math.isfinite(float(iou_threshold)):
        raise ValueError(f"NMS IoU threshold must be finite, got {iou_threshold!r}")
    if not 0.0 <= float(iou_threshold) <= 1.0:
        raise ValueError(f"NMS IoU threshold must be in [0, 1], got {iou_threshold!r}")
    boxes = np.asarray(boxes, dtype=np.float32)
    scores = np.asarray(scores, dtype=np.float32).reshape(-1)
    if boxes.ndim != 2 or boxes.shape[1:] != (4,) or boxes.shape[0] != scores.shape[0]:
        raise ValueError(
            f"NMS expects boxes=(N,4) and scores=(N,), got {boxes.shape} and {scores.shape}"
        )
    if boxes.shape[0] == 0:
        return []
    valid = (
        np.isfinite(boxes).all(axis=1)
        & np.isfinite(scores)
        & (boxes[:, 2] > boxes[:, 0])
        & (boxes[:, 3] > boxes[:, 1])
    )
    original_indices = np.flatnonzero(valid)
    if original_indices.size == 0:
        return []
    boxes = boxes[valid]
    scores = scores[valid]
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
        union = areas[i] + areas[order[1:]] - inter
        ovr = np.divide(inter, union, out=np.zeros_like(inter), where=union > 0)

        inds = np.where(ovr <= iou_threshold)[0]
        order = order[inds + 1]
    return [int(original_indices[i]) for i in keep]


def _normalise_yolo_output(raw: np.ndarray) -> np.ndarray:
    """Return a single-class YOLO tensor in canonical ``(5, N)`` form.

    Ultralytics exports have used both ``(1, 5, N)`` and ``(1, N, 5)``
    layouts.  Accept both explicitly; reject everything else instead of
    indexing an arbitrary tensor as confidence/boxes.
    """
    value = np.asarray(raw)
    if value.ndim == 3:
        if value.shape[0] != 1:
            raise RuntimeError(f"Detector output batch must be 1, got {value.shape}")
        value = value[0]
    if value.ndim != 2:
        raise RuntimeError(f"Detector output must be rank 2/3, got {value.shape}")
    if value.shape[0] == 5:
        canonical = value
    elif value.shape[1] == 5:
        canonical = value.T
    else:
        raise RuntimeError(
            f"Single-class YOLO output must have one axis of width 5, got {value.shape}"
        )
    if canonical.shape[1] == 0:
        raise RuntimeError("Detector output contains no prediction anchors")
    return np.asarray(canonical, dtype=np.float32)


def letterbox(img: Image.Image, target_size: int = 640) -> tuple[np.ndarray, float, tuple[int, int]]:
    """Resize keeping aspect ratio, and pad to target_size x target_size."""
    w, h = img.size
    if w <= 0 or h <= 0:
        raise ValueError(f"Cannot letterbox an empty image with size {img.size}")
    if target_size <= 0:
        raise ValueError(f"target_size must be positive, got {target_size}")
    scale = target_size / max(w, h)
    new_w = int(round(w * scale))
    new_h = int(round(h * scale))
    resized = img.resize((new_w, new_h), Image.Resampling.BILINEAR)

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
        self.model: Any = None  # onnxruntime.InferenceSession (untyped package)
        self.input_name = ""

        meta = _load_detector_metadata(model_path.parent) if model_path else {}
        (
            self.img_size,
            self.min_confidence,
            self.nms_iou,
            self.max_detections,
            self.class_name,
        ) = _resolve_detector_settings(meta)

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

    def _load_model(self, path: Path) -> None:
        import onnxruntime as ort

        opts = ort.SessionOptions()
        # Both ORT sessions (this one and the classifier's) read the same
        # key and default through ``config.onnx_intra_op_threads``.
        opts.intra_op_num_threads = onnx_intra_op_threads()
        opts.inter_op_num_threads = 1
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        self.model = ort.InferenceSession(
            str(path),
            sess_options=opts,
            providers=["CPUExecutionProvider"],
        )
        inputs = self.model.get_inputs()
        outputs = self.model.get_outputs()
        if len(inputs) != 1 or len(outputs) != 1:
            raise DetectorLoadError(
                f"Detector graph requires exactly one input and one output, got "
                f"{len(inputs)} and {len(outputs)}"
            )
        inp = inputs[0]
        out = outputs[0]
        expected_shape = [1, 3, self.img_size, self.img_size]
        if inp.type != "tensor(float)" or list(inp.shape) != expected_shape:
            raise DetectorLoadError(
                f"Detector input must be float32 NCHW {expected_shape}, got "
                f"type={inp.type} shape={inp.shape}"
            )
        if out.type != "tensor(float)" or len(out.shape) != 3:
            raise DetectorLoadError(
                f"Detector output must be rank-3 float32, got type={out.type} "
                f"shape={out.shape}"
            )
        if out.shape[0] != 1 or 5 not in out.shape[1:]:
            raise DetectorLoadError(
                f"Detector output must be [1,5,N] or [1,N,5], got {out.shape}"
            )
        self.input_name = inp.name

        # Warm up so the first request isn't paying JIT/init cost.
        dummy_input = np.zeros((1, 3, self.img_size, self.img_size), dtype=np.float32)
        warm = _normalise_yolo_output(self.model.run(None, {self.input_name: dummy_input})[0])
        if not np.isfinite(warm).all():
            raise DetectorLoadError("Detector emits non-finite values on warmup input")
        logger.info(
            "ONNX YOLOv8 detector loaded (img=%d conf=%.3f nms=%.3f max=%d)",
            self.img_size, self.min_confidence, self.nms_iou, self.max_detections,
        )

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
        input_data, scale, (pad_x, pad_y) = letterbox(img, self.img_size)

        outputs = self.model.run(None, {self.input_name: input_data})
        output = _normalise_yolo_output(outputs[0])

        confs = output[4, :]
        raw_boxes = output[:4, :].T
        valid = (
            np.isfinite(confs)
            & (confs >= 0.0)
            & (confs <= 1.0)
            & np.isfinite(raw_boxes).all(axis=1)
            & (raw_boxes[:, 2] > 0)
            & (raw_boxes[:, 3] > 0)
        )
        indices = np.where(valid & (confs >= self.min_confidence))[0]
        if len(indices) == 0:
            return self._empty_result()
        if len(indices) > MAX_PRE_NMS_CANDIDATES:
            local = np.argpartition(
                confs[indices], -MAX_PRE_NMS_CANDIDATES,
            )[-MAX_PRE_NMS_CANDIDATES:]
            indices = indices[local]

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
        keep = nms(xyxy, confs, iou_threshold=self.nms_iou)
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
                    "class": self.class_name,
                    # Preserve the detector score through NMS, voting and the
                    # OOD gate. Rounding here could move a borderline score
                    # across a calibrated routing floor; presentation rounds
                    # only at the API boundary.
                    "confidence": float(conf),
                })

        detections.sort(key=lambda d: -d["confidence"])
        detections = detections[:self.max_detections]

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
            "confidence": float(best_conf),
            "primary_box": primary,
        }
