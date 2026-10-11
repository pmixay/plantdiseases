"""Tests for the AI species-profile helper + endpoint.

``species_profile`` asks the chat model for a species' flowering pattern
(a 12-month bloom-intensity calendar + bloom triggers) and native range
(per-country grows-more/grows-less zones + a summary), returning a small
sanitised object. It must degrade gracefully (``available=False``) with no
API key or on any failure so the client just hides the section. The
OpenRouter call is fully mocked — no network.
"""
from __future__ import annotations

import json

import pytest

import ai_services


@pytest.fixture(autouse=True)
def _clear_cache(tmp_path, monkeypatch):
    ai_services._SPECIES_PROFILE_CACHE._d.clear()
    # Isolate the persistent disk layer per test (point it at a tmp dir) so a
    # real result from one test never satisfies another and the repo's
    # ``server/ai_cache/`` stays clean.
    monkeypatch.setattr(
        ai_services._SPECIES_PROFILE_DISK, "dir", tmp_path / "species_profile",
    )
    yield
    ai_services._SPECIES_PROFILE_CACHE._d.clear()


def _valid_profile_json(**overrides) -> str:
    payload = {
        "flowering": {
            "monthly_intensity": [0, 0, 1, 2, 2, 1, 0, 0, 0, 0, 0, 0],
            "indoor_note": "Blooms less reliably indoors without a bright winter rest.",
        },
        "flowering_conditions": "Needs a cool, dry winter dormancy to set buds.",
        "native_range": {
            "zones": [
                {"country": "Brazil", "status": "grows_more"},
                {"country": "Mexico", "status": "grows_less"},
            ],
            "summary": "Native to the understory of Brazilian rainforests.",
        },
    }
    payload.update(overrides)
    return json.dumps(payload)


def _valid_profile_v2_json() -> str:
    return json.dumps({
        "flowering": {
            "monthly_intensity": [0, 0, 1, 2, 2, 1, 0, 0, 0, 0, 0, 0],
            "indoor_note": {
                "en": "Blooms less reliably indoors.",
                "ru": "В помещении цветёт менее стабильно.",
            },
        },
        "flowering_conditions": {
            "en": "Long days and warm temperatures help it bloom.",
            "ru": "Для цветения нужны длинный день и тепло.",
        },
        "native_range": {
            "zones": [
                {"country_code": "PER", "country": {"en": "Peru", "ru": "Перу"}, "status": "grows_more"},
                {"country_code": "BOL", "country": {"en": "Bolivia", "ru": "Боливия"}, "status": "grows_more"},
                {"country_code": "BOL", "country": {"en": "Bolivia", "ru": "Боливия"}, "status": "grows_less"},
            ],
            "summary": {
                "en": "Native to the Andes.",
                "ru": "Родом из Анд.",
            },
        },
    })


# ── schema ↔ parser contract ────────────────────────────────────────
#
# The shape now lives in _SPECIES_PROFILE_SCHEMA (sent to the provider as a
# strict JSON schema) while the flowering calendar and native range are read
# back out by _parse_species_profile_v2. Those two must describe the same
# object: a schema that drifts from the parser produces replies that validate
# upstream and are then thrown away here as malformed, which surfaces as the
# whole section silently vanishing from the species screen.

def _assert_conforms(instance, schema, path="$"):
    """Validate against the JSON Schema subset we actually send (strict mode:
    type / properties / required / additionalProperties=false / enum / items)."""
    types = schema.get("type")
    types = [types] if isinstance(types, str) else list(types or [])
    checks = {
        "object": dict, "array": list, "string": str, "integer": int, "null": type(None),
    }
    if types:
        assert any(
            isinstance(instance, checks[t]) and not (t == "integer" and isinstance(instance, bool))
            for t in types
        ), f"{path}: {type(instance).__name__} is not one of {types}"
    if "enum" in schema and instance is not None:
        assert instance in schema["enum"], f"{path}: {instance!r} not in enum"
    if isinstance(instance, dict) and "properties" in schema:
        for key in schema.get("required", []):
            assert key in instance, f"{path}: missing required key {key!r}"
        if schema.get("additionalProperties") is False:
            extra = set(instance) - set(schema["properties"])
            assert not extra, f"{path}: unexpected keys {sorted(extra)}"
        for key, value in instance.items():
            if key in schema["properties"]:
                _assert_conforms(value, schema["properties"][key], f"{path}.{key}")
    if isinstance(instance, list) and "items" in schema:
        for i, item in enumerate(instance):
            _assert_conforms(item, schema["items"], f"{path}[{i}]")


