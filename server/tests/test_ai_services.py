"""Smoke tests for the smart APIs.

External services (PlantNet, OpenRouter) are intentionally NOT mocked
into pretending to be present — the test fixtures don't set the API
keys, so the public surface returns ``available: false`` deterministically
and we assert on that "graceful no-op" contract."""
import os

import pytest

# Make sure env-driven config doesn't accidentally enable the live API
# during unit tests (CI machines may have leaked secrets).
os.environ.pop("OPENROUTER_API_KEY", None)
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


def test_openrouter_call_retries_transient_5xx(monkeypatch):
    """`_call_openrouter` must retry on 503 / 502 / 504 / 429 / 408 and
    on connection / timeout errors, but NOT on auth / bad-request 4xx.
    The retry logic is what keeps a flaky upstream from killing a chat
    turn (and burning the user's session budget on the failure)."""
    import requests

    import ai_services as ai

    # Force the API-key check to pass so we exercise the retry path.
    monkeypatch.setattr(ai, "OPENROUTER_API_KEY", "test-key")
    # Don't actually sleep between retries — speed up the test.
    monkeypatch.setattr(ai, "_sleep_for_retry", lambda *a, **kw: None)

    class _Resp:
        def __init__(self, status, body=None):
            self.status_code = status
            self.headers = {}
            self._body = body or {}
        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(f"{self.status_code}", response=self)
        def json(self):
            return self._body

    # First two calls: 503; third call succeeds.
    calls = {"n": 0}
    def _fake_post(url, headers=None, json=None, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            return _Resp(503)
        return _Resp(200, {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2},
        })

    monkeypatch.setattr(requests, "post", _fake_post)
    text, tin, tout = ai._call_openrouter("system", [{"role": "user", "content": "hi"}])
    assert text == "ok"
    assert (tin, tout) == (5, 2)
    assert calls["n"] == 3


def test_openrouter_call_does_not_retry_4xx(monkeypatch):
    """Auth (401) / bad-request (400) errors fail fast — retrying
    those just wastes the user's turn budget."""
    import requests

    import ai_services as ai

    monkeypatch.setattr(ai, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(ai, "_sleep_for_retry", lambda *a, **kw: None)

    class _Resp:
        def __init__(self, status):
            self.status_code = status
            self.headers = {}
        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(str(self.status_code), response=self)
        def json(self):
            return {}

    calls = {"n": 0}
    def _fake_post(url, headers=None, json=None, timeout=None):
        calls["n"] += 1
        return _Resp(401)

    monkeypatch.setattr(requests, "post", _fake_post)
    with pytest.raises(requests.HTTPError):
        ai._call_openrouter("system", [{"role": "user", "content": "hi"}])
    # Single attempt — no retries on 4xx.
    assert calls["n"] == 1


def test_chat_session_caps_message_history_growth():
    """The per-session message buffer must not grow without bound even
    if the turn-count cap is generous: ``CHAT_SESSION_MAX_MESSAGES``
    enforces a hard ceiling."""
    import ai_services as ai

    sess = ai._ChatSession("smoke")
    # Push more messages than the cap and verify trimming.
    for i in range(ai.CHAT_SESSION_MAX_MESSAGES + 25):
        sess.append("user" if i % 2 == 0 else "assistant", f"msg {i}")
    assert len(sess.messages) == ai.CHAT_SESSION_MAX_MESSAGES
    # The oldest messages should be the ones evicted (FIFO).
    assert sess.messages[0]["content"] == f"msg {25}"
