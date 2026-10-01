"""
Embedding-space out-of-distribution (OOD) detection for PlantVision.

Why this exists
---------------
The historical OOD signal was **MaxLogit** (Hendrycks 2019), AND-gated with
the Stage-1 detector confidence. On the frozen-DINOv2 classifier the
``not_a_plant`` head became confident enough that the MaxLogit separation
between plant and NAP medians went non-positive, so the export auto-disables
the MaxLogit floor (``ood.max_logit_floor: null``) and the AND-gate degrades
to *detector-confidence only*. That throws away the classifier's own opinion
about whether the crop even lies on the in-domain plant manifold.

This module restores a live classifier-side OOD signal that does **not**
depend on the logits at all: a **deep k-NN distance** in the DINOv2 CLS
embedding space (Sun et al., "Out-of-Distribution Detection with Deep Nearest
Neighbors", ICML 2022). We keep a memory bank of L2-normalised in-distribution
(plant) CLS embeddings; a query's non-conformity score is ``1 - mean cosine
similarity to its k nearest bank vectors``. A test crop that lands far from
every in-distribution plant embedding scores high → OOD → routed to
``not_a_plant``. The threshold is calibrated in the training notebook (§6) at a
target in-distribution false-positive rate, exactly mirroring how the old
MaxLogit floor was fit.

The CLS embedding is exported as the classifier ONNX graph's **second output**
(``embedding``), so ``run(...)[0]`` stays the logits for every existing call
site and a legacy single-output bundle simply has no embedding to score
(``load_embedding_ood`` then returns ``None`` and the pipeline keeps its
prior behaviour).

Artefact format (``models/ood_embedding.npz`` + ``classes.json -> ood.embedding``)::

    classes.json:
      "ood": {
        "embedding": {
          "method": "knn",          # only "knn" today
          "metric": "cosine",
          "k": 5,
          "threshold": 0.41,         # score > threshold  => OOD
          "bank_file": "ood_embedding.npz",
          "embed_dim": 384,
          ...calibration metadata...
        }
      }
    ood_embedding.npz:
      bank: (M, D) float32  — in-distribution plant CLS embeddings.

The section is optional. When it is absent, embedding OOD is off. When it is
present, however, every declared artifact is part of the bundle contract:
malformed metadata or a missing/corrupt bank fails readiness rather than
silently changing the routing policy.
"""

from __future__ import annotations

import logging
import math
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

_EPS = 1e-12
_MAX_BANK_UNCOMPRESSED_BYTES = 256 * 1024 * 1024


def _l2_normalize(x: np.ndarray, axis: int = -1) -> np.ndarray:
    """Row-wise L2 normalisation; zero rows are left as zeros (cos→0)."""
    # Scale first so a finite but very large untrusted vector cannot overflow
    # the float32 square/sum inside np.linalg.norm.
    scale = np.max(np.abs(x), axis=axis, keepdims=True)
    scaled = x / np.maximum(scale, _EPS)
    norm = np.linalg.norm(scaled, axis=axis, keepdims=True)
    return scaled / np.maximum(norm, _EPS)


@dataclass(frozen=True)
class EmbeddingOODConfig:
    """Persisted ``classes.json -> ood.embedding`` configuration."""

    method: str          # only "knn" supported today
    metric: str          # declarative only — scoring is ALWAYS cosine
    k: int               # neighbours averaged for the score
    threshold: float     # score strictly above this ⇒ OOD
    embed_dim: int
    bank_file: str = "ood_embedding.npz"


class EmbeddingOODLoadError(RuntimeError):
    """A bundle declared embedding OOD but its contract cannot be loaded."""