def test_schema_and_parser_describe_the_same_profile():
    """A reply that satisfies the schema must parse into a complete profile —
    12-month calendar, native-range zones and bilingual text all intact."""
    reply = _valid_profile_v2_json()
    _assert_conforms(json.loads(reply), ai_services._SPECIES_PROFILE_SCHEMA)

    parsed = ai_services._parse_species_profile_v2(reply)
    assert parsed is not None, "schema-conformant reply must survive the parser"
    assert len(parsed["flowering"]["monthly_intensity"]) == 12
    assert parsed["flowering"]["indoor_note"]["en"] and parsed["flowering"]["indoor_note"]["ru"]
    assert parsed["flowering_conditions"]["en"] and parsed["flowering_conditions"]["ru"]
    zones = parsed["native_range"]["zones"]
    assert [z["country_code"] for z in zones] == ["PER", "BOL"]  # duplicate dropped
    assert {z["status"] for z in zones} <= ai_services._NATIVE_RANGE_STATUSES
    assert parsed["native_range"]["summary"]["en"] and parsed["native_range"]["summary"]["ru"]


def test_schema_pins_the_calendar_and_zone_vocabulary():
    """The two fields with a closed vocabulary are the two the sanitiser drops
    the WHOLE profile over, so the schema has to constrain them at the source."""
    schema = ai_services._SPECIES_PROFILE_SCHEMA["properties"]
    months = schema["flowering"]["properties"]["monthly_intensity"]
    assert months["items"]["enum"] == [0, 1, 2]
    assert "12" in months["description"]
    zone = schema["native_range"]["properties"]["zones"]["items"]["properties"]
    assert set(zone["status"]["enum"]) == ai_services._NATIVE_RANGE_STATUSES
    assert "alpha-3" in zone["country_code"]["description"]


# ── parser ──────────────────────────────────────────────────────────

def test_parser_plain_json():
    result = ai_services._parse_species_profile(_valid_profile_json())
    assert result == {
        "flowering": {
            "monthly_intensity": [0, 0, 1, 2, 2, 1, 0, 0, 0, 0, 0, 0],
            "indoor_note": "Blooms less reliably indoors without a bright winter rest.",
        },
        "flowering_conditions": "Needs a cool, dry winter dormancy to set buds.",
        "native_range": {
            "zones": [
                {"country": "Brazil", "status": "grows_more"},
                {"country": "Mexico", "status": "grows_less"},
            ],
            "summary": "Native to the understory of Brazilian rainforests.",
        },
    }


def test_v2_parser_deduplicates_country_codes_and_keeps_both_languages():
    result = ai_services._parse_species_profile_v2(_valid_profile_v2_json())
    assert result is not None
    assert [z["country_code"] for z in result["native_range"]["zones"]] == ["PER", "BOL"]
    assert result["flowering_conditions"] == {
        "en": "Long days and warm temperatures help it bloom.",
        "ru": "Для цветения нужны длинный день и тепло.",
    }


def test_parser_tolerates_code_fence_and_prose():
    text = "Sure! Here it is:\n```json\n" + _valid_profile_json() + "\n```"
    result = ai_services._parse_species_profile(text)
    assert result is not None
    assert result["flowering"]["monthly_intensity"] == [0, 0, 1, 2, 2, 1, 0, 0, 0, 0, 0, 0]


@pytest.mark.parametrize("bad_months", [
    [0, 1, 2],                                   # wrong length
    [0] * 11 + [3],                              # out-of-range value
    [0] * 11 + ["1"],                            # wrong type (string, not int)
    None,                                        # missing entirely
])
def test_parser_rejects_bad_monthly_intensity(bad_months):
    payload = json.loads(_valid_profile_json())
    payload["flowering"]["monthly_intensity"] = bad_months
    assert ai_services._parse_species_profile(json.dumps(payload)) is None


def test_parser_rejects_bool_in_monthly_intensity():
    # bool is a subclass of int in Python — must not sneak past the 0/1/2 check.
    payload = json.loads(_valid_profile_json())
    payload["flowering"]["monthly_intensity"] = [True] + [0] * 11
    assert ai_services._parse_species_profile(json.dumps(payload)) is None


def test_parser_drops_malformed_zones():
    payload = json.loads(_valid_profile_json())
    payload["native_range"]["zones"] = [
        {"country": "Brazil", "status": "grows_more"},
        {"country": "", "status": "grows_less"},          # dropped — blank name
        {"country": "X", "status": "not_a_status"},        # dropped — bad status
        {"country": "L" * 200, "status": "grows_less"},    # dropped — too long (>80)
        {"country": "Mexico", "status": "grows_less"},
    ]
    result = ai_services._parse_species_profile(json.dumps(payload))
    assert result is not None
    assert result["native_range"]["zones"] == [
        {"country": "Brazil", "status": "grows_more"},
        {"country": "Mexico", "status": "grows_less"},
    ]


