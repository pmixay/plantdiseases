"""Coverage for pet-toxicity surface on /api/analyze and /api/care-plan."""

from __future__ import annotations


def test_toxicity_lookup_known_toxic_species():
    from diseases_data import lookup_pet_toxicity

    out = lookup_pet_toxicity("Monstera deliciosa")
    assert out["toxic_to_pets"] is True
    assert "cats" in out["species_list"] and "dogs" in out["species_list"]
    assert out["source"] == "db"


def test_toxicity_lookup_returns_independent_species_list():
    """Regression: ``lookup_pet_toxicity`` used to ``{**entry, ...}`` the
    matched row, which is a shallow copy — the ``species_list`` was
    still aliased to the module-level ``PET_TOXICITY_DATABASE`` entry.
    A caller mutating the returned list (a UI helper appending
    "humans", or a JSON serializer doing list_in_place tricks) would
    corrupt the global for every subsequent request.
    """
    from diseases_data import lookup_pet_toxicity

    first = lookup_pet_toxicity("Monstera deliciosa")
    assert "cats" in first["species_list"]
    first["species_list"].append("__poisoned__")

    # A fresh lookup must NOT see the poisoned entry.
    second = lookup_pet_toxicity("Monstera deliciosa")
    assert "__poisoned__" not in second["species_list"], (
        "lookup_pet_toxicity is leaking the global PET_TOXICITY_DATABASE row"
    )


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


def test_analyze_cache_hit_re_resolves_toxicity_for_override(client, png_bytes):
    """Regression: the same-photo dedup cache keys on image hash only,
    so a second /api/analyze on the same bytes with a different
    ``?species=`` override used to silently keep the first call's
    toxicity. The cache-hit path now re-resolves toxicity against the
    fresh override and patches just those three fields, leaving the
    rest of the cached payload (verdict, detection, species block)
    untouched.
    """
    # First call: toxic species → cache populated with toxic_to_pets=True.
    r1 = client.post(
        "/api/analyze?species=Monstera%20deliciosa",
        files={"image": ("leaf.png", png_bytes, "image/png")},
    )
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    assert body1["is_repeat"] is False
    assert body1["toxic_to_pets"] is True

    # Second call: same bytes, safe species override → must reflect the
    # override AND surface ``is_repeat=true`` to confirm the cache fired.
    r2 = client.post(
        "/api/analyze?species=Calathea%20ornata",
        files={"image": ("leaf.png", png_bytes, "image/png")},
    )
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert body2["is_repeat"] is True
    assert body2["toxic_to_pets"] is False
    assert body2["toxic_species_list"] == []
    assert body2["toxicity_source"] == "db"
    # Verdict & detection MUST be byte-identical — the species override
    # is a toxicity hint only, it must not change the diagnosis.
    assert body2["class_name"] == body1["class_name"]
    assert body2["confidence"] == body1["confidence"]
    assert body2["detection"] == body1["detection"]


def test_analyze_cache_hit_without_override_preserves_cached_toxicity(client, png_bytes):
    """The cache-hit re-resolve path must ONLY fire when the override
    is supplied — otherwise it stomps the cached toxicity with
    'unknown'. A second call with no ``?species=`` should replay the
    first call's toxicity verbatim.
    """
    r1 = client.post(
        "/api/analyze?species=Dieffenbachia",
        files={"image": ("leaf.png", png_bytes, "image/png")},
    )
    body1 = r1.json()
    assert body1["toxic_to_pets"] is True

    r2 = client.post(
        "/api/analyze",
        files={"image": ("leaf.png", png_bytes, "image/png")},
    )
    body2 = r2.json()
    assert body2["is_repeat"] is True
    assert body2["toxic_to_pets"] is True  # NOT overwritten to unknown
    assert body2["toxicity_source"] == "db"


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
