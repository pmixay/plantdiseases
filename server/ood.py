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

The file is optional. When missing/malformed, ``load_embedding_ood`` returns
``None`` and OOD falls back to whatever ``classes.json -> ood.max_logit_floor``
declares (typically the auto-disabled gate).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

_EPS = 1e-12


def _l2_normalize(x: np.ndarray, axis: int = -1) -> np.ndarray:
    """Row-wise L2 normalisation; zero rows are left as zeros (cos→0)."""
    norm = np.linalg.norm(x, axis=axis, keepdims=True)
    return x / np.maximum(norm, _EPS)


@dataclass(frozen=True)
class EmbeddingOODConfig:
    """Persisted ``classes.json -> ood.embedding`` configuration."""

    method: str          # only "knn" supported today
    metric: str          # "cosine"
    k: int               # neighbours averaged for the score
    threshold: float     # score strictly above this ⇒ OOD
    embed_dim: int
    bank_file: str = "ood_embedding.npz"


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
        # Normalise once at load so scoring is a single matmul + top-k.
        self._bank = _l2_normalize(bank.astype(np.float32, copy=False), axis=1)
        self.cfg = cfg
        self.embed_dim = int(bank.shape[1])
        # Never ask for more neighbours than the bank holds.
        self._k = max(1, min(int(cfg.k), self._bank.shape[0]))

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

    Returns ``None`` (no scorer) when the section is absent or malformed —
    OOD then falls back to the MaxLogit floor path. A present-but-broken
    section is treated as absent (logged) rather than raising, so a stale
    bundle can never crash boot.
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
        method = str(emb.get("method", "knn")).lower()
        threshold = float(emb["threshold"])
        embed_dim = int(emb["embed_dim"])
        cfg = EmbeddingOODConfig(
            method=method,
            metric=str(emb.get("metric", "cosine")).lower(),
            k=int(emb.get("k", 5)),
            threshold=threshold,
            embed_dim=embed_dim,
            bank_file=str(emb.get("bank_file", "ood_embedding.npz")),
        )
    except (KeyError, ValueError, TypeError) as e:
        logger.warning("Malformed ood.embedding section in classes.json: %s", e)
        return None
    if cfg.method != "knn":
        logger.warning("Unsupported ood.embedding method %r — ignoring", cfg.method)
        return None
    return cfg


def load_embedding_ood(model_dir: Path | None, meta: dict) -> EmbeddingOODScorer | None:
    """Build an :class:`EmbeddingOODScorer` from a model bundle, or ``None``.

    Looks for the ``ood.embedding`` config in ``meta`` (parsed ``classes.json``)
    and the companion ``bank_file`` npz next to the weights. Any problem
    (missing file, bad shape, dim mismatch) downgrades to ``None`` with a log
    line — embedding OOD is a best-effort enhancement, never a boot blocker.
    """
    cfg = parse_embedding_ood_config(meta)
    if cfg is None or model_dir is None:
        return None
    npz_path = Path(model_dir) / cfg.bank_file
    if not npz_path.exists():
        logger.warning(
            "ood.embedding configured but bank file %s is missing — "
            "embedding OOD disabled", npz_path,
        )
        return None
    try:
        with np.load(npz_path) as data:
            bank = np.asarray(data["bank"], dtype=np.float32)
        if bank.ndim != 2 or bank.shape[0] == 0:
            raise ValueError(f"bank has shape {bank.shape}")
        if bank.shape[1] != cfg.embed_dim:
            raise ValueError(
                f"bank embed_dim {bank.shape[1]} != config embed_dim {cfg.embed_dim}"
            )
        scorer = EmbeddingOODScorer(bank, cfg)
    except Exception as e:  # noqa: BLE001 — never block boot on OOD setup
        logger.warning("Failed to load embedding OOD bank %s: %s", npz_path, e)
        return None
    logger.info(
        "Embedding OOD loaded: method=%s metric=%s k=%d threshold=%.4f "
        "bank=%d×%d from %s",
        cfg.method, cfg.metric, scorer._k, cfg.threshold,
        bank.shape[0], bank.shape[1], npz_path,
    )
    return scorer
