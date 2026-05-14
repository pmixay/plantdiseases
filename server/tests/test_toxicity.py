"""Coverage for pet-toxicity surface on /api/analyze and /api/care-plan."""

from __future__ import annotations


def test_toxicity_lookup_known_toxic_species():
    from diseases_data import lookup_pet_toxicity

    out = lookup_pet_toxicity("Monstera deliciosa")
    assert out["toxic_to_pets"] is True
    assert "cats" in out["species_list"] and "dogs" in out["species_list"]
    assert out["source"] == "db"


def test_toxicity_lookup_known_safe_species():
    from diseases_data import lookup_pet_toxicity

    out = lookup_pet_toxicity("Calathea ornata")
    assert out["toxic_to_pets"] is False
    assert out["species_list"] == []
    assert out["source"] == "db"


def test_toxicity_lookup_genus_fallback():
    """A species PlantNet returns that isn't directly indexed should
    still resolve via its genus when the genus is uniformly toxic /
    safe."""
    from diseases_data import lookup_pet_toxicity

    # "Ficus elastica" isn't an explicit key but "ficus" is in the DB.
    out = lookup_pet_toxicity("Ficus elastica")
    assert out["source"] == "db"
    assert out["toxic_to_pets"] is True


def test_toxicity_lookup_unknown_species():
    from diseases_data import lookup_pet_toxicity

    out = lookup_pet_toxicity("Some Made-Up Plant XYZ")
    # We never falsely reassure: unknown plants come back source="unknown".
    assert out["source"] == "unknown"
    assert out["toxic_to_pets"] is False


def test_analyze_response_includes_toxicity_when_species_passed(client, png_bytes):
    r = client.post(
        "/api/analyze?species=Dieffenbachia",
        files={"image": ("leaf.png", png_bytes, "image/png")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "toxic_to_pets" in body
    assert "toxic_species_list" in body
    assert "toxicity_source" in body
    assert body["toxicity_source"] == "db"
    assert body["toxic_to_pets"] is True


def test_analyze_response_toxicity_unknown_without_species(client, png_bytes):
    r = client.post(
        "/api/analyze",
        files={"image": ("leaf.png", png_bytes, "image/png")},
    )
    body = r.json()
    assert body["toxicity_source"] == "unknown"
    assert body["toxic_to_pets"] is False
    assert body["toxic_species_list"] == []


def test_care_plan_response_includes_toxicity(client):
    r = client.post(
        "/api/care-plan",
        json={"disease": "powdery_mildew", "species": "Monstera deliciosa"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["toxic_to_pets"] is True
    assert "cats" in body["toxic_species_list"]
    assert body["toxicity_source"] == "db"
