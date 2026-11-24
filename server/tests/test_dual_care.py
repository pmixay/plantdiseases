"""Dual care-card surfacing for the physiological-stress split (P0(b)).

When the kept-both taxonomy leaves the conformal set unable to separate
under-watering (``leaf_scorch_underwatering``) from ``nutrient_deficiency``,
the response must carry BOTH full care cards so the user isn't forced to
guess "water me" vs "feed me". Every other set stays lean.
"""

from __future__ import annotations


def test_attach_dual_care_fires_on_physiological_pair():
    import analysis_payload
    ps = {"classes": ["leaf_scorch_underwatering", "nutrient_deficiency"]}
    analysis_payload.attach_dual_care(ps)
    assert ps.get("dual_care") is True
    cards = ps.get("care_cards")
    assert cards is not None and len(cards) == 2
    classes = {c["class"] for c in cards}
    assert classes == {"leaf_scorch_underwatering", "nutrient_deficiency"}
    # Each card must carry actionable bilingual care, not just a name.
    for card in cards:
        assert card["name_en"] and card["name_ru"]
        assert card["treatment"] and card["treatment_ru"]
        assert card["prevention"] and card["prevention_ru"]
        assert card["description"] and card["description_ru"]


def test_attach_dual_care_preserves_set_order():
    import analysis_payload
    ps = {"classes": ["nutrient_deficiency", "leaf_scorch_underwatering"]}
    analysis_payload.attach_dual_care(ps)
    assert [c["class"] for c in ps["care_cards"]] == [
        "nutrient_deficiency", "leaf_scorch_underwatering",
    ]


def test_attach_dual_care_noop_when_only_one_member_present():
    # A set with just one of the pair (the common case) must NOT add cards.
    import analysis_payload
    for classes in (
        ["leaf_scorch_underwatering"],
        ["nutrient_deficiency", "leaf_spot"],
        ["leaf_spot", "viral_disease"],
    ):
        ps = {"classes": classes}
        analysis_payload.attach_dual_care(ps)
        assert "dual_care" not in ps
        assert "care_cards" not in ps


def test_attach_dual_care_noop_on_empty_or_missing_classes():
    import analysis_payload
    for ps in ({}, {"classes": []}, {"classes": None}):
        analysis_payload.attach_dual_care(ps)
        assert "care_cards" not in ps


def test_compact_care_card_falls_back_for_unknown_class():
    import analysis_payload
    card = analysis_payload.compact_care_card("totally_unknown_class")
    # Unknown class resolves to the 'unknown' entry but still produces a
    # well-formed card (no KeyError, names present).
    assert card["class"] == "totally_unknown_class"
    assert isinstance(card["treatment"], list)
    assert card["name_en"]
