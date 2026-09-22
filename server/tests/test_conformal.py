"""Runtime-only conformal artifact loading and prediction-set tests."""

from __future__ import annotations

import json

import numpy as np
import pytest

from conformal import (
    ConformalConfig,
    ConformalLoadError,
    aps_score,
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
    # Same-calibration-row coverage is diagnostic, not independent evidence.
    assert cfg.coverage_target == pytest.approx(0.90)


def test_coverage_target_falls_back_to_nominal_without_measurement(tmp_path):
    """A bundle predating `empirical_coverage` still reports 1 - alpha."""
    (tmp_path / "conformal.json").write_text(json.dumps({
        "method": "raps", "alpha": 0.1, "q_hat": 0.84,
    }))
    cfg = load_conformal_config(tmp_path)
    assert cfg is not None
    assert cfg.empirical_coverage is None
    assert cfg.coverage_target == pytest.approx(0.9)


def test_calibration_coverage_does_not_override_nominal_target(tmp_path):
    """The 2.7.0 value was measured on calibration rows, not a locked test."""
    (tmp_path / "conformal.json").write_text(json.dumps({
        "method": "raps", "alpha": 0.35, "q_hat": 0.987,
        "lambda_reg": 0.75, "k_reg": 1, "calibration_n": 982,
        "empirical_coverage": 0.9675,
    }))
    cfg = load_conformal_config(tmp_path)
    assert cfg is not None
    assert cfg.empirical_coverage == pytest.approx(0.9675)
    assert cfg.coverage_target == pytest.approx(0.65)


@pytest.mark.parametrize(
    "patch",
    [
        {"alpha": float("nan")},
        {"alpha": True},
        {"q_hat": float("inf")},
        {"q_hat": -0.1},
        {"lambda_reg": -0.01},
        {"k_reg": 1.5},
        {"calibration_n": -1},
        {"empirical_coverage": 1.1},
        {"target_coverage": 1.0},
        {"target_coverage": "0.9"},
        {"audit_n": -5},
        {"empirical_coverage_kind": 7},
    ],
)
def test_load_conformal_config_rejects_invalid_numeric_contract(tmp_path, patch):
    payload = {
        "method": "raps", "alpha": 0.1, "q_hat": 0.84,
        "lambda_reg": 0.01, "k_reg": 1, "calibration_n": 100,
    }
    payload.update(patch)
    (tmp_path / "conformal.json").write_text(json.dumps(payload))
    with pytest.raises(ConformalLoadError):
        load_conformal_config(tmp_path)


def test_prediction_set_rejects_non_probability_vectors():
    cfg = ConformalConfig(method="aps", alpha=0.1, q_hat=0.8)
    with pytest.raises(ValueError, match="finite"):
        prediction_set({"a": float("nan"), "b": 1.0}, cfg)
    with pytest.raises(ValueError, match="sum"):
        prediction_set({"a": 0.7, "b": 0.7}, cfg)


def test_load_conformal_config_returns_none_when_missing(tmp_path, monkeypatch):
    # The loader has cwd-relative fallbacks (`server/models/conformal.json`,
    # `models/conformal.json`) so the bare assertion below would silently
    # pick up the real repo artefact once it exists. Run inside tmp_path so
    # those fallbacks resolve to absent paths too.
    monkeypatch.chdir(tmp_path)
    assert load_conformal_config(tmp_path) is None
    assert load_conformal_config(None) is None


def test_load_conformal_config_handles_malformed_json(tmp_path):
    (tmp_path / "conformal.json").write_text("{not valid json")
    with pytest.raises(ConformalLoadError):
        load_conformal_config(tmp_path)


def test_load_conformal_config_rejects_unknown_method(tmp_path):
    (tmp_path / "conformal.json").write_text(json.dumps({
        "method": "fancy", "alpha": 0.1, "q_hat": 0.5,
    }))
    with pytest.raises(ConformalLoadError):
        load_conformal_config(tmp_path)


def test_load_conformal_config_searches_classifier_dir(tmp_path):
    """The pipeline asks ``load_conformal_config(classifier_dir)``; the
    helper must pick up the calibration file dropped next to the
    weights."""
    payload = {"method": "aps", "alpha": 0.1, "q_hat": 0.95}
    (tmp_path / "conformal.json").write_text(json.dumps(payload))
    cfg = load_conformal_config(tmp_path)
    assert cfg is not None
    assert cfg.method == "aps"
    assert cfg.q_hat == pytest.approx(0.95)


def test_audit_backed_target_is_advertised_instead_of_one_minus_alpha(tmp_path):
    """Notebook §6 picks alpha as a search knob (0.6 since 2.7.0) subject to the
    out-of-sample audit clearing the product target; the API must advertise
    that target, not 1 - alpha = 0.4."""
    (tmp_path / "conformal.json").write_text(json.dumps({
        "method": "raps", "alpha": 0.6, "q_hat": 0.9708, "lambda_reg": 0.3,
        "k_reg": 1, "calibration_n": 934, "empirical_coverage": 0.9872,
        "empirical_coverage_kind": "out_of_sample", "audit_n": 234,
        "target_coverage": 0.9, "coverage_basis": "audit_target",
    }))
    cfg = load_conformal_config(tmp_path)
    assert cfg is not None
    assert cfg.audit_backs_target is True
    assert cfg.coverage_basis == "audit_target"
    assert cfg.coverage_target == pytest.approx(0.9)


@pytest.mark.parametrize(
    "patch",
    [
        {"empirical_coverage_kind": "in_sample"},     # measured on the fitting rows
        {"audit_n": 20},                               # too few audit rows to lean on
        {"empirical_coverage": 0.85},                  # audit missed the target
        {"target_coverage": None},                     # legacy sidecar, no target
    ],
)
def test_target_is_not_advertised_without_a_backing_audit(tmp_path, patch):
    payload = {
        "method": "raps", "alpha": 0.6, "q_hat": 0.9708, "lambda_reg": 0.3,
        "k_reg": 1, "calibration_n": 934, "empirical_coverage": 0.9872,
        "empirical_coverage_kind": "out_of_sample", "audit_n": 234,
        "target_coverage": 0.9,
    }
    payload.update(patch)
    payload = {k: v for k, v in payload.items() if v is not None}
    (tmp_path / "conformal.json").write_text(json.dumps(payload))
    cfg = load_conformal_config(tmp_path)
    assert cfg is not None
    assert cfg.audit_backs_target is False
    assert cfg.coverage_basis == "nominal_one_minus_alpha"
    assert cfg.coverage_target == pytest.approx(0.4)
