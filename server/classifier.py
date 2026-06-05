"""
Stage 2 — PlantVision houseplant disease classifier.

Classifies a (preferably cropped) leaf region into one of the
houseplant-relevant condition classes including the rejection class
``not_a_plant``. Class names + input resolution live in
``models/classes.json``; if the file is missing the built-in default
matching the current trainable taxonomy (9 classes) is used.

The production artefact is an ONNX graph and the runtime is
**ONNX-only and architecture-neutral** — it serves whatever backbone+head
graph is shipped (the current frozen-DINOv2 ViT-S/14 + LoRA + MLP head)
with no code change. ``classes.json`` declares the ``architecture``
(reported by ``/api/version``), ``img_size`` and any ``inference.tta``
mode. Loading order:

1. ``classifier_int8.onnx`` (quantized, smallest + fastest)
2. ``classifier.onnx``      (FP32 ONNX)

Loading is fail-fast: if no usable ONNX artefact is found, ``__init__``
raises ``ClassifierLoadError`` so the API never boots in a half-broken
state. There is no PyTorch / heuristic / demo fallback path, and no
class-count migration shim — ``classes.json`` must match the trained
head exactly or boot aborts.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

DEFAULT_IMG_SIZE = 224
# Train/serve crop parity: the notebook eval/calibration transform is
# ``Resize(int(img_size * EVAL_RESIZE_RATIO)) -> CenterCrop(img_size)``
# (a mild center zoom that trims the border). Conformal q_hat and the
# temperature were fit on that distribution, so serving must reproduce
# it — resizing the short side straight to ``img_size`` (ratio 1.0) fed
# the model a slightly wider frame than calibration ever saw. The ratio
# is overridable from ``classes.json -> training.eval_resize_ratio``.
DEFAULT_EVAL_RESIZE_RATIO = 1.05

# Alphabetical — must match training-time ``sorted(ImageFolder.classes)``.
# Current trainable taxonomy (9 classes). ``leaf_spot`` is the merged
# bacterial+fungal leaf-spot class (the distinction is not reliably
# separable from a phone photo); ``pests`` is the combined sap-sucking
# pest class (mealybugs + scale). Production reads
# ``classes.json -> class_names`` first; this list is only used as a
# fallback when the artefact is missing.
DEFAULT_CLASS_NAMES = [
    "gray_mold_botrytis",
    "healthy",
    "leaf_scorch_underwatering",
    "leaf_spot",
    "not_a_plant",
    "nutrient_deficiency",
    "pests",
    "powdery_mildew",
    "viral_disease",
]

NORM_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
NORM_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

# Backbone family of the trained checkpoint, reported by /api/version.
# Production loads an architecture-neutral ONNX graph, so this is metadata
# only. ``classes.json -> architecture`` overrides it.
DEFAULT_ARCHITECTURE = "dinov2_vits14+mlp_head"

# Test-time augmentation mode. ``none`` (default) is single-pass. ``hflip``
# runs the image and its horizontal mirror and AVERAGES THE LOGITS (not the
# softmax probs): averaging logits keeps every downstream calibration step —
# temperature scaling, the MaxLogit OOD score, conformal RAPS — operating on
# the same kind of quantity it was fit on, so enabling TTA does not silently
# decalibrate the model. The contract is therefore: temperature.json /
# conformal.json / the OOD floor MUST be re-fit with the matching TTA mode
# (the training notebook does this from ``classes.json -> inference``).
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


def _resolve_model_family(meta: dict) -> str:
    """Capacity tier declared by the bundle (classes.json -> model_family):
    ``aqua`` (light/on-device), ``gamma`` (current medium), ``frontier``
    (future high-capacity). Defaults to ``gamma`` for bundles trained before
    the field existed — the runtime stays architecture-neutral either way."""
    fam = meta.get("model_family")
    if isinstance(fam, str) and fam:
        return fam
    return "gamma"


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
    """Houseplant disease classifier (ONNX only)."""

    def __init__(self, model_path: Path | None = None):
        self.backend = ""               # "onnx"
        self._onnx_session = None
        self._onnx_input_name: str = ""
        # Set when the shipped graph exports the DINOv2 CLS embedding as a
        # second output (``output_names=['logits','embedding']``). Drives
        # the embedding-space OOD score in the pipeline; a legacy
        # single-output graph leaves this False and OOD falls back to the
        # MaxLogit floor.
        self._has_embedding: bool = False

        meta = _load_metadata(model_path.parent) if model_path else {}
        self.meta = meta
        # Track provenance: a class list that came from classes.json is a
        # hard contract (must match the checkpoint head exactly), whereas
        # the built-in default is a best-effort fallback for a fresh clone.
        self._class_names_from_meta = bool(meta.get("class_names"))
        self.eval_resize_ratio = _resolve_eval_resize_ratio(meta)
        self.architecture = _resolve_architecture(meta)
        self.model_family = _resolve_model_family(meta)
        self.tta = _resolve_tta(meta)
        self.CLASS_NAMES = _resolve_class_names(meta)
        self.NUM_CLASSES = len(self.CLASS_NAMES)
        self.img_size = _resolve_img_size(meta)

        onnx_path = _resolve_onnx_path(model_path)
        if onnx_path is None:
            raise ClassifierLoadError(
                "Cannot start without a trained classifier ONNX artefact "
                "(classifier_int8.onnx / classifier.onnx) next to the "
                f"configured model path {model_path}. Provision the model "
                "files into the models directory before starting."
            )
        try:
            self._load_onnx(onnx_path)
        except ClassifierLoadError:
            raise
        except Exception as e:
            raise ClassifierLoadError(
                f"Failed to load ONNX classifier {onnx_path}: {e}"
            ) from e

    @property
    def is_loaded(self) -> bool:
        return bool(self.backend)

    # ── loader ───────────────────────────────────────────────────────

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
        outputs = sess.get_outputs()
        out = outputs[0]
        # A 2-output graph ships the CLS embedding alongside the logits.
        # Output[0] stays the logits so every existing ``run(...)[0]`` call
        # site is unaffected; output[1], when present, is the embedding.
        self._has_embedding = len(outputs) >= 2

        out_dim = next((d for d in out.shape if isinstance(d, int)), None)
        if out_dim is not None and out_dim != self.NUM_CLASSES:
            # The class set MUST match the trained head exactly. We do not
            # pad / truncate (the old ``_fit_class_names`` migration shim
            # silently mislabelled every prediction when a stale classes.json
            # met a newer head). Fail fast instead so a wrong class list can
            # never boot serving mislabeled answers.
            source = "classes.json" if self._class_names_from_meta else "built-in default"
            raise ClassifierLoadError(
                f"The {source} declares {self.NUM_CLASSES} classes "
                f"({self.CLASS_NAMES}) but the ONNX checkpoint at {path} has "
                f"{out_dim} outputs. Ship a classes.json whose class_names "
                f"match the trained head, in training order."
            )

        self._onnx_session = sess
        self._onnx_input_name = inp.name
        self.backend = "onnx"

        # Warm the graph once at load so the first real request doesn't
        # eat the one-off allocation / kernel-selection cost.
        try:
            warm = np.zeros((1, 3, self.img_size, self.img_size), dtype=np.float32)
            sess.run(None, {inp.name: warm})
        except Exception as e:  # noqa: BLE001 — warmup is best-effort
            logger.debug("Classifier ONNX warmup skipped: %s", e)

        logger.info(
            "Classifier (ONNX) loaded from %s (%d classes, img_size=%d, "
            "arch=%s, tta=%s, embedding_output=%s)",
            path, self.NUM_CLASSES, self.img_size, self.architecture, self.tta,
            self._has_embedding,
        )

    # ── public API ───────────────────────────────────────────────────

    def classify(self, img: Image.Image) -> dict:
        return self._classify_onnx(img)

    def classify_batch(self, imgs: list[Image.Image]) -> list[dict]:
        if not imgs:
            return []
        return self._classify_onnx_batch(imgs)

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
        ``int(side * eval_resize_ratio)`` (235 px at side=224, ratio=1.05)
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
        # Mirror a normalized NCHW batch along the width axis. ORT needs a
        # contiguous buffer, so materialise the reversed view.
        return np.ascontiguousarray(x[..., ::-1])

    # ── ONNX inference ───────────────────────────────────────────────

    def _onnx_forward(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray | None]:
        """Run the graph, returning ``(logits, embeddings_or_None)``.

        ``embeddings`` is the second graph output (the DINOv2 CLS vector)
        when the shipped bundle exports it, else ``None``. Under hflip TTA
        both the logits *and* the embeddings are averaged over the image and
        its mirror — the embedding then matches the (averaged) logits the
        calibration was fit on, so the OOD score stays consistent with TTA.
        """
        sess, in_name = self._onnx_session, self._onnx_input_name
        if self.tta == "hflip":
            # Batch the original and its mirror into a single 2N forward
            # instead of two N-sized sess.run calls. The result is
            # byte-identical (same two sets of outputs, averaged) but pays
            # the ORT/Python dispatch + kernel-selection cost once, and
            # lets ORT schedule both halves together — material on the
            # single-intra-op-thread CPU sessions we run.
            n = x.shape[0]
            batched = np.concatenate([x, self._hflip(x)], axis=0)
            outs = sess.run(None, {in_name: batched})
            logits = (outs[0][:n] + outs[0][n:]) * 0.5
            emb = ((outs[1][:n] + outs[1][n:]) * 0.5) if self._has_embedding else None
            return logits, emb
        outs = sess.run(None, {in_name: x})
        emb = outs[1] if self._has_embedding else None
        return outs[0], emb

    def _classify_onnx(self, img: Image.Image) -> dict:
        logits, emb = self._onnx_forward(self._preprocess(img))
        return self._format_result_from_logits(
            logits[0], emb[0] if emb is not None else None,
        )

    def _classify_onnx_batch(self, imgs: list[Image.Image]) -> list[dict]:
        logits, emb = self._onnx_forward(self._preprocess_batch(imgs))
        return [
            self._format_result_from_logits(
                logits[i], emb[i] if emb is not None else None,
            )
            for i in range(logits.shape[0])
        ]

    def _format_result_from_logits(
        self, logits: np.ndarray, embedding: np.ndarray | None = None,
    ) -> dict:
        # Numerically-stable softmax (max-shift) in float32. The
        # max-shift caps `exp` arguments at 0 so float32 doesn't
        # overflow; the smallest meaningful tail (`exp(-30)`) is well
        # within float32 range (~9.4e-14).
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
        result = {
            "class_name": self.CLASS_NAMES[top_idx],
            "confidence": round(top_conf, 4),
            "all_probs": {
                self.CLASS_NAMES[i]: round(float(probs[i]), 4)
                for i in range(self.NUM_CLASSES)
            },
            "max_logit": max_logit,
        }
        # The raw CLS embedding (when the graph exports it) rides along for
        # the pipeline's embedding-space OOD score. It is an internal
        # numpy array — the pipeline consumes it and never forwards it into
        # the JSON response, so it doesn't touch the public API contract.
        if embedding is not None:
            result["embedding"] = np.asarray(embedding, dtype=np.float32)
        return result
