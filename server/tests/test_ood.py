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
    EmbeddingOODLoadError,
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


def test_score_for_45_degree_vector():
    scorer = EmbeddingOODScorer(_basis_bank(), _cfg(threshold=0.5, k=1))
    # A 45° vector between two axes: nearest cos = cos(45°) ≈ 0.707 → score ≈ 0.293
    half = np.array([1.0, 1.0, 0, 0], dtype=np.float32)
    assert scorer.score(half) == pytest.approx(1 - (1 / np.sqrt(2)), abs=1e-6)
    # A vector mostly opposite the basis scores far from the manifold.
    assert scorer.score(np.array([-1.0, -1.0, 0, 0], dtype=np.float32)) > 0.5


def test_k_larger_than_bank_is_rejected_not_recalibrated_by_clamping():
    with pytest.raises(ValueError, match="exceeds bank row count"):
        EmbeddingOODScorer(_basis_bank(), _cfg(k=99))


def test_dim_mismatch_returns_nan():
    scorer = EmbeddingOODScorer(_basis_bank(), _cfg(embed_dim=4))
    s = scorer.score(np.array([1.0, 0, 0], dtype=np.float32))  # 3-dim query
    # NaN, never a number: the pipeline's finite-check is what routes this
    # to the MaxLogit fallback rather than to a bogus OOD verdict.
    assert np.isnan(s)


def test_nonfinite_or_zero_query_skips_ood_score():
    scorer = EmbeddingOODScorer(_basis_bank(), _cfg(embed_dim=4))
    assert np.isnan(scorer.score(np.zeros(4, dtype=np.float32)))
    assert np.isnan(scorer.score(np.array([1.0, np.nan, 0, 0], dtype=np.float32)))


