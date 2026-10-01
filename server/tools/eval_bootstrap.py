#!/usr/bin/env python3
"""Bootstrap confidence intervals for disease macro-F1 + paired model A/B.

Why this exists (report Stage 0 — "make evaluation trustworthy"):
at n≈450 val_realworld samples the macro-F1 sampling error is wide
(roughly ±0.04–0.05), so a 0.04 gap — e.g. the historical
``f1_int8=0.73 > f1_fp32=0.69`` inversion in ``model_card.json`` — is
almost certainly noise, not a real ordering. Point estimates alone are
not a trustworthy ship/no-ship gate; CIs and a *paired* A/B test are.

The estimators:

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
* :func:`bootstrap_per_class_precision_ci` — the per-class **precision**
  counterpart of the recall CI, over :func:`per_class_precision`. Recall
  alone cannot tell a genuinely better class from one the model simply
  predicts more often; the precision CI is what separates them.

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
    """Disease-only macro-F1 over the disease classes PRESENT in ``y_true``.

    On a fully populated slice (``val_realworld``) this is exactly the
    notebook's ship-gate ``disease_macro_f1``. On a slice missing classes —
    an OOD benchmark holding only healthy + scorch, or a bootstrap replicate
    that drew no row of a thin class — the absent classes are left out rather
    than averaged in as hard zeros (the notebook's ``supported_disease_macro_f1``).
    A perfect model on a two-class subset used to score 1/7 here, and every
    replicate that missed a rare class dragged that class's CI toward 0. An
    absent class is not a failure; it is no evidence. Returns ``nan`` when
    ``y_true`` holds no disease class at all.
    """
    disease_idx = _disease_indices(class_names)
    if not disease_idx:
        raise ValueError("class_names contains no disease classes to score")
    present = set(np.unique(np.asarray(y_true)).tolist())
    labels = [i for i in disease_idx if i in present]
    if not labels:
        return float("nan")
    return float(
        f1_score(
            y_true, y_pred, labels=labels, average="macro", zero_division=0
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


def per_class_precision(
    y_true: np.ndarray, y_pred: np.ndarray, class_names: list[str]
) -> dict[str, float]:
    """Precision for every class, keyed by class name.

    The companion to :func:`per_class_recall`. The weak classes this repo
    targets (``nutrient_deficiency``, ``leaf_scorch_underwatering``) are
    bound by **precision**, not recall — they over-trigger (false
    positives) far more than they miss — so a recall CI alone hides their
    real failure mode. A class the model never *predicts* reports ``nan``
    (precision is undefined with no positive predictions), which the
    bootstrap below propagates so an unpredicted class is never silently
    scored 0 or 1.
    """
    yt = np.asarray(y_true)
    yp = np.asarray(y_pred)
    out: dict[str, float] = {}
    for idx, name in enumerate(class_names):
        predicted = int(np.sum(yp == idx))
        if predicted == 0:
            out[name] = float("nan")
        else:
            out[name] = float(np.sum((yt == idx) & (yp == idx)) / predicted)
    return out


def _as_int_array(x) -> np.ndarray:
    a = np.asarray(x, dtype=np.int64)
    if a.ndim != 1:
        raise ValueError(f"expected a 1-D label vector, got shape {a.shape}")
    return a


def _percentile_ci(samples: np.ndarray, ci: float) -> tuple[float, float]:
    """Percentile CI over the FINITE replicates; an all-``nan`` set is an error."""
    samples = np.asarray(samples, dtype=np.float64)
    valid = samples[~np.isnan(samples)]
    if valid.size == 0:
        raise ValueError("no bootstrap replicate produced a finite metric")
    lo_q = (1.0 - ci) / 2.0 * 100.0
    hi_q = (1.0 + ci) / 2.0 * 100.0
    return float(np.percentile(valid, lo_q)), float(np.percentile(valid, hi_q))


def _validate_bootstrap_config(n_boot: int, ci: float) -> None:
    if isinstance(n_boot, bool) or int(n_boot) != n_boot or n_boot <= 0:
        raise ValueError("n_boot must be a positive integer")
    if not 0.0 < float(ci) < 1.0:
        raise ValueError("ci must be strictly between 0 and 1")


_MISSING_GROUP_TOKENS = frozenset({"none", "nan", "null", "na", "n/a"})


def _prepare_groups(group_ids, n: int) -> list[np.ndarray] | None:
    """Validate group IDs and return row indices per independent family."""
    if group_ids is None:
        return None
    values = np.asarray(group_ids, dtype=object)
    if values.ndim != 1 or len(values) != n:
        raise ValueError("group_ids must be a 1-D vector matching the labels")
    groups: dict[str, list[int]] = {}
    for i, raw in enumerate(values):
        # ``None``/``NaN`` used to stringify to "None"/"nan" and pool every
        # unlabelled row into ONE pseudo-family — the opposite of independence.
        if raw is None or (isinstance(raw, float) and np.isnan(raw)):
            raise ValueError(f"group_ids contains a missing value at row {i}")
        key = str(raw).strip()
        if not key or key.lower() in _MISSING_GROUP_TOKENS:
            raise ValueError(f"group_ids contains a missing value at row {i}")
        groups.setdefault(key, []).append(i)
    return [np.asarray(indices, dtype=np.int64) for indices in groups.values()]


def _resample_indices(rng, n: int, groups: list[np.ndarray] | None) -> np.ndarray:
    if groups is None:
        return rng.integers(0, n, size=n)
    chosen = rng.integers(0, len(groups), size=len(groups))
    return np.concatenate([groups[i] for i in chosen])


def _sampling_meta(groups: list[np.ndarray] | None, n: int) -> dict:
    if groups is None:
        return {
            "resampling_unit": "row",
            "n_groups": None,
            "independence_advisory": (
                "capture-family IDs absent; CI does not control pseudo-replication"),
        }
    return {
        "resampling_unit": "capture_family",
        "n_groups": len(groups),
        "independence_advisory": None,
    }


def bootstrap_macro_f1_ci(
    y_true,
    y_pred,
    class_names: list[str],
    *,
    n_boot: int = 5000,
    ci: float = 0.95,
    seed: int = 0,
    group_ids=None,
) -> dict:
    """Percentile-bootstrap CI for disease macro-F1 of a single model."""
    _validate_bootstrap_config(n_boot, ci)
    y_true = _as_int_array(y_true)
    y_pred = _as_int_array(y_pred)
    if y_true.shape != y_pred.shape:
        raise ValueError("y_true and y_pred must have the same length")
    n = y_true.shape[0]
    if n == 0:
        raise ValueError("empty prediction set")
    groups = _prepare_groups(group_ids, n)

    point = disease_macro_f1(y_true, y_pred, class_names)
    if np.isnan(point):
        raise ValueError("y_true holds no disease class; disease macro-F1 is undefined")
    rng = np.random.default_rng(seed)
    boot = np.empty(n_boot, dtype=np.float64)
    for b in range(n_boot):
        idx = _resample_indices(rng, n, groups)
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
        "n_boot_effective": int(np.sum(~np.isnan(boot))),
        "ci_half_width": (hi - lo) / 2.0,
        **_sampling_meta(groups, n),
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
    group_ids=None,
) -> dict:
    """Paired bootstrap for ``A − B`` disease macro-F1 on a shared test set.

    One resampled index vector per iteration is applied to BOTH models,
    so the difference distribution reflects only the model gap, not the
    luck of the draw. ``p_a_gt_b`` is the bootstrap fraction where A
    beats B; ``significant`` is True when the CI of the difference
    excludes zero (the Stage 0 gate: "you can measure a 0.03 difference
    with a CI that excludes zero").
    """
    _validate_bootstrap_config(n_boot, ci)
    y_true = _as_int_array(y_true)
    y_pred_a = _as_int_array(y_pred_a)
    y_pred_b = _as_int_array(y_pred_b)
    if not (y_true.shape == y_pred_a.shape == y_pred_b.shape):
        raise ValueError("y_true, y_pred_a and y_pred_b must have equal length")
    n = y_true.shape[0]
    if n == 0:
        raise ValueError("empty prediction set")

    groups = _prepare_groups(group_ids, n)
    f1_a = disease_macro_f1(y_true, y_pred_a, class_names)
    f1_b = disease_macro_f1(y_true, y_pred_b, class_names)
    if np.isnan(f1_a) or np.isnan(f1_b):
        raise ValueError("y_true holds no disease class; disease macro-F1 is undefined")
    rng = np.random.default_rng(seed)
    diffs = np.empty(n_boot, dtype=np.float64)
    for b in range(n_boot):
        idx = _resample_indices(rng, n, groups)
        yt = y_true[idx]
        diffs[b] = (
            disease_macro_f1(yt, y_pred_a[idx], class_names)
            - disease_macro_f1(yt, y_pred_b[idx], class_names)
        )
    valid = diffs[~np.isnan(diffs)]
    lo, hi = _percentile_ci(valid, ci)
    return {
        "metric": "disease_macro_f1",
        "f1_a": f1_a,
        "f1_b": f1_b,
        "diff_point": f1_a - f1_b,
        "diff_ci_low": lo,
        "diff_ci_high": hi,
        "ci_level": ci,
        # Ties are reported, not dropped: two identical models give
        # p_a_gt_b = p_b_gt_a = 0 and p_tie = 1, never a spurious 0/1 split.
        "p_a_gt_b": float(np.mean(valid > 0.0)),
        "p_b_gt_a": float(np.mean(valid < 0.0)),
        "p_tie": float(np.mean(valid == 0.0)),
        "significant": bool(lo > 0.0 or hi < 0.0),
        "n": int(n),
        "n_boot": int(n_boot),
        "n_boot_effective": int(valid.size),
        **_sampling_meta(groups, n),
    }


def bootstrap_per_class_recall_ci(
    y_true,
    y_pred,
    class_names: list[str],
    *,
    n_boot: int = 5000,
    ci: float = 0.95,
    seed: int = 0,
    group_ids=None,
) -> dict[str, dict]:
    """Percentile-bootstrap recall CI for every class (Stage 0).

    Returns ``{class_name: {point, ci_low, ci_high, ci_half_width,
    support, ...}}``. A bootstrap replicate that happens to draw no
    samples of a class yields ``nan`` for that replicate and is dropped
    from that class's percentile computation (``np.nanpercentile``), so a
    rare class still gets a CI from the replicates where it was present.
    """
    _validate_bootstrap_config(n_boot, ci)
    y_true = _as_int_array(y_true)
    y_pred = _as_int_array(y_pred)
    if y_true.shape != y_pred.shape:
        raise ValueError("y_true and y_pred must have the same length")
    n = y_true.shape[0]
    if n == 0:
        raise ValueError("empty prediction set")

    groups = _prepare_groups(group_ids, n)
    point = per_class_recall(y_true, y_pred, class_names)
    support = {c: int(np.sum(y_true == i)) for i, c in enumerate(class_names)}
    rng = np.random.default_rng(seed)
    boot = np.full((n_boot, len(class_names)), np.nan, dtype=np.float64)
    for b in range(n_boot):
        idx = _resample_indices(rng, n, groups)
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
        **_sampling_meta(groups, n),
    }


def bootstrap_per_class_precision_ci(
    y_true,
    y_pred,
    class_names: list[str],
    *,
    n_boot: int = 5000,
    ci: float = 0.95,
    seed: int = 0,
    group_ids=None,
) -> dict[str, dict]:
    """Percentile-bootstrap precision CI for every class.

    Mirrors :func:`bootstrap_per_class_recall_ci`. ``support`` is the
    true-class support (``y_true == i``, comparable to the recall CI);
    ``n_predicted`` is the number of positive predictions (precision's
    denominator). A bootstrap replicate that predicts a class zero times
    yields ``nan`` for that replicate and is dropped from that class's
    percentile computation, so a rarely-predicted class still gets a CI
    from the replicates where it was predicted.
    """
    _validate_bootstrap_config(n_boot, ci)
    y_true = _as_int_array(y_true)
    y_pred = _as_int_array(y_pred)
    if y_true.shape != y_pred.shape:
        raise ValueError("y_true and y_pred must have the same length")
    n = y_true.shape[0]
    if n == 0:
        raise ValueError("empty prediction set")

    groups = _prepare_groups(group_ids, n)
    point = per_class_precision(y_true, y_pred, class_names)
    support = {c: int(np.sum(y_true == i)) for i, c in enumerate(class_names)}
    n_predicted = {c: int(np.sum(y_pred == i)) for i, c in enumerate(class_names)}
    rng = np.random.default_rng(seed)
    boot = np.full((n_boot, len(class_names)), np.nan, dtype=np.float64)
    for b in range(n_boot):
        idx = _resample_indices(rng, n, groups)
        prec = per_class_precision(y_true[idx], y_pred[idx], class_names)
        boot[b] = [prec[c] for c in class_names]

    out: dict[str, dict] = {}
    for i, c in enumerate(class_names):
        col = boot[:, i]
        valid = col[~np.isnan(col)]
        if valid.size == 0:
            out[c] = {
                "point": point[c], "ci_low": float("nan"),
                "ci_high": float("nan"), "ci_half_width": float("nan"),
                "support": support[c], "n_predicted": n_predicted[c],
                "n_boot_valid": 0,
            }
            continue
        lo, hi = _percentile_ci(valid, ci)
        out[c] = {
            "point": point[c],
            "ci_low": lo,
            "ci_high": hi,
            "ci_half_width": (hi - lo) / 2.0,
            "support": support[c],
            "n_predicted": n_predicted[c],
            "n_boot_valid": int(valid.size),
        }
    return {
        "metric": "per_class_precision",
        "ci_level": ci,
        "n": int(n),
        "n_boot": int(n_boot),
        "classes": out,
        **_sampling_meta(groups, n),
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
    group_ids=None,
) -> dict[str, dict]:
    """Paired bootstrap for ``A − B`` per-class recall on a shared test set.

    The honest test for a per-class improvement (e.g. ``nutrient_deficiency``
    recall rising from 0.55 toward >0.75 after Stage 2): ``significant`` is
    True for a class only when the CI of its recall difference excludes
    zero. One resampled index vector per iteration is applied to BOTH
    models so the difference reflects only the model gap.
    """
    _validate_bootstrap_config(n_boot, ci)
    y_true = _as_int_array(y_true)
    y_pred_a = _as_int_array(y_pred_a)
    y_pred_b = _as_int_array(y_pred_b)
    if not (y_true.shape == y_pred_a.shape == y_pred_b.shape):
        raise ValueError("y_true, y_pred_a and y_pred_b must have equal length")
    n = y_true.shape[0]
    if n == 0:
        raise ValueError("empty prediction set")

    groups = _prepare_groups(group_ids, n)
    rec_a = per_class_recall(y_true, y_pred_a, class_names)
    rec_b = per_class_recall(y_true, y_pred_b, class_names)
    support = {c: int(np.sum(y_true == i)) for i, c in enumerate(class_names)}
    rng = np.random.default_rng(seed)
    diffs = np.full((n_boot, len(class_names)), np.nan, dtype=np.float64)
    for b in range(n_boot):
        idx = _resample_indices(rng, n, groups)
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
        **_sampling_meta(groups, n),
    }


def paired_per_class_precision(
    y_true,
    y_pred_a,
    y_pred_b,
    class_names: list[str],
    *,
    n_boot: int = 5000,
    ci: float = 0.95,
    seed: int = 0,
    group_ids=None,
) -> dict[str, dict]:
    """Paired bootstrap for ``A − B`` per-class precision.

    The same sampled indices are applied to both models. Replicates where
    either model makes no prediction for a class are undefined and excluded
    for that class rather than being silently scored as zero.
    """
    _validate_bootstrap_config(n_boot, ci)
    y_true = _as_int_array(y_true)
    y_pred_a = _as_int_array(y_pred_a)
    y_pred_b = _as_int_array(y_pred_b)
    if not (y_true.shape == y_pred_a.shape == y_pred_b.shape):
        raise ValueError("y_true, y_pred_a and y_pred_b must have equal length")
    n = y_true.shape[0]
    if n == 0:
        raise ValueError("empty prediction set")

    groups = _prepare_groups(group_ids, n)
    prec_a = per_class_precision(y_true, y_pred_a, class_names)
    prec_b = per_class_precision(y_true, y_pred_b, class_names)
    n_pred_a = {c: int(np.sum(y_pred_a == i)) for i, c in enumerate(class_names)}
    n_pred_b = {c: int(np.sum(y_pred_b == i)) for i, c in enumerate(class_names)}
    rng = np.random.default_rng(seed)
    diffs = np.full((n_boot, len(class_names)), np.nan, dtype=np.float64)
    for b in range(n_boot):
        idx = _resample_indices(rng, n, groups)
        yt = y_true[idx]
        pa = per_class_precision(yt, y_pred_a[idx], class_names)
        pb = per_class_precision(yt, y_pred_b[idx], class_names)
        diffs[b] = [pa[c] - pb[c] for c in class_names]

    out: dict[str, dict] = {}
    for i, c in enumerate(class_names):
        valid = diffs[:, i]
        valid = valid[~np.isnan(valid)]
        base = {"precision_a": prec_a[c], "precision_b": prec_b[c],
                "n_predicted_a": n_pred_a[c], "n_predicted_b": n_pred_b[c]}
        if valid.size == 0:
            out[c] = {
                **base, "diff_point": float("nan"), "diff_ci_low": float("nan"),
                "diff_ci_high": float("nan"), "p_a_gt_b": float("nan"),
                "significant": False, "n_boot_valid": 0,
            }
            continue
        lo, hi = _percentile_ci(valid, ci)
        out[c] = {
            **base,
            "diff_point": prec_a[c] - prec_b[c],
            "diff_ci_low": lo,
            "diff_ci_high": hi,
            "p_a_gt_b": float(np.mean(valid > 0.0)),
            "significant": bool(lo > 0.0 or hi < 0.0),
            "n_boot_valid": int(valid.size),
        }
    return {
        "metric": "per_class_precision_diff",
        "compared": "a_minus_b",
        "ci_level": ci,
        "n": int(n),
        "n_boot": int(n_boot),
        "classes": out,
        **_sampling_meta(groups, n),
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
    group_ids = data.get("group_ids")

    report: dict = {
        "class_names": class_names,
        label_a: {
            "disease_macro_f1": bootstrap_macro_f1_ci(
                y_true, y_pred_a, class_names,
                n_boot=args.n_boot, ci=args.ci, seed=args.seed, group_ids=group_ids,
            ),
            "per_class_recall": bootstrap_per_class_recall_ci(
                y_true, y_pred_a, class_names,
                n_boot=args.n_boot, ci=args.ci, seed=args.seed, group_ids=group_ids,
            ),
            "per_class_precision": bootstrap_per_class_precision_ci(
                y_true, y_pred_a, class_names,
                n_boot=args.n_boot, ci=args.ci, seed=args.seed, group_ids=group_ids,
            ),
        },
    }
    if data.get("y_pred_b") is not None:
        label_b = data.get("label_b", "model_b")
        report[label_b] = {
            "disease_macro_f1": bootstrap_macro_f1_ci(
                y_true, data["y_pred_b"], class_names,
                n_boot=args.n_boot, ci=args.ci, seed=args.seed, group_ids=group_ids,
            ),
            "per_class_recall": bootstrap_per_class_recall_ci(
                y_true, data["y_pred_b"], class_names,
                n_boot=args.n_boot, ci=args.ci, seed=args.seed, group_ids=group_ids,
            ),
            "per_class_precision": bootstrap_per_class_precision_ci(
                y_true, data["y_pred_b"], class_names,
                n_boot=args.n_boot, ci=args.ci, seed=args.seed, group_ids=group_ids,
            ),
        }
        report[f"{label_a}_vs_{label_b}"] = {
            "disease_macro_f1": paired_bootstrap(
                y_true, y_pred_a, data["y_pred_b"], class_names,
                n_boot=args.n_boot, ci=args.ci, seed=args.seed, group_ids=group_ids,
            ),
            "per_class_recall": paired_per_class_recall(
                y_true, y_pred_a, data["y_pred_b"], class_names,
                n_boot=args.n_boot, ci=args.ci, seed=args.seed, group_ids=group_ids,
            ),
            "per_class_precision": paired_per_class_precision(
                y_true, y_pred_a, data["y_pred_b"], class_names,
                n_boot=args.n_boot, ci=args.ci, seed=args.seed, group_ids=group_ids,
            ),
        }

    print(_fmt(report))
    if args.json_out:
        args.json_out.write_text(_fmt(report), encoding="utf-8")
        print(f"\nwrote {args.json_out}")


if __name__ == "__main__":
    main()
