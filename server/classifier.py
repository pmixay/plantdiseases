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
(reported by ``/api/capabilities``), ``img_size`` and any ``inference.tta``
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

# Backbone family of the trained checkpoint, reported by /api/capabilities.
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
MODEL_FAMILIES = ("aqua", "gamma", "frontier")


class ClassifierLoadError(RuntimeError):
    """Raised when no usable classifier artefact can be loaded."""


def _load_metadata(model_dir: Path) -> dict:
    meta = model_dir / "classes.json"
    if not meta.exists():
        return {}
    try:
        parsed = json.loads(meta.read_text(encoding="utf-8"))
    except Exception as e:
        raise ClassifierLoadError(f"Failed to read classifier metadata {meta}: {e}") from e
    if not isinstance(parsed, dict):
        raise ClassifierLoadError(
            f"Classifier metadata {meta} must contain a JSON object, got "
            f"{type(parsed).__name__}"
        )
    return parsed


def _section(meta: dict, name: str) -> dict:
    value = meta.get(name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ClassifierLoadError(
            f"classes.json -> {name} must be an object, got {type(value).__name__}"
        )
    return value


def _resolve_class_names(meta: dict) -> list[str]:
    if "class_names" not in meta:
        return list(DEFAULT_CLASS_NAMES)
    names = meta["class_names"]
    if not isinstance(names, list) or not names:
        raise ClassifierLoadError("classes.json -> class_names must be a non-empty array")
    if any(not isinstance(name, str) or not name or name != name.strip() for name in names):
        raise ClassifierLoadError(
            "classes.json -> class_names must contain non-empty, trimmed strings"
        )
    if len(set(names)) != len(names):
        raise ClassifierLoadError("classes.json -> class_names contains duplicate keys")
    return list(names)


def _resolve_img_size(meta: dict) -> int:
    training = _section(meta, "training")
    if "img_size" not in training:
        return DEFAULT_IMG_SIZE
    size = training["img_size"]
    if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= 4096:
        raise ClassifierLoadError(
            "classes.json -> training.img_size must be an integer in [1, 4096]"
        )
    return size


def _resolve_eval_resize_ratio(meta: dict) -> float:
    training = _section(meta, "training")
    if "eval_resize_ratio" not in training:
        return DEFAULT_EVAL_RESIZE_RATIO
    ratio = training["eval_resize_ratio"]
    if (
        isinstance(ratio, bool)
        or not isinstance(ratio, (int, float))
        or not math.isfinite(float(ratio))
        or not 1.0 <= float(ratio) <= 4.0
    ):
        raise ClassifierLoadError(
            "classes.json -> training.eval_resize_ratio must be finite and in [1, 4]"
        )
    return float(ratio)


def _resolve_architecture(meta: dict) -> str:
    arch = meta.get("architecture") or _section(meta, "training").get("architecture")
    if isinstance(arch, str) and arch:
        return arch
    return DEFAULT_ARCHITECTURE


def _resolve_tta(meta: dict) -> str:
    inference = _section(meta, "inference")
    if "tta" not in inference:
        return "none"
    mode = inference["tta"]
    if not isinstance(mode, str) or mode not in TTA_MODES:
        raise ClassifierLoadError(
            f"classes.json -> inference.tta must be one of {TTA_MODES}, got {mode!r}"
        )
    return mode


def _resolve_normalization(meta: dict) -> tuple[np.ndarray, np.ndarray]:
    """Resolve optional ImageNet-style RGB normalisation from the sidecar.

    Historical gamma bundles omit these fields and use the built-in ImageNet
    defaults.  When a future bundle declares them, serving must honour them;
    silently keeping stale hard-coded values would invalidate every fitted
    calibration artefact.
    """
    inference = _section(meta, "inference")

    def _triple(key: str, fallback: np.ndarray, *, positive: bool) -> np.ndarray:
        if key not in inference:
            return fallback.copy()
        raw = inference[key]
        if not isinstance(raw, list) or len(raw) != 3:
            raise ClassifierLoadError(
                f"classes.json -> inference.{key} must be an array of three numbers"
            )
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in raw):
            raise ClassifierLoadError(
                f"classes.json -> inference.{key} must contain only numbers"
            )
        arr = np.asarray(raw, dtype=np.float32)
        if not np.isfinite(arr).all() or (positive and np.any(arr <= 0)):
            qualifier = "finite positive" if positive else "finite"
            raise ClassifierLoadError(
                f"classes.json -> inference.{key} values must be {qualifier}"
            )
        return arr

    return (
        _triple("normalize_mean", NORM_MEAN, positive=False),
        _triple("normalize_std", NORM_STD, positive=True),
    )


