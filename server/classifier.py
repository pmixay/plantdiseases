"""
Stage 2 — PlantVision houseplant disease classifier.

Classifies a (preferably cropped) leaf region into one of 11
houseplant-relevant condition classes including the rejection class
``not_a_plant``. Class names + input resolution live in
``models/classes.json``; if the file is missing a built-in default
matching the v2.0 taxonomy is used.

Production artefact is a distilled MobileNetV3-Large student
(EfficientNetV2-S teacher → KD). Loading order:

1. ``classifier_int8.onnx`` (dynamic-quantized, smallest + fastest)
2. ``classifier.onnx``      (FP32 ONNX)
3. ``classifier.pth``       (PyTorch fallback)

If none of those load successfully we fall back to a colour heuristic
so the server is usable in demo mode.
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
# Final 11-class taxonomy (v2.0): coarse-grained groups optimised for
# indoor real-world photos.
DEFAULT_CLASS_NAMES = [
    "bacterial_leaf_spot",
    "edema",
    "gray_mold_botrytis",
    "healthy",
    "leaf_scorch_underwatering",
    "leaf_spot_fungal",
    "not_a_plant",
    "nutrient_deficiency",
    "powdery_mildew",
    "sunburn_or_scorch",
    "viral_disease",
]

NORM_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
NORM_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

LOW_CONFIDENCE_THRESHOLD = 0.35


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
        self.is_loaded = False
        self.backend = "demo"           # "onnx" | "torch" | "demo"
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
        if onnx_path is not None:
            try:
                self._load_onnx(onnx_path)
            except Exception as e:
                logger.error("ONNX classifier failed to load (%s): %s", onnx_path, e)

        if not self.is_loaded and model_path and model_path.exists():
            try:
                self._load_pytorch_model(model_path)
            except Exception as e:
                logger.error("Failed to load PyTorch classifier %s: %s", model_path, e)

        if not self.is_loaded:
            logger.warning("Classifier: no model — running in DEMO mode")

    # ── loaders ──────────────────────────────────────────────────────

    def _load_onnx(self, path: Path) -> None:
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2
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
        self.is_loaded = True
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

        head_weight = None
        for key, tensor in state.items():
            if key.startswith("classifier.") and key.endswith(".weight") and tensor.ndim == 2:
                head_weight = tensor
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
        self.is_loaded = True
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
        if self.backend == "torch":
            return self._classify_torch(img)
        return self._classify_demo(img)

    def classify_batch(self, imgs: list[Image.Image]) -> list[dict]:
        if not imgs:
            return []
        if self.backend == "onnx":
            return self._classify_onnx_batch(imgs)
        if self.backend == "torch":
            return self._classify_torch_batch(imgs)
        return [self._classify_demo(im) for im in imgs]

    # ── preprocessing (NumPy) ────────────────────────────────────────

    def _preprocess(self, img: Image.Image) -> np.ndarray:
        if img.mode != "RGB":
            img = img.convert("RGB")
        if img.size != (self.img_size, self.img_size):
            img = img.resize((self.img_size, self.img_size), Image.BILINEAR)
        arr = np.asarray(img, dtype=np.float32) / 255.0
        arr = (arr - NORM_MEAN) / NORM_STD
        return np.ascontiguousarray(arr.transpose(2, 0, 1)[None], dtype=np.float32)

    def _preprocess_batch(self, imgs: list[Image.Image]) -> np.ndarray:
        return np.concatenate([self._preprocess(im) for im in imgs], axis=0)

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
        # Stable softmax + keep the raw logit vector around so the
        # pipeline can score OOD with MaxLogit (Hendrycks 2019), which
        # stays informative when temperature scaling drives MSP low.
        shifted = logits - logits.max()
        e = np.exp(shifted)
        probs = e / e.sum()
        return self._build_result(probs, logits)

    def _build_result(self, probs: np.ndarray, logits: np.ndarray) -> dict:
        top_idx = int(np.argmax(probs))
        top_conf = float(probs[top_idx])
        return {
            "class_name": self.CLASS_NAMES[top_idx],
            "confidence": round(top_conf, 4),
            "all_probs": {
                self.CLASS_NAMES[i]: round(float(probs[i]), 4)
                for i in range(self.NUM_CLASSES)
            },
            "logits": {
                self.CLASS_NAMES[i]: float(logits[i])
                for i in range(self.NUM_CLASSES)
            },
            "max_logit": float(logits.max()),
            "low_confidence": top_conf < LOW_CONFIDENCE_THRESHOLD,
        }

    # ── demo mode (deterministic colour heuristics) ──────────────────

    @staticmethod
    def _image_seed(img: Image.Image) -> int:
        small = np.array(img.resize((16, 16)), dtype=np.uint8)
        return int(small.sum()) % (2**31)

    def _classify_demo(self, img: Image.Image) -> dict:
        rng = np.random.RandomState(self._image_seed(img))
        arr = np.array(img.resize((64, 64)), dtype=np.float32)
        r_mean = float(arr[:, :, 0].mean())
        g_mean = float(arr[:, :, 1].mean())
        b_mean = float(arr[:, :, 2].mean())
        total = r_mean + g_mean + b_mean + 1e-6
        green_ratio = g_mean / total
        brown_ratio = r_mean / total
        brightness = total / 3.0

        names = self.CLASS_NAMES
        if not names:
            return {"class_name": "unknown", "confidence": 0.0,
                    "all_probs": {}, "logits": {}, "max_logit": 0.0,
                    "low_confidence": True}

        g_std = float(arr[:, :, 1].std())

        if green_ratio < 0.32 and "not_a_plant" in names:
            cls, conf = "not_a_plant", 0.60 + rng.uniform(0, 0.20)
        elif green_ratio > 0.42 and g_std < 35 and "healthy" in names:
            cls, conf = "healthy", 0.78 + rng.uniform(0, 0.15)
        elif green_ratio > 0.36 and g_std > 50 and "viral_disease" in names:
            cls, conf = "viral_disease", 0.55 + rng.uniform(0, 0.20)
        elif brightness > 190 and g_mean < 170:
            pool = [n for n in ("powdery_mildew", "gray_mold_botrytis") if n in names]
            cls = rng.choice(pool) if pool else names[0]
            conf = 0.55 + rng.uniform(0, 0.25)
        elif brown_ratio > 0.42 and g_std > 45:
            pool = [n for n in ("bacterial_leaf_spot", "leaf_spot_fungal") if n in names]
            cls = rng.choice(pool) if pool else names[0]
            conf = 0.55 + rng.uniform(0, 0.20)
        elif brown_ratio > 0.42:
            pool = [n for n in ("leaf_scorch_underwatering", "sunburn_or_scorch")
                    if n in names]
            cls = rng.choice(pool) if pool else names[0]
            conf = 0.55 + rng.uniform(0, 0.25)
        elif r_mean > g_mean and r_mean > 140:
            pool = [n for n in ("nutrient_deficiency", "leaf_spot_fungal") if n in names]
            cls = rng.choice(pool) if pool else names[0]
            conf = 0.55 + rng.uniform(0, 0.25)
        else:
            pool = [n for n in ("nutrient_deficiency", "edema") if n in names]
            cls = rng.choice(pool) if pool else names[0]
            conf = 0.50 + rng.uniform(0, 0.25)

        probs = {n: 0.0 for n in names}
        probs[cls] = round(conf, 4)
        leftover = 1.0 - conf
        others = [n for n in names if n != cls]
        if others:
            rnd = rng.dirichlet(np.ones(len(others))) * leftover
            for name, p in zip(others, rnd, strict=False):
                probs[name] = round(float(p), 4)
        # Synthesise pseudo-logits so the OOD pipeline can run end-to-end
        # in demo mode without crashing on missing fields. log(p) is
        # deterministic from probs and preserves argmax/relative ordering.
        logits = {n: float(np.log(max(p, 1e-12))) for n, p in probs.items()}
        return {
            "class_name": cls,
            "confidence": round(float(conf), 4),
            "all_probs": probs,
            "logits": logits,
            "max_logit": max(logits.values()),
            "low_confidence": conf < LOW_CONFIDENCE_THRESHOLD,
        }
