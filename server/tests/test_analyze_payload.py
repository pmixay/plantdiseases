"""``build_analyze_payload`` — the /api/analyze response shape, without a TestClient.

The handler keeps the I/O (inference, the PlantNet wait, minting the
scan_id, the cache); everything below is the pure assembly a frozen APK parses.
"""

from __future__ import annotations

import pytest

# The keys a shipped app parses. Adding one is fine for old clients; dropping
# or renaming one breaks them, so this list changes only on purpose.
RESPONSE_KEYS = {
    "class_name", "family", "requested_family", "family_fallback_reason",
    "disease_name", "disease_name_ru", "confidence",
    "description", "description_ru", "treatment", "treatment_ru",
    "prevention", "prevention_ru", "is_healthy", "detection", "severity",
    "all_probs", "top_k", "warnings", "abstained", "abstention_reason",
    "verdict_status", "prediction_set", "toxic_to_pets", "toxic_species_list",
    "toxicity_source", "species", "elapsed_ms", "server_version", "is_repeat",
    "scan_id",
}


@pytest.fixture
def build():
    import main as server_main

    def _build(result, **overrides):
        kwargs = {
            "scan_id": "scan-abc",
            "species_payload": None,
            "species_override": None,
            "served_family": "gamma",
            "requested_family": "gamma",
            "family_fallback_reason": None,
        }
        kwargs.update(overrides)
        return server_main.build_analyze_payload(result, **kwargs)

    return _build


def _result(class_name="leaf_spot", confidence=0.81234, **extra):
    base = {
        "class_name": class_name,
        "confidence": confidence,
        "all_probs": {class_name: confidence, "healthy": 1 - confidence},
        "top_k": [
            {"class": class_name, "confidence": confidence},
            {"class": "healthy", "confidence": 1 - confidence},
        ],
        "warnings": [],
        "severity": "medium",
        "prediction_set": None,
        "detection": {"is_diseased": True, "detector_confidence": 0.9, "regions": [],
                      "primary_region": None},
        "elapsed_ms": 123.0,
    }
    base.update(extra)
    return base


def _species(scientific="Ficus elastica"):
    return {
        "available": True, "source": "plantnet",
        "best": {"scientific_name": scientific, "common_name": "Rubber plant", "score": 0.9},
        "candidates": [],
    }


def test_accepted_payload_has_the_full_contract(build):
    import main as server_main

    payload = build(_result(), species_payload=_species())

    assert set(payload) == RESPONSE_KEYS
    assert payload["class_name"] == "leaf_spot"
    assert payload["confidence"] == 0.812
    assert payload["all_probs"]["leaf_spot"] == round(0.81234, 4)
    assert payload["verdict_status"] == "accepted"
    assert payload["abstained"] is False
    assert payload["is_healthy"] is False
    assert payload["scan_id"] == "scan-abc"
    assert payload["is_repeat"] is False
    assert payload["server_version"] == server_main.APP_VERSION
    info = server_main.DISEASES_DATABASE["leaf_spot"]
    assert payload["disease_name"] == info["name_en"]
    assert payload["treatment_ru"] == info["treatment_ru"]
    top = payload["top_k"][0]
    assert top["name_en"] == info["name_en"] and top["name_ru"] == info["name_ru"]
    assert payload["top_k"][1]["is_healthy"] is True


def test_healthy_flag_follows_the_displayed_class(build):
    assert build(_result("healthy"))["is_healthy"] is True


def test_toxicity_prefers_the_species_hint_over_plantnet(build, monkeypatch):
    import main as server_main

    seen: list[str] = []
    monkeypatch.setattr(
        server_main, "lookup_pet_toxicity",
        lambda sp: seen.append(sp) or {"toxic_to_pets": True, "species_list": ["cat"], "source": "db"},
    )
    hinted = build(_result(), species_payload=_species(), species_override="Monstera deliciosa")
    auto = build(_result(), species_payload=_species())
    unknown = build(_result())

    assert seen == ["Monstera deliciosa", "Ficus elastica"]
    assert hinted["toxic_to_pets"] is True and hinted["toxicity_source"] == "db"
    assert auto["toxic_species_list"] == ["cat"]
    assert unknown["toxicity_source"] == "unknown" and unknown["toxic_to_pets"] is False


def test_prediction_set_is_advisory_named_and_carries_dual_care(build):
    pset = {"classes": ["leaf_scorch_underwatering", "nutrient_deficiency"], "coverage": 0.9}
    payload = build(_result("leaf_scorch_underwatering", prediction_set=pset))

    out = payload["prediction_set"]
    assert out["advisory"] is True
    assert [i["class"] for i in out["items"]] == pset["classes"]
    assert all(i["name_en"] and i["name_ru"] for i in out["items"])
    # The one physiological pair whose care diverges gets both care cards.
    assert out["dual_care"] is True
    assert len(out["care_cards"]) == 2


def test_not_a_plant_suppresses_species_toxicity_and_the_set(build):
    pset = {"classes": ["not_a_plant", "healthy"]}
    payload = build(
        _result("not_a_plant", prediction_set=pset),
        species_payload=_species(), species_override="Ficus elastica",
    )

    assert payload["abstained"] is True
    assert payload["verdict_status"] == "abstained"
    assert payload["abstention_reason"] == "not_a_plant"
    assert payload["species"]["source"] == "skipped_nap"
    assert payload["species"]["best"] is None
    assert payload["toxicity_source"] == "unknown"
    assert payload["prediction_set"] is None
    assert payload["is_healthy"] is False
    # The raw classifier diagnostics stay.
    assert payload["class_name"] == "not_a_plant"


def test_ood_gate_abstains_with_its_own_reason(build):
    payload = build(_result("healthy", warnings=["nap_ood_gated"]))

    assert payload["abstained"] is True
    assert payload["abstention_reason"] != "not_a_plant"
    assert payload["species"]["source"] == "skipped_abstained"
    assert payload["detection"]["is_diseased"] is False
    assert payload["severity"] == "low"
