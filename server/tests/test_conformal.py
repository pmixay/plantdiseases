"""Inductive conformal prediction (RAPS / APS) sanity tests.

The sets are non-empty by construction, monotonic in ``q_hat``, and
empirical coverage on the calibration set itself must match the
nominal target within Monte-Carlo noise. We seed everything so the
tests stay deterministic on CI.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from conformal import (
    ConformalConfig,
    aps_score,
    calibrate_q_hat,
    load_conformal_config,
    prediction_set,
    raps_score,
)

CLASSES = ["a", "b", "c", "d", "e"]


def _probs(values):
    return dict(zip(CLASSES, values, strict=False))


def test_aps_score_is_cumulative_sorted_descending():
    p = np.array([0.6, 0.2, 0.1, 0.07, 0.03])
    np.testing.assert_allclose(aps_score(p), np.cumsum(p))


def test_raps_adds_size_penalty_only_past_k_reg():
    p = np.array([0.5, 0.3, 0.1, 0.07, 0.03])
    s_aps = aps_score(p)
    s_raps = raps_score(p, lambda_reg=0.1, k_reg=2)
    # ranks 1 and 2 are inside the regulariser → identical to APS.
    np.testing.assert_allclose(s_raps[:2], s_aps[:2])
    # ranks 3..5 grow by lambda * (rank - k_reg).
    expected_extra = np.array([0.1, 0.2, 0.3])
    np.testing.assert_allclose(s_raps[2:] - s_aps[2:], expected_extra)


def test_prediction_set_is_never_empty():
    cfg = ConformalConfig(method="aps", alpha=0.1, q_hat=0.0)
    out = prediction_set(_probs([0.4, 0.3, 0.2, 0.07, 0.03]), cfg)
    assert len(out) >= 1


def test_prediction_set_grows_monotonically_with_q_hat():
    p = _probs([0.4, 0.25, 0.2, 0.1, 0.05])
    sizes = []
    for q in (0.30, 0.50, 0.70, 0.90, 1.10):
        cfg = ConformalConfig(method="aps", alpha=0.1, q_hat=q)
        sizes.append(len(prediction_set(p, cfg)))
    # Non-decreasing as q_hat grows.
    assert sizes == sorted(sizes)


def test_aps_set_matches_hand_computed_threshold():
    """At q_hat=0.65 we accumulate top-1 (0.5) and top-2 (0.5+0.2=0.7≥0.65),
    so the set has exactly 2 classes."""
    p = _probs([0.5, 0.2, 0.15, 0.1, 0.05])
    cfg = ConformalConfig(method="aps", alpha=0.1, q_hat=0.65)
    out = prediction_set(p, cfg)
    assert set(out) == {"a", "b"}


def test_calibrate_q_hat_meets_marginal_coverage_guarantee():
    """The conformal coverage guarantee is *marginal*: empirical
    coverage on a held-out test split must be at least ``1 - alpha``
    (within MC noise) under exchangeability.

    We deliberately split into cal/test so the cal-set isn't double-
    used; on the cal-set itself coverage trivially upper-bounds
    1-alpha and isn't a useful sanity check."""
    rng = np.random.RandomState(0)
    n_classes, n = 6, 3000
    labels = rng.randint(0, n_classes, size=n)
    logits = rng.randn(n, n_classes) * 0.4
    logits[np.arange(n), labels] += 1.5
    probs = np.exp(logits - logits.max(axis=1, keepdims=True))
    probs /= probs.sum(axis=1, keepdims=True)

    cal_p, test_p = probs[:1500], probs[1500:]
    cal_y, test_y = labels[:1500], labels[1500:]

    cfg = calibrate_q_hat(cal_p, cal_y, method="raps", alpha=0.1)
    assert cfg.calibration_n == 1500
    assert 0.0 < cfg.q_hat < 1.0 + 5 * cfg.lambda_reg

    classes = list(map(str, range(n_classes)))
    covered = 0
    for i in range(test_p.shape[0]):
        ps = prediction_set({classes[k]: float(test_p[i, k]) for k in range(n_classes)}, cfg)
        if classes[test_y[i]] in ps:
            covered += 1
    coverage = covered / test_p.shape[0]
    # Marginal guarantee: ≥ 1 - alpha within MC noise.
    assert coverage >= 0.88, f"coverage={coverage:.3f}"


