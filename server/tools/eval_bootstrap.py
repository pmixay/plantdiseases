#!/usr/bin/env python3
"""Bootstrap confidence intervals for disease macro-F1 + paired model A/B.

Why this exists (report Stage 0 — "make evaluation trustworthy"):
at n≈450 val_realworld samples the macro-F1 sampling error is wide
(roughly ±0.04–0.05), so a 0.04 gap — e.g. the historical
``f1_int8=0.73 > f1_fp32=0.69`` inversion in ``model_card.json`` — is
almost certainly noise, not a real ordering. Point estimates alone are
not a trustworthy ship/no-ship gate; CIs and a *paired* A/B test are.

Two estimators are provided:

* :func:`bootstrap_macro_f1_ci` — percentile bootstrap CI for a single
  model's disease macro-F1 (resample the test set with replacement,
  recompute, take the 2.5/97.5 percentiles). Distribution-free.
* :func:`paired_bootstrap` — the **paired** bootstrap for comparing two
  models on the *same* test set: resample one index vector per
  iteration and apply it to BOTH models' predictions, then look at the
  distribution of the difference. This controls for which specific
  images are hard and is far more sensitive than comparing two
  independent CIs (Stage 0: "use the paired bootstrap").

The disease-macro-F1 definition mirrors the training notebook exactly
(``sklearn.f1_score`` over the disease labels only — ``healthy`` and
``not_a_plant`` are excluded — ``average='macro'``, ``zero_division=0``)
so a CI computed here is directly comparable to the ship-gate number.

This module deliberately depends only on ``numpy`` + ``scikit-learn``;
it consumes pre-computed integer label vectors, never a model, so it is
cheap to unit-test and import from the notebook (§7) without dragging in
torch / onnxruntime.

CLI:
    python tools/eval_bootstrap.py preds.json [--n-boot 5000] [--ci 0.95]

``preds.json`` schema::

    {
      "class_names": ["gray_mold_botrytis", "healthy", ...],
      "y_true":  [int, ...],
      "y_pred_a": [int, ...],          # required
      "y_pred_b": [int, ...],          # optional → triggers paired A/B
      "label_a": "int8",               # optional display names
      "label_b": "fp32"
    }
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from sklearn.metrics import f1_score

# Classes excluded from the disease macro-F1 — identical to the notebook
# eval helper and ``server/pipeline.py``'s ``_NON_DISEASE``.
NON_DISEASE = ("healthy", "not_a_plant")


def _disease_indices(class_names: list[str]) -> list[int]:
    return [i for i, n in enumerate(class_names) if n not in NON_DISEASE]


def disease_macro_f1(
    y_true: np.ndarray, y_pred: np.ndarray, class_names: list[str]
) -> float:
    """Disease-only macro-F1, matching the notebook ship-gate metric."""
    disease_idx = _disease_indices(class_names)
    if not disease_idx:
        raise ValueError("class_names contains no disease classes to score")
    return float(
        f1_score(
            y_true, y_pred, labels=disease_idx, average="macro", zero_division=0
        )
    )


def _as_int_array(x) -> np.ndarray:
    a = np.asarray(x, dtype=np.int64)
    if a.ndim != 1:
        raise ValueError(f"expected a 1-D label vector, got shape {a.shape}")
    return a


def _percentile_ci(samples: np.ndarray, ci: float) -> tuple[float, float]:
    lo_q = (1.0 - ci) / 2.0 * 100.0
    hi_q = (1.0 + ci) / 2.0 * 100.0
    return float(np.percentile(samples, lo_q)), float(np.percentile(samples, hi_q))


def bootstrap_macro_f1_ci(
    y_true,
    y_pred,
    class_names: list[str],
    *,
    n_boot: int = 5000,
    ci: float = 0.95,
    seed: int = 0,
) -> dict:
    """Percentile-bootstrap CI for disease macro-F1 of a single model."""
    y_true = _as_int_array(y_true)
    y_pred = _as_int_array(y_pred)
    if y_true.shape != y_pred.shape:
        raise ValueError("y_true and y_pred must have the same length")
    n = y_true.shape[0]
    if n == 0:
        raise ValueError("empty prediction set")

    point = disease_macro_f1(y_true, y_pred, class_names)
    rng = np.random.default_rng(seed)
    boot = np.empty(n_boot, dtype=np.float64)
    for b in range(n_boot):
        idx = rng.integers(0, n, size=n)
        boot[b] = disease_macro_f1(y_true[idx], y_pred[idx], class_names)
    lo, hi = _percentile_ci(boot, ci)
    return {
        "metric": "disease_macro_f1",
        "point": point,
        "ci_low": lo,
        "ci_high": hi,
        "ci_level": ci,
        "n": int(n),
        "n_boot": int(n_boot),
        "ci_half_width": (hi - lo) / 2.0,
    }


def paired_bootstrap(
    y_true,
    y_pred_a,
    y_pred_b,
    class_names: list[str],
    *,
    n_boot: int = 5000,
    ci: float = 0.95,
    seed: int = 0,
) -> dict:
    """Paired bootstrap for ``A − B`` disease macro-F1 on a shared test set.

    One resampled index vector per iteration is applied to BOTH models,
    so the difference distribution reflects only the model gap, not the
    luck of the draw. ``p_a_gt_b`` is the bootstrap fraction where A
    beats B; ``significant`` is True when the CI of the difference
    excludes zero (the Stage 0 gate: "you can measure a 0.03 difference
    with a CI that excludes zero").
    """
    y_true = _as_int_array(y_true)
    y_pred_a = _as_int_array(y_pred_a)
    y_pred_b = _as_int_array(y_pred_b)
    if not (y_true.shape == y_pred_a.shape == y_pred_b.shape):
        raise ValueError("y_true, y_pred_a and y_pred_b must have equal length")
    n = y_true.shape[0]
    if n == 0:
        raise ValueError("empty prediction set")

    f1_a = disease_macro_f1(y_true, y_pred_a, class_names)
    f1_b = disease_macro_f1(y_true, y_pred_b, class_names)
    rng = np.random.default_rng(seed)
    diffs = np.empty(n_boot, dtype=np.float64)
    for b in range(n_boot):
        idx = rng.integers(0, n, size=n)
        yt = y_true[idx]
        diffs[b] = (
            disease_macro_f1(yt, y_pred_a[idx], class_names)
            - disease_macro_f1(yt, y_pred_b[idx], class_names)
        )
    lo, hi = _percentile_ci(diffs, ci)
    return {
        "metric": "disease_macro_f1",
        "f1_a": f1_a,
        "f1_b": f1_b,
        "diff_point": f1_a - f1_b,
        "diff_ci_low": lo,
        "diff_ci_high": hi,
        "ci_level": ci,
        "p_a_gt_b": float(np.mean(diffs > 0.0)),
        "significant": bool(lo > 0.0 or hi < 0.0),
        "n": int(n),
        "n_boot": int(n_boot),
    }


def _fmt(d: dict) -> str:
    return json.dumps(d, indent=2)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("preds", type=Path, help="predictions JSON (see module docstring)")
    ap.add_argument("--n-boot", type=int, default=5000)
    ap.add_argument("--ci", type=float, default=0.95)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--json-out", type=Path, default=None)
    args = ap.parse_args()

    data = json.loads(args.preds.read_text(encoding="utf-8"))
    class_names = data["class_names"]
    y_true = data["y_true"]
    y_pred_a = data["y_pred_a"]
    label_a = data.get("label_a", "model_a")

    report: dict = {
        "class_names": class_names,
        label_a: bootstrap_macro_f1_ci(
            y_true, y_pred_a, class_names, n_boot=args.n_boot, ci=args.ci, seed=args.seed
        ),
    }
    if data.get("y_pred_b") is not None:
        label_b = data.get("label_b", "model_b")
        report[label_b] = bootstrap_macro_f1_ci(
            y_true, data["y_pred_b"], class_names,
            n_boot=args.n_boot, ci=args.ci, seed=args.seed,
        )
        report[f"{label_a}_vs_{label_b}"] = paired_bootstrap(
            y_true, y_pred_a, data["y_pred_b"], class_names,
            n_boot=args.n_boot, ci=args.ci, seed=args.seed,
        )

    print(_fmt(report))
    if args.json_out:
        args.json_out.write_text(_fmt(report), encoding="utf-8")
        print(f"\nwrote {args.json_out}")


if __name__ == "__main__":
    main()