class EmbeddingOODScorer:
    """k-NN cosine-distance OOD scorer over a fixed in-distribution bank.

    The score for a query embedding is ``1 - mean(top-k cosine similarity)``
    against the bank, so it lives in ``[0, 2]`` (0 = identical to its nearest
    in-distribution neighbours, larger = further onto the OOD tail). A query
    is OOD when ``score > threshold``.
    """

    def __init__(self, bank: np.ndarray, cfg: EmbeddingOODConfig):
        if bank.ndim != 2 or bank.shape[0] == 0:
            raise ValueError(f"OOD bank must be a non-empty 2-D array, got {bank.shape}")
        if cfg.method != "knn":
            raise ValueError(f"Unsupported OOD method {cfg.method!r} (only 'knn')")
        if cfg.metric != "cosine":
            raise ValueError(f"Unsupported OOD metric {cfg.metric!r} (only 'cosine')")
        if cfg.k <= 0:
            raise ValueError(f"OOD k must be positive, got {cfg.k}")
        if cfg.k > bank.shape[0]:
            raise ValueError(
                f"OOD k={cfg.k} exceeds bank row count {bank.shape[0]}; "
                "clamping would invalidate the calibrated threshold"
            )
        if not math.isfinite(float(cfg.threshold)) or not 0.0 <= cfg.threshold <= 2.0:
            raise ValueError(f"OOD threshold must be finite and in [0, 2], got {cfg.threshold}")
        if cfg.embed_dim <= 0 or bank.shape[1] != cfg.embed_dim:
            raise ValueError(
                f"OOD bank width {bank.shape[1]} != configured embed_dim {cfg.embed_dim}"
            )
        if not np.isfinite(bank).all():
            raise ValueError("OOD bank contains NaN or infinite values")
        row_scale = np.max(np.abs(bank.astype(np.float32, copy=False)), axis=1)
        if np.any(row_scale <= _EPS):
            raise ValueError("OOD bank contains zero-length embeddings")
        # Normalise once at load so scoring is a single matmul + top-k.
        self._bank = _l2_normalize(bank.astype(np.float32, copy=False), axis=1)
        self.cfg = cfg
        self.embed_dim = int(bank.shape[1])
        self._k = int(cfg.k)

    @property
    def threshold(self) -> float:
        return float(self.cfg.threshold)

    def score(self, embedding: np.ndarray) -> float:
        """Return the k-NN cosine non-conformity score for one embedding.

        A dimension mismatch (a query from a different backbone than the bank)
        returns ``nan`` so callers treat the signal as unavailable rather than
        silently rejecting on a meaningless distance.
        """
        z = np.asarray(embedding, dtype=np.float32).reshape(-1)
        if z.shape[0] != self.embed_dim:
            logger.warning(
                "OOD embedding dim %d != bank dim %d — skipping OOD score",
                z.shape[0], self.embed_dim,
            )
            return float("nan")
        if not np.isfinite(z).all() or float(np.max(np.abs(z))) <= _EPS:
            logger.warning("OOD query embedding is non-finite or zero-length — skipping OOD score")
            return float("nan")
        z = _l2_normalize(z, axis=0)
        sims = self._bank @ z                      # (M,) cosine sims
        # Top-k similarities without a full sort (argpartition is O(M)).
        if self._k >= sims.shape[0]:
            topk = sims
        else:
            idx = np.argpartition(-sims, self._k - 1)[: self._k]
            topk = sims[idx]
        return float(1.0 - float(np.mean(topk)))


def parse_embedding_ood_config(meta: dict) -> EmbeddingOODConfig | None:
    """Pull ``ood.embedding`` out of a parsed ``classes.json`` dict.

    Returns ``None`` when the section is absent or malformed. The bundle loader
    distinguishes those cases: absence is an optional capability; a declared
    section that parses to ``None`` is a readiness error.
    """
    if not isinstance(meta, dict):
        return None
    ood = meta.get("ood")
    if not isinstance(ood, dict):
        return None
    emb = ood.get("embedding")
    if not isinstance(emb, dict):
        return None
    try:
        method_raw = emb.get("method", "knn")
        metric_raw = emb.get("metric", "cosine")
        k_raw = emb.get("k", 5)
        threshold_raw = emb["threshold"]
        embed_dim_raw = emb["embed_dim"]
        bank_file_raw = emb.get("bank_file", "ood_embedding.npz")
        if not isinstance(method_raw, str) or not isinstance(metric_raw, str):
            raise TypeError("method and metric must be strings")
        if isinstance(k_raw, bool) or not isinstance(k_raw, int):
            raise TypeError("k must be an integer")
        if (
            isinstance(threshold_raw, bool)
            or not isinstance(threshold_raw, (int, float))
        ):
            raise TypeError("threshold must be numeric")
        if isinstance(embed_dim_raw, bool) or not isinstance(embed_dim_raw, int):
            raise TypeError("embed_dim must be an integer")
        if not isinstance(bank_file_raw, str):
            raise TypeError("bank_file must be a string")
        cfg = EmbeddingOODConfig(
            method=method_raw.lower(),
            metric=metric_raw.lower(),
            k=k_raw,
            threshold=float(threshold_raw),
            embed_dim=embed_dim_raw,
            bank_file=bank_file_raw,
        )
    except (KeyError, ValueError, TypeError) as e:
        logger.warning("Malformed ood.embedding section in classes.json: %s", e)
        return None
    if cfg.method != "knn":
        logger.warning("Unsupported ood.embedding method %r — ignoring", cfg.method)
        return None
    if cfg.metric != "cosine":
        logger.warning(
            "ood.embedding declares metric=%r but scoring is always cosine — "
            "embedding OOD disabled to avoid applying an incompatible threshold",
            cfg.metric,
        )
        return None
    if cfg.k <= 0:
        logger.warning("ood.embedding k must be positive, got %d", cfg.k)
        return None
    if not math.isfinite(cfg.threshold) or not 0.0 <= cfg.threshold <= 2.0:
        logger.warning(
            "ood.embedding threshold must be finite and in [0, 2], got %r",
            cfg.threshold,
        )
        return None
    if cfg.embed_dim <= 0:
        logger.warning("ood.embedding embed_dim must be positive, got %d", cfg.embed_dim)
        return None
    bank_path = Path(cfg.bank_file)
    if (
        bank_path.is_absolute()
        or len(bank_path.parts) != 1
        or bank_path.name != cfg.bank_file
        or bank_path.suffix.lower() != ".npz"
    ):
        logger.warning(
            "ood.embedding bank_file must be a plain .npz filename inside the bundle, got %r",
            cfg.bank_file,
        )
        return None
    return cfg


