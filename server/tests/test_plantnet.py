"""PlantNet species identification: graceful no-key state, key redaction, bounded
retries and the budget authority that gates every upstream attempt."""
import os

import pytest

from tests import module_state

# Make sure env-driven config doesn't accidentally enable the live API
# during unit tests (CI machines may have leaked secrets).
os.environ.pop("PLANTNET_API_KEY", None)


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    """Give each test plantnet as it is straight after import, with a budget
    authority that allows every attempt (tests swap in a denying one)."""
    import plantnet

    module_state.restore(plantnet)

    class _AllowBudget:
        def __init__(self):
            self.request_ids = []

        def reserve(self, *, request_id):
            self.request_ids.append(request_id)
            used = len(self.request_ids)
            return plantnet.PlantNetBudgetDecision(
                allowed=True,
                replayed=False,
                policy_version="unit-test-v1",
                usage_date="2030-01-02",
                attempt_limit=100,
                attempts_used=used,
                remaining_attempts=100 - used,
                attempt_cost_micros=5_000,
                cost_limit_micros=500_000,
                cost_exposure_micros=used * 5_000,
                remaining_cost_micros=500_000 - used * 5_000,
                reason=None,
            )

    budget = _AllowBudget()
    monkeypatch.setattr(plantnet, "plantnet_budget_client", budget)
    yield plantnet, budget


def test_identify_species_no_key_returns_graceful_state():
    import plantnet

    out = plantnet.identify_species(b"\xff\xd8\xfffake-jpeg")
    assert out["available"] is False
    assert out["results"] == []
    assert out["source"] == "no_api_key"


def test_redact_key_scrubs_api_key_param():
    """The api-key query param requests embeds in error URLs must be redacted."""
    import plantnet

    msg = "404 Client Error for url: https://my-api.plantnet.org/v2/identify/all?api-key=SECRET123&lang=en"
    out = plantnet._redact_key(msg)
    assert "SECRET123" not in out
    assert "api-key=***" in out


def test_identify_species_error_does_not_leak_key(monkeypatch, caplog):
    """A non-2xx (the routine 404 on non-plant images) must not log the key."""
    import logging

    import requests

    import plantnet

    monkeypatch.setattr(plantnet, "PLANTNET_API_KEY", "SECRET123")

    def _boom(url, params=None, files=None, data=None, timeout=None):
        # Mirror what requests raises: the full URL (with the api-key param) in the message.
        raise requests.exceptions.HTTPError(
            "404 Client Error for url: "
            "https://my-api.plantnet.org/v2/identify/all?api-key=SECRET123&lang=en"
        )

    monkeypatch.setattr(requests, "post", _boom)
    with caplog.at_level(logging.WARNING):
        out = plantnet.identify_species(b"\xff\xd8\xfffake-jpeg")
    assert out["source"] == "error"
    assert "SECRET123" not in caplog.text


def test_identify_species_default_organ_is_auto(monkeypatch):
    """PlantNet identifies flower/fruit/leaf/whole-plant itself when organs=auto
    — forcing 'leaf' on a non-leaf photo is a known wrong-species cause."""
    import requests

    import plantnet

    monkeypatch.setattr(plantnet, "PLANTNET_API_KEY", "test-key")
    sent = {}

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [], "remainingIdentificationRequests": 9}

    def _fake_post(url, params=None, files=None, data=None, timeout=None):
        sent["data"] = data
        return _Resp()

    monkeypatch.setattr(requests, "post", _fake_post)
    plantnet.identify_species(b"\xff\xd8\xfffake-jpeg")  # organ omitted -> default
    assert sent["data"] == {"organs": "auto"}


