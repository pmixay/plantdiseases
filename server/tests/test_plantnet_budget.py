from __future__ import annotations

import io
import json
import uuid

import pytest

import plantnet_budget


class _Response(io.BytesIO):
    def __init__(self, body: bytes, *, status: int = 200):
        super().__init__(body)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def _decision(**overrides):
    payload = {
        "allowed": True,
        "replayed": False,
        "policy_version": "plantnet-2026-08",
        "usage_date": "2026-08-29",
        "attempt_limit": 100,
        "attempts_used": 1,
        "remaining_attempts": 99,
        "attempt_cost_micros": 5_000,
        "cost_limit_micros": 500_000,
        "cost_exposure_micros": 5_000,
        "remaining_cost_micros": 495_000,
        "reason": None,
    }
    payload.update(overrides)
    return payload


def test_reserve_sends_only_random_attempt_id_and_private_credential(monkeypatch):
    captured = {}

    def _open(request, *, timeout):  # noqa: ANN001
        captured["request"] = request
        captured["timeout"] = timeout
        return _Response(json.dumps(_decision()).encode())

    monkeypatch.setattr(plantnet_budget, "_open_no_redirect", _open)
    client = plantnet_budget.PlantNetBudgetClient(
        reserve_url=(
            "https://account.invalid/v1/internal/vendor-budgets/plantnet/reserve"
        ),
        service_token="s" * 64,
    )
    request_id = str(uuid.uuid4())

    result = client.reserve(request_id=request_id)

    assert result.allowed is True
    assert result.cost_exposure_micros == 5_000
    assert captured["timeout"] == 2.0
    request = captured["request"]
    assert request.get_method() == "POST"
    assert request.get_header("X-internal-service-token") == "s" * 64
    assert json.loads(request.data) == {"request_id": request_id}


@pytest.mark.parametrize(
    "payload",
    [
        _decision(extra="unexpected"),
        _decision(policy_version="bad version"),
        _decision(usage_date="tomorrow"),
        _decision(replayed=1),
        _decision(remaining_attempts=100),
        _decision(remaining_cost_micros=500_000),
        _decision(cost_exposure_micros=600_000, remaining_cost_micros=-100_000),
        _decision(allowed=False, reason=None),
        _decision(allowed=True, reason="attempt_limit"),
        _decision(
            allowed=False,
            attempts_used=99,
            remaining_attempts=1,
            reason="attempt_limit",
        ),
        _decision(
            allowed=False,
            attempts_used=50,
            remaining_attempts=50,
            cost_exposure_micros=490_000,
            remaining_cost_micros=10_000,
            reason="cost_limit",
        ),
    ],
)
def test_reserve_rejects_malformed_authority_responses(monkeypatch, payload):
    monkeypatch.setattr(
        plantnet_budget,
        "_open_no_redirect",
        lambda _request, *, timeout: _Response(json.dumps(payload).encode()),
    )
    client = plantnet_budget.PlantNetBudgetClient(
        reserve_url=(
            "https://account.invalid/v1/internal/vendor-budgets/plantnet/reserve"
        ),
        service_token="s" * 64,
    )

    with pytest.raises(plantnet_budget.PlantNetBudgetUnavailable):
        client.reserve(request_id=str(uuid.uuid4()))


def test_typed_ceiling_denial_is_returned(monkeypatch):
    payload = _decision(
        allowed=False,
        attempts_used=100,
        remaining_attempts=0,
        cost_exposure_micros=500_000,
        remaining_cost_micros=0,
        reason="attempt_limit",
    )
    monkeypatch.setattr(
        plantnet_budget,
        "_open_no_redirect",
        lambda _request, *, timeout: _Response(json.dumps(payload).encode()),
    )
    client = plantnet_budget.PlantNetBudgetClient(
        reserve_url=(
            "https://account.invalid/v1/internal/vendor-budgets/plantnet/reserve"
        ),
        service_token="s" * 64,
    )

    result = client.reserve(request_id=str(uuid.uuid4()))

    assert result.allowed is False
    assert result.reason == "attempt_limit"


def test_non_200_or_oversized_response_fails_closed(monkeypatch):
    client = plantnet_budget.PlantNetBudgetClient(
        reserve_url=(
            "https://account.invalid/v1/internal/vendor-budgets/plantnet/reserve"
        ),
        service_token="s" * 64,
    )
    monkeypatch.setattr(
        plantnet_budget,
        "_open_no_redirect",
        lambda _request, *, timeout: _Response(b"{}", status=503),
    )
    with pytest.raises(plantnet_budget.PlantNetBudgetUnavailable):
        client.reserve(request_id=str(uuid.uuid4()))

    monkeypatch.setattr(
        plantnet_budget,
        "_open_no_redirect",
        lambda _request, *, timeout: _Response(b"x" * 4097),
    )
    with pytest.raises(plantnet_budget.PlantNetBudgetUnavailable):
        client.reserve(request_id=str(uuid.uuid4()))