def load_embedding_ood(model_dir: Path | None, meta: dict) -> EmbeddingOODScorer | None:
    """Build an :class:`EmbeddingOODScorer` from a model bundle, or ``None``.

    Looks for the ``ood.embedding`` config in ``meta`` (parsed ``classes.json``)
    and the companion ``bank_file`` npz next to the weights. Absence of the
    section, or an explicit JSON ``null``, returns ``None`` so MaxLogit-only
    bundles can boot. A declared non-object, or an object that cannot be
    parsed, raises :class:`EmbeddingOODLoadError` so artifact damage cannot
    silently turn a three-signal policy into another policy.
    """
    ood = meta.get("ood") if isinstance(meta, dict) else None
    if not isinstance(ood, dict):
        return None
    emb = ood.get("embedding")
    if emb is None:
        return None
    if not isinstance(emb, dict):
        raise EmbeddingOODLoadError("Declared ood.embedding metadata is invalid")
    cfg = parse_embedding_ood_config(meta)
    if cfg is None:
        raise EmbeddingOODLoadError("Declared ood.embedding metadata is invalid")
    if model_dir is None:
        raise EmbeddingOODLoadError("Declared ood.embedding has no model directory")
    model_root = Path(model_dir).resolve()
    npz_path = model_root / cfg.bank_file
    if not npz_path.exists():
        raise EmbeddingOODLoadError(
            f"ood.embedding configured but bank file is missing: {npz_path}"
        )
    try:
        # Resolve symlinks too: an untrusted sidecar may not escape the bundle
        # with ``../`` or by pointing a filename symlink at an arbitrary host
        # file.  The parser above already limits bank_file to one path segment.
        resolved = npz_path.resolve(strict=True)
        if resolved.parent != model_root:
            raise ValueError(f"bank resolves outside model bundle: {resolved}")
        # NPZ is a ZIP container.  Bound the expanded members before NumPy
        # allocates them so a tiny decompression bomb cannot exhaust the host.
        with zipfile.ZipFile(resolved) as archive:
            expanded = sum(info.file_size for info in archive.infolist())
        if expanded > _MAX_BANK_UNCOMPRESSED_BYTES:
            raise ValueError(
                f"expanded bank is {expanded} bytes (limit {_MAX_BANK_UNCOMPRESSED_BYTES})"
            )
        with np.load(resolved, allow_pickle=False) as data:
            if "bank" not in data.files:
                raise ValueError("NPZ has no 'bank' array")
            bank = np.asarray(data["bank"], dtype=np.float32)
        if bank.ndim != 2 or bank.shape[0] == 0:
            raise ValueError(f"bank has shape {bank.shape}")
        if bank.shape[1] != cfg.embed_dim:
            raise ValueError(
                f"bank embed_dim {bank.shape[1]} != config embed_dim {cfg.embed_dim}"
            )
        scorer = EmbeddingOODScorer(bank, cfg)
    except EmbeddingOODLoadError:
        raise
    except Exception as e:
        raise EmbeddingOODLoadError(
            f"Failed to load declared embedding OOD bank {npz_path}: {e}"
        ) from e
    logger.info(
        "Embedding OOD loaded: method=%s metric=%s k=%d threshold=%.4f "
        "bank=%d×%d from %s",
        cfg.method, cfg.metric, scorer._k, cfg.threshold,
        bank.shape[0], bank.shape[1], npz_path,
    )
    return scorer
