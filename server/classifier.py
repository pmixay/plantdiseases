"""
Stage 2 — PlantVision houseplant disease classifier.

Classifies a (preferably cropped) leaf region into one of the
houseplant-relevant condition classes including the rejection class
``not_a_plant``. Class names + input resolution live in
``models/classes.json``; if the file is missing the built-in default
matching the v2.2 taxonomy (8 trainable classes) is used.

The production artefact is an ONNX graph, and the ONNX path is
architecture-neutral — it serves whatever backbone+head graph is shipped
(the historical MobileNetV3-Large KD student, or a frozen-DINOv2 ViT-S/14
+ trained head) with no code change; ``classes.json`` declares the
``architecture`` (for the .pth dev fallback), ``img_size`` and any
``inference.tta`` mode. Loading order:

1. ``classifier_int8.onnx`` (quantized, smallest + fastest)
2. ``classifier.onnx``      (FP32 ONNX)
3. ``classifier.pth``       (PyTorch fallback — ``mobilenet_v3_large`` only)

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
# Train/serve crop parity: the notebook eval/calibration transform is
# ``Resize(int(img_size * EVAL_RESIZE_RATIO)) -> CenterCrop(img_size)``
# (a mild center zoom that trims the border). Conformal q_hat and the
# temperature were fit on that distribution, so serving must reproduce
# it — resizing the short side straight to ``img_size`` (ratio 1.0) fed
# the model a slightly wider frame than calibration ever saw. The ratio
# is overridable from ``classes.json -> training.eval_resize_ratio``.
DEFAULT_EVAL_RESIZE_RATIO = 1.05

# Alphabetical — must match training-time ``sorted(ImageFolder.classes)``.
# v2.x trainable taxonomy. ``leaf_scorch_underwatering`` and
# ``nutrient_deficiency`` were added per ``train_notebook.ipynb`` §1
# (INCLUDE_PHYSIOLOGICAL_CLASSES) — the two remaining physiological
# classes (``edema``, ``sunburn_or_scorch``) stay deferred until
# upstream supply justifies them.
# Production reads ``classes.json -> class_names`` first; this list is
# only used as a fallback when the artefact is missing.
# ``leaf_spot`` is the merged bacterial+fungal leaf-spot class (the
# distinction is not reliably separable from a phone photo).
DEFAULT_CLASS_NAMES = [
    "gray_mold_botrytis",
    "healthy",
    "leaf_scorch_underwatering",
    "leaf_spot",
    "not_a_plant",
    "nutrient_deficiency",
    "powdery_mildew",
    "viral_disease",
]

NORM_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
NORM_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

# Backbone family of the trained checkpoint. Production loads ONNX (an
# architecture-neutral graph), so this only steers the *.pth* PyTorch
# fallback loader. ``classes.json -> architecture`` overrides it; the
# default keeps the historical MobileNetV3-Large student working with
# no metadata change. A frozen-DINOv2 model ships as ONNX only — see
# ``_load_pytorch_model``.
DEFAULT_ARCHITECTURE = "mobilenet_v3_large"

# Test-time augmentation mode. ``none`` (default) is single-pass and
# byte-for-byte the historical behaviour. ``hflip`` runs the image and
# its horizontal mirror and AVERAGES THE LOGITS (not the softmax probs):
# averaging logits keeps every downstream calibration step — temperature
# scaling, the MaxLogit OOD score, conformal RAPS — operating on the same
# kind of quantity it was fit on, so enabling TTA does not silently
# decalibrate the model. The contract is therefore: temperature.json /
# conformal.json / the OOD floor MUST be re-fit with the matching TTA
# mode (the training notebook does this from ``classes.json -> inference``).
# Turning ``hflip`` on at serve time against a model calibrated without it
# would break train/serve parity, so the default stays ``none``.
TTA_MODES = ("none", "hflip")


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


def _resolve_eval_resize_ratio(meta: dict) -> float:
    ratio = (meta.get("training") or {}).get("eval_resize_ratio")
    if isinstance(ratio, (int, float)) and ratio >= 1.0:
        return float(ratio)
    return DEFAULT_EVAL_RESIZE_RATIO


def _resolve_architecture(meta: dict) -> str:
    arch = meta.get("architecture") or (meta.get("training") or {}).get("architecture")
    if isinstance(arch, str) and arch:
        return arch
    return DEFAULT_ARCHITECTURE


def _resolve_tta(meta: dict) -> str:
    mode = (meta.get("inference") or {}).get("tta")
    if isinstance(mode, str) and mode in TTA_MODES:
        return mode
    return "none"


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
        # Track provenance: a class list that came from classes.json is a
        # hard contract (must match the checkpoint head exactly), whereas
        # the built-in default is a best-effort fallback we may pad.
        self._class_names_from_meta = bool(meta.get("class_names"))
        self.eval_resize_ratio = _resolve_eval_resize_ratio(meta)
        self.architecture = _resolve_architecture(meta)
        self.tta = _resolve_tta(meta)
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
            except ImportError:
                logger.info("PyTorch is not installed; skipping PyTorch classifier fallback")
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
            if self._class_names_from_meta:
                # classes.json explicitly declared a class list that does
                # not match the checkpoint head. Padding with ``class_N``
                # would silently mislabel every prediction (the v2.1
                # 7-class metadata against the v2.2 8-output head shipped
                # exactly this corruption). Fail fast instead so a wrong
                # classes.json can never boot serving mislabeled answers.
                raise ClassifierLoadError(
                    f"classes.json declares {self.NUM_CLASSES} classes "
                    f"({self.CLASS_NAMES}) but the ONNX checkpoint at {path} "
                    f"has {out_dim} outputs. Ship a classes.json whose "
                    f"class_names match the trained head, in training order."
                )
            logger.warning(
                "Classifier ONNX has %d outputs but only the built-in default "
                "(%d) was available. Adjusting the fallback class list.",
                out_dim, self.NUM_CLASSES,
            )
            self.CLASS_NAMES = self._fit_class_names(self.CLASS_NAMES, out_dim)
            self.NUM_CLASSES = out_dim

        self._onnx_session = sess
        self._onnx_input_name = inp.name
        self.backend = "onnx"

        # Warm the graph once at load so the first real request doesn't
        # eat the one-off allocation / kernel-selection cost (the detector
        # already warms up; the classifier previously did not, adding tens
        # of ms of jitter to the first user's scan after a deploy).
        try:
            warm = np.zeros((1, 3, self.img_size, self.img_size), dtype=np.float32)
            sess.run(None, {inp.name: warm})
        except Exception as e:  # noqa: BLE001 — warmup is best-effort
            logger.debug("Classifier ONNX warmup skipped: %s", e)

        logger.info(
            "Classifier (ONNX) loaded from %s (%d classes, img_size=%d, "
            "arch=%s, tta=%s)",
            path, self.NUM_CLASSES, self.img_size, self.architecture, self.tta,
        )

    def _load_pytorch_model(self, path: Path) -> None:
        # The .pth path is a development fallback for the torchvision
        # student only. A frozen-DINOv2 (or any non-torchvision) backbone
        # would need its feature extractor rebuilt at boot (e.g. via
        # torch.hub, which is a network call), so we refuse to guess and
        # direct the operator to the ONNX artefact instead — exporting
        # backbone+head into one graph is exactly what the notebook §6
        # does. This keeps a wrong .pth from silently mislabelling.
        if self.architecture != "mobilenet_v3_large":
            raise ClassifierLoadError(
                f"classes.json declares architecture={self.architecture!r}, which "
                f"is only served from an ONNX graph (classifier_int8.onnx / "
                f"classifier.onnx). The .pth fallback supports "
                f"{DEFAULT_ARCHITECTURE!r} only. Export the ONNX artefact and ship "
                f"it next to the configured model path."
            )

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
            if self._class_names_from_meta:
                raise ClassifierLoadError(
                    f"classes.json declares {self.NUM_CLASSES} classes "
                    f"({self.CLASS_NAMES}) but the .pth checkpoint at {path} "
                    f"has {ckpt_classes} outputs. Ship a classes.json whose "
                    f"class_names match the trained head, in training order."
                )
            logger.warning(
                "Classifier .pth has %d outputs but only the built-in default "
                "(%d) was available. Adjusting the fallback class list.",
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
            img = self._aspect_preserving_square(img, self.img_size)
        arr = np.asarray(img, dtype=np.float32) / 255.0
        arr = (arr - NORM_MEAN) / NORM_STD
        return arr.transpose(2, 0, 1)

    def _aspect_preserving_square(self, img: Image.Image, side: int) -> Image.Image:
        """Resize ``img`` to a ``side × side`` square WITHOUT stretching.

        Mirror the notebook eval/calibration transform exactly:
        ``Resize(int(side * eval_resize_ratio)) -> CenterCrop(side)``.
        torchvision ``Resize`` with a single int scales the SHORT side to
        that value (aspect preserved), so we resize the short side to
        ``int(side * eval_resize_ratio)`` (268 px at side=256, ratio=1.05)
        and center-crop ``side``. The 1.05 oversize is the mild center
        zoom the conformal q_hat + temperature were calibrated against;
        dropping it (resizing the short side straight to ``side``) fed the
        model a wider frame than calibration saw and skewed both accuracy
        and calibration. Squashing a non-square upload to ``side × side``
        would distort phone photos that are rarely square, so the aspect
        is always preserved.
        """
        w, h = img.size
        target_short = max(side, int(side * self.eval_resize_ratio))
        if w == h == target_short == side:
            return img
        scale = target_short / float(min(w, h))
        new_w = max(side, round(w * scale))
        new_h = max(side, round(h * scale))
        resized = img.resize((new_w, new_h), Image.BILINEAR)
        if new_w == side and new_h == side:
            return resized
        left = (new_w - side) // 2
        top = (new_h - side) // 2
        return resized.crop((left, top, left + side, top + side))

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

    @staticmethod
    def _hflip(x: np.ndarray) -> np.ndarray:
        # Mirror a normalized NCHW batch along the width axis. ORT/torch
        # both need a contiguous buffer, so materialise the reversed view.
        return np.ascontiguousarray(x[..., ::-1])

    # ── ONNX inference ───────────────────────────────────────────────

    def _onnx_logits(self, x: np.ndarray) -> np.ndarray:
        logits = self._onnx_session.run(None, {self._onnx_input_name: x})[0]
        if self.tta == "hflip":
            flipped = self._onnx_session.run(
                None, {self._onnx_input_name: self._hflip(x)}
            )[0]
            logits = (logits + flipped) * 0.5
        return logits

    def _classify_onnx(self, img: Image.Image) -> dict:
        logits = self._onnx_logits(self._preprocess(img))
        return self._format_result_from_logits(logits[0])

    def _classify_onnx_batch(self, imgs: list[Image.Image]) -> list[dict]:
        logits = self._onnx_logits(self._preprocess_batch(imgs))
        return [self._format_result_from_logits(logits[i]) for i in range(logits.shape[0])]

    # ── PyTorch inference ────────────────────────────────────────────

    def _torch_logits(self, x: np.ndarray) -> np.ndarray:
        import torch
        with torch.no_grad():
            logits = self._torch_model(torch.from_numpy(x)).cpu().numpy()
            if self.tta == "hflip":
                flipped = self._torch_model(
                    torch.from_numpy(self._hflip(x))
                ).cpu().numpy()
                logits = (logits + flipped) * 0.5
        return logits

    def _classify_torch(self, img: Image.Image) -> dict:
        logits = self._torch_logits(self._preprocess(img))
        return self._format_result_from_logits(logits[0])

    def _classify_torch_batch(self, imgs: list[Image.Image]) -> list[dict]:
        logits = self._torch_logits(self._preprocess_batch(imgs))
        return [self._format_result_from_logits(logits[i]) for i in range(logits.shape[0])]

    def _format_result_from_logits(self, logits: np.ndarray) -> dict:
        # Numerically-stable softmax (max-shift) in float32. The
        # max-shift caps `exp` arguments at 0 so float32 doesn't
        # overflow; the smallest meaningful tail (`exp(-30)`) is well
        # within float32 range (~9.4e-14). float64 was a precaution
        # that doubled the dict-construction allocation per call for
        # no measurable accuracy delta on the head (7 classes in v2.1,
        # 8 in v2.2).
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
        # ``low_confidence`` is set later, post-aggregation, by
        # ``pipeline._pick_primary`` against ``HEALTHY_MIN_CONFIDENCE``
        # — the per-crop value is not used anywhere downstream.
        return {
            "class_name": self.CLASS_NAMES[top_idx],
            "confidence": round(top_conf, 4),
            "all_probs": {
                self.CLASS_NAMES[i]: round(float(probs[i]), 4)
                for i in range(self.NUM_CLASSES)
            },
            "max_logit": max_logit,
        }
