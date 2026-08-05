"""Rule-based symptom triage — the no-photo diagnosis fallback.

When the camera/ML pipeline can't get a confident read (a blurry shot, an
unusual angle, ``not_a_plant`` / ``unknown``), the client offers a short
guided questionnaire instead. This module turns those answers into a ranked
shortlist of the most likely conditions, scored against a transparent weight
table — no model, no network, fully explainable.

The question and option IDs below are a STABLE CONTRACT with the Android
client (which ships them in the frozen APK). New weights can be tuned here
without an app update, but the IDs must not change or be removed. Unknown
question/option IDs are ignored, so an older client and a newer server stay
compatible.
"""
from __future__ import annotations

# question_id -> option_id -> {disease_class: weight}
SYMPTOM_WEIGHTS: dict[str, dict[str, dict[str, float]]] = {
    "part": {
        "leaves": {},  # neutral — most conditions show on leaves
        "stems": {"scale": 1.5, "mealybug": 1.0, "powdery_mildew": 0.5},
        "whole": {
            "leaf_scorch_underwatering": 1.0,
            "viral_disease": 1.0,
            "nutrient_deficiency": 0.5,
        },
        "flowers": {"gray_mold_botrytis": 2.5},
        "soil": {"edema": 1.0, "leaf_scorch_underwatering": 0.5},
    },
    "symptom": {
        "spots": {"leaf_spot": 3.0, "sunburn_or_scorch": 1.0},
        "yellowing": {
            "nutrient_deficiency": 3.0,
            "pests": 1.0,
            "viral_disease": 1.0,
            "leaf_scorch_underwatering": 1.0,
        },
        "wilting": {"leaf_scorch_underwatering": 2.0, "edema": 1.0},
        "powder": {"powdery_mildew": 3.0, "gray_mold_botrytis": 2.0},
        "webbing": {"pests": 3.0},
        "sticky": {"scale": 3.0, "mealybug": 2.0, "pests": 1.0},
        "dry_edges": {
            "leaf_scorch_underwatering": 3.0,
            "nutrient_deficiency": 1.0,
            "sunburn_or_scorch": 1.0,
        },
        "cottony": {"mealybug": 3.0, "powdery_mildew": 1.0},
        "deformed": {"viral_disease": 3.0},
        "holes": {"pests": 2.0},
    },
    "pattern": {
        "tips_edges": {"leaf_scorch_underwatering": 2.0, "nutrient_deficiency": 1.0},
        "whole_leaf": {"nutrient_deficiency": 1.0, "viral_disease": 1.0},
        "underside": {"edema": 2.0, "pests": 1.0, "scale": 1.0},
        "new_growth": {
            "gray_mold_botrytis": 1.0,
            "mealybug": 1.0,
            "viral_disease": 1.0,
        },
        "lower_leaves": {"nutrient_deficiency": 2.0},
        "window_side": {"sunburn_or_scorch": 3.0},
    },
    "moisture": {
        "overwatered": {"edema": 2.0, "gray_mold_botrytis": 1.0, "leaf_spot": 1.0},
        "underwatered": {"leaf_scorch_underwatering": 2.0, "sunburn_or_scorch": 1.0},
        "normal": {},
    },
    "pests": {
        "moving_dots": {"pests": 3.0},
        "white_cotton": {"mealybug": 3.0},
        "brown_domes": {"scale": 3.0},
        "flying": {"pests": 2.0},
        "none": {
            "powdery_mildew": 0.5,
            "leaf_spot": 0.5,
            "nutrient_deficiency": 0.5,
            "sunburn_or_scorch": 0.5,
            "leaf_scorch_underwatering": 0.5,
        },
    },
}


def rank_symptoms(answers: dict[str, str]) -> list[tuple[str, float]]:
    """Score the disease classes against the supplied answers.

    ``answers`` maps question_id -> option_id. Unknown question or option
    IDs are skipped (forward-compatibility). Returns ``(class, score)``
    pairs with score > 0, sorted by score descending then class name (a
    stable tie-break so the ordering is deterministic for tests).
    """
    scores: dict[str, float] = {}
    for question_id, option_id in (answers or {}).items():
        question = SYMPTOM_WEIGHTS.get(question_id)
        if not question:
            continue
        option = question.get(option_id)
        if not option:
            continue
        for disease, weight in option.items():
            scores[disease] = scores.get(disease, 0.0) + weight
    ranked = [(cls, round(score, 3)) for cls, score in scores.items() if score > 0]
    ranked.sort(key=lambda item: (-item[1], item[0]))
    return ranked


def diagnose(answers: dict[str, str], top_k: int = 3) -> dict:
    """Return a ranked shortlist + a confidence read for the client.

    The payload carries only the scoring/meta; the HTTP endpoint enriches
    each result with the bilingual care card so the care knowledge stays in
    one place (``diseases_data``). ``low_confidence`` is the honest-uncertainty
    signal: too few answers or a weak top score means the client should nudge
    the user to take a photo for a more reliable result.
    """
    answered = sum(1 for v in (answers or {}).values() if v)
    ranked = rank_symptoms(answers)[:top_k]
    top_score = ranked[0][1] if ranked else 0.0
    total = sum(score for _, score in ranked) or 1.0

    results = []
    for rank, (cls, score) in enumerate(ranked, start=1):
        results.append(
            {
                "class": cls,
                "score": score,
                # Share of the shortlist's total weight — a rough, honest
                # "how much of the signal points here" rather than a
                # calibrated probability.
                "confidence": round(score / total, 3),
                "rank": rank,
            }
        )

    low_confidence = answered < 2 or top_score < 3.0
    return {
        "results": results,
        "answered": answered,
        "low_confidence": low_confidence,
    }
