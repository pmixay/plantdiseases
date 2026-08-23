"""Tests for the paired-bootstrap evaluation tool (report Stage 0).

These exercise the statistical contract, not a model: the disease-only
macro-F1 must match a hand-computed value, the single-model CI must
bracket the point estimate, and the paired bootstrap must detect a
clear, real ordering while correctly calling a tiny gap *not*
significant.
"""

from __future__ import annotations

import numpy as np
import pytest

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


def test_per_class_recall_matches_hand_computed_and_marks_absent_class():
    # disease_a: 2 true, 1 hit → recall 0.5.  disease_b: 1 true, 1 hit → 1.0.
    # healthy: 1 true, 0 hit → 0.0.  not_a_plant: 0 true → undefined (nan).
    y_true = np.array([0, 0, 1, 2])
    y_pred = np.array([0, 1, 1, 0])
    rec = eb.per_class_recall(y_true, y_pred, CLASS_NAMES)
    assert rec["disease_a"] == 0.5
    assert rec["disease_b"] == 1.0
    assert rec["healthy"] == 0.0
    assert np.isnan(rec["not_a_plant"])  # no support → undefined, never 0/1


def test_per_class_recall_ci_brackets_point_and_reports_support():
    rng = np.random.default_rng(0)
    n = 400
    y_true = rng.integers(0, 4, size=n)
    y_pred = y_true.copy()
    flip = rng.random(n) < 0.2
    y_pred[flip] = (y_true[flip] + rng.integers(1, 4, size=flip.sum())) % 4

    out = eb.bootstrap_per_class_recall_ci(y_true, y_pred, CLASS_NAMES, n_boot=400, seed=1)
    assert out["metric"] == "per_class_recall"
    for c in CLASS_NAMES:
        cls = out["classes"][c]
        assert cls["ci_low"] <= cls["point"] <= cls["ci_high"]
        assert cls["support"] == int(np.sum(y_true == CLASS_NAMES.index(c)))
        # ~80% correct → recall well above zero, CI a sane width.
        assert 0.0 < cls["ci_half_width"] < 0.25


def test_paired_per_class_recall_flags_only_the_degraded_class():
    rng = np.random.default_rng(7)
    n = 600
    y_true = rng.integers(0, 4, size=n)
    # A near-perfect; B drops ~half of disease_a's true positives to a
    # wrong class → only disease_a recall should be significantly worse.
    y_a = y_true.copy()
    y_b = y_true.copy()
    corrupt = (rng.random(n) < 0.5) & (y_true == 0)
    y_b[corrupt] = 2  # mispredict disease_a as healthy

    out = eb.paired_per_class_recall(y_true, y_a, y_b, CLASS_NAMES, n_boot=400, seed=2)
    a = out["classes"]["disease_a"]
    assert a["diff_point"] > 0.0        # A recalls disease_a better
    assert a["diff_ci_low"] > 0.0       # CI excludes zero
    assert a["significant"] is True
    # disease_b was untouched → not significant.
    assert out["classes"]["disease_b"]["significant"] is False


def test_per_class_recall_ci_handles_rare_class_without_crashing():
    # A class with tiny support must still yield a CI from the replicates
    # where it was drawn, never a divide-by-zero or all-nan crash.
    y_true = np.array([0, 0, 0, 0, 0, 0, 0, 0, 1, 2])  # disease_b support=1
    y_pred = y_true.copy()
    out = eb.bootstrap_per_class_recall_ci(y_true, y_pred, CLASS_NAMES, n_boot=300, seed=5)
    db = out["classes"]["disease_b"]
    assert db["support"] == 1
    assert db["n_boot_valid"] > 0
    assert db["point"] == 1.0


def test_per_class_precision_matches_hand_computed_and_marks_unpredicted_class():
    # disease_a: predicted at {0,3}, true only at 0 → precision 0.5.
    # disease_b: predicted at {1,2}, true only at 2 → precision 0.5.
    # healthy / not_a_plant: never predicted → undefined (nan), never 0/1.
    y_true = np.array([0, 0, 1, 2])
    y_pred = np.array([0, 1, 1, 0])
    prec = eb.per_class_precision(y_true, y_pred, CLASS_NAMES)
    assert prec["disease_a"] == 0.5
    assert prec["disease_b"] == 0.5
    assert np.isnan(prec["healthy"])
    assert np.isnan(prec["not_a_plant"])