def test_invalid_bank_or_contract_is_rejected():
    with pytest.raises(ValueError, match="NaN"):
        EmbeddingOODScorer(
            np.array([[1.0, np.nan, 0, 0]], dtype=np.float32), _cfg(),
        )
    with pytest.raises(ValueError, match="zero-length"):
        EmbeddingOODScorer(np.zeros((1, 4), dtype=np.float32), _cfg())
    bad_metric = EmbeddingOODConfig(
        method="knn", metric="euclidean", k=1, threshold=0.5, embed_dim=4,
    )
    with pytest.raises(ValueError, match="metric"):
        EmbeddingOODScorer(_basis_bank(), bad_metric)


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
    # Thresholds are calibrated for one metric; neither path-like bank names
    # nor incompatible metrics may silently reach the cosine scorer.
    assert parse_embedding_ood_config({
        "ood": {"embedding": {
            "method": "knn", "metric": "euclidean", "k": 1,
            "threshold": 0.4, "embed_dim": 4,
        }}
    }) is None
    assert parse_embedding_ood_config({
        "ood": {"embedding": {
            "method": "knn", "metric": "cosine", "k": 0,
            "threshold": 0.4, "embed_dim": 4,
        }}
    }) is None
    assert parse_embedding_ood_config({
        "ood": {"embedding": {
            "method": "knn", "metric": "cosine", "k": "5",
            "threshold": 0.4, "embed_dim": 4,
        }}
    }) is None
    assert parse_embedding_ood_config({
        "ood": {"embedding": {
            "method": "knn", "metric": "cosine", "k": 1,
            "threshold": float("nan"), "embed_dim": 4,
        }}
    }) is None
    assert parse_embedding_ood_config({
        "ood": {"embedding": {
            "method": "knn", "metric": "cosine", "k": 1,
            "threshold": 0.4, "embed_dim": 4, "bank_file": "../bank.npz",
        }}
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
    assert scorer.score(np.array([1.0, 0, 0, 0], dtype=np.float32)) == pytest.approx(0.0)


def test_load_embedding_ood_missing_declared_bank_fails_readiness(tmp_path):
    meta = {"ood": {"embedding": {
        "method": "knn", "threshold": 0.5, "embed_dim": 4, "bank_file": "absent.npz",
    }}}
    with pytest.raises(EmbeddingOODLoadError, match="missing"):
        load_embedding_ood(tmp_path, meta)


def test_load_embedding_ood_dim_mismatch_fails_readiness(tmp_path):
    np.savez(tmp_path / "ood_embedding.npz", bank=np.eye(8, dtype=np.float32))
    meta = {"ood": {"embedding": {
        "method": "knn", "threshold": 0.5, "embed_dim": 4, "bank_file": "ood_embedding.npz",
    }}}
    with pytest.raises(EmbeddingOODLoadError, match="embed_dim"):
        load_embedding_ood(tmp_path, meta)


def test_load_embedding_ood_missing_key_or_nonfinite_bank_fails_readiness(tmp_path):
    meta = {"ood": {"embedding": {
        "method": "knn", "metric": "cosine", "k": 1,
        "threshold": 0.5, "embed_dim": 4, "bank_file": "ood_embedding.npz",
    }}}
    np.savez(tmp_path / "ood_embedding.npz", wrong=np.eye(4, dtype=np.float32))
    with pytest.raises(EmbeddingOODLoadError, match="no 'bank'"):
        load_embedding_ood(tmp_path, meta)
    np.savez(
        tmp_path / "ood_embedding.npz",
        bank=np.array([[1.0, np.inf, 0, 0]], dtype=np.float32),
    )
    with pytest.raises(EmbeddingOODLoadError, match="infinite"):
        load_embedding_ood(tmp_path, meta)


def test_load_embedding_ood_absent_is_optional_but_declared_malformed_fails(tmp_path):
    assert load_embedding_ood(tmp_path, {}) is None
    assert load_embedding_ood(tmp_path, {"ood": {}}) is None
    # Notebook MaxLogit-only bundles emit explicit JSON null, not a missing key.
    assert load_embedding_ood(tmp_path, {"ood": {"embedding": None}}) is None
    with pytest.raises(EmbeddingOODLoadError, match="metadata is invalid"):
        load_embedding_ood(tmp_path, {"ood": {"embedding": {"threshold": 0.5}}})
    with pytest.raises(EmbeddingOODLoadError, match="metadata is invalid"):
        load_embedding_ood(tmp_path, {"ood": {"embedding": "knn"}})


# ── NAP-gate decision (pipeline._decide_nap_route) ───────────────────

_CLASS_NAMES = [
    "gray_mold_botrytis", "healthy", "leaf_scorch_underwatering",
    "leaf_spot", "not_a_plant", "nutrient_deficiency", "pests",
    "powdery_mildew", "viral_disease",
]


def _route(*, det_conf, ood_score=None, ood_threshold=None,
           ml_floor=0.0, max_logit=0.0, class_name="leaf_spot",
           head_nap=None, head_mass_floor=0.0):
    from pipeline import _decide_nap_route

    triggered, _ = _decide_nap_route(
        class_name=class_name, class_names=_CLASS_NAMES, det_conf=det_conf,
        ood_score=ood_score, ood_threshold=ood_threshold,
        ml_floor=ml_floor, max_logit=max_logit,
        head_nap=head_nap, head_mass_floor=head_mass_floor,
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


def test_gate_never_degrades_to_detector_only_without_classifier_signal():
    # Detector confidence is localisation evidence, not an OOD classifier.
    assert _route(det_conf=0.20, ood_score=None, ml_floor=0.0) is False
    assert _route(det_conf=0.80, ood_score=None, ml_floor=0.0) is False


# ── Head veto (third condition) ──────────────────────────────────────
# A distance/logit heuristic can mean “unfamiliar plant,” which is not the same
# as “not a plant.” The trained NAP head therefore has veto authority whenever
# a fully calibrated secondary overlay is enabled.

def test_head_veto_blocks_gate_when_classifier_is_sure_it_is_a_plant():
    # Detector unsure + embedding says OOD → the two-of-two gate would fire…
    assert _route(det_conf=0.20, ood_score=0.9, ood_threshold=0.5) is True
    # …but a near-zero not_a_plant head vetoes it.
    assert _route(
        det_conf=0.20, ood_score=0.9, ood_threshold=0.5,
        head_nap=0.002, head_mass_floor=0.05,
    ) is False
    # A real not_a_plant frame clears the floor and still routes.
    assert _route(
        det_conf=0.20, ood_score=0.9, ood_threshold=0.5,
        head_nap=0.98, head_mass_floor=0.05,
    ) is True


def test_head_veto_needs_both_a_probability_and_a_positive_floor():
    # NB: the veto is ON by default for a bundle (see the resolver test below);
    # what is optional is the *pure function's* arguments. Callers that pass
    # neither get the historical two-of-two behaviour unchanged.
    assert _route(det_conf=0.20, ood_score=0.9, ood_threshold=0.5,
                  head_mass_floor=0.05) is True
    # Floor explicitly disabled (bundle sets ood.nap_head_mass_floor: null).
    assert _route(det_conf=0.20, ood_score=0.9, ood_threshold=0.5,
                  head_nap=0.0, head_mass_floor=0.0) is True


def test_pipeline_passes_the_head_probability_into_the_gate():
    """The plumbing, not the decision. Every other test here calls
    ``_decide_nap_route`` directly, so dropping ``head_nap=`` from
    ``_should_route_to_nap`` would leave the veto permanently inert and the
    whole suite green — a silent revert of the probability-preserving policy."""
    from unittest.mock import patch

    import pipeline as P

    # conftest swaps ``pipeline.PlantDiseasePipeline`` for FakePipeline before
    # the FastAPI lifespan runs and stashes the real class here.
    Real = P._RealPipelineForTests

    class _Stub:
        CLASS_NAMES = _CLASS_NAMES

    pipe = Real.__new__(Real)
    pipe.classifier = _Stub()
    pipe._secondary_ood_gate_enabled = True
    pipe.embedding_ood = None
    pipe.nap_max_logit_floor = 4.0
    pipe.nap_max_logit_scope = "plant_only"
    pipe._max_logit_key = "plant_max_logit"
    pipe.nap_head_mass_floor = 0.05

    classification = {
        "class_name": "leaf_scorch_underwatering",
        "all_probs": {c: 0.0 for c in _CLASS_NAMES} | {
            "leaf_scorch_underwatering": 0.988, "not_a_plant": 0.0018},
        "max_logit": 1.0, "plant_max_logit": 1.0,
    }
    with patch.object(P, "_decide_nap_route", wraps=P._decide_nap_route) as spy:
        fired = pipe._should_route_to_nap(classification, det_conf=0.0)
    kwargs = spy.call_args.kwargs
    assert kwargs["head_nap"] == 0.0018, "the head's own NAP mass never reached the gate"
    assert kwargs["head_mass_floor"] == 0.05
    # And end-to-end through the real decision: detector says 0, MaxLogit says
    # OOD, but the head is 98.8% sure it is scorch — so the gate must not fire.
    assert fired is False


def test_head_veto_also_applies_on_the_no_detection_path():
    # The no-detection path is where the veto matters most: det_conf is a
    # synthetic 0, so the whole decision rests on the classifier side.
    assert _route(
        det_conf=0.0, ood_score=0.9, ood_threshold=0.5,
        head_nap=0.002, head_mass_floor=0.05,
    ) is False


def test_head_mass_floor_resolver_contract():
    from pipeline import NAP_HEAD_MASS_FLOOR_DEFAULT, _resolve_head_mass_floor

    # Default is ON — bundles exported before the key existed still get it.
    assert _resolve_head_mass_floor({}) == NAP_HEAD_MASS_FLOOR_DEFAULT
    assert _resolve_head_mass_floor({"ood": {}}) == NAP_HEAD_MASS_FLOOR_DEFAULT
    # Explicit null restores the historical two-of-two gate.
    assert _resolve_head_mass_floor({"ood": {"nap_head_mass_floor": None}}) == 0.0
    assert _resolve_head_mass_floor({"ood": {"nap_head_mass_floor": 0.2}}) == 0.2
    # Garbage falls back rather than taking the boot down; range is clamped.
    assert _resolve_head_mass_floor(
        {"ood": {"nap_head_mass_floor": "x"}}
    ) == NAP_HEAD_MASS_FLOOR_DEFAULT
    assert _resolve_head_mass_floor({"ood": {"nap_head_mass_floor": 5}}) == 1.0
    assert _resolve_head_mass_floor({"ood": {"nap_head_mass_floor": -1}}) == 0.0


def test_gate_checks_healthy_but_bypasses_natural_nap_verdict():
    # Healthy is still a claim that the frame contains recognisable plant
    # content; otherwise-valid OOD evidence must be allowed to reject sharp
    # junk that the head happened to map to healthy.
    assert _route(
        det_conf=0.0, ood_score=0.99, ood_threshold=0.5,
        class_name="healthy",
    ) is True
    # A trained-head not_a_plant argmax is already an honest rejection and does
    # not need a second gate verdict.
    assert _route(
        det_conf=0.0, ood_score=0.99, ood_threshold=0.5,
        class_name="not_a_plant",
    ) is False


# ── No classifier signal: every path keeps the classifier verdict ───────────

def test_no_detection_keeps_verdict_when_no_classifier_signal():
    # det_conf=0 (no detection), no embedding bank, MaxLogit floor off. The
    # Both detection and no-detection paths require real classifier evidence.
    assert _route(det_conf=0.0, ood_score=None, ml_floor=0.0) is False


def test_no_detection_reroutes_only_when_embedding_signal_fires():
    # Embedding OOD fires (far from manifold) → NAP even on the guarded path.
    assert _route(
        det_conf=0.0, ood_score=0.9, ood_threshold=0.5,
    ) is True
    # In-distribution embedding (wide plant shot YOLO missed) → keep verdict.
    assert _route(
        det_conf=0.0, ood_score=0.1, ood_threshold=0.5,
    ) is False


def test_no_detection_uses_maxlogit_floor_when_that_is_the_signal():
    # No bank but a positive MaxLogit floor counts as a real classifier signal.
    assert _route(
        det_conf=0.0, ood_score=None, ml_floor=4.0, max_logit=1.0,
    ) is True
    assert _route(
        det_conf=0.0, ood_score=None, ml_floor=4.0, max_logit=9.0,
    ) is False
