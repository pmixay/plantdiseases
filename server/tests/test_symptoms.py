"""Symptom-triage scorer + /api/symptom-diagnose endpoint."""
from __future__ import annotations

import symptoms


# ── Pure scorer ─────────────────────────────────────────────────────


def test_spots_rank_leaf_spot_first():
    ranked = symptoms.rank_symptoms({"symptom": "spots", "pests": "none"})
    assert ranked[0][0] == "leaf_spot"


def test_powder_ranks_powdery_mildew_first():
    ranked = symptoms.rank_symptoms({"symptom": "powder"})
    assert ranked[0][0] == "powdery_mildew"


def test_white_cotton_and_cottony_rank_mealybug_first():
    ranked = symptoms.rank_symptoms({"symptom": "cottony", "pests": "white_cotton"})
    assert ranked[0][0] == "mealybug"


def test_brown_domes_rank_scale_first():
    ranked = symptoms.rank_symptoms(
        {"part": "stems", "symptom": "sticky", "pests": "brown_domes"}
    )
    assert ranked[0][0] == "scale"


def test_window_side_dry_edges_surface_sunburn():
    ranked = symptoms.rank_symptoms(
        {"symptom": "spots", "pattern": "window_side", "moisture": "underwatered"}
    )
    classes = [c for c, _ in ranked]
    assert "sunburn_or_scorch" in classes


def test_moving_dots_rank_pests_first():
    ranked = symptoms.rank_symptoms({"symptom": "webbing", "pests": "moving_dots"})
    assert ranked[0][0] == "pests"


def test_unknown_ids_are_ignored():
    ranked = symptoms.rank_symptoms({"bogus_q": "bogus_o", "symptom": "deformed"})
    assert ranked[0][0] == "viral_disease"


def test_empty_answers_score_nothing():
    assert symptoms.rank_symptoms({}) == []


def test_ranking_is_deterministic_with_tie_break():
    # Two runs of the same ambiguous answer set produce identical ordering.
    answers = {"symptom": "yellowing"}
    assert symptoms.rank_symptoms(answers) == symptoms.rank_symptoms(answers)


def test_diagnose_flags_low_confidence_for_sparse_answers():
    out = symptoms.diagnose({"part": "leaves"})
    assert out["low_confidence"] is True
    assert out["answered"] == 1


def test_diagnose_confident_for_strong_signal():
    out = symptoms.diagnose(
        {"symptom": "powder", "part": "leaves", "pattern": "whole_leaf"}
    )
    assert out["low_confidence"] is False
    assert out["results"][0]["class"] == "powdery_mildew"
    assert out["results"][0]["rank"] == 1
    assert 0.0 < out["results"][0]["confidence"] <= 1.0


def test_diagnose_caps_top_k():
    out = symptoms.diagnose(
        {"symptom": "yellowing", "pests": "none", "pattern": "lower_leaves"},
        top_k=2,
    )
    assert len(out["results"]) <= 2


# ── HTTP endpoint ───────────────────────────────────────────────────


def test_endpoint_returns_ranked_care_cards(client):
    r = client.post(
        "/api/symptom-diagnose",
        json={"answers": {"symptom": "powder", "part": "leaves"}, "locale": "en"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["results"], "expected at least one result"
    top = body["results"][0]
    # Each result carries the full bilingual care card + scoring meta.
    for key in ("class", "name_en", "name_ru", "description", "treatment", "score", "rank"):
        assert key in top
    assert top["class"] == "powdery_mildew"
    assert body["low_confidence"] is False


def test_endpoint_empty_answers_yield_unknown_card_low_confidence(client):
    r = client.post("/api/symptom-diagnose", json={"answers": {}})
    assert r.status_code == 200
    body = r.json()
    assert body["low_confidence"] is True
    assert body["results"][0]["class"] == "unknown"


def test_endpoint_rejects_oversized_payload(client):
    too_many = {f"q{i}": "x" for i in range(25)}
    r = client.post("/api/symptom-diagnose", json={"answers": too_many})
    assert r.status_code == 422
