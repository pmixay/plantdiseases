"""
Stage 2 — PlantVision houseplant disease classifier.

Classifies a (preferably cropped) leaf region into one of 10
houseplant-relevant condition classes including the rejection class
``not_a_plant``. Class names + input resolution live in
``models/classes.json``; if the file is missing a built-in default is
used (EfficientNet-B0 @ 240, optional V2-S @ 300 ensemble).

Optional second checkpoint (``classifier_v2s.onnx``) loaded as an
ensemble peer and weighted-averaged at inference. Falls back to the
single classifier when the peer is missing.

The classifier prefers ONNX Runtime (CPU EP) when an ``.onnx`` weight
is present — that's the path the training notebook produces and the
recommended default for production deployment. Loading order:

1. ``classifier_int8.onnx`` (dynamic-quantized, smallest + fastest)
2. ``classifier.onnx``      (FP32 ONNX)
3. ``classifier.pth``       (PyTorch fallback)

The PyTorch fallback auto-detects the backbone from the state dict
shape so either EfficientNet-B0 @ 240 or EfficientNetV2-S @ 320
checkpoints load cleanly without server-side reconfiguration.

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

# Default training resolution (EfficientNet-B0 @ 240). Used only when
# classes.json is missing AND the backbone resolution can't be inferred
# from the state dict.
DEFAULT_IMG_SIZE = 240
# Fallback resolution for EfficientNetV2-S checkpoints. classes.json
# ["training"]["img_size"] remains the single source of truth.
V2S_IMG_SIZE = 320

# Alphabetical — must match training-time ``sorted(ImageFolder.classes)``.
# Loader (_load_pytorch_model + ONNX path) will both expand OR truncate
# this list to match the actual head width and prefer the class names
# declared in classes.json when present.
DEFAULT_CLASS_NAMES = [
    # PlantVision — 10 houseplant disease classes (alphabetical),
    # matching sorted(ImageFolder.classes) at training time.
    "anthracnose",
    "bacterial_leaf_spot",
    "gray_mold",
    "healthy",
    "iron_chlorosis",
    "late_blight",
    "mosaic_virus",
    "not_a_plant",
    "powdery_mildew",
    "sunburn",
]

# Per-channel ImageNet normalisation (matches the training transforms).
NORM_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
NORM_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

LOW_CONFIDENCE_THRESHOLD = 0.35


def _load_metadata(model_dir: Path) -> dict:
    """Load classes.json next to the checkpoint, returning {} on failure."""
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


def _softmax(x: np.ndarray) -> np.ndarray:
    x = x - x.max()
    e = np.exp(x)
    return e / e.sum()


def _resolve_onnx_path(model_path: Path | None) -> Path | None:
    """Look for an ONNX file co-located with the (optional) ``.pth`` path.

    Preference order: int8 → fp32. Returns ``None`` if neither exists.
    """
    if model_path is None:
        return None
    parent = model_path.parent
    for candidate in ("classifier_int8.onnx", "classifier.onnx"):
        p = parent / candidate
        if p.exists():
            return p
    return None


def _resolve_ensemble_path(model_path: Path | None) -> Path | None:
    """Optional secondary backbone (V2-S) co-located with the primary.

    The notebook can train a second checkpoint at higher resolution and
    export it as ``classifier_v2s.onnx`` (or ``_int8`` variant). When
    present we weighted-average its softmax with the primary's at
    inference.
    """
    if model_path is None:
        return None
    parent = model_path.parent
    for candidate in ("classifier_v2s_int8.onnx", "classifier_v2s.onnx"):
        p = parent / candidate
        if p.exists():
            return p
    return None


class DiseaseClassifier:
    """Houseplant disease classifier (ONNX preferred, PyTorch fallback)."""

    def __init__(self, model_path: Path | None = None):
        self.is_loaded = False
        self.backend = "demo"           # "onnx" | "torch" | "demo"
        self._onnx_session = None       # onnxruntime.InferenceSession
        self._onnx_input_name: str = ""
        self._torch_model = None        # torch.nn.Module
        self._torch_device = None       # torch.device

        # Optional ensemble peer (V2-S @ higher res). Loaded only if a
        # sibling ``classifier_v2s*.onnx`` is found next to the primary;
        # otherwise inference stays single-model.
        self._peer_session = None
        self._peer_input_name: str = ""
        self._peer_img_size: int = 0
        self.ensemble_size: int = 1
        # Primary weight in the ensemble softmax average. Tuned at
        # training time and persisted in classes.json["ensemble"].
        self._peer_weight: float = 0.45

        meta = _load_metadata(model_path.parent) if model_path else {}
        self.CLASS_NAMES = _resolve_class_names(meta)
        self.NUM_CLASSES = len(self.CLASS_NAMES)
        self.img_size = _resolve_img_size(meta)

        # Try ONNX first (it's the production path).
        onnx_path = _resolve_onnx_path(model_path)
        if onnx_path is not None:
            try:
                self._load_onnx(onnx_path)
            except Exception as e:
                logger.error("ONNX classifier failed to load (%s): %s", onnx_path, e)

        # Fall back to PyTorch .pth if ONNX wasn't available / loadable.
        if not self.is_loaded and model_path and model_path.exists():
            try:
                self._load_pytorch_model(model_path)
            except Exception as e:
                logger.error("Failed to load PyTorch classifier %s: %s", model_path, e)

        # Optional ensemble peer — we never *fail* on a missing peer; the
        # primary is enough to serve traffic. Only attach if the head
        # widths match (i.e. the peer was trained on the same taxonomy).
        peer_path = _resolve_ensemble_path(model_path)
        if self.is_loaded and peer_path is not None:
            try:
                self._attach_peer(peer_path, meta)
            except Exception as e:
                logger.warning("Ensemble peer skipped (%s): %s", peer_path, e)

        if not self.is_loaded:
            logger.warning("Classifier: no model — running in DEMO mode")

    # ── loaders ──────────────────────────────────────────────────────

    def _load_onnx(self, path: Path) -> None:
        # Lazy import: onnxruntime is optional.
        import onnxruntime as ort

        # CPU-only: keep thread counts modest so multiple uvicorn workers
        # don't oversubscribe the host.
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

        # Reconcile head width with the published class list. The ONNX
        # output shape is (batch, num_classes); for fixed-batch exports
        # ``shape[0]`` may be a string ('batch') so we trust shape[1].
        out_dim = None
        for d in out.shape:
            if isinstance(d, int):
                out_dim = d
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
        # Lazy import — torch is heavyweight, optional when ONNX is in use.
        import torch
        import torch.nn as nn
        from torchvision import models

        state = torch.load(str(path), map_location="cpu", weights_only=True)

        # Detect head width from the classifier weight matrix.
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

        # Distinguish B0 from V2-S by Sequential length:
        #   torchvision EfficientNet-B0 has 9 entries in `features` (stem +
        #   blocks 1..7 + head conv = indices 0..8). MBConv blocks expose
        #   ``features.{i}.0.block.0.0.weight`` for i in 1..7.
        #   torchvision EfficientNet-V2-S has 8 entries (stem + Fused/MBConv
        #   blocks 1..6 + head conv = indices 0..7). The head conv at
        #   features.7 has no `block.` child.
        # So the cleanest signal: presence of ``features.7.0.block.*`` (a
        # block, not the head conv) → B0; absence → V2-S.
        v2s_signal = "features.6.0.block.0.0.weight" in state
        b0_signal = "features.7.0.block.0.0.weight" in state
        is_v2s = v2s_signal and not b0_signal
        if is_v2s:
            net = models.efficientnet_v2_s(weights=None)
        else:
            net = models.efficientnet_b0(weights=None)
        in_features = net.classifier[-1].in_features
        net.classifier[-1] = nn.Linear(in_features, self.NUM_CLASSES)
        net.load_state_dict(state)
        net.eval()

        self._torch_model = net
        self._torch_device = torch.device("cpu")
        # If classes.json was missing we have to guess. B0 trains at 240;
        # V2-S uses 320. Honour whatever came from classes.json when
        # present (img_size != default).
        if is_v2s and self.img_size == DEFAULT_IMG_SIZE:
            self.img_size = V2S_IMG_SIZE
        self.is_loaded = True
        self.backend = "torch"
        logger.info(
            "Classifier (PyTorch %s) loaded from %s (%d classes, img_size=%d)",
            "EfficientNetV2-S" if is_v2s else "EfficientNet-B0",
            path, self.NUM_CLASSES, self.img_size,
        )

    def _attach_peer(self, path: Path, meta: dict) -> None:
        """Load the optional V2-S ensemble peer and validate its head.

        Missing peer → primary-only inference; the field
        ``ensemble_size`` in ``/api/version`` reflects the actual count.
        """
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2
        opts.inter_op_num_threads = 1
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        sess = ort.InferenceSession(
            str(path), sess_options=opts, providers=["CPUExecutionProvider"],
        )
        out = sess.get_outputs()[0]
        out_dim = next((d for d in out.shape if isinstance(d, int)), None)
        if out_dim is not None and out_dim != self.NUM_CLASSES:
            raise ValueError(
                f"peer head width {out_dim} != primary {self.NUM_CLASSES}"
            )
        ensemble_meta = (meta.get("ensemble") or {})
        self._peer_img_size = int(
            ensemble_meta.get("peer_img_size", 300) or 300
        )
        weight = float(ensemble_meta.get("peer_weight", 0.45) or 0.45)
        self._peer_weight = max(0.0, min(0.95, weight))
        self._peer_session = sess
        self._peer_input_name = sess.get_inputs()[0].name
        self.ensemble_size = 2
        logger.info(
            "Ensemble peer attached %s @ %dpx (weight=%.2f)",
            path, self._peer_img_size, self._peer_weight,
        )

    @staticmethod
    def _fit_class_names(names: list[str], n: int) -> list[str]:
        if n <= len(names):
            return names[:n]
        return names + [f"class_{i}" for i in range(len(names), n)]

    # ── public API ───────────────────────────────────────────────────

    def classify(self, img: Image.Image) -> dict:
        """Classify a leaf image. Returns class_name, confidence, all_probs."""
        if self.backend == "onnx":
            return self._classify_onnx(img)
        if self.backend == "torch":
            return self._classify_torch(img)
        return self._classify_demo(img)

    def classify_batch(self, imgs: list[Image.Image]) -> list[dict]:
        """Classify N crops in a single forward pass when possible.

        ONNX (the production path) and PyTorch both support dynamic
        batch — the export sets ``dynamic_axes={'input':{0:'batch'}}`` —
        so we save the per-call setup cost (~5-10 ms each on CPU) by
        running one stacked inference. Demo mode falls back to per-image
        evaluation; the heuristic is cheap enough that batching adds no
        value there.

        Returns one dict per input in the same order as ``imgs``.
        """
        if not imgs:
            return []
        if self.backend == "onnx":
            return self._classify_onnx_batch(imgs)
        if self.backend == "torch":
            return self._classify_torch_batch(imgs)
        return [self._classify_demo(im) for im in imgs]

    # ── preprocessing (NumPy) ────────────────────────────────────────

    def _preprocess(self, img: Image.Image) -> np.ndarray:
        # Single resize + float normalise. Faster than the PIL→torchvision
        # transforms.Compose path and gives identical numerics for
        # bilinear interpolation.
        if img.mode != "RGB":
            img = img.convert("RGB")
        if img.size != (self.img_size, self.img_size):
            img = img.resize((self.img_size, self.img_size), Image.BILINEAR)
        arr = np.asarray(img, dtype=np.float32) / 255.0
        arr = (arr - NORM_MEAN) / NORM_STD
        return np.ascontiguousarray(arr.transpose(2, 0, 1)[None], dtype=np.float32)

    def _preprocess_batch(self, imgs: list[Image.Image]) -> np.ndarray:
        """Stack N preprocessed crops into a single (N,3,H,W) tensor."""
        return np.concatenate([self._preprocess(im) for im in imgs], axis=0)

    def _preprocess_peer(self, img: Image.Image) -> np.ndarray:
        """Same normalisation but resize to the peer's native input size."""
        size = self._peer_img_size
        if img.mode != "RGB":
            img = img.convert("RGB")
        if img.size != (size, size):
            img = img.resize((size, size), Image.BILINEAR)
        arr = np.asarray(img, dtype=np.float32) / 255.0
        arr = (arr - NORM_MEAN) / NORM_STD
        return np.ascontiguousarray(arr.transpose(2, 0, 1)[None], dtype=np.float32)

    def _peer_probs(self, imgs: list[Image.Image]) -> np.ndarray | None:
        """Run the peer on N crops, return (N, num_classes) softmax or None."""
        if self._peer_session is None:
            return None
        try:
            x = np.concatenate([self._preprocess_peer(im) for im in imgs], axis=0)
            logits = self._peer_session.run(None, {self._peer_input_name: x})[0]
        except Exception as e:  # noqa: BLE001 — never let a peer crash inference
            logger.warning("Peer inference failed, dropping ensemble: %s", e)
            return None
        # Stable per-row softmax.
        logits = logits - logits.max(axis=1, keepdims=True)
        e = np.exp(logits)
        return e / e.sum(axis=1, keepdims=True)

    def _blend_with_peer(
        self, primary_probs: np.ndarray, imgs: list[Image.Image],
    ) -> np.ndarray:
        """Weighted-average primary softmax with the ensemble peer when active.

        primary_probs: (N, num_classes)
        Returns the blended (N, num_classes) softmax.
        """
        if self._peer_session is None:
            return primary_probs
        peer = self._peer_probs(imgs)
        if peer is None or peer.shape != primary_probs.shape:
            return primary_probs
        w = self._peer_weight
        return (1.0 - w) * primary_probs + w * peer

    # ── ONNX inference ───────────────────────────────────────────────

    def _classify_onnx(self, img: Image.Image) -> dict:
        x = self._preprocess(img)
        logits = self._onnx_session.run(None, {self._onnx_input_name: x})[0]
        # Stable softmax over the batch dim (here N=1).
        logits = logits - logits.max(axis=1, keepdims=True)
        e = np.exp(logits)
        probs = e / e.sum(axis=1, keepdims=True)
        # Blend with the optional V2-S peer when present.
        probs = self._blend_with_peer(probs, [img])
        return self._format_result(probs[0])

    def _classify_onnx_batch(self, imgs: list[Image.Image]) -> list[dict]:
        x = self._preprocess_batch(imgs)
        # logits: (N, num_classes)
        logits = self._onnx_session.run(None, {self._onnx_input_name: x})[0]
        logits = logits - logits.max(axis=1, keepdims=True)
        e = np.exp(logits)
        probs = e / e.sum(axis=1, keepdims=True)
        probs = self._blend_with_peer(probs, imgs)
        return [self._format_result(probs[i]) for i in range(probs.shape[0])]

    # ── PyTorch inference ────────────────────────────────────────────

    def _classify_torch(self, img: Image.Image) -> dict:
        import torch
        x = torch.from_numpy(self._preprocess(img))
        with torch.no_grad():
            logits = self._torch_model(x).cpu().numpy()
        logits = logits - logits.max(axis=1, keepdims=True)
        e = np.exp(logits)
        probs = e / e.sum(axis=1, keepdims=True)
        probs = self._blend_with_peer(probs, [img])
        return self._format_result(probs[0])

    def _classify_torch_batch(self, imgs: list[Image.Image]) -> list[dict]:
        import torch
        x = torch.from_numpy(self._preprocess_batch(imgs))
        with torch.no_grad():
            logits = self._torch_model(x).cpu().numpy()
        logits = logits - logits.max(axis=1, keepdims=True)
        e = np.exp(logits)
        probs = e / e.sum(axis=1, keepdims=True)
        probs = self._blend_with_peer(probs, imgs)
        return [self._format_result(probs[i]) for i in range(probs.shape[0])]

    def _format_result(self, probs: np.ndarray) -> dict:
        top_idx = int(np.argmax(probs))
        top_conf = float(probs[top_idx])
        return {
            "class_name": self.CLASS_NAMES[top_idx],
            "confidence": round(top_conf, 4),
            "all_probs": {
                self.CLASS_NAMES[i]: round(float(probs[i]), 4)
                for i in range(self.NUM_CLASSES)
            },
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
                    "all_probs": {}, "low_confidence": True}

        # Demo branches map to the 10-class PlantVision taxonomy. Pests
        # stay UI hints, not classes. Standard deviation of the green
        # channel separates uniformly green ("healthy") from mottled
        # ("mosaic") and clean from spotted ("bacterial").
        g_std = float(arr[:, :, 1].std())

        if green_ratio < 0.32 and "not_a_plant" in names:
            cls, conf = "not_a_plant", 0.60 + rng.uniform(0, 0.20)
        elif green_ratio > 0.42 and g_std < 35 and "healthy" in names:
            cls, conf = "healthy", 0.78 + rng.uniform(0, 0.15)
        elif green_ratio > 0.36 and g_std > 50 and "mosaic_virus" in names:
            cls, conf = "mosaic_virus", 0.55 + rng.uniform(0, 0.20)
        elif brightness > 190 and g_mean < 170:
            pool = [n for n in ("powdery_mildew", "gray_mold") if n in names]
            cls = rng.choice(pool) if pool else names[0]
            conf = 0.55 + rng.uniform(0, 0.25)
        elif brown_ratio > 0.42 and g_std > 45:
            # High contrast brown spots — classic bacterial / anthracnose
            # signature. Halo signal can't be detected from RGB stats
            # alone, so we split bacterial vs anthracnose by chance.
            pool = [n for n in ("bacterial_leaf_spot", "anthracnose") if n in names]
            cls = rng.choice(pool) if pool else names[0]
            conf = 0.55 + rng.uniform(0, 0.20)
        elif brown_ratio > 0.42:
            pool = [n for n in ("anthracnose", "late_blight", "sunburn") if n in names]
            cls = rng.choice(pool) if pool else names[0]
            conf = 0.55 + rng.uniform(0, 0.25)
        elif r_mean > g_mean and r_mean > 140:
            pool = [n for n in ("anthracnose", "iron_chlorosis") if n in names]
            cls = rng.choice(pool) if pool else names[0]
            conf = 0.55 + rng.uniform(0, 0.25)
        else:
            pool = [n for n in ("iron_chlorosis", "gray_mold") if n in names]
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
        return {
            "class_name": cls,
            "confidence": round(float(conf), 4),
            "all_probs": probs,
            "low_confidence": conf < LOW_CONFIDENCE_THRESHOLD,
        }
