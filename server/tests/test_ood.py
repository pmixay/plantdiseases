"""Embedding-space OOD (k-NN cosine) scorer + NAP-gate integration tests.

The scorer math is deterministic, so these assert exact behaviour: an
in-distribution query sits at ~0 distance from its neighbours, an
orthogonal query at ~1, and the gate only fires when the detector AND the
embedding signal both call OOD.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from ood import (
    EmbeddingOODConfig,
    EmbeddingOODScorer,
    load_embedding_ood,
    parse_embedding_ood_config,
)


def _cfg(threshold=0.5, k=1, embed_dim=4):
    return EmbeddingOODConfig(
        method="knn", metric="cosine", k=k, threshold=threshold, embed_dim=embed_dim,
    )


def _basis_bank():
    # Four orthonormal basis vectors in R^4.
    return np.eye(4, dtype=np.float32)


def test_score_zero_for_in_distribution_vector():
    scorer = EmbeddingOODScorer(_basis_bank(), _cfg(k=1))
    # Exactly a bank vector → nearest cosine sim is 1 → score 0.
    assert scorer.score(np.array([1.0, 0, 0, 0], dtype=np.float32)) == pytest.approx(0.0)
    # Magnitude doesn't matter (cosine) — a scaled copy scores the same.
    assert scorer.score(np.array([5.0, 0, 0, 0], dtype=np.float32)) == pytest.approx(0.0)


def test_score_one_for_far_vector():
    scorer = EmbeddingOODScorer(_basis_bank(), _cfg(k=1))
    # k-NN takes the NEAREST neighbour (max cosine). For [-1,0,0,0] the
    # nearest basis vector is an orthogonal one (cos 0, not the anti-parallel
    # cos -1), so the score is 1 - 0 = 1.0 — a clean "far from manifold".
    q = np.array([-1.0, 0, 0, 0], dtype=np.float32)
    assert scorer.score(q) == pytest.approx(1.0)


def test_is_ood_respects_threshold():
    scorer = EmbeddingOODScorer(_basis_bank(), _cfg(threshold=0.5, k=1))
    assert scorer.is_ood(np.array([1.0, 0, 0, 0], dtype=np.float32)) is False  # score 0
    # A 45° vector between two axes: nearest cos = cos(45°) ≈ 0.707 → score ≈ 0.293
    half = np.array([1.0, 1.0, 0, 0], dtype=np.float32)
    assert scorer.score(half) == pytest.approx(1 - (1 / np.sqrt(2)), abs=1e-6)
    assert scorer.is_ood(half) is False  # 0.293 < 0.5
    # A vector mostly opposite the basis is far → OOD.
    assert scorer.is_ood(np.array([-1.0, -1.0, 0, 0], dtype=np.float32)) is True


def test_k_is_clamped_to_bank_size():
    # k larger than the bank must not error; it averages all available sims.
    scorer = EmbeddingOODScorer(_basis_bank(), _cfg(k=99))
    q = np.array([1.0, 0, 0, 0], dtype=np.float32)
    # Mean cosine to all 4 basis vectors = (1+0+0+0)/4 = 0.25 → score 0.75.
    assert scorer.score(q) == pytest.approx(0.75)


def test_dim_mismatch_returns_nan_and_not_ood():
    scorer = EmbeddingOODScorer(_basis_bank(), _cfg(embed_dim=4))
    s = scorer.score(np.array([1.0, 0, 0], dtype=np.float32))  # 3-dim query
    assert np.isnan(s)
    assert scorer.is_ood(np.array([1.0, 0, 0], dtype=np.float32)) is False


def test_parse_embedding_ood_config_variants():
    assert parse_embedding_ood_config({}) is None
    assert parse_embedding_ood_config({"ood": {}}) is None
    # Missing required keys → None, not a crash.
    assert parse_embedding_ood_config({"ood": {"embedding": {"method": "knn"}}}) is None
    cfg = parse_embedding_ood_config({
        "ood": {"embedding": {
            "method": "knn", "metric": "cosine", "k": 7,
            "threshold": 0.4, "embed_dim": 384,
        }}
    })
    assert cfg is not None and cfg.k == 7 and cfg.threshold == 0.4
    # Unsupported method is rejected.
    assert parse_embedding_ood_config({
        "ood": {"embedding": {"method": "isoforest", "threshold": 0.4, "embed_dim": 4}}
    }) is None


def test_load_embedding_ood_round_trip(tmp_path):
    bank = np.eye(4, dtype=np.float32)
    np.savez(tmp_path / "ood_embedding.npz", bank=bank)
    (tmp_path / "classes.json").write_text(json.dumps({
        "ood": {"embedding": {
            "method": "knn", "metric": "cosine", "k": 1,
            "threshold": 0.5, "embed_dim": 4, "bank_file": "ood_embedding.npz",
        }}
    }))
    meta = json.loads((tmp_path / "classes.json").read_text())
    scorer = load_embedding_ood(tmp_path, meta)
    assert scorer is not None
    assert scorer.is_ood(np.array([1.0, 0, 0, 0], dtype=np.float32)) is False


def test_load_embedding_ood_missing_bank_returns_none(tmp_path):
    meta = {"ood": {"embedding": {
        "method": "knn", "threshold": 0.5, "embed_dim": 4, "bank_file": "absent.npz",
    }}}
    assert load_embedding_ood(tmp_path, meta) is None


def test_load_embedding_ood_dim_mismatch_returns_none(tmp_path):
    np.savez(tmp_path / "ood_embedding.npz", bank=np.eye(8, dtype=np.float32))
    meta = {"ood": {"embedding": {
        "method": "knn", "threshold": 0.5, "embed_dim": 4, "bank_file": "ood_embedding.npz",
    }}}
    assert load_embedding_ood(tmp_path, meta) is None


# ── NAP-gate decision (pipeline._decide_nap_route) ───────────────────

_CLASS_NAMES = [
    "gray_mold_botrytis", "healthy", "leaf_scorch_underwatering",
    "leaf_spot", "not_a_plant", "nutrient_deficiency", "pests",
    "powdery_mildew", "viral_disease",
]


def _route(*, det_conf, ood_score=None, ood_threshold=None,
           ml_floor=0.0, max_logit=0.0, class_name="leaf_spot",
           require_classifier_signal=False):
    from pipeline import _decide_nap_route

    triggered, _ = _decide_nap_route(
        class_name=class_name, class_names=_CLASS_NAMES, det_conf=det_conf,
        ood_score=ood_score, ood_threshold=ood_threshold,
        ml_floor=ml_floor, max_logit=max_logit,
        require_classifier_signal=require_classifier_signal,
    )
    return triggered


def test_gate_fires_only_when_detector_low_and_embedding_ood():
    # OOD score high (far from manifold) + detector low → reroute.
    assert _route(det_conf=0.20, ood_score=0.9, ood_threshold=0.5) is True
    # Same OOD score but a confident detector → keep the verdict (AND-gate).
    assert _route(det_conf=0.80, ood_score=0.9, ood_threshold=0.5) is False
    # In-distribution embedding (low score) + low detector → keep verdict.
    assert _route(det_conf=0.20, ood_score=0.1, ood_threshold=0.5) is False


def test_gate_prefers_embedding_over_maxlogit_when_both_present():
    # With a bank shipped, a high MaxLogit must NOT veto an embedding-OOD
    # reroute — embedding is the live signal, MaxLogit is the dead one.
    assert _route(
        det_conf=0.20, ood_score=0.9, ood_threshold=0.5,
        ml_floor=4.0, max_logit=99.0,
    ) is True


def test_gate_falls_back_to_maxlogit_without_embedding_bank():
    # No ood_threshold → use the MaxLogit floor.
    assert _route(det_conf=0.20, ood_score=None, ml_floor=4.0, max_logit=1.0) is True
    assert _route(det_conf=0.20, ood_score=None, ml_floor=4.0, max_logit=9.0) is False
    # Bank configured but no embedding produced (ood_score None) also falls
    # back to the MaxLogit floor rather than skipping the classifier signal.
    assert _route(
        det_conf=0.20, ood_score=None, ood_threshold=0.5, ml_floor=4.0, max_logit=1.0,
    ) is True


def test_gate_degrades_to_detector_only_with_no_classifier_signal():
    # No embedding bank and MaxLogit floor off → detector confidence alone.
    assert _route(det_conf=0.20, ood_score=None, ml_floor=0.0) is True
    assert _route(det_conf=0.80, ood_score=None, ml_floor=0.0) is False


def test_gate_never_reroutes_non_disease_classes():
    for cls in ("healthy", "not_a_plant"):
        assert _route(
            det_conf=0.0, ood_score=0.99, ood_threshold=0.5, class_name=cls,
        ) is False


# ── No-detection path: require_classifier_signal guard ───────────────
# On the no-detection path det_conf is a synthetic 0, so detector_low is always
# True. Without the guard the detector-only degradation would route EVERY
# no-detection input to NAP. These pin the safe behaviour.

def test_no_detection_keeps_verdict_when_no_classifier_signal():
    # det_conf=0 (no detection), no embedding bank, MaxLogit floor off. The
    # detection path degrades to detector-only (→ NAP), but the no-detection
    # path must NOT: with no classifier signal it keeps the verdict.
    assert _route(det_conf=0.0, ood_score=None, ml_floor=0.0) is True
    assert _route(
        det_conf=0.0, ood_score=None, ml_floor=0.0, require_classifier_signal=True,
    ) is False


def test_no_detection_reroutes_only_when_embedding_signal_fires():
    # Embedding OOD fires (far from manifold) → NAP even on the guarded path.
    assert _route(
        det_conf=0.0, ood_score=0.9, ood_threshold=0.5,
        require_classifier_signal=True,
    ) is True
    # In-distribution embedding (wide plant shot YOLO missed) → keep verdict.
    assert _route(
        det_conf=0.0, ood_score=0.1, ood_threshold=0.5,
        require_classifier_signal=True,
    ) is False


def test_no_detection_uses_maxlogit_floor_when_that_is_the_signal():
    # No bank but a positive MaxLogit floor counts as a real classifier signal.
    assert _route(
        det_conf=0.0, ood_score=None, ml_floor=4.0, max_logit=1.0,
        require_classifier_signal=True,
    ) is True
    assert _route(
        det_conf=0.0, ood_score=None, ml_floor=4.0, max_logit=9.0,
        require_classifier_signal=True,
    ) is False
