"""
Inductive conformal prediction for the PlantVision classifier.

Temperature scaling fixes the *miscalibration* of the softmax (so a 0.85
probability really means 85 %). It does **not** fix the second failure
mode the UI cares about: at the top-1 boundary between two visually
similar classes (e.g. ``leaf_spot`` ↔ ``viral_disease``)
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

The calibration scheme is **inductive**: the Colab training notebook uses a
held-out calibration split to produce one ``q_hat`` per ``alpha``. This server
module deliberately contains only artifact loading and O(C log C) inference;
it cannot fit or rewrite calibration artifacts.

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

The file is optional for isolated loader tests. Production API boot requires
it via ``bundle_manifest.json``; when the loader is called without a file,
the pipeline simply omits the ``prediction_set`` field — clients should
treat it as a hint, never gate UX on its presence.
"""

from __future__ import annotations

import json
import logging
import math
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
        """Return the configured *nominal* marginal coverage target.

        Historical exporters wrote ``empirical_coverage`` from the same rows
        used to choose ``q_hat``. That is an in-sample calibration diagnostic,
        not an independent coverage estimate, so it must not replace the
        client-facing target. Current API prediction sets remain advisory.
        """
        return 1.0 - self.alpha


class ConformalLoadError(RuntimeError):
    """A conformal sidecar exists but cannot be applied safely."""


def load_conformal_config(model_dir: Path | None) -> ConformalConfig | None:
    """Look for ``conformal.json`` next to the classifier weights.

    Resolution is strictly bundle-relative: a per-family bundle (e.g.
    ``models/frontier/``) must never silently inherit another family's
    q_hat. Returns ``None`` (with a single info-level log line) when the file
    is missing. Once the sidecar is present, malformed calibration fails
    readiness instead of silently changing the served uncertainty policy.
    """
    if model_dir is None:
        return None
    path = Path(model_dir) / "conformal.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TypeError("conformal.json must contain a JSON object")
        method_raw = data.get("method", "raps")
        if not isinstance(method_raw, str):
            raise TypeError("method must be a string")
        method = method_raw.lower()
        if method not in ("aps", "raps"):
            raise ValueError(f"unknown conformal method {method!r}")
        numeric_fields = {
            "alpha": data["alpha"],
            "q_hat": data["q_hat"],
            "lambda_reg": data.get("lambda_reg", 0.0),
        }
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            for value in numeric_fields.values()
        ):
            raise TypeError("alpha, q_hat and lambda_reg must be numeric")
        alpha = float(numeric_fields["alpha"])
        q_hat = float(numeric_fields["q_hat"])
        lambda_reg = float(numeric_fields["lambda_reg"])
        if not math.isfinite(alpha) or not 0.0 < alpha < 1.0:
            raise ValueError("alpha must be finite and strictly between 0 and 1")
        if not math.isfinite(q_hat) or q_hat < 0.0:
            raise ValueError("q_hat must be finite and non-negative")
        if not math.isfinite(lambda_reg) or lambda_reg < 0.0:
            raise ValueError("lambda_reg must be finite and non-negative")
        k_reg = data.get("k_reg", 1)
        calibration_n = data.get("calibration_n", 0)
        if isinstance(k_reg, bool) or not isinstance(k_reg, int) or k_reg < 0:
            raise TypeError("k_reg must be a non-negative integer")
        if (
            isinstance(calibration_n, bool)
            or not isinstance(calibration_n, int)
            or calibration_n < 0
        ):
            raise TypeError("calibration_n must be a non-negative integer")
        empirical = None
        if "empirical_coverage" in data:
            empirical_raw = data["empirical_coverage"]
            if (
                isinstance(empirical_raw, bool)
                or not isinstance(empirical_raw, (int, float))
            ):
                raise TypeError("empirical_coverage must be numeric")
            empirical = float(empirical_raw)
            if not math.isfinite(empirical) or not 0.0 <= empirical <= 1.0:
                raise ValueError("empirical_coverage must be finite and in [0, 1]")
        cfg = ConformalConfig(
            method=method,
            alpha=alpha,
            q_hat=q_hat,
            lambda_reg=lambda_reg,
            k_reg=k_reg,
            calibration_n=calibration_n,
            empirical_coverage=empirical,
        )
    except (KeyError, ValueError, TypeError, json.JSONDecodeError) as e:
        raise ConformalLoadError(f"Could not parse declared {path}: {e}") from e
    logger.info(
        "Loaded conformal config from %s (method=%s alpha=%.3f q_hat=%.3f)",
        path, cfg.method, cfg.alpha, cfg.q_hat,
    )
    return cfg


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
    if not np.isfinite(p).all() or np.any(p < 0.0):
        raise ValueError("prediction probabilities must be finite and non-negative")
    total = float(p.sum())
    if total <= 0.0:
        raise ValueError("prediction probabilities must have positive mass")
    if not np.isclose(total, 1.0, rtol=1e-5, atol=1e-6):
        raise ValueError(f"prediction probabilities sum to {total:.6g}, expected 1")
    p = p / total
    order = np.argsort(-p)                  # descending
    p_sorted = p[order]

    scores = (
        aps_score(p_sorted) if cfg.method == "aps"
        else raps_score(p_sorted, cfg.lambda_reg, cfg.k_reg)
    )

    # Smallest k such that score[k] >= q_hat → set size is k+1.
    above = np.where(scores >= cfg.q_hat)[0]
    set_size = int(above[0]) + 1 if above.size else len(p_sorted)
    set_size = max(set_size, 1)

    chosen = order[:set_size]
    return [names[i] for i in chosen]