def test_parser_caps_zones_scanned_to_eight():
    payload = json.loads(_valid_profile_json())
    payload["native_range"]["zones"] = [
        {"country": f"C{i}", "status": "grows_less"} for i in range(10)
    ]
    result = ai_services._parse_species_profile(json.dumps(payload))
    assert result is not None
    assert len(result["native_range"]["zones"]) == 8


def test_parser_requires_at_least_one_zone():
    payload = json.loads(_valid_profile_json())
    payload["native_range"]["zones"] = []
    assert ai_services._parse_species_profile(json.dumps(payload)) is None


def test_parser_requires_summary_and_conditions():
    payload = json.loads(_valid_profile_json())
    payload["native_range"]["summary"] = ""
    assert ai_services._parse_species_profile(json.dumps(payload)) is None

    payload = json.loads(_valid_profile_json())
    payload["flowering_conditions"] = "   "
    assert ai_services._parse_species_profile(json.dumps(payload)) is None


def test_parser_caps_long_notes():
    payload = json.loads(_valid_profile_json())
    payload["flowering"]["indoor_note"] = "z" * 500
    payload["native_range"]["summary"] = "z" * 500
    result = ai_services._parse_species_profile(json.dumps(payload))
    assert result is not None
    assert len(result["flowering"]["indoor_note"]) <= 240
    assert len(result["native_range"]["summary"]) <= 240


@pytest.mark.parametrize("text", ["", "not json at all", "{}", json.dumps({"flowering": "x"})])
def test_parser_returns_none_on_junk(text):
    assert ai_services._parse_species_profile(text) is None


# ── helper: graceful + structured paths ─────────────────────────────

def test_no_api_key_is_graceful(monkeypatch):
    monkeypatch.setattr(ai_services, "OPENROUTER_API_KEY", "")
    out = ai_services.species_profile("Ficus elastica", lang="en")
    assert out["available"] is False
    assert out["source"] == "no_api_key"


def test_blank_name_is_graceful():
    out = ai_services.species_profile("   ", lang="en")
    assert out["available"] is False and out["source"] == "no_species"


def test_structured_success_and_cache(monkeypatch):
    monkeypatch.setattr(ai_services, "OPENROUTER_API_KEY", "test-key")
    calls = {"n": 0}

    def fake_call(system_text, history, *, max_tokens=None, require_complete=False,
                  reasoning_effort=None, json_schema=None, schema_name="response"):
        calls["n"] += 1
        # The shape is a provider-enforced schema now, not prose in the prompt.
        assert json_schema is not None
        assert json_schema["properties"]["native_range"]["properties"]["zones"][
            "items"]["properties"]["status"]["enum"] == ["grows_less", "grows_more"]
        # The hardest recall in this module (bloom calendar + ISO-3166 range)
        # and cached for 30 days per species — it earns more than the floor.
        assert reasoning_effort == "high"
        return (_valid_profile_json(), 10, 20, {})

    monkeypatch.setattr(ai_services, "_call_openrouter", fake_call)

    out = ai_services.species_profile("Ficus elastica", lang="en")
    assert out["available"] is True
    assert out["species"] == "Ficus elastica"
    assert out["flowering"]["monthly_intensity"] == [0, 0, 1, 2, 2, 1, 0, 0, 0, 0, 0, 0]
    assert out["native_range"]["zones"][0]["status"] == "grows_more"

    # Second call for the same (species, lang) is served from cache (no API hit).
    cached = ai_services.species_profile("Ficus elastica", lang="en")
    assert cached.get("cached") is True
    assert calls["n"] == 1


def test_v2_profile_is_shared_between_locales(monkeypatch):
    monkeypatch.setattr(ai_services, "OPENROUTER_API_KEY", "test-key")
    calls = {"n": 0}

    def fake_call(*args, **kwargs):
        calls["n"] += 1
        return (_valid_profile_v2_json(), 10, 20, {})

    monkeypatch.setattr(ai_services, "_call_openrouter", fake_call)
    en = ai_services.species_profile("Potato", lang="en")
    ru = ai_services.species_profile("Potato", lang="ru")

    assert calls["n"] == 1
    assert [z["country_code"] for z in en["native_range"]["zones"]] == ["PER", "BOL"]
    assert [z["country_code"] for z in ru["native_range"]["zones"]] == ["PER", "BOL"]
    assert en["flowering"]["monthly_intensity"] == ru["flowering"]["monthly_intensity"]
    assert en["flowering_conditions"] != ru["flowering_conditions"]
    assert en["localized_text"]["ru"]["native_range_summary"] == "Родом из Анд."