def test_identify_species_retries_once_on_timeout(monkeypatch, _reset_module_state):
    """A transient timeout on the first attempt is retried once and succeeds."""
    import requests

    import plantnet

    monkeypatch.setattr(plantnet, "PLANTNET_API_KEY", "test-key")
    monkeypatch.setattr(plantnet, "PLANTNET_ATTEMPTS", 2)
    calls = {"n": 0}

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [], "remainingIdentificationRequests": 9}

    def _flaky_post(url, params=None, files=None, data=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.exceptions.Timeout("read timed out")
        return _Resp()

    monkeypatch.setattr(requests, "post", _flaky_post)
    out = plantnet.identify_species(b"\xff\xd8\xfffake-jpeg")
    assert calls["n"] == 2
    assert len(_reset_module_state[1].request_ids) == 2
    assert len(set(_reset_module_state[1].request_ids)) == 2
    assert out["available"] is True


def test_identify_species_gives_up_after_bounded_retries(monkeypatch):
    """Both attempts timing out degrades gracefully (no unbounded retry)."""
    import requests

    import plantnet

    monkeypatch.setattr(plantnet, "PLANTNET_API_KEY", "test-key")
    monkeypatch.setattr(plantnet, "PLANTNET_ATTEMPTS", 2)
    calls = {"n": 0}

    def _always_timeout(url, params=None, files=None, data=None, timeout=None):
        calls["n"] += 1
        raise requests.exceptions.Timeout("read timed out")

    monkeypatch.setattr(requests, "post", _always_timeout)
    out = plantnet.identify_species(b"\xff\xd8\xfffake-jpeg")
    assert calls["n"] == 2  # exactly PLANTNET_ATTEMPTS, not more
    assert out["source"] == "error"


def test_retry_budget_denial_preserves_prior_upstream_attempt(monkeypatch):
    """A denied retry cannot erase the fact that attempt one reached PlantNet."""
    import requests

    import plantnet

    class _AllowThenDenyBudget:
        def __init__(self):
            self.calls = 0

        def reserve(self, *, request_id):
            self.calls += 1
            if self.calls == 1:
                return plantnet.PlantNetBudgetDecision(
                    allowed=True,
                    replayed=False,
                    policy_version="unit-test-v1",
                    usage_date="2030-01-02",
                    attempt_limit=1,
                    attempts_used=1,
                    remaining_attempts=0,
                    attempt_cost_micros=5_000,
                    cost_limit_micros=10_000,
                    cost_exposure_micros=5_000,
                    remaining_cost_micros=5_000,
                    reason=None,
                )
            return plantnet.PlantNetBudgetDecision(
                allowed=False,
                replayed=False,
                policy_version="unit-test-v1",
                usage_date="2030-01-02",
                attempt_limit=1,
                attempts_used=1,
                remaining_attempts=0,
                attempt_cost_micros=5_000,
                cost_limit_micros=10_000,
                cost_exposure_micros=5_000,
                remaining_cost_micros=5_000,
                reason="attempt_limit",
            )

    budget = _AllowThenDenyBudget()
    monkeypatch.setattr(plantnet, "PLANTNET_API_KEY", "test-key")
    monkeypatch.setattr(plantnet, "PLANTNET_ATTEMPTS", 2)
    monkeypatch.setattr(plantnet, "plantnet_budget_client", budget)
    network_calls = {"count": 0}

    def _timeout(*_args, **_kwargs):
        network_calls["count"] += 1
        raise requests.exceptions.Timeout("read timed out")

    monkeypatch.setattr(requests, "post", _timeout)

    out = plantnet.identify_species(b"\xff\xd8\xffretry-budget-denied")

    assert budget.calls == 2
    assert network_calls["count"] == 1
    assert out["source"] == "budget_exhausted"
    assert out["_upstream_attempted"] is True


def test_identify_species_fails_closed_before_network_without_budget_authority(
    monkeypatch,
):
    import requests

    import plantnet

    monkeypatch.setattr(plantnet, "PLANTNET_API_KEY", "test-key")
    monkeypatch.setattr(plantnet, "plantnet_budget_client", None)
    calls = {"n": 0}

    def _unexpected_post(*_args, **_kwargs):
        calls["n"] += 1
        raise AssertionError("PlantNet network call escaped the budget gate")

    monkeypatch.setattr(requests, "post", _unexpected_post)
    out = plantnet.identify_species(b"\xff\xd8\xffbudget-unavailable")

    assert out["source"] == "budget_unavailable"
    assert calls["n"] == 0
    assert plantnet.plantnet_metrics_snapshot()["budget_unavailable_total"] == 1


def test_identify_species_budget_denial_prevents_network_and_records_exposure(
    monkeypatch,
):
    import requests

    import plantnet

    class _DenyBudget:
        def reserve(self, *, request_id):
            return plantnet.PlantNetBudgetDecision(
                allowed=False,
                replayed=False,
                policy_version="unit-test-v1",
                usage_date="2030-01-02",
                attempt_limit=2,
                attempts_used=2,
                remaining_attempts=0,
                attempt_cost_micros=5_000,
                cost_limit_micros=10_000,
                cost_exposure_micros=10_000,
                remaining_cost_micros=0,
                reason="attempt_limit",
            )

    monkeypatch.setattr(plantnet, "PLANTNET_API_KEY", "test-key")
    monkeypatch.setattr(plantnet, "plantnet_budget_client", _DenyBudget())
    monkeypatch.setattr(
        requests,
        "post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("PlantNet network call escaped the budget gate")
        ),
    )

    out = plantnet.identify_species(b"\xff\xd8\xffbudget-denied")
    metrics = plantnet.plantnet_metrics_snapshot()

    assert out["source"] == "budget_exhausted"
    assert metrics["budget_denials_total"] == 1
    assert metrics["upstream_attempts_total"] == 0
    assert metrics["daily_cost_exposure_micros"] == 10_000


def test_replayed_allowed_reservation_never_authorizes_a_second_network_call(
    monkeypatch,
):
    import requests

    import plantnet

    class _ReplayBudget:
        def reserve(self, *, request_id):
            return plantnet.PlantNetBudgetDecision(
                allowed=True,
                replayed=True,
                policy_version="unit-test-v1",
                usage_date="2030-01-02",
                attempt_limit=100,
                attempts_used=1,
                remaining_attempts=99,
                attempt_cost_micros=5_000,
                cost_limit_micros=500_000,
                cost_exposure_micros=5_000,
                remaining_cost_micros=495_000,
                reason=None,
            )

    monkeypatch.setattr(plantnet, "PLANTNET_API_KEY", "test-key")
    monkeypatch.setattr(plantnet, "plantnet_budget_client", _ReplayBudget())
    monkeypatch.setattr(
        requests,
        "post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("replayed reservation reached PlantNet")
        ),
    )

    out = plantnet.identify_species(b"\xff\xd8\xffbudget-replay")

    assert out["source"] == "budget_unavailable"
    assert plantnet.plantnet_metrics_snapshot()["upstream_attempts_total"] == 0
