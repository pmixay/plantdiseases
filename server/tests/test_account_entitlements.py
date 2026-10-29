from __future__ import annotations

import io
import json
import uuid

import pytest

import account_entitlements


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
        "plan": "free",
        "feature": "gamma_analysis",
        "limit": 5,
        "remaining": 4,
        "reset_at": "2026-08-27T00:00:00Z",
        "reason": None,
        "policy_version": "launch-2026-08-v1",
        "released": False,
    }
    payload.update(overrides)
    return payload


def test_consume_sends_only_pseudonymous_ids_and_private_credential(monkeypatch):
    captured = {}

    def _open(request, *, timeout):  # noqa: ANN001
        captured["request"] = request
        captured["timeout"] = timeout
        return _Response(json.dumps(_decision()).encode())

    monkeypatch.setattr(account_entitlements, "_open_no_redirect", _open)
    client = account_entitlements.AccountEntitlementClient(
        consume_url="https://account.invalid/v1/internal/entitlements/consume",
        service_token="s" * 64,
    )
    actor_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())

    result = client.consume(
        actor_id=actor_id,
        feature="gamma_analysis",
        request_id=request_id,
    )

    assert result.allowed is True
    assert result.remaining == 4
    assert result.policy_version == "launch-2026-08-v1"
    assert result.released is False
    assert captured["timeout"] == 2.0
    request = captured["request"]
    assert request.get_method() == "POST"
    assert request.get_header("X-internal-service-token") == "s" * 64
    assert json.loads(request.data) == {
        "actor_id": actor_id,
        "feature": "gamma_analysis",
        "request_id": request_id,
    }


def test_release_uses_dedicated_internal_endpoint_and_typed_receipt(monkeypatch):
    captured = {}

    def _open(request, *, timeout):  # noqa: ANN001
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data)
        return _Response(
            json.dumps(_decision(replayed=True, released=True)).encode()
        )

    monkeypatch.setattr(account_entitlements, "_open_no_redirect", _open)
    client = account_entitlements.AccountEntitlementClient(
        consume_url="https://account.invalid/v1/internal/entitlements/consume",
        service_token="s" * 64,
    )
    actor_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())

    result = client.release(
        actor_id=actor_id,
        feature="gamma_analysis",
        request_id=request_id,
    )

    assert captured["url"].endswith("/v1/internal/entitlements/release")
    assert captured["body"] == {
        "actor_id": actor_id,
        "feature": "gamma_analysis",
        "request_id": request_id,
    }
    assert result.released is True
    assert result.replayed is True


@pytest.mark.parametrize(
    "payload",
    [
        _decision(extra="reflected"),
        _decision(feature="frontier_analysis"),
        _decision(remaining=6),
        _decision(allowed=False, reason=None),
        _decision(allowed=True, reason="quota_exhausted"),
        _decision(replayed=1),
        _decision(limit=0, remaining=0),
        _decision(allowed=False, limit=0, remaining=0, reason="quota_exhausted"),
        _decision(
            allowed=False,
            plan="pro",
            limit=0,
            remaining=0,
            reason="pro_required",
        ),
        _decision(reset_at="tomorrow"),
        _decision(reset_at="2026-08-27T00:00:00"),
    ],
)
def test_consume_rejects_malformed_or_mismatched_responses(monkeypatch, payload):
    monkeypatch.setattr(
        account_entitlements,
        "_open_no_redirect",
        lambda _request, *, timeout: _Response(json.dumps(payload).encode()),
    )
    client = account_entitlements.AccountEntitlementClient(
        consume_url="https://account.invalid/v1/internal/entitlements/consume",
        service_token="s" * 64,
    )

    with pytest.raises(account_entitlements.EntitlementUnavailable):
        client.consume(
            actor_id=str(uuid.uuid4()),
            feature="gamma_analysis",
            request_id=str(uuid.uuid4()),
        )


def test_denial_contract_is_typed(monkeypatch):
    payload = _decision(
        allowed=False,
        plan="free",
        limit=0,
        remaining=0,
        reason="pro_required",
    )
    monkeypatch.setattr(
        account_entitlements,
        "_open_no_redirect",
        lambda _request, *, timeout: _Response(json.dumps(payload).encode()),
    )
    client = account_entitlements.AccountEntitlementClient(
        consume_url="https://account.invalid/v1/internal/entitlements/consume",
        service_token="s" * 64,
    )

    decision = client.consume(
        actor_id=str(uuid.uuid4()),
        feature="gamma_analysis",
        request_id=str(uuid.uuid4()),
    )

    assert decision.allowed is False
    assert decision.reason == "pro_required"