def test_per_class_precision_ci_brackets_point_and_reports_predicted():
    rng = np.random.default_rng(0)
    n = 400
    y_true = rng.integers(0, 4, size=n)
    y_pred = y_true.copy()
    flip = rng.random(n) < 0.2
    y_pred[flip] = (y_true[flip] + rng.integers(1, 4, size=flip.sum())) % 4

    out = eb.bootstrap_per_class_precision_ci(y_true, y_pred, CLASS_NAMES, n_boot=400, seed=1)
    assert out["metric"] == "per_class_precision"
    for c in CLASS_NAMES:
        cls = out["classes"][c]
        assert cls["ci_low"] <= cls["point"] <= cls["ci_high"]
        assert cls["support"] == int(np.sum(y_true == CLASS_NAMES.index(c)))
        assert cls["n_predicted"] == int(np.sum(y_pred == CLASS_NAMES.index(c)))
        assert 0.0 < cls["ci_half_width"] < 0.25


def test_per_class_precision_handles_never_predicted_class_without_crashing():
    # A class the model never predicts must yield a nan point + empty-CI
    # record, never a divide-by-zero or all-nan crash.
    y_true = np.array([0, 0, 1, 1, 2, 2, 0, 1])
    y_pred = np.array([0, 0, 1, 1, 0, 0, 0, 1])  # not_a_plant (3) never predicted
    out = eb.bootstrap_per_class_precision_ci(y_true, y_pred, CLASS_NAMES, n_boot=200, seed=3)
    nap = out["classes"]["not_a_plant"]
    assert nap["n_predicted"] == 0
    assert nap["n_boot_valid"] == 0
    assert np.isnan(nap["point"])


def test_paired_per_class_precision_flags_only_polluted_class():
    rng = np.random.default_rng(19)
    n = 800
    y_true = rng.integers(0, 4, size=n)
    y_a = y_true.copy()
    y_b = y_true.copy()
    # Add many false positives for disease_a in model B without changing the
    # rows whose truth is disease_a.  This is a precision-only regression.
    pollute = (rng.random(n) < 0.65) & (y_true == 2)
    y_b[pollute] = 0

    out = eb.paired_per_class_precision(
        y_true, y_a, y_b, CLASS_NAMES, n_boot=400, seed=20)
    da = out["classes"]["disease_a"]
    assert da["diff_point"] > 0.0
    assert da["diff_ci_low"] > 0.0
    assert da["significant"] is True
    assert out["classes"]["disease_b"]["significant"] is False


@pytest.mark.parametrize("n_boot", [0, -1, 1.5, True])
def test_bootstrap_rejects_invalid_iteration_count(n_boot):
    y = np.array([0, 1, 2, 3])
    with pytest.raises(ValueError, match="n_boot"):
        eb.bootstrap_macro_f1_ci(y, y, CLASS_NAMES, n_boot=n_boot)


@pytest.mark.parametrize("ci", [0.0, 1.0, -0.1, 1.1])
def test_bootstrap_rejects_invalid_ci(ci):
    y = np.array([0, 1, 2, 3])
    with pytest.raises(ValueError, match="ci"):
        eb.bootstrap_macro_f1_ci(y, y, CLASS_NAMES, n_boot=10, ci=ci)


def test_capture_family_bootstrap_resamples_groups_and_marks_row_fallback():
    y_true = np.array([0, 0, 1, 1, 2, 3])
    y_pred = y_true.copy()
    groups = ["capture-a", "capture-a", "capture-b", "capture-b", "c", "d"]
    out = eb.bootstrap_macro_f1_ci(
        y_true, y_pred, CLASS_NAMES, n_boot=30, seed=9, group_ids=groups)
    assert out["resampling_unit"] == "capture_family"
    assert out["n_groups"] == 4
    assert out["independence_advisory"] is None

    advisory = eb.paired_bootstrap(
        y_true, y_pred, y_pred, CLASS_NAMES, n_boot=20, seed=10)
    assert advisory["resampling_unit"] == "row"
    assert "pseudo-replication" in advisory["independence_advisory"]


def test_capture_family_ids_must_be_complete_and_aligned():
    y = np.array([0, 1, 2, 3])
    with pytest.raises(ValueError, match="matching"):
        eb.bootstrap_macro_f1_ci(
            y, y, CLASS_NAMES, n_boot=5, group_ids=["a"])
    with pytest.raises(ValueError, match="blank"):
        eb.bootstrap_macro_f1_ci(
            y, y, CLASS_NAMES, n_boot=5, group_ids=["a", "b", "", "d"])
