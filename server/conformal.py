"""
Inductive conformal prediction for the PlantVision classifier.

Temperature scaling fixes the *miscalibration* of the softmax (so a 0.85
probability really means 85 %). It does **not** fix the second failure
mode the UI cares about: at the top-1 boundary between two visually
similar classes (e.g. ``leaf_spot_fungal`` ↔ ``bacterial_leaf_spot``)
the model still routinely returns a single confident answer when the
honest answer is "either of these two".

Inductive conformal prediction (Vovk; Angelopoulos & Bates, 2021) wraps
a calibrated classifier and emits a **prediction set** with a finite-
sample coverage guarantee: under exchangeability, the true label lies
in the set with probability at least ``1 - alpha`` (e.g. 90 % for
``alpha=0.1``).

We support two scoring schemes:

* **APS** (Adaptive Prediction Sets, Romano et al. 2020) — accumulate
  sorted softmax mass until the cumulative score crosses the calibrated
  quantile ``q_hat``.
* **RAPS** (Regularized APS, Angelopoulos et al. 2021) — APS plus a
  size penalty ``lambda * max(0, k - k_reg)`` that discourages
  pathologically large sets on hard examples.

The calibration scheme is **inductive**: a held-out calibration split
(typically 1–2 k user photos) yields one ``q_hat`` per ``alpha``. Online
inference is then an O(C log C) sort + a comparison; no re-training.

Calibration artefact format (``models/conformal.json``)::

    {
      "method": "raps",        # "aps" | "raps"
      "alpha": 0.1,             # 1 - target coverage
      "q_hat": 0.86,            # calibrated quantile threshold
      "lambda_reg": 0.01,       # RAPS size penalty (RAPS only)
      "k_reg": 1,               # RAPS truncation rank (RAPS only)
      "calibration_n": 1500,    # held-out cal-set size
      "empirical_coverage": 0.91
    }

The file is optional. When missing, the pipeline simply omits the
``prediction_set`` field — clients should treat it as a hint, never
gate UX on its presence.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConformalConfig:
    """Persisted configuration loaded from ``conformal.json``."""

    method: str          # "aps" | "raps"
    alpha: float         # in (0, 1); coverage target = 1 - alpha
    q_hat: float         # calibrated quantile of the non-conformity score
    lambda_reg: float = 0.0
    k_reg: int = 1
    calibration_n: int = 0
    empirical_coverage: float | None = None

    @property
    def coverage_target(self) -> float:
        return 1.0 - self.alpha


def load_conformal_config(model_dir: Path | None) -> ConformalConfig | None:
    """Look for ``conformal.json`` next to the classifier weights.

    Returns ``None`` (with a single info-level log line) when the file
    is missing or malformed; the pipeline must keep working uncalibrated.
    """
    if model_dir is None:
        return None
    candidates = [
        Path(model_dir) / "conformal.json",
        Path("server/models/conformal.json"),
        Path("models/conformal.json"),
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            method = str(data.get("method", "raps")).lower()
            if method not in ("aps", "raps"):
                logger.warning("Unknown conformal method %r in %s", method, path)
                return None
            cfg = ConformalConfig(
                method=method,
                alpha=float(data["alpha"]),
                q_hat=float(data["q_hat"]),
                lambda_reg=float(data.get("lambda_reg", 0.0)),
                k_reg=int(data.get("k_reg", 1)),
                calibration_n=int(data.get("calibration_n", 0)),
                empirical_coverage=(
                    float(data["empirical_coverage"])
                    if "empirical_coverage" in data else None
                ),
            )
            logger.info(
                "Loaded conformal config from %s (method=%s alpha=%.3f q_hat=%.3f)",
                path, cfg.method, cfg.alpha, cfg.q_hat,
            )
            return cfg
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as e:
            logger.warning("Could not parse %s: %s", path, e)
            return None
    return None


def aps_score(probs_sorted_desc: np.ndarray) -> np.ndarray:
    """APS non-conformity score: cumulative mass over sorted probs.

    ``score[k]`` is the sum of the top ``k+1`` probabilities. The
    prediction set at threshold ``tau`` is ``{classes : score <= tau}``,
    i.e. include classes top-down until the cumulative mass crosses
    the calibrated quantile.
    """
    return np.cumsum(probs_sorted_desc)


def raps_score(
    probs_sorted_desc: np.ndarray, lambda_reg: float, k_reg: int,
) -> np.ndarray:
    """RAPS non-conformity score: APS + size penalty (Angelopoulos 2021).

    Score for the rank-``k`` element (1-indexed) is
    ``cumsum[k-1] + lambda * max(0, k - k_reg)``.
    """
    cumsum = np.cumsum(probs_sorted_desc)
    ranks = np.arange(1, len(probs_sorted_desc) + 1, dtype=np.float64)
    penalty = lambda_reg * np.maximum(0.0, ranks - k_reg)
    return cumsum + penalty


def prediction_set(
    probs: dict[str, float], cfg: ConformalConfig,
) -> list[str]:
    """Return the conformal prediction set for a single sample.

    The set is non-empty by construction (we always include the top-1
    even if its score already exceeds ``q_hat``) so the UI never has
    to render an "empty diagnosis".
    """
    if not probs:
        return []

    names = list(probs.keys())
    p = np.asarray([probs[n] for n in names], dtype=np.float64)
    order = np.argsort(-p)                  # descending
    p_sorted = p[order]

    if cfg.method == "aps":
        scores = aps_score(p_sorted)
    else:                                   # raps
        scores = raps_score(p_sorted, cfg.lambda_reg, cfg.k_reg)

    # Smallest k such that score[k] >= q_hat → set size is k+1.
    above = np.where(scores >= cfg.q_hat)[0]
    set_size = int(above[0]) + 1 if above.size else len(p_sorted)
    set_size = max(set_size, 1)

    chosen = order[:set_size]
    return [names[i] for i in chosen]


def calibrate_q_hat(
    cal_probs: np.ndarray,
    cal_labels: np.ndarray,
    *,
    method: str = "raps",
    alpha: float = 0.1,
    lambda_reg: float = 0.01,
    k_reg: int = 1,
) -> ConformalConfig:
    """Compute the conformal quantile from a held-out calibration set.

    Parameters
    ----------
    cal_probs : (N, C) float — calibrated softmax probabilities.
    cal_labels : (N,) int — true class indices in ``[0, C)``.
    alpha : 1 - desired coverage. ``alpha=0.1`` ⇒ 90 % coverage.

    Returns
    -------
    A ``ConformalConfig`` with the empirical ``q_hat`` and the
    calibration-set coverage as a sanity check.

    Notes
    -----
    The marginal coverage guarantee is **finite-sample** with the
    standard ``ceil((n+1)(1-alpha)) / n`` correction; we use the
    correction here so the resulting set sizes don't under-cover on
    small calibration splits.
    """
    n = cal_probs.shape[0]
    if n == 0:
        raise ValueError("Empty calibration set")
    if not (0.0 < alpha < 1.0):
        raise ValueError(f"alpha must be in (0,1), got {alpha}")
    if method not in ("aps", "raps"):
        raise ValueError(f"method must be 'aps' or 'raps', got {method!r}")

    # For each calibration sample, find the score at the rank of the
    # true label (the smallest threshold that would have included it).
    nonconf = np.empty(n, dtype=np.float64)
    for i in range(n):
        order = np.argsort(-cal_probs[i])
        p_sorted = cal_probs[i, order]
        if method == "aps":
            scores = aps_score(p_sorted)
        else:
            scores = raps_score(p_sorted, lambda_reg, k_reg)
        rank_of_true = int(np.where(order == cal_labels[i])[0][0])
        nonconf[i] = scores[rank_of_true]

    # Finite-sample-corrected (1 - alpha) quantile.
    q_level = min(1.0, np.ceil((n + 1) * (1.0 - alpha)) / n)
    q_hat = float(np.quantile(nonconf, q_level, method="higher"))

    cfg = ConformalConfig(
        method=method,
        alpha=alpha,
        q_hat=q_hat,
        lambda_reg=lambda_reg,
        k_reg=k_reg,
        calibration_n=n,
    )
    # Sanity-check empirical coverage on the calibration set itself
    # (should match 1-alpha within Monte-Carlo noise).
    covered = 0
    for i in range(n):
        order = np.argsort(-cal_probs[i])
        p_sorted = cal_probs[i, order]
        if method == "aps":
            scores = aps_score(p_sorted)
        else:
            scores = raps_score(p_sorted, lambda_reg, k_reg)
        above = np.where(scores >= q_hat)[0]
        size = int(above[0]) + 1 if above.size else len(p_sorted)
        size = max(size, 1)
        if cal_labels[i] in order[:size]:
            covered += 1
    return ConformalConfig(
        method=cfg.method,
        alpha=cfg.alpha,
        q_hat=cfg.q_hat,
        lambda_reg=cfg.lambda_reg,
        k_reg=cfg.k_reg,
        calibration_n=cfg.calibration_n,
        empirical_coverage=covered / n,
    )
