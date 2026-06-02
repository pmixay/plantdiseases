"""Tests for the paired-bootstrap evaluation tool (report Stage 0).

These exercise the statistical contract, not a model: the disease-only
macro-F1 must match a hand-computed value, the single-model CI must
bracket the point estimate, and the paired bootstrap must detect a
clear, real ordering while correctly calling a tiny gap *not*
significant.
"""

from __future__ import annotations

import numpy as np

from tools import eval_bootstrap as eb

# 4-class toy taxonomy: two diseases + the two non-disease classes that
# the macro-F1 deliberately ignores.
CLASS_NAMES = ["disease_a", "disease_b", "healthy", "not_a_plant"]


def test_disease_macro_f1_excludes_non_disease():
    # Perfect predictions → macro-F1 over the two disease labels = 1.0.
    y = np.array([0, 1, 2, 3, 0, 1])
    assert eb.disease_macro_f1(y, y, CLASS_NAMES) == 1.0

    # A perfectly-classified healthy/not_a_plant set but a totally wrong
    # disease set must NOT score 1.0 — proves the non-disease labels are
    # excluded from the metric, not silently inflating it.
    y_true = np.array([0, 1, 2, 3])
    y_pred = np.array([1, 0, 2, 3])  # diseases swapped, non-disease correct
    assert eb.disease_macro_f1(y_true, y_pred, CLASS_NAMES) == 0.0


def test_single_model_ci_brackets_point_and_is_ordered():
    rng = np.random.default_rng(0)
    n = 400
    y_true = rng.integers(0, 4, size=n)
    # 85%-correct predictions: flip ~15% to a random other class.
    y_pred = y_true.copy()
    flip = rng.random(n) < 0.15
    y_pred[flip] = (y_true[flip] + rng.integers(1, 4, size=flip.sum())) % 4

    out = eb.bootstrap_macro_f1_ci(y_true, y_pred, CLASS_NAMES, n_boot=500, seed=1)
    assert out["ci_low"] <= out["point"] <= out["ci_high"]
    assert 0.0 < out["ci_low"] < out["ci_high"] <= 1.0
    assert out["n"] == n
    # Half-width should be a sane small-sample magnitude, not degenerate.
    assert 0.0 < out["ci_half_width"] < 0.25


def test_paired_bootstrap_detects_clear_winner():
    rng = np.random.default_rng(7)
    n = 500
    y_true = rng.integers(0, 4, size=n)
    # Model A: near-perfect. Model B: 35% of disease rows corrupted.
    y_a = y_true.copy()
    y_b = y_true.copy()
    corrupt = (rng.random(n) < 0.35) & np.isin(y_true, [0, 1])
    y_b[corrupt] = (y_true[corrupt] + 1) % 2  # swap disease_a<->disease_b

    out = eb.paired_bootstrap(y_true, y_a, y_b, CLASS_NAMES, n_boot=500, seed=2)
    assert out["f1_a"] > out["f1_b"]
    assert out["diff_point"] > 0.0
    assert out["diff_ci_low"] > 0.0  # CI excludes zero
    assert out["significant"] is True
    assert out["p_a_gt_b"] > 0.95


def test_paired_bootstrap_calls_noise_not_significant():
    # Two models that are identical → the difference must straddle zero
    # and never be flagged significant (the report's core warning about
    # the 0.73-vs-0.69 INT8>FP32 inversion being noise).
    rng = np.random.default_rng(3)
    n = 450
    y_true = rng.integers(0, 4, size=n)
    y_pred = y_true.copy()
    mask = rng.random(n) < 0.2
    y_pred[mask] = rng.integers(0, 4, size=int(mask.sum()))

    out = eb.paired_bootstrap(y_true, y_pred, y_pred, CLASS_NAMES, n_boot=300, seed=4)
    assert out["diff_point"] == 0.0
    assert out["diff_ci_low"] <= 0.0 <= out["diff_ci_high"]
    assert out["significant"] is False