def _resolve_model_family(meta: dict) -> str:
    """Capacity tier declared by the bundle (classes.json -> model_family):
    ``aqua`` (light/on-device), ``gamma`` (current medium), ``frontier``
    (future high-capacity). Defaults to ``gamma`` for bundles trained before
    the field existed — the runtime stays architecture-neutral either way."""
    if "model_family" not in meta:
        return "gamma"
    fam = meta["model_family"]
    if not isinstance(fam, str) or fam not in MODEL_FAMILIES:
        raise ClassifierLoadError(
            f"classes.json -> model_family must be one of {MODEL_FAMILIES}, got {fam!r}"
        )
    return fam


def _resolve_onnx_candidates(
    model_path: Path | None,
    *,
    exact_model_path: bool = False,
) -> list[Path]:
    """Return the single historical active graph for an unmanifested bundle.

    The exporter fits temperature/conformal/OOD sidecars against INT8 whenever
    it ships. Falling back to the sibling FP32 graph after an INT8 load failure
    would therefore combine one model with another model's calibration. Fail
    readiness instead. A future checksum manifest selects one exact graph and
    calibration profile explicitly; it must never encode an implicit fallback.
    """
    if model_path is None:
        return []
    if exact_model_path:
        path = Path(model_path)
        return [path] if path.is_file() else []
    int8 = model_path.parent / "classifier_int8.onnx"
    fp32 = model_path.parent / "classifier.onnx"
    if int8.is_file():
        return [int8]
    return [fp32] if fp32.is_file() else []


