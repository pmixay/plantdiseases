"""Symptom-triage scorer + /api/symptom-diagnose endpoint."""
from __future__ import annotations

import pytest

import symptoms

# ── Pure scorer ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("answers", "top_class"),
    [
        ({"symptom": "spots", "pests": "none"}, "leaf_spot"),
        ({"symptom": "powder"}, "powdery_mildew"),
        ({"symptom": "cottony", "pests": "white_cotton"}, "mealybug"),
        ({"part": "stems", "symptom": "sticky", "pests": "brown_domes"}, "scale"),
        ({"symptom": "webbing", "pests": "moving_dots"}, "pests"),
        # Unknown question/option ids are ignored, not an error.
        ({"bogus_q": "bogus_o", "symptom": "deformed"}, "viral_disease"),
    ],
)
def test_rank_symptoms_top_class(answers, top_class):
    assert symptoms.rank_symptoms(answers)[0][0] == top_class


def test_window_side_dry_edges_surface_sunburn():
    ranked = symptoms.rank_symptoms(
        {"symptom": "spots", "pattern": "window_side", "moisture": "underwatered"}
    )
    classes = [c for c, _ in ranked]
    assert "sunburn_or_scorch" in classes


def test_empty_answers_score_nothing():
    assert symptoms.rank_symptoms({}) == []


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
