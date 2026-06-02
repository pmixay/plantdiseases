#!/usr/bin/env python3
"""Bootstrap confidence intervals for disease macro-F1 + paired model A/B.

Why this exists (report Stage 0 — "make evaluation trustworthy"):
at n≈450 val_realworld samples the macro-F1 sampling error is wide
(roughly ±0.04–0.05), so a 0.04 gap — e.g. the historical
``f1_int8=0.73 > f1_fp32=0.69`` inversion in ``model_card.json`` — is
almost certainly noise, not a real ordering. Point estimates alone are
not a trustworthy ship/no-ship gate; CIs and a *paired* A/B test are.

Three estimators are provided:

* :func:`bootstrap_macro_f1_ci` — percentile bootstrap CI for a single
  model's disease macro-F1 (resample the test set with replacement,
  recompute, take the 2.5/97.5 percentiles). Distribution-free.
* :func:`paired_bootstrap` — the **paired** bootstrap for comparing two
  models on the *same* test set: resample one index vector per
  iteration and apply it to BOTH models' predictions, then look at the
  distribution of the difference. This controls for which specific
  images are hard and is far more sensitive than comparing two
  independent CIs (Stage 0: "use the paired bootstrap").
* :func:`bootstrap_per_class_recall_ci` / :func:`paired_per_class_recall`
  — the same two estimators applied to **per-class recall**. The report
  (Stage 0) asks for CIs on macro-F1 *and* per-class recall explicitly,
  because macro-F1 is dominated by the smallest classes and the weak
  classes it targets (``nutrient_deficiency``, ``leaf_scorch_*``,
  ``gray_mold_*``) are tracked by recall: a class with ~30–50 samples has
  a recall CI of ±0.10 or more, so a per-class recall delta is only real
  when its paired CI excludes zero.

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


def per_class_recall(
    y_true: np.ndarray, y_pred: np.ndarray, class_names: list[str]
) -> dict[str, float]:
    """Recall for every class, keyed by class name.

    Mirrors ``training_metrics.json``'s ``per_class_recall``. A class with
    no support in ``y_true`` reports ``nan`` (recall is undefined), which
    the bootstrap below propagates so an absent class is never silently
    scored as 0 or 1.
    """
    yt = np.asarray(y_true)
    yp = np.asarray(y_pred)
    out: dict[str, float] = {}
    for idx, name in enumerate(class_names):
        support = int(np.sum(yt == idx))
        if support == 0:
            out[name] = float("nan")
        else:
            out[name] = float(np.sum((yt == idx) & (yp == idx)) / support)
    return out


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


def bootstrap_per_class_recall_ci(
    y_true,
    y_pred,
    class_names: list[str],
    *,
    n_boot: int = 5000,
    ci: float = 0.95,
    seed: int = 0,
) -> dict[str, dict]:
    """Percentile-bootstrap recall CI for every class (Stage 0).

    Returns ``{class_name: {point, ci_low, ci_high, ci_half_width,
    support, ...}}``. A bootstrap replicate that happens to draw no
    samples of a class yields ``nan`` for that replicate and is dropped
    from that class's percentile computation (``np.nanpercentile``), so a
    rare class still gets a CI from the replicates where it was present.
    """
    y_true = _as_int_array(y_true)
    y_pred = _as_int_array(y_pred)
    if y_true.shape != y_pred.shape:
        raise ValueError("y_true and y_pred must have the same length")
    n = y_true.shape[0]
    if n == 0:
        raise ValueError("empty prediction set")

    point = per_class_recall(y_true, y_pred, class_names)
    support = {c: int(np.sum(y_true == i)) for i, c in enumerate(class_names)}
    rng = np.random.default_rng(seed)
    boot = np.full((n_boot, len(class_names)), np.nan, dtype=np.float64)
    for b in range(n_boot):
        idx = rng.integers(0, n, size=n)
        rec = per_class_recall(y_true[idx], y_pred[idx], class_names)
        boot[b] = [rec[c] for c in class_names]

    out: dict[str, dict] = {}
    for i, c in enumerate(class_names):
        col = boot[:, i]
        valid = col[~np.isnan(col)]
        if valid.size == 0:
            out[c] = {
                "point": point[c], "ci_low": float("nan"),
                "ci_high": float("nan"), "ci_half_width": float("nan"),
                "support": support[c], "n_boot_valid": 0,
            }
            continue
        lo, hi = _percentile_ci(valid, ci)
        out[c] = {
            "point": point[c],
            "ci_low": lo,
            "ci_high": hi,
            "ci_half_width": (hi - lo) / 2.0,
            "support": support[c],
            "n_boot_valid": int(valid.size),
        }
    return {
        "metric": "per_class_recall",
        "ci_level": ci,
        "n": int(n),
        "n_boot": int(n_boot),
        "classes": out,
    }


def paired_per_class_recall(
    y_true,
    y_pred_a,
    y_pred_b,
    class_names: list[str],
    *,
    n_boot: int = 5000,
    ci: float = 0.95,
    seed: int = 0,
) -> dict[str, dict]:
    """Paired bootstrap for ``A − B`` per-class recall on a shared test set.

    The honest test for a per-class improvement (e.g. ``nutrient_deficiency``
    recall rising from 0.55 toward >0.75 after Stage 2): ``significant`` is
    True for a class only when the CI of its recall difference excludes
    zero. One resampled index vector per iteration is applied to BOTH
    models so the difference reflects only the model gap.
    """
    y_true = _as_int_array(y_true)
    y_pred_a = _as_int_array(y_pred_a)
    y_pred_b = _as_int_array(y_pred_b)
    if not (y_true.shape == y_pred_a.shape == y_pred_b.shape):
        raise ValueError("y_true, y_pred_a and y_pred_b must have equal length")
    n = y_true.shape[0]
    if n == 0:
        raise ValueError("empty prediction set")

    rec_a = per_class_recall(y_true, y_pred_a, class_names)
    rec_b = per_class_recall(y_true, y_pred_b, class_names)
    support = {c: int(np.sum(y_true == i)) for i, c in enumerate(class_names)}
    rng = np.random.default_rng(seed)
    diffs = np.full((n_boot, len(class_names)), np.nan, dtype=np.float64)
    for b in range(n_boot):
        idx = rng.integers(0, n, size=n)
        yt = y_true[idx]
        ra = per_class_recall(yt, y_pred_a[idx], class_names)
        rb = per_class_recall(yt, y_pred_b[idx], class_names)
        diffs[b] = [ra[c] - rb[c] for c in class_names]

    out: dict[str, dict] = {}
    for i, c in enumerate(class_names):
        col = diffs[:, i]
        valid = col[~np.isnan(col)]
        if valid.size == 0:
            out[c] = {
                "recall_a": rec_a[c], "recall_b": rec_b[c],
                "diff_point": float("nan"), "diff_ci_low": float("nan"),
                "diff_ci_high": float("nan"), "p_a_gt_b": float("nan"),
                "significant": False, "support": support[c], "n_boot_valid": 0,
            }
            continue
        lo, hi = _percentile_ci(valid, ci)
        out[c] = {
            "recall_a": rec_a[c],
            "recall_b": rec_b[c],
            "diff_point": rec_a[c] - rec_b[c],
            "diff_ci_low": lo,
            "diff_ci_high": hi,
            "p_a_gt_b": float(np.mean(valid > 0.0)),
            "significant": bool(lo > 0.0 or hi < 0.0),
            "support": support[c],
            "n_boot_valid": int(valid.size),
        }
    return {
        "metric": "per_class_recall_diff",
        "compared": "a_minus_b",
        "ci_level": ci,
        "n": int(n),
        "n_boot": int(n_boot),
        "classes": out,
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
        label_a: {
            "disease_macro_f1": bootstrap_macro_f1_ci(
                y_true, y_pred_a, class_names,
                n_boot=args.n_boot, ci=args.ci, seed=args.seed,
            ),
            "per_class_recall": bootstrap_per_class_recall_ci(
                y_true, y_pred_a, class_names,
                n_boot=args.n_boot, ci=args.ci, seed=args.seed,
            ),
        },
    }
    if data.get("y_pred_b") is not None:
        label_b = data.get("label_b", "model_b")
        report[label_b] = {
            "disease_macro_f1": bootstrap_macro_f1_ci(
                y_true, data["y_pred_b"], class_names,
                n_boot=args.n_boot, ci=args.ci, seed=args.seed,
            ),
            "per_class_recall": bootstrap_per_class_recall_ci(
                y_true, data["y_pred_b"], class_names,
                n_boot=args.n_boot, ci=args.ci, seed=args.seed,
            ),
        }
        report[f"{label_a}_vs_{label_b}"] = {
            "disease_macro_f1": paired_bootstrap(
                y_true, y_pred_a, data["y_pred_b"], class_names,
                n_boot=args.n_boot, ci=args.ci, seed=args.seed,
            ),
            "per_class_recall": paired_per_class_recall(
                y_true, y_pred_a, data["y_pred_b"], class_names,
                n_boot=args.n_boot, ci=args.ci, seed=args.seed,
            ),
        }

    print(_fmt(report))
    if args.json_out:
        args.json_out.write_text(_fmt(report), encoding="utf-8")
        print(f"\nwrote {args.json_out}")


if __name__ == "__main__":
    main()
