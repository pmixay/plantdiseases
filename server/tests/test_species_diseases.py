"""Tests for the AI species common-diseases helper + endpoint.

``species_common_diseases`` asks the chat model for the conditions a species
is commonly affected by indoors and returns a small sanitised list, each item
optionally tagged with a guide ``doc_key`` the Android client can deep-link.
It must degrade gracefully (``available=False``) with no API key or on any
failure so the client always falls back to its offline disease guide. The
OpenRouter call is fully mocked — no network.
"""
from __future__ import annotations

import json

import pytest

import ai_services


@pytest.fixture(autouse=True)
def _clear_cache():
    ai_services._SPECIES_DISEASES_CACHE._d.clear()
    yield
    ai_services._SPECIES_DISEASES_CACHE._d.clear()


# ── parser ──────────────────────────────────────────────────────────

def test_parser_plain_json():
    text = json.dumps({"diseases": [
        {"name": "Powdery mildew", "doc_key": "powdery_mildew", "note": "White dust on leaves; improve airflow."},
    ]})
    items = ai_services._parse_species_diseases(text)
    assert items == [
        {"name": "Powdery mildew", "doc_key": "powdery_mildew",
         "note": "White dust on leaves; improve airflow."},
    ]


def test_parser_tolerates_code_fence_and_prose():
    text = "Sure! Here it is:\n```json\n" + json.dumps(
        {"diseases": [{"name": "Root rot", "doc_key": None, "note": "Soggy soil."}]}
    ) + "\n```"
    items = ai_services._parse_species_diseases(text)
    assert len(items) == 1 and items[0]["name"] == "Root rot"


def test_parser_drops_unknown_doc_key_and_caps():
    text = json.dumps({"diseases": [
        {"name": "X", "doc_key": "not_a_real_key", "note": "n"},
        {"name": "", "doc_key": "leaf_spot", "note": "skip me — no name"},
        {"name": "L" * 200, "note": "too long a name"},  # dropped (>80)
        {"name": "Long note", "doc_key": "pests", "note": "z" * 500},
    ]})
    items = ai_services._parse_species_diseases(text)
    assert len(items) == 2
    assert items[0] == {"name": "X", "doc_key": None, "note": "n"}
    assert items[1]["doc_key"] == "pests"
    assert len(items[1]["note"]) <= 240


def test_parser_limits_to_five_items():
    text = json.dumps({"diseases": [
        {"name": f"d{i}", "note": ""} for i in range(9)
    ]})
    assert len(ai_services._parse_species_diseases(text)) == 5


@pytest.mark.parametrize("text", ["", "not json at all", "{}", json.dumps({"diseases": "x"})])
def test_parser_returns_empty_on_junk(text):
    assert ai_services._parse_species_diseases(text) == []


# ── helper: graceful + structured paths ─────────────────────────────

def test_no_api_key_is_graceful(monkeypatch):
    monkeypatch.setattr(ai_services, "OPENROUTER_API_KEY", "")
    out = ai_services.species_common_diseases("Ficus elastica", lang="en")
    assert out["available"] is False
    assert out["diseases"] == []
    assert out["source"] == "no_api_key"


def test_blank_name_is_graceful():
    out = ai_services.species_common_diseases("   ", lang="en")
    assert out["available"] is False and out["source"] == "no_species"


def test_structured_success_and_cache(monkeypatch):
    monkeypatch.setattr(ai_services, "OPENROUTER_API_KEY", "test-key")
    calls = {"n": 0}

    def fake_call(system_text, history, *, max_tokens=None):
        calls["n"] += 1
        assert "JSON" in system_text
        return (json.dumps({"diseases": [
            {"name": "Spider mites", "doc_key": "pests", "note": "Fine webbing; rinse + neem."},
            {"name": "Edema", "doc_key": "edema", "note": "Blisters; water less."},
        ]}), 10, 20)

    monkeypatch.setattr(ai_services, "_call_openrouter", fake_call)

    out = ai_services.species_common_diseases("Ficus elastica", common_name="Rubber plant", lang="en")
    assert out["available"] is True
    assert out["species"] == "Ficus elastica"
    assert [d["name"] for d in out["diseases"]] == ["Spider mites", "Edema"]
    assert out["diseases"][0]["doc_key"] == "pests"

    # Second call for the same (species, lang) is served from cache (no API hit).
    cached = ai_services.species_common_diseases("Ficus elastica", lang="en")
    assert cached.get("cached") is True
    assert calls["n"] == 1


def test_api_error_is_graceful(monkeypatch):
    monkeypatch.setattr(ai_services, "OPENROUTER_API_KEY", "test-key")

    def boom(*a, **k):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(ai_services, "_call_openrouter", boom)
    out = ai_services.species_common_diseases("Monstera deliciosa", lang="en")
    assert out["available"] is False and out["source"] == "api_error"


# ── endpoint ────────────────────────────────────────────────────────

def test_endpoint_graceful_without_key(client):
    """With no OpenRouter key configured in the test env, the endpoint still
    returns 200 with a graceful unavailable payload (never an error)."""
    r = client.get("/api/species-diseases", params={"name": "Ficus elastica"})
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["diseases"] == []


def test_endpoint_rejects_blank_name(client):
    r = client.get("/api/species-diseases", params={"name": "   "})
    assert r.status_code == 422