def test_disk_cache_survives_inmemory_wipe(monkeypatch):
    """A real result persists on disk, so after a server restart / fresh worker
    (in-memory LRU empty) it's reused without paying for the LLM again."""
    monkeypatch.setattr(ai_services, "OPENROUTER_API_KEY", "test-key")
    calls = {"n": 0}

    def fake_call(system_text, history, *, max_tokens=None, require_complete=False,
                  reasoning_effort=None, json_schema=None, schema_name="response"):
        calls["n"] += 1
        return (_valid_profile_json(), 5, 5, {})

    monkeypatch.setattr(ai_services, "_call_openrouter", fake_call)

    first = ai_services.species_profile("Calathea orbifolia", lang="en")
    assert first["available"] is True and calls["n"] == 1

    # Simulate a restart: drop the in-memory layer; the disk layer remains.
    ai_services._SPECIES_PROFILE_CACHE._d.clear()
    again = ai_services.species_profile("Calathea orbifolia", lang="en")
    assert again["available"] is True
    assert again.get("cached") is True
    assert calls["n"] == 1  # disk hit — the LLM was NOT called a second time


def test_api_error_is_graceful(monkeypatch):
    monkeypatch.setattr(ai_services, "OPENROUTER_API_KEY", "test-key")

    def boom(*a, **k):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(ai_services, "_call_openrouter", boom)
    out = ai_services.species_profile("Monstera deliciosa", lang="en")
    assert out["available"] is False and out["source"] == "api_error"


def test_malformed_reply_is_graceful(monkeypatch):
    monkeypatch.setattr(ai_services, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        ai_services, "_call_openrouter",
        lambda *a, **k: ("not valid json", 5, 5, {}),
    )
    out = ai_services.species_profile("Monstera deliciosa", lang="en")
    assert out["available"] is False and out["source"] == "empty"


# ── endpoint ────────────────────────────────────────────────────────

def test_endpoint_graceful_without_key(client):
    """With no OpenRouter key configured in the test env, the endpoint still
    returns 200 with a graceful unavailable payload (never an error)."""
    r = client.get("/api/species-profile", params={"name": "Ficus elastica"})
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False


def test_endpoint_rejects_blank_name(client):
    r = client.get("/api/species-profile", params={"name": "   "})
    assert r.status_code == 422


def test_v2_shape_with_bad_zone_codes_is_not_laundered_through_the_v1_parser(monkeypatch):
    """A v2 reply whose zones fail validation must degrade, not leak dict reprs.

    ``_sanitize_canonical_zones`` requires ISO alpha-3, so a model answering
    with alpha-2 ("PE") drops every zone and the v2 parser returns None.
    Control then falls through to the v1 parser on the *same* text. The v1
    helpers used to ``str()`` whatever they were given, which turned the v2
    ``{"en": ..., "ru": ...}`` dicts into Python dict reprs and served
    ``"{'en': 'Cool rest', 'ru': '...'}"`` to users — cached for 30 days.
    """
    v2_with_alpha2 = json.dumps({
        "flowering": {
            "monthly_intensity": [0, 0, 1, 2, 2, 1, 0, 0, 0, 0, 0, 0],
            "indoor_note": {"en": "Rarely indoors.", "ru": "Редко в помещении."},
        },
        "flowering_conditions": {"en": "Cool dry rest.", "ru": "Прохладный покой."},
        "native_range": {
            "zones": [
                {"country_code": "PE", "country": {"en": "Peru", "ru": "Перу"},
                 "status": "grows_more"},
            ],
            "summary": {"en": "Andean slopes.", "ru": "Склоны Анд."},
        },
    })
    monkeypatch.setattr(ai_services, "_chat_api_key_present", lambda: True)
    monkeypatch.setattr(
        ai_services, "_call_openrouter", lambda *a, **k: (v2_with_alpha2, None, None, None),
    )

    out = ai_services.species_profile("Ficus elastica", lang="en")

    assert out["available"] is False
    assert "{'en'" not in json.dumps(out, ensure_ascii=False)


def test_legacy_reply_is_not_served_to_the_other_locale(monkeypatch):
    """The legacy adapter copies one language into both slots.

    The v3 cache key carries no locale, so caching that under the shared key
    would serve the Russian text to every English client for the whole TTL.
    Legacy-derived profiles are locale-scoped instead.
    """
    ru_legacy = json.dumps({
        "flowering": {"monthly_intensity": [0] * 12, "indoor_note": "Редко цветёт."},
        "flowering_conditions": "Нужен прохладный период покоя.",
        "native_range": {
            "zones": [{"country": "Перу", "status": "grows_more"}],
            "summary": "Родом из Анд.",
        },
    })
    monkeypatch.setattr(ai_services, "_chat_api_key_present", lambda: True)
    monkeypatch.setattr(
        ai_services, "_call_openrouter", lambda *a, **k: (ru_legacy, None, None, None),
    )

    ru = ai_services.species_profile("Testus plantus", lang="ru")
    assert ru["available"] is True
    assert ru["flowering_conditions"] == "Нужен прохладный период покоя."

    # The English client must not be handed the Russian copy from cache.
    en = ai_services.species_profile("Testus plantus", lang="en")
    assert en.get("cached") is not True