class DiseaseClassifier:
    """Houseplant disease classifier (ONNX only)."""

    def __init__(
        self,
        model_path: Path | None = None,
        *,
        exact_model_path: bool = False,
    ):
        self.backend = ""               # "onnx"
        self._onnx_session: Any = None  # onnxruntime.InferenceSession (untyped package)
        self._onnx_input_name: str = ""
        # Set when the shipped graph exports the DINOv2 CLS embedding as a
        # second output (``output_names=['logits','embedding']``). Drives
        # the embedding-space OOD score in the pipeline; a legacy
        # single-output graph leaves this False and OOD falls back to the
        # MaxLogit floor.
        self._has_embedding: bool = False
        self._embedding_dim: int | None = None

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
        self.norm_mean, self.norm_std = _resolve_normalization(meta)
        self.CLASS_NAMES = _resolve_class_names(meta)
        self.NUM_CLASSES = len(self.CLASS_NAMES)
        self.img_size = _resolve_img_size(meta)
        # Logit indices EXCLUDING ``not_a_plant``. The all-class MaxLogit is a
        # dead OOD signal on this head: ``not_a_plant`` is a *trained* class, so
        # a NAP photo scores a high NAP logit and its all-class max sits ABOVE
        # the plant median — which is why every export since v2.3 auto-disabled
        # the floor (``separation_p50`` < 0 in each run log). The max over the
        # PLANT logits only is the quantity the gate actually wants ("does this
        # look like any plant condition we know?") and separates by
        # construction. Empty tuple ⇒ no NAP class ⇒ falls back to the all-class
        # max, so a taxonomy without ``not_a_plant`` behaves exactly as before.
        self._plant_logit_idx = tuple(
            i for i, n in enumerate(self.CLASS_NAMES) if n != "not_a_plant"
        )

        candidates = _resolve_onnx_candidates(
            model_path,
            exact_model_path=exact_model_path,
        )
        if not candidates:
            raise ClassifierLoadError(
                "Cannot start without a trained classifier ONNX artefact "
                "(classifier_int8.onnx / classifier.onnx) next to the "
                f"configured model path {model_path}. Provision the model "
                "files into the models directory before starting."
            )
        errors: list[str] = []
        for onnx_path in candidates:
            try:
                self._load_onnx(onnx_path)
                break
            except Exception as e:
                errors.append(f"{onnx_path.name}: {type(e).__name__}: {e}")
                self._onnx_session = None
                self._onnx_input_name = ""
                self._has_embedding = False
                self._embedding_dim = None
                logger.error("Classifier candidate %s is unusable: %s", onnx_path, e)
        else:
            raise ClassifierLoadError(
                "No usable ONNX classifier in the bundle. " + " | ".join(errors)
            )

    # ── loader ───────────────────────────────────────────────────────

    def _load_onnx(self, path: Path) -> None:
        import onnxruntime as ort

        opts = ort.SessionOptions()
        # Intra-op threads default to ORT's own pick (= physical cores).
        # The classifier now runs a DINOv2 ViT backbone whose latency is
        # dominated by intra-op parallelism, so a single inference wants
        # all the cores it can get. To avoid oversubscription this is
        # paired with `INFER_WORKERS=1` on the FastAPI side (one inference
        # at a time, each using the full CPU) — see main.py. Both are
        # config-overridable for shared hosts.
        opts.intra_op_num_threads = onnx_intra_op_threads()
        opts.inter_op_num_threads = 1
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        sess = ort.InferenceSession(
            str(path),
            sess_options=opts,
            providers=["CPUExecutionProvider"],
        )
        inputs = sess.get_inputs()
        outputs = sess.get_outputs()
        if len(inputs) != 1:
            raise ClassifierLoadError(
                f"Classifier graph must have exactly one input, found {len(inputs)}"
            )
        if len(outputs) not in (1, 2):
            raise ClassifierLoadError(
                "Classifier graph must expose logits and optionally one embedding "
                f"output, found {len(outputs)} outputs"
            )
        inp = inputs[0]
        out = outputs[0]
        # A 2-output graph ships the CLS embedding alongside the logits.
        # Output[0] stays the logits so every existing ``run(...)[0]`` call
        # site is unaffected; output[1], when present, is the embedding.
        if inp.type != "tensor(float)" or len(inp.shape) != 4:
            raise ClassifierLoadError(
                "Classifier input must be a rank-4 float32 NCHW tensor, got "
                f"type={inp.type} shape={inp.shape}"
            )
        expected_input = (3, self.img_size, self.img_size)
        if tuple(inp.shape[1:]) != expected_input:
            raise ClassifierLoadError(
                f"Classifier input shape {inp.shape} does not match metadata "
                f"NCHW [N, {expected_input[0]}, {expected_input[1]}, {expected_input[2]}]"
            )
        if isinstance(inp.shape[0], int):
            raise ClassifierLoadError(
                "Classifier batch dimension must be dynamic: serving batches all "
                "detector crops and hflip TTA doubles that batch"
            )
        if out.type != "tensor(float)" or len(out.shape) != 2:
            raise ClassifierLoadError(
                f"Classifier logits must be rank-2 float32, got type={out.type} "
                f"shape={out.shape}"
            )
        if isinstance(out.shape[0], int):
            raise ClassifierLoadError(
                "Classifier logits batch dimension must be dynamic to match the "
                "dynamic input and multi-crop serving path"
            )
        out_dim = out.shape[-1]
        if not isinstance(out_dim, int) or out_dim != self.NUM_CLASSES:
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

        self._has_embedding = len(outputs) == 2
        if self._has_embedding:
            emb_out = outputs[1]
            emb_dim = emb_out.shape[-1] if len(emb_out.shape) == 2 else None
            if (
                emb_out.type != "tensor(float)"
                or isinstance(emb_out.shape[0], int)
                or not isinstance(emb_dim, int)
                or emb_dim <= 0
            ):
                raise ClassifierLoadError(
                    "Classifier embedding must be rank-2 float32 with a fixed positive "
                    f"width, got type={emb_out.type} shape={emb_out.shape}"
                )
            self._embedding_dim = emb_dim

        ood = _section(self.meta, "ood")
        emb_meta = ood.get("embedding")
        if emb_meta is not None:
            if not isinstance(emb_meta, dict):
                raise ClassifierLoadError("classes.json -> ood.embedding must be an object")
            if not self._has_embedding:
                raise ClassifierLoadError(
                    "classes.json configures embedding OOD but the classifier graph has "
                    "no embedding output"
                )
            declared_dim = emb_meta.get("embed_dim")
            if (
                isinstance(declared_dim, bool)
                or not isinstance(declared_dim, int)
                or declared_dim != self._embedding_dim
            ):
                raise ClassifierLoadError(
                    f"classes.json declares OOD embed_dim={declared_dim!r}, but the "
                    f"classifier graph emits {self._embedding_dim} values"
                )

        self._onnx_session = sess
        self._onnx_input_name = inp.name
        self.backend = "onnx"

        # Warm AND contract-test the graph.  For hflip, use batch=2: accepting
        # batch=1 says nothing about whether the declared TTA path can run.
        # Exercise a real multi-member batch even without hflip. The pipeline
        # submits several detector crops (plus an optional whole-frame vote),
        # so a graph with a nominally dynamic input but an internal batch-1
        # reshape must fail readiness rather than the first user request.
        warm_n = 2
        warm = np.zeros((warm_n, 3, self.img_size, self.img_size), dtype=np.float32)
        warm_out = sess.run(None, {inp.name: warm})
        expected_shapes: list[tuple[int, int | None]] = [(warm_n, self.NUM_CLASSES)]
        if self._has_embedding:
            expected_shapes.append((warm_n, self._embedding_dim))
        if [tuple(value.shape) for value in warm_out] != expected_shapes:
            raise ClassifierLoadError(
                f"Classifier runtime outputs {[tuple(v.shape) for v in warm_out]} do not "
                f"match expected {expected_shapes}"
            )
        if not all(np.isfinite(value).all() for value in warm_out):
            raise ClassifierLoadError("Classifier emits non-finite values on warmup input")

        logger.info(
            "Classifier (ONNX) loaded (%d classes, img_size=%d, "
            "arch=%s, tta=%s, embedding_output=%s)",
            self.NUM_CLASSES, self.img_size, self.architecture, self.tta,
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
        # Always apply Resize -> CenterCrop.  A source that already happens to
        # be img_size square still needs the configured 1.05 centre zoom; the
        # old size shortcut silently gave just those inputs a different eval
        # distribution from training/calibration.
        img = self._aspect_preserving_square(img, self.img_size)
        arr = np.asarray(img, dtype=np.float32) / 255.0
        arr = (arr - self.norm_mean) / self.norm_std
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
        if w <= 0 or h <= 0:
            raise ValueError(f"Cannot preprocess an empty image with size {img.size}")
        target_short = max(side, int(side * self.eval_resize_ratio))
        scale = target_short / float(min(w, h))
        new_w = max(side, round(w * scale))
        new_h = max(side, round(h * scale))
        resized = img.resize((new_w, new_h), Image.Resampling.BILINEAR)
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
        logits32 = np.asarray(logits, dtype=np.float32).reshape(-1)
        if logits32.shape != (self.NUM_CLASSES,):
            raise RuntimeError(
                f"Classifier returned {logits32.shape} logits for {self.NUM_CLASSES} classes"
            )
        if not np.isfinite(logits32).all():
            # A fabricated uniform response looks usable and can become a real
            # diagnosis downstream.  Surface an inference failure instead.
            raise RuntimeError("Classifier returned non-finite logits")

        # Numerically-stable softmax (max-shift) in float32. The
        # max-shift caps `exp` arguments at 0 so float32 doesn't
        # overflow; the smallest meaningful tail (`exp(-30)`) is well
        # within float32 range (~9.4e-14).
        max_logit = float(logits32.max())
        e = np.exp(logits32 - max_logit)
        denom = float(e.sum())
        if denom <= 0 or not np.isfinite(denom):
            raise RuntimeError("Classifier softmax denominator is not finite and positive")
        probs = e / denom
        top_idx = int(np.argmax(probs))
        top_conf = float(probs[top_idx])
        # ``low_confidence`` is set later, post-aggregation, by
        # ``pipeline._pick_primary`` against ``HEALTHY_MIN_CONFIDENCE``
        # — the per-crop value is not used anywhere downstream.
        result = {
            "class_name": self.CLASS_NAMES[top_idx],
            # Keep full precision through temperature scaling, voting and OOD
            # policy.  The API response boundary rounds for display; rounding
            # here perturbed the very probabilities the bundle calibrated.
            "confidence": top_conf,
            "all_probs": {
                self.CLASS_NAMES[i]: float(probs[i])
                for i in range(self.NUM_CLASSES)
            },
            "max_logit": max_logit,
            # Plant-only MaxLogit (see ``_plant_logit_idx``). Always emitted so
            # the pipeline can honour ``classes.json -> ood.max_logit_scope``
            # without re-deriving it; equals ``max_logit`` when the taxonomy has
            # no ``not_a_plant`` class.
            "plant_max_logit": (
                float(logits32[list(self._plant_logit_idx)].max())
                if self._plant_logit_idx
                else max_logit
            ),
        }
        # The raw CLS embedding (when the graph exports it) rides along for
        # the pipeline's embedding-space OOD score. It is an internal
        # numpy array — the pipeline consumes it and never forwards it into
        # the JSON response, so it doesn't touch the public API contract.
        if embedding is not None:
            emb = np.asarray(embedding, dtype=np.float32).reshape(-1)
            if emb.shape == (self._embedding_dim,) and np.isfinite(emb).all():
                result["embedding"] = emb
            else:
                logger.warning(
                    "Classifier emitted unusable embedding shape=%s finite=%s; "
                    "embedding OOD will fall back",
                    emb.shape, bool(np.isfinite(emb).all()),
                )
        return result
