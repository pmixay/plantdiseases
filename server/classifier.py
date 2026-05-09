"""
Stage 2 — PlantVision houseplant disease classifier.

Classifies a (preferably cropped) leaf region into one of the
houseplant-relevant condition classes including the rejection class
``not_a_plant``. Class names + input resolution live in
``models/classes.json``; if the file is missing the built-in default
matching the v2.1 taxonomy (7 trainable classes) is used.

Production artefact is a distilled MobileNetV3-Large student
(EfficientNetV2-S teacher → KD). Loading order:

1. ``classifier_int8.onnx`` (dynamic-quantized, smallest + fastest)
2. ``classifier.onnx``      (FP32 ONNX)
3. ``classifier.pth``       (PyTorch fallback)

Loading is fail-fast: if no usable artefact is found, ``__init__``
raises ``ClassifierLoadError`` so the API never boots in a half-broken
state. There is no heuristic / demo fallback path.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

DEFAULT_IMG_SIZE = 256

# Alphabetical — must match training-time ``sorted(ImageFolder.classes)``.
# v2.1 trainable taxonomy. The four physiological classes from the v2.0
# design (``edema``, ``leaf_scorch_underwatering``, ``nutrient_deficiency``,
# ``sunburn_or_scorch``) are deferred until labelled real-world data is
# added — see server/models/MODEL_CARD.md.
DEFAULT_CLASS_NAMES = [
    "bacterial_leaf_spot",
    "gray_mold_botrytis",
    "healthy",
    "leaf_spot_fungal",
    "not_a_plant",
    "powdery_mildew",
    "viral_disease",
]

NORM_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
NORM_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

LOW_CONFIDENCE_THRESHOLD = 0.35


class ClassifierLoadError(RuntimeError):
    """Raised when no usable classifier artefact can be loaded."""


def _load_metadata(model_dir: Path) -> dict:
    meta = model_dir / "classes.json"
    if not meta.exists():
        return {}
    try:
        return json.loads(meta.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Failed to read %s: %s", meta, e)
        return {}


def _resolve_class_names(meta: dict) -> list[str]:
    names = meta.get("class_names") or DEFAULT_CLASS_NAMES
    return list(names)


def _resolve_img_size(meta: dict) -> int:
    size = (meta.get("training") or {}).get("img_size")
    if isinstance(size, int) and size > 0:
        return size
    return DEFAULT_IMG_SIZE


def _resolve_onnx_path(model_path: Path | None) -> Path | None:
    if model_path is None:
        return None
    parent = model_path.parent
    for candidate in ("classifier_int8.onnx", "classifier.onnx"):
        p = parent / candidate
        if p.exists():
            return p
    return None


class DiseaseClassifier:
    """Houseplant disease classifier (ONNX preferred, PyTorch fallback)."""

    def __init__(self, model_path: Path | None = None):
        self.backend = ""               # "onnx" | "torch"
        self._onnx_session = None
        self._onnx_input_name: str = ""
        self._torch_model = None
        self._torch_device = None

        meta = _load_metadata(model_path.parent) if model_path else {}
        self.meta = meta
        self.CLASS_NAMES = _resolve_class_names(meta)
        self.NUM_CLASSES = len(self.CLASS_NAMES)
        self.img_size = _resolve_img_size(meta)

        onnx_path = _resolve_onnx_path(model_path)
        load_errors: list[str] = []

        if onnx_path is not None:
            try:
                self._load_onnx(onnx_path)
            except Exception as e:
                load_errors.append(f"ONNX {onnx_path}: {e}")
                logger.error("ONNX classifier failed to load (%s): %s", onnx_path, e)

        if not self.is_loaded and model_path and model_path.exists():
            try:
                self._load_pytorch_model(model_path)
            except Exception as e:
                load_errors.append(f"PyTorch {model_path}: {e}")
                logger.error("Failed to load PyTorch classifier %s: %s", model_path, e)

        if not self.is_loaded:
            details = "; ".join(load_errors) if load_errors else (
                "no classifier artefact (classifier_int8.onnx / classifier.onnx / "
                "classifier.pth) found next to the configured model path"
            )
            raise ClassifierLoadError(
                f"Cannot start without a trained classifier. {details}"
            )

    @property
    def is_loaded(self) -> bool:
        return bool(self.backend)

    # ── loaders ──────────────────────────────────────────────────────

    def _load_onnx(self, path: Path) -> None:
        import onnxruntime as ort

        opts = ort.SessionOptions()
        # 1 intra-op thread per session — the FastAPI side runs
        # `INFER_WORKERS` (default 2) parallel inferences in its
        # ThreadPoolExecutor, and ORT spawning 2 internal threads
        # *per* concurrent request would oversubscribe a 2-vCPU host
        # (4 OS threads, two of them context-switching constantly).
        # Single-thread-per-session keeps total OS threads ≈ vCPU and
        # lets the executor's parallelism deliver the throughput.
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        sess = ort.InferenceSession(
            str(path),
            sess_options=opts,
            providers=["CPUExecutionProvider"],
        )
        inp = sess.get_inputs()[0]
        out = sess.get_outputs()[0]

        out_dim = next((d for d in out.shape if isinstance(d, int)), None)
        if out_dim is not None and out_dim != self.NUM_CLASSES:
            logger.warning(
                "Classifier ONNX has %d outputs but classes.json declares %d. "
                "Trusting the ONNX checkpoint and adjusting the class list.",
                out_dim, self.NUM_CLASSES,
            )
            self.CLASS_NAMES = self._fit_class_names(self.CLASS_NAMES, out_dim)
            self.NUM_CLASSES = out_dim

        self._onnx_session = sess
        self._onnx_input_name = inp.name
        self.backend = "onnx"
        logger.info(
            "Classifier (ONNX) loaded from %s (%d classes, img_size=%d)",
            path, self.NUM_CLASSES, self.img_size,
        )

    def _load_pytorch_model(self, path: Path) -> None:
        import torch
        import torch.nn as nn
        from torchvision import models

        state = torch.load(str(path), map_location="cpu", weights_only=True)

        head_weight = next(
            (
                t for k, t in state.items()
                if k.startswith("classifier.") and k.endswith(".weight") and t.ndim == 2
            ),
            None,
        )
        ckpt_classes = (
            int(head_weight.shape[0]) if head_weight is not None else self.NUM_CLASSES
        )
        if ckpt_classes != self.NUM_CLASSES:
            logger.warning(
                "Classifier .pth has %d outputs but classes.json declares %d. "
                "Trusting checkpoint, padding/truncating class list.",
                ckpt_classes, self.NUM_CLASSES,
            )
            self.CLASS_NAMES = self._fit_class_names(self.CLASS_NAMES, ckpt_classes)
            self.NUM_CLASSES = ckpt_classes

        # Production student is MobileNetV3-Large (KD from EfficientNetV2-S).
        # The torchvision factory matches the timm/torchvision head shape
        # the training notebook saves.
        net = models.mobilenet_v3_large(weights=None)
        in_features = net.classifier[-1].in_features
        net.classifier[-1] = nn.Linear(in_features, self.NUM_CLASSES)
        net.load_state_dict(state)
        net.eval()

        self._torch_model = net
        self._torch_device = torch.device("cpu")
        self.backend = "torch"
        logger.info(
            "Classifier (PyTorch MobileNetV3-Large student) loaded from %s "
            "(%d classes, img_size=%d)",
            path, self.NUM_CLASSES, self.img_size,
        )

    @staticmethod
    def _fit_class_names(names: list[str], n: int) -> list[str]:
        if n <= len(names):
            return names[:n]
        return names + [f"class_{i}" for i in range(len(names), n)]

    # ── public API ───────────────────────────────────────────────────

    def classify(self, img: Image.Image) -> dict:
        if self.backend == "onnx":
            return self._classify_onnx(img)
        return self._classify_torch(img)

    def classify_batch(self, imgs: list[Image.Image]) -> list[dict]:
        if not imgs:
            return []
        if self.backend == "onnx":
            return self._classify_onnx_batch(imgs)
        return self._classify_torch_batch(imgs)

    # ── preprocessing (NumPy) ────────────────────────────────────────

    def _to_chw(self, img: Image.Image) -> np.ndarray:
        if img.mode != "RGB":
            img = img.convert("RGB")
        if img.size != (self.img_size, self.img_size):
            img = img.resize((self.img_size, self.img_size), Image.BILINEAR)
        arr = np.asarray(img, dtype=np.float32) / 255.0
        arr = (arr - NORM_MEAN) / NORM_STD
        return arr.transpose(2, 0, 1)

    def _preprocess(self, img: Image.Image) -> np.ndarray:
        return np.ascontiguousarray(self._to_chw(img)[None], dtype=np.float32)

    def _preprocess_batch(self, imgs: list[Image.Image]) -> np.ndarray:
        # Stack the per-crop CHW arrays into a single contiguous NCHW
        # tensor — one allocation, no per-crop concat round-trip.
        n = len(imgs)
        out = np.empty(
            (n, 3, self.img_size, self.img_size), dtype=np.float32,
        )
        for i, im in enumerate(imgs):
            out[i] = self._to_chw(im)
        return out

    # ── ONNX inference ───────────────────────────────────────────────

    def _classify_onnx(self, img: Image.Image) -> dict:
        x = self._preprocess(img)
        logits = self._onnx_session.run(None, {self._onnx_input_name: x})[0]
        return self._format_result_from_logits(logits[0])

    def _classify_onnx_batch(self, imgs: list[Image.Image]) -> list[dict]:
        x = self._preprocess_batch(imgs)
        logits = self._onnx_session.run(None, {self._onnx_input_name: x})[0]
        return [self._format_result_from_logits(logits[i]) for i in range(logits.shape[0])]

    # ── PyTorch inference ────────────────────────────────────────────

    def _classify_torch(self, img: Image.Image) -> dict:
        import torch
        x = torch.from_numpy(self._preprocess(img))
        with torch.no_grad():
            logits = self._torch_model(x).cpu().numpy()
        return self._format_result_from_logits(logits[0])

    def _classify_torch_batch(self, imgs: list[Image.Image]) -> list[dict]:
        import torch
        x = torch.from_numpy(self._preprocess_batch(imgs))
        with torch.no_grad():
            logits = self._torch_model(x).cpu().numpy()
        return [self._format_result_from_logits(logits[i]) for i in range(logits.shape[0])]

    def _format_result_from_logits(self, logits: np.ndarray) -> dict:
        # Numerically-stable softmax (max-shift) in float32. The
        # max-shift caps `exp` arguments at 0 so float32 doesn't
        # overflow; the smallest meaningful tail (`exp(-30)`) is well
        # within float32 range (~9.4e-14). float64 was a precaution
        # that doubled the dict-construction allocation per call for
        # no measurable accuracy delta on the 7-class head.
        logits32 = logits.astype(np.float32, copy=False)
        max_logit = float(logits32.max())
        e = np.exp(logits32 - max_logit)
        denom = float(e.sum())
        if denom <= 0 or not np.isfinite(denom):
            # Degenerate (all -inf or NaN). Fall back to uniform so the
            # response stays well-formed instead of poisoning downstream.
            probs = np.full_like(logits32, 1.0 / max(len(logits32), 1))
        else:
            probs = e / denom
        top_idx = int(np.argmax(probs))
        top_conf = float(probs[top_idx])
        return {
            "class_name": self.CLASS_NAMES[top_idx],
            "confidence": round(top_conf, 4),
            "all_probs": {
                self.CLASS_NAMES[i]: round(float(probs[i]), 4)
                for i in range(self.NUM_CLASSES)
            },
            "max_logit": max_logit,
            "low_confidence": top_conf < LOW_CONFIDENCE_THRESHOLD,
        }