def test_calibrate_q_hat_aps_mode_also_works():
    rng = np.random.RandomState(1)
    n_classes, n_cal = 4, 500
    labels = rng.randint(0, n_classes, size=n_cal)
    logits = rng.randn(n_cal, n_classes)
    logits[np.arange(n_cal), labels] += 2.0
    probs = np.exp(logits - logits.max(axis=1, keepdims=True))
    probs /= probs.sum(axis=1, keepdims=True)

    cfg = calibrate_q_hat(probs, labels, method="aps", alpha=0.05)
    assert cfg.method == "aps"
    assert cfg.empirical_coverage is not None
    # On-cal-set coverage upper-bounds 1-alpha; this is just a smoke
    # check that calibration ran and produced a usable threshold.
    assert cfg.empirical_coverage >= 0.94


def test_calibrate_rejects_bad_inputs():
    rng = np.random.RandomState(2)
    probs = rng.dirichlet(np.ones(3), size=10)
    labels = rng.randint(0, 3, size=10)
    with pytest.raises(ValueError):
        calibrate_q_hat(probs, labels, method="aps", alpha=0.0)
    with pytest.raises(ValueError):
        calibrate_q_hat(probs, labels, method="bogus", alpha=0.1)
    with pytest.raises(ValueError):
        calibrate_q_hat(np.empty((0, 3)), np.empty(0, dtype=int), alpha=0.1)


def test_load_conformal_config_round_trip(tmp_path):
    payload = {
        "method": "raps",
        "alpha": 0.1,
        "q_hat": 0.84,
        "lambda_reg": 0.01,
        "k_reg": 1,
        "calibration_n": 1500,
        "empirical_coverage": 0.91,
    }
    (tmp_path / "conformal.json").write_text(json.dumps(payload))
    cfg = load_conformal_config(tmp_path)
    assert cfg is not None
    assert cfg.method == "raps"
    assert cfg.q_hat == pytest.approx(0.84)
    assert abs(cfg.coverage_target - 0.9) < 1e-6


def test_load_conformal_config_returns_none_when_missing(tmp_path):
    assert load_conformal_config(tmp_path) is None
    assert load_conformal_config(None) is None


def test_load_conformal_config_handles_malformed_json(tmp_path):
    (tmp_path / "conformal.json").write_text("{not valid json")
    assert load_conformal_config(tmp_path) is None


def test_load_conformal_config_rejects_unknown_method(tmp_path):
    (tmp_path / "conformal.json").write_text(json.dumps({
        "method": "fancy", "alpha": 0.1, "q_hat": 0.5,
    }))
    assert load_conformal_config(tmp_path) is None


def test_pipeline_emits_prediction_set_when_calibration_loaded(tmp_path):
    """Smoke test: drop a conformal.json next to a fake model dir and
    confirm the pipeline reads it and surfaces a prediction_set."""
    from PIL import Image

    from pipeline import PlantDiseasePipeline

    (tmp_path / "conformal.json").write_text(json.dumps({
        "method": "aps", "alpha": 0.1, "q_hat": 0.95,
    }))
    fake_classifier_path = tmp_path / "classifier.pth"  # doesn't need to exist
    pipe = PlantDiseasePipeline(detector_path=None,
                                classifier_path=fake_classifier_path)
    assert pipe.conformal is not None

    img = Image.new("RGB", (96, 96), color=(40, 150, 60))
    out = pipe.analyze(img)
    assert "prediction_set" in out
    ps = out["prediction_set"]
    assert ps is not None
    assert ps["method"] == "aps"
    assert 0.0 < ps["coverage"] <= 1.0
    assert isinstance(ps["classes"], list) and len(ps["classes"]) >= 1


def test_pipeline_omits_prediction_set_without_calibration(tmp_path):
    from PIL import Image

    from pipeline import PlantDiseasePipeline

    pipe = PlantDiseasePipeline()
    assert pipe.conformal is None
    img = Image.new("RGB", (96, 96), color=(40, 150, 60))
    out = pipe.analyze(img)
    assert out.get("prediction_set") is None
