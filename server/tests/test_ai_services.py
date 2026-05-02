"""Smoke tests for the smart APIs.

External services (PlantNet, Anthropic) are intentionally NOT mocked
into pretending to be present — the test fixtures don't set the API
keys, so the public surface returns ``available: false`` deterministically
and we assert on that "graceful no-op" contract."""
import os

import pytest

# Make sure env-driven config doesn't accidentally enable the live API
# during unit tests (CI machines may have leaked secrets).
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("PLANTNET_API_KEY", None)


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Reload ai_services so each test sees a fresh _LRU + chat session map."""
    import importlib

    import ai_services as ai
    importlib.reload(ai)
    yield


def test_identify_species_no_key_returns_graceful_state():
    import ai_services as ai

    out = ai.identify_species(b"\xff\xd8\xfffake-jpeg")
    assert out["available"] is False
    assert out["results"] == []
    assert out["source"] == "no_api_key"


def test_chat_reply_no_key_returns_offline_fallback():
    import ai_services as ai

    out = ai.chat_reply(
        session_id="s1", user_message="My ficus has yellow leaves.",
    )
    assert out["available"] is False
    assert out["reason"] == "no_api_key"
    assert "fallback" in out


def test_chat_reply_rejects_empty_or_too_long_messages():
    import ai_services as ai

    blank = ai.chat_reply(session_id="s1", user_message="   ")
    assert blank["available"] is False and blank["reason"] == "empty_message"

    too_long = ai.chat_reply(session_id="s1", user_message="x" * 3000)
    assert too_long["available"] is False
    assert too_long["reason"] == "message_too_long"


def test_care_plan_offline_has_all_fields():
    import ai_services as ai

    plan = ai.generate_care_plan(
        disease="powdery_mildew", species="Begonia", light="bright_indirect",
    )
    for key in (
        "species", "disease", "light_tier",
        "watering_days", "fertilise_weeks", "humidity_target",
        "key_action", "follow_up_days",
    ):
        assert key in plan, f"missing care-plan key: {key}"
    # No LLM key configured ⇒ no summary_md polish.
    assert "summary_md" not in plan


def test_care_plan_unknown_disease_falls_through():
    import ai_services as ai

    # An unknown disease string should not raise — care-plan returns a
    # neutral monitoring plan instead.
    plan = ai.generate_care_plan(disease="unknown_disease_x", light="low_light")
    assert "watering_days" in plan
    assert plan["disease"] == "unknown_disease_x"


def test_progression_baseline_when_no_previous():
    import ai_services as ai

    out = ai.compare_progression(
        current={"class_name": "leaf_spot_fungal", "confidence": 0.7,
                 "severity": "medium"},
        previous=None,
    )
    assert out["trend"] == "baseline"
    assert out["delta_severity"] == 0


def test_progression_worsening_on_severity_step_up():
    import ai_services as ai

    out = ai.compare_progression(
        current={"class_name": "leaf_spot_fungal", "confidence": 0.85,
                 "severity": "high"},
        previous={"class_name": "leaf_spot_fungal", "confidence": 0.65,
                  "severity": "medium"},
    )
    assert out["trend"] == "worsening"
    assert out["delta_severity"] == 1


def test_progression_improving_disease_to_healthy():
    import ai_services as ai

    out = ai.compare_progression(
        current={"class_name": "healthy", "confidence": 0.92, "severity": "low"},
        previous={"class_name": "viral_disease", "confidence": 0.7,
                  "severity": "medium"},
    )
    assert out["trend"] == "improving"
    assert out["class_changed"] is True


def test_chat_metrics_snapshot_initially_empty():
    import ai_services as ai

    snap = ai.chat_metrics_snapshot()
    assert snap["active_sessions"] == 0
    assert snap["requests"] == 0
    assert snap["rejected"] == 0
