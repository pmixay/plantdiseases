from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Iterator

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

import app.services.accounts as accounts_service_module
from app.api.errors import AccountAPIError
from app.config import Settings
from app.db.models import (
    Account,
    AccountDeletionJob,
    AccountIdentity,
    AccountNicknameClaim,
    AccountNicknameJob,
    AccountSecurityEvent,
    PendingSignup,
    PushDevice,
    ReauthChallenge,
    RefreshToken,
    Session,
    SignupCompletion,
)
from app.db.repository import AccountRepository
from app.main import create_app
from app.providers.registry import build_provider_registry
from app.rate_limit import InMemoryRateLimiter
from app.security.push_crypto import PushTokenProtector
from app.security.tokens import SigningKeyRing, TokenService, hash_opaque_token
from app.services.accounts import AccountService


class MutableClock:
    def __init__(self):
        self.now = datetime(2030, 1, 2, 3, 4, 5, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


class TestDatabase:
    __test__ = False

    def __init__(self, factory: sessionmaker[OrmSession]):
        self.session_factory = factory
        self.closed = False

    def ping(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


@dataclass(slots=True)
class ApiHarness:
    client: TestClient
    service: AccountService
    engine: Engine
    clock: MutableClock

    def headers(self, access_token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {access_token}"}


@pytest.fixture
def api(db_engine: Engine) -> Iterator[ApiHarness]:
    settings = Settings.from_env(
        {
            "ENVIRONMENT": "test",
            "AUTH_PROVIDER_MODE": "mock",
            "ACCOUNT_ISSUER": "http://beta-account-api:8000",
            "ACCOUNT_AUDIENCE": "plantdiseases-app",
            "ACCOUNT_DATABASE_URL": (
                "postgresql+psycopg://beta:synthetic-test-only@beta-postgres/plantdisease_beta"
            ),
            "ACCOUNT_ALLOW_EPHEMERAL_KEYS": "true",
            "ACCOUNT_BLOCKED_NICKNAMES": "SyntheticBlocked",
            "ACCOUNT_RATE_LIMIT_REQUESTS": "1000",
        }
    )
    factory = sessionmaker(
        bind=db_engine,
        class_=OrmSession,
        autoflush=False,
        expire_on_commit=False,
    )
    providers = build_provider_registry(settings)
    clock = MutableClock()
    token_service = TokenService(
        issuer=settings.issuer,
        audience=settings.audience,
        key_ring=SigningKeyRing.ephemeral(),
    )
    service = AccountService(
        repository=AccountRepository(factory),
        providers=providers,
        token_service=token_service,
        push_protector=PushTokenProtector(b"p" * 32),
        limiter=InMemoryRateLimiter(requests=1000, window_seconds=60),
        blocked_nicknames=settings.blocked_nicknames,
        clock=clock,
        notification_sink=lambda targets, _payload: len(targets),
    )
    database = TestDatabase(factory)
    application = create_app(
        settings,
        providers=providers,
        database=database,
        account_service=service,
    )
    with TestClient(application) as client:
        yield ApiHarness(client=client, service=service, engine=db_engine, clock=clock)
    assert database.closed is True


def _exchange(
    api: ApiHarness,
    *,
    provider: str,
    provider_token: str,
    installation_id: uuid.UUID | None = None,
):
    return api.client.post(
        f"/v1/auth/{provider}/exchange",
        json={
            "provider_access_token": provider_token,
            "installation_id": str(installation_id or uuid.uuid4()),
        },
    )


def _signup(
    api: ApiHarness,
    *,
    provider: str = "yandex",
    provider_token: str = "mock-yandex-new",
    nickname: str = "SyntheticLeaf",
    installation_id: uuid.UUID | None = None,
) -> dict:
    installation = installation_id or uuid.uuid4()
    exchange = _exchange(
        api,
        provider=provider,
        provider_token=provider_token,
        installation_id=installation,
    )
    assert exchange.status_code == 200, exchange.text
    assert exchange.json()["status"] == "profile_required"
    completion_id = uuid.uuid4()
    pending_token = exchange.json()["pending_signup_token"]
    complete = api.client.post(
        "/v1/auth/signup/complete",
        json={
            "pending_signup_token": pending_token,
            "completion_id": str(completion_id),
            "nickname": nickname,
            "installation_id": str(installation),
        },
    )
    assert complete.status_code == 201, complete.text
    assert complete.json()["status"] == "confirmation_required"
    confirmed = api.client.post(
        "/v1/auth/signup/confirm",
        json={
            "pending_signup_token": pending_token,
            "completion_id": str(completion_id),
            "installation_id": str(installation),
        },
    )
    assert confirmed.status_code == 204, confirmed.text
    return complete.json()


def _error(response) -> str:
    return response.json()["error"]["code"]


def test_signup_tokens_profile_sessions_and_validation_are_private(api: ApiHarness):
    installation_id = uuid.uuid4()
    exchange = _exchange(
        api,
        provider="yandex",
        provider_token="mock-yandex-new",
        installation_id=installation_id,
    )
    assert exchange.status_code == 200
    pending_payload = exchange.json()
    assert pending_payload["status"] == "profile_required"
    assert set(pending_payload) == {
        "status",
        "provider",
        "pending_signup_token",
        "expires_in",
    }

    pending_token = pending_payload["pending_signup_token"]
    with api.service.repository.transaction() as transaction:
        pending = transaction.scalar(select(PendingSignup))
        assert pending is not None
        assert pending.token_hash == hash_opaque_token(pending_token)
        assert pending_token.encode() not in pending.token_hash

    completion_id = uuid.uuid4()
    completed = api.client.post(
        "/v1/auth/signup/complete",
        json={
            "pending_signup_token": pending_token,
            "completion_id": str(completion_id),
            "nickname": "SyntheticLeaf",
            "installation_id": str(installation_id),
        },
    )
    assert completed.status_code == 201
    tokens = completed.json()
    assert tokens["status"] == "confirmation_required"
    confirmed = api.client.post(
        "/v1/auth/signup/confirm",
        json={
            "pending_signup_token": pending_token,
            "completion_id": str(completion_id),
            "installation_id": str(installation_id),
        },
    )
    assert confirmed.status_code == 204
    access_token = tokens["access_token"]
    refresh_token = tokens["refresh_token"]
    claims = jwt.decode(access_token, options={"verify_signature": False})
    assert set(claims) == {"sid", "act", "nick", "iss", "aud", "iat", "nbf", "exp", "av"}
    assert "account_id" not in completed.text
    assert "provider_subject" not in completed.text

    me = api.client.get("/v1/me", headers=api.headers(access_token))
    assert me.status_code == 200
    assert me.json()["nickname"] == "SyntheticLeaf"
    assert me.json()["primary_provider"] == {
        "provider": "yandex",
        "display_name": "Synthetic Yandex New",
        "avatar_url": "https://profiles.invalid/yandex/001.png",
        "refreshed_at": "2030-01-02T03:04:05Z",
    }
    assert me.json()["linked_providers"][0]["provider"] == "yandex"
    assert "subject" not in me.text

    sessions = api.client.get("/v1/auth/sessions", headers=api.headers(access_token))
    assert sessions.status_code == 200
    assert sessions.json()["sessions"][0]["device_label"] == "Android device"
    assert sessions.json()["sessions"][0]["installation_id"] == str(installation_id)
    session_payload = sessions.json()["sessions"][0]
    created_at = datetime.fromisoformat(session_payload["created_at"].replace("Z", "+00:00"))
    idle_expiry = datetime.fromisoformat(
        session_payload["idle_expires_at"].replace("Z", "+00:00")
    )
    absolute_expiry = datetime.fromisoformat(
        session_payload["absolute_expires_at"].replace("Z", "+00:00")
    )
    assert idle_expiry - created_at == timedelta(days=30)
    assert absolute_expiry - created_at == timedelta(days=180)
    assert all(
        forbidden not in sessions.text
        for forbidden in ("manufacturer", "hardware_model", "product", "serial")
    )

    jwks = api.client.get("/.well-known/jwks.json")
    assert jwks.status_code == 200
    assert jwks.json()["keys"][0]["kid"] == jwt.get_unverified_header(access_token)["kid"]
    assert jwks.headers["Cache-Control"] == "public, max-age=300, must-revalidate"
    assert me.headers["Cache-Control"] == "no-store"

    with api.service.repository.transaction() as transaction:
        stored_refresh = transaction.scalar(select(RefreshToken))
        assert stored_refresh is not None
        assert stored_refresh.token_hash == hash_opaque_token(refresh_token)
        assert refresh_token.encode() not in stored_refresh.token_hash

    sentinel = "provider-secret-must-not-be-reflected"
    invalid = api.client.post(
        "/v1/auth/yandex/exchange",
        json={
            "provider_access_token": sentinel,
            "installation_id": str(uuid.uuid4()),
            "hardware_model": sentinel,
        },
    )
    assert invalid.status_code == 422
    assert invalid.json() == {"error": {"code": "invalid_request"}}
    assert sentinel not in invalid.text


@pytest.mark.parametrize(
    ("token", "expected_code", "expected_status"),
    [
        ("mock-yandex-expired", "invalid_provider_token", 401),
        ("mock-yandex-forged", "invalid_provider_token", 401),
        ("mock-yandex-wrong-application", "provider_application_mismatch", 401),
        ("mock-yandex-unavailable", "provider_unavailable", 503),
        ("mock-yandex-incomplete-profile", "profile_required", 401),
    ],
)
def test_provider_failures_are_stable_and_never_reflect_tokens(
    api: ApiHarness, token: str, expected_code: str, expected_status: int
):
    response = _exchange(api, provider="yandex", provider_token=token)
    assert response.status_code == expected_status
    assert _error(response) == expected_code
    assert token not in response.text


def test_disabled_provider_fails_closed(api: ApiHarness):
    vk_adapter = api.service.providers.pop("vk")
    try:
        response = _exchange(api, provider="vk", provider_token="not-reflected")
    finally:
        api.service.providers["vk"] = vk_adapter

    assert response.status_code == 503
    assert _error(response) == "provider_unavailable"
    assert "not-reflected" not in response.text


def test_presented_secrets_are_not_logged(api: ApiHarness, caplog: pytest.LogCaptureFixture):
    provider_sentinel = "mock-yandex-not-a-secret-log-sentinel"
    refresh_sentinel = "refresh-not-a-secret-log-sentinel"
    _exchange(api, provider="yandex", provider_token=provider_sentinel)
    api.client.post("/v1/auth/refresh", json={"refresh_token": refresh_sentinel})

    assert provider_sentinel not in caplog.text
    assert refresh_sentinel not in caplog.text


def test_response_contract_failure_does_not_log_or_reflect_issued_tokens(
    api: ApiHarness,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
):
    access_sentinel = "issued-access-token-must-not-reach-validation-logs"
    refresh_sentinel = "issued-refresh-token-must-not-reach-validation-logs"

    async def invalid_exchange(**_kwargs):
        return {
            "status": "authenticated",
            "access_token": access_sentinel,
            "refresh_token": refresh_sentinel,
        }

    monkeypatch.setattr(api.service, "exchange", invalid_exchange)
    response = _exchange(
        api,
        provider="yandex",
        provider_token="mock-yandex-new",
    )
    assert response.status_code == 500
    assert response.json() == {"error": {"code": "service_unavailable"}}
    assert access_sentinel not in response.text
    assert refresh_sentinel not in response.text
    assert access_sentinel not in caplog.text
    assert refresh_sentinel not in caplog.text


def test_exchange_ip_rate_limit_is_wired_without_persisting_or_reflecting_address(
    api: ApiHarness,
):
    api.service.limiter = InMemoryRateLimiter(requests=1, window_seconds=60)
    first = _exchange(
        api,
        provider="yandex",
        provider_token="mock-yandex-forged",
    )
    assert first.status_code == 401

    limited = _exchange(
        api,
        provider="yandex",
        provider_token="mock-yandex-forged",
    )
    assert limited.status_code == 429
    assert _error(limited) == "rate_limited"
    assert limited.headers["Retry-After"] == "60"
    assert "testclient" not in limited.text


def test_pending_signup_cancellation_and_expiry_are_one_use(api: ApiHarness):
    installation_id = uuid.uuid4()
    completion_id = uuid.uuid4()
    exchange = _exchange(
        api,
        provider="yandex",
        provider_token="mock-yandex-new",
        installation_id=installation_id,
    )
    pending_token = exchange.json()["pending_signup_token"]
    cancelled = api.client.post(
        "/v1/auth/signup/cancel",
        json={
            "pending_signup_token": pending_token,
            "completion_id": str(completion_id),
            "installation_id": str(installation_id),
        },
    )
    assert cancelled.status_code == 204
    replay = api.client.post(
        "/v1/auth/signup/complete",
        json={
            "pending_signup_token": pending_token,
            "completion_id": str(completion_id),
            "nickname": "SyntheticLeaf",
            "installation_id": str(installation_id),
        },
    )
    assert replay.status_code == 409
    assert _error(replay) == "pending_signup_replayed"

    replacement_installation = uuid.uuid4()
    replacement = _exchange(
        api,
        provider="yandex",
        provider_token="mock-yandex-new",
        installation_id=replacement_installation,
    )
    api.clock.advance(timedelta(minutes=11))
    expired = api.client.post(
        "/v1/auth/signup/complete",
        json={
            "pending_signup_token": replacement.json()["pending_signup_token"],
            "completion_id": str(uuid.uuid4()),
            "nickname": "SyntheticLeaf",
            "installation_id": str(replacement_installation),
        },
    )
    assert expired.status_code == 401
    assert _error(expired) == "pending_signup_expired"


def test_signup_completion_lost_response_is_exactly_idempotent_and_two_phase(
    api: ApiHarness,
):
    installation_id = uuid.uuid4()
    completion_id = uuid.uuid4()
    exchange = _exchange(
        api,
        provider="yandex",
        provider_token="mock-yandex-new",
        installation_id=installation_id,
    )
    pending_token = exchange.json()["pending_signup_token"]
    request = {
        "pending_signup_token": pending_token,
        "completion_id": str(completion_id),
        "nickname": "RetryFern",
        "installation_id": str(installation_id),
    }

    first = api.client.post("/v1/auth/signup/complete", json=request)
    # Mutable moderation policy must not strand an already-committed logical
    # operation after its first response was lost.
    api.service.blocked_nicknames = ("RetryFern",)
    replay = api.client.post("/v1/auth/signup/complete", json=request)

    assert first.status_code == replay.status_code == 201
    assert first.json()["status"] == "confirmation_required"
    assert replay.json()["refresh_token"] == first.json()["refresh_token"]
    assert replay.json()["session"] == first.json()["session"]
    with api.service.repository.transaction() as transaction:
        assert len(list(transaction.scalars(select(Account)))) == 1
        assert len(list(transaction.scalars(select(Session)))) == 1
        completion = transaction.scalar(select(SignupCompletion))
        pending = transaction.scalar(select(PendingSignup))
        assert completion is not None and pending is not None
        assert pending.installation_id == installation_id
        assert completion.id == completion_id
        assert pending_token.encode() not in completion.request_fingerprint
        assert b"retryfern" not in completion.request_fingerprint

    # Neither provisional credential is usable before the durable client ack.
    access_denied = api.client.get(
        "/v1/me",
        headers=api.headers(first.json()["access_token"]),
    )
    refresh_denied = api.client.post(
        "/v1/auth/refresh",
        json={
            "refresh_token": first.json()["refresh_token"],
            "rotation_id": str(uuid.uuid4()),
        },
    )
    assert access_denied.status_code == refresh_denied.status_code == 409
    assert _error(access_denied) == "signup_confirmation_required"
    assert _error(refresh_denied) == "signup_confirmation_required"

    for changed in (
        request | {"completion_id": str(uuid.uuid4())},
        request | {"installation_id": str(uuid.uuid4())},
        request | {"nickname": "DifferentFern"},
    ):
        conflict = api.client.post("/v1/auth/signup/complete", json=changed)
        assert conflict.status_code == 409
        assert _error(conflict) == "signup_completion_conflict"

    wrong_confirm = api.client.post(
        "/v1/auth/signup/confirm",
        json={
            "pending_signup_token": pending_token,
            "completion_id": str(uuid.uuid4()),
            "installation_id": str(installation_id),
        },
    )
    assert wrong_confirm.status_code == 409
    assert _error(wrong_confirm) == "signup_completion_conflict"

    confirmation = {
        "pending_signup_token": pending_token,
        "completion_id": str(completion_id),
        "installation_id": str(installation_id),
    }
    assert api.client.post("/v1/auth/signup/confirm", json=confirmation).status_code == 204
    assert api.client.post("/v1/auth/signup/confirm", json=confirmation).status_code == 204
    assert api.client.get(
        "/v1/me",
        headers=api.headers(first.json()["access_token"]),
    ).status_code == 200


@pytest.mark.parametrize(
    ("active_cap", "retained_cap"),
    [(1, 64), (8, 1)],
)
def test_unexpired_completion_session_is_never_an_admission_or_retention_victim(
    api: ApiHarness,
    monkeypatch: pytest.MonkeyPatch,
    active_cap: int,
    retained_cap: int,
):
    installation_id = uuid.uuid4()
    completion_id = uuid.uuid4()
    exchange = _exchange(
        api,
        provider="yandex",
        provider_token="mock-yandex-new",
        installation_id=installation_id,
    )
    request = {
        "pending_signup_token": exchange.json()["pending_signup_token"],
        "completion_id": str(completion_id),
        "nickname": "ProtectedFern",
        "installation_id": str(installation_id),
    }
    first = api.client.post("/v1/auth/signup/complete", json=request)
    assert first.status_code == 201
    assert api.client.post(
        "/v1/auth/signup/confirm",
        json={
            "pending_signup_token": request["pending_signup_token"],
            "completion_id": str(completion_id),
            "installation_id": str(installation_id),
        },
    ).status_code == 204
    protected_session_id = first.json()["session"]["session_id"]
    monkeypatch.setattr(
        accounts_service_module,
        "MAX_ACTIVE_SESSIONS_PER_ACCOUNT",
        active_cap,
    )
    monkeypatch.setattr(
        accounts_service_module,
        "MAX_RETAINED_SESSIONS_PER_ACCOUNT",
        retained_cap,
    )

    admission = _exchange(
        api,
        provider="yandex",
        provider_token="mock-yandex-new",
        installation_id=installation_id,
    )

    assert admission.status_code == 429
    assert _error(admission) == "rate_limited"
    exact_retry = api.client.post("/v1/auth/signup/complete", json=request)
    assert exact_retry.status_code == 201
    assert exact_retry.json()["session"]["session_id"] == protected_session_id


def test_provider_proof_restarts_an_unconfirmed_signup_without_minting_a_session(
    api: ApiHarness,
):
    first_installation = uuid.uuid4()
    first_completion = uuid.uuid4()
    exchange = _exchange(
        api,
        provider="yandex",
        provider_token="mock-yandex-new",
        installation_id=first_installation,
    )
    first_pending = exchange.json()["pending_signup_token"]
    completed = api.client.post(
        "/v1/auth/signup/complete",
        json={
            "pending_signup_token": first_pending,
            "completion_id": str(first_completion),
            "nickname": "ReinstallFern",
            "installation_id": str(first_installation),
        },
    )
    assert completed.status_code == 201

    replacement_installation = uuid.uuid4()
    replacement = _exchange(
        api,
        provider="yandex",
        provider_token="mock-yandex-new",
        installation_id=replacement_installation,
    )
    assert replacement.status_code == 200
    assert replacement.json()["status"] == "profile_required"
    assert replacement.json()["pending_signup_token"] != first_pending
    with api.service.repository.transaction() as transaction:
        assert transaction.scalar(select(Account)) is None
        assert transaction.scalar(select(Session)) is None
        old_completion = transaction.get(SignupCompletion, first_completion)
        assert old_completion is not None
        assert old_completion.cancelled_at is not None
        assert old_completion.account_id is None
        assert old_completion.session_id is None

    old_retry = api.client.post(
        "/v1/auth/signup/complete",
        json={
            "pending_signup_token": first_pending,
            "completion_id": str(first_completion),
            "nickname": "ReinstallFern",
            "installation_id": str(first_installation),
        },
    )
    assert old_retry.status_code == 409
    assert _error(old_retry) == "signup_completion_cancelled"


def test_reinstall_exchange_uses_pending_completion_account_lock_order(api: ApiHarness):
    installation_id = uuid.uuid4()
    exchange = _exchange(
        api,
        provider="yandex",
        provider_token="mock-yandex-new",
        installation_id=installation_id,
    )
    assert api.client.post(
        "/v1/auth/signup/complete",
        json={
            "pending_signup_token": exchange.json()["pending_signup_token"],
            "completion_id": str(uuid.uuid4()),
            "nickname": "OrderedFern",
            "installation_id": str(installation_id),
        },
    ).status_code == 201
    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(" ".join(statement.lower().split()))

    event.listen(api.engine, "before_cursor_execute", capture)
    try:
        replacement = _exchange(
            api,
            provider="yandex",
            provider_token="mock-yandex-new",
            installation_id=uuid.uuid4(),
        )
    finally:
        event.remove(api.engine, "before_cursor_execute", capture)
    assert replacement.status_code == 200

    pending_lock = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("select pending_signups.id")
        and "where pending_signups.id =" in statement
    )
    completion_lock = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("select signup_completions.id")
        and "where signup_completions.id =" in statement
    )
    account_lock = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("select accounts.id") and "where accounts.id =" in statement
    )
    assert pending_lock < completion_lock < account_lock


def test_new_provider_exchange_locks_pending_before_identity_discovery(api: ApiHarness):
    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(" ".join(statement.lower().split()))

    event.listen(api.engine, "before_cursor_execute", capture)
    try:
        exchange = _exchange(
            api,
            provider="yandex",
            provider_token="mock-yandex-new",
            installation_id=uuid.uuid4(),
        )
    finally:
        event.remove(api.engine, "before_cursor_execute", capture)
    assert exchange.status_code == 200
    assert exchange.json()["status"] == "profile_required"

    pending_lock = next(
        index
        for index, statement in enumerate(statements)
        if "from pending_signups" in statement
        and "pending_signups.provider =" in statement
        and "pending_signups.provider_subject =" in statement
    )
    identity_read = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith(
            "select account_identities.id, account_identities.account_id"
        )
    )
    assert "order by pending_signups.id" in statements[pending_lock]
    assert pending_lock < identity_read


def test_ambiguous_signup_cancel_rolls_back_or_recovers_confirmed_account(
    api: ApiHarness,
):
    installation_id = uuid.uuid4()
    completion_id = uuid.uuid4()
    exchange = _exchange(
        api,
        provider="vk",
        provider_token="mock-vk-new",
        installation_id=installation_id,
    )
    pending_token = exchange.json()["pending_signup_token"]
    completion_request = {
        "pending_signup_token": pending_token,
        "completion_id": str(completion_id),
        "nickname": "CancelFern",
        "installation_id": str(installation_id),
    }
    assert api.client.post(
        "/v1/auth/signup/complete", json=completion_request
    ).status_code == 201
    cancellation = {
        "pending_signup_token": pending_token,
        "completion_id": str(completion_id),
        "installation_id": str(installation_id),
    }
    assert api.client.post("/v1/auth/signup/cancel", json=cancellation).status_code == 204
    assert api.client.post("/v1/auth/signup/cancel", json=cancellation).status_code == 204
    with api.service.repository.transaction() as transaction:
        assert transaction.scalar(select(Account)) is None
        assert transaction.scalar(select(Session)) is None
        marker = transaction.get(SignupCompletion, completion_id)
        assert marker is not None and marker.cancelled_at is not None
        assert marker.account_id is None and marker.session_id is None
    cancelled_retry = api.client.post(
        "/v1/auth/signup/complete", json=completion_request
    )
    assert cancelled_retry.status_code == 409
    assert _error(cancelled_retry) == "signup_completion_cancelled"

    confirmed_installation = uuid.uuid4()
    confirmed_completion = uuid.uuid4()
    confirmed_exchange = _exchange(
        api,
        provider="yandex",
        provider_token="mock-yandex-generated-00000000000000000000000000000001",
        installation_id=confirmed_installation,
    )
    confirmed_pending = confirmed_exchange.json()["pending_signup_token"]
    confirmed = api.client.post(
        "/v1/auth/signup/complete",
        json={
            "pending_signup_token": confirmed_pending,
            "completion_id": str(confirmed_completion),
            "nickname": "ConfirmedFern",
            "installation_id": str(confirmed_installation),
        },
    ).json()
    confirmed_proof = {
        "pending_signup_token": confirmed_pending,
        "completion_id": str(confirmed_completion),
        "installation_id": str(confirmed_installation),
    }
    assert api.client.post(
        "/v1/auth/signup/confirm", json=confirmed_proof
    ).status_code == 204
    # A lost confirmation response followed by Cancel must surface the durable
    # account, not silently report a successful profile cancellation.
    rejected_cancel = api.client.post(
        "/v1/auth/signup/cancel",
        json=confirmed_proof,
    )
    assert rejected_cancel.status_code == 409
    assert _error(rejected_cancel) == "signup_already_confirmed"
    assert api.client.get(
        "/v1/me", headers=api.headers(confirmed["access_token"])
    ).status_code == 200


def test_signup_completion_prune_rolls_back_only_unconfirmed_accounts(api: ApiHarness):
    installation_id = uuid.uuid4()
    completion_id = uuid.uuid4()
    exchange = _exchange(
        api,
        provider="yandex",
        provider_token="mock-yandex-new",
        installation_id=installation_id,
    )
    pending_token = exchange.json()["pending_signup_token"]
    assert api.client.post(
        "/v1/auth/signup/complete",
        json={
            "pending_signup_token": pending_token,
            "completion_id": str(completion_id),
            "nickname": "PrunedFern",
            "installation_id": str(installation_id),
        },
    ).status_code == 201
    api.clock.advance(timedelta(days=7, seconds=1))

    assert api.service.prune_expired_private_state()["pending_signups"] == 1
    with api.service.repository.transaction() as transaction:
        assert transaction.scalar(select(SignupCompletion)) is None
        assert transaction.scalar(select(PendingSignup)) is None
        assert transaction.scalar(select(Account)) is None
        assert transaction.scalar(select(Session)) is None

    confirmed = _signup(
        api,
        provider="vk",
        provider_token="mock-vk-new",
        nickname="KeptFern",
    )
    api.clock.advance(timedelta(days=7, seconds=1))
    assert api.service.prune_expired_private_state()["pending_signups"] == 1
    with api.service.repository.transaction() as transaction:
        assert transaction.scalar(select(SignupCompletion)) is None
        assert transaction.scalar(select(PendingSignup)) is None
        assert transaction.scalar(select(Account)) is not None
        assert transaction.get(Session, uuid.UUID(confirmed["session"]["session_id"])) is not None


def test_expired_unconfirmed_signup_can_still_be_cancelled_exactly(api: ApiHarness):
    installation_id = uuid.uuid4()
    completion_id = uuid.uuid4()
    exchange = _exchange(
        api,
        provider="yandex",
        provider_token="mock-yandex-new",
        installation_id=installation_id,
    )
    pending_token = exchange.json()["pending_signup_token"]
    assert api.client.post(
        "/v1/auth/signup/complete",
        json={
            "pending_signup_token": pending_token,
            "completion_id": str(completion_id),
            "nickname": "LateCancelFern",
            "installation_id": str(installation_id),
        },
    ).status_code == 201
    api.clock.advance(timedelta(days=7, seconds=1))

    cancelled = api.client.post(
        "/v1/auth/signup/cancel",
        json={
            "pending_signup_token": pending_token,
            "completion_id": str(completion_id),
            "installation_id": str(installation_id),
        },
    )

    assert cancelled.status_code == 204
    with api.service.repository.transaction() as transaction:
        marker = transaction.get(SignupCompletion, completion_id)
        assert marker is not None and marker.cancelled_at is not None
        assert marker.account_id is None and marker.session_id is None
        assert transaction.scalar(select(Account)) is None
        assert transaction.scalar(select(Session)) is None


def test_signup_prune_locks_pending_before_completion_and_account(api: ApiHarness):
    installation_id = uuid.uuid4()
    exchange = _exchange(
        api,
        provider="vk",
        provider_token="mock-vk-new",
        installation_id=installation_id,
    )
    assert api.client.post(
        "/v1/auth/signup/complete",
        json={
            "pending_signup_token": exchange.json()["pending_signup_token"],
            "completion_id": str(uuid.uuid4()),
            "nickname": "PruneOrderFern",
            "installation_id": str(installation_id),
        },
    ).status_code == 201
    api.clock.advance(timedelta(days=7, seconds=1))
    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(" ".join(statement.lower().split()))

    event.listen(api.engine, "before_cursor_execute", capture)
    try:
        api.service.prune_expired_private_state()
    finally:
        event.remove(api.engine, "before_cursor_execute", capture)

    pending_lock = next(
        index
        for index, statement in enumerate(statements)
        if "from pending_signups" in statement and "pending_signups.id in" in statement
    )
    completion_lock = next(
        index
        for index, statement in enumerate(statements)
        if "from signup_completions" in statement
        and "signup_completions.id in" in statement
    )
    pending_delete = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("delete from pending_signups")
    )
    account_read = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("select accounts.id") and "where accounts.id =" in statement
    )
    assert pending_lock < completion_lock < pending_delete < account_read


def test_signup_prune_rechecks_unconsumed_pending_rows_before_delete(api: ApiHarness):
    unconsumed_hash = hash_opaque_token("ordinary-expired-unconsumed")
    consumed_hash = hash_opaque_token("ordinary-expired-consumed")
    with api.service.repository.transaction() as transaction:
        transaction.add_all(
            [
                PendingSignup(
                    token_hash=unconsumed_hash,
                    provider="vk",
                    provider_subject="vk-ordinary-expired-unconsumed",
                    provider_display_name="Unconsumed",
                    created_at=api.clock.now - timedelta(minutes=20),
                    expires_at=api.clock.now - timedelta(minutes=10),
                ),
                PendingSignup(
                    token_hash=consumed_hash,
                    provider="vk",
                    provider_subject="vk-ordinary-expired-consumed",
                    provider_display_name="Consumed",
                    created_at=api.clock.now - timedelta(minutes=20),
                    expires_at=api.clock.now - timedelta(minutes=10),
                    consumed_at=api.clock.now - timedelta(minutes=9),
                ),
            ]
        )

    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(" ".join(statement.lower().split()))

    event.listen(api.engine, "before_cursor_execute", capture)
    try:
        counts = api.service.prune_expired_private_state()
    finally:
        event.remove(api.engine, "before_cursor_execute", capture)

    assert counts["pending_signups"] == 1
    with api.service.repository.transaction() as transaction:
        assert transaction.scalar(
            select(PendingSignup).where(PendingSignup.token_hash == unconsumed_hash)
        ) is None
        assert transaction.scalar(
            select(PendingSignup).where(PendingSignup.token_hash == consumed_hash)
        ) is not None

    candidate_lock = next(
        index
        for index, statement in enumerate(statements)
        if "from pending_signups" in statement
        and "pending_signups.expires_at <=" in statement
        and "pending_signups.consumed_at is null" in statement
    )
    pending_delete = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("delete from pending_signups")
    )
    assert candidate_lock < pending_delete
    assert all(
        "not in" not in statement
        for statement in statements
        if statement.startswith("delete from pending_signups")
    )


def test_nickname_uniqueness_cooldown_and_authorization_version(api: ApiHarness):
    first = _signup(api, nickname="LeafOwner")
    second_installation = uuid.uuid4()
    second_exchange = _exchange(
        api,
        provider="vk",
        provider_token="mock-vk-new",
        installation_id=second_installation,
    )
    duplicate = api.client.post(
        "/v1/auth/signup/complete",
        json={
            "pending_signup_token": second_exchange.json()["pending_signup_token"],
            "completion_id": str(uuid.uuid4()),
            "nickname": "leafowner",
            "installation_id": str(second_installation),
        },
    )
    assert duplicate.status_code == 409
    assert _error(duplicate) == "nickname_taken"

    access_token = first["access_token"]
    immediate = api.client.patch(
        "/v1/me/nickname",
        headers=api.headers(access_token),
        json={"nickname": "NewLeafOwner"},
    )
    assert immediate.status_code == 409
    assert _error(immediate) == "nickname_cooldown"

    with api.service.repository.transaction() as transaction:
        account = transaction.scalar(select(Account))
        assert account is not None
        account.nickname_changed_at = api.clock.now - timedelta(days=31)

    blocked = api.client.patch(
        "/v1/me/nickname",
        headers=api.headers(access_token),
        json={"nickname": "SyntheticBlocked"},
    )
    assert blocked.status_code == 400
    assert _error(blocked) == "nickname_invalid"

    current = api.service.authenticate(access_token)
    propagated: list[tuple[uuid.UUID, str]] = []
    api.service.nickname_dispatcher = (
        lambda actor, nickname: propagated.append((actor, nickname)) or True
    )
    changed = api.client.patch(
        "/v1/me/nickname",
        headers=api.headers(access_token),
        json={"nickname": "NewLeafOwner"},
    )
    assert changed.status_code == 200
    assert changed.json()["nickname"] == "NewLeafOwner"
    assert propagated == [(current.community_actor_id, "NewLeafOwner")]
    new_access_token = changed.json()["access_token"]

    old_access = api.client.get("/v1/me", headers=api.headers(access_token))
    assert old_access.status_code == 401
    assert _error(old_access) == "session_revoked"
    assert api.client.get("/v1/me", headers=api.headers(new_access_token)).status_code == 200


def test_nickname_change_commits_and_retries_when_callback_response_is_lost(
    api: ApiHarness,
):
    tokens = _signup(api, nickname="HistoryFern")
    current = api.service.authenticate(tokens["access_token"])
    with api.service.repository.transaction() as transaction:
        account = transaction.scalar(select(Account))
        assert account is not None
        account.nickname_changed_at = api.clock.now - timedelta(days=31)
    delivered: list[tuple[uuid.UUID, str]] = []

    def response_lost_once(actor_id: uuid.UUID, nickname: str) -> bool:
        # Both calls represent an idempotent community update. The first one
        # succeeds remotely but its response is lost; the durable job retries.
        delivered.append((actor_id, nickname))
        return len(delivered) > 1

    api.service.nickname_dispatcher = response_lost_once

    response = api.client.patch(
        "/v1/me/nickname",
        headers=api.headers(tokens["access_token"]),
        json={"nickname": "FutureFern"},
    )

    assert response.status_code == 200
    assert response.json()["nickname"] == "FutureFern"
    assert delivered == [(current.community_actor_id, "FutureFern")]
    with api.service.repository.transaction() as transaction:
        account = transaction.scalar(select(Account))
        job = transaction.scalar(select(AccountNicknameJob))
        claims = list(
            transaction.scalars(
                select(AccountNicknameClaim).order_by(
                    AccountNicknameClaim.normalized_nickname
                )
            )
        )
        assert account is not None and account.nickname == "FutureFern"
        assert job is not None
        assert job.nickname == "FutureFern"
        assert job.state == "retry"
        assert job.attempt_count == 1
        assert [(claim.normalized_nickname, claim.state) for claim in claims] == [
            ("futurefern", "current"),
            ("historyfern", "reserved"),
        ]

    api.clock.advance(timedelta(seconds=60))
    assert api.service.process_nickname_jobs() == {"completed": 1, "retried": 0}
    assert delivered == [
        (current.community_actor_id, "FutureFern"),
        (current.community_actor_id, "FutureFern"),
    ]
    with api.service.repository.transaction() as transaction:
        assert transaction.scalar(select(AccountNicknameJob)) is None
        claims = list(transaction.scalars(select(AccountNicknameClaim)))
        assert [(claim.normalized_nickname, claim.state) for claim in claims] == [
            ("futurefern", "current")
        ]


def test_nickname_outage_reserves_every_possibly_visible_alias(api: ApiHarness):
    tokens = _signup(api, nickname="FirstFern")
    api.service.nickname_dispatcher = lambda _actor, _nickname: False
    with api.service.repository.transaction() as transaction:
        account = transaction.scalar(select(Account))
        assert account is not None
        account.nickname_changed_at = api.clock.now - timedelta(days=31)

    second = api.client.patch(
        "/v1/me/nickname",
        headers=api.headers(tokens["access_token"]),
        json={"nickname": "SecondFern"},
    )
    assert second.status_code == 200
    # Compress the 30-day product wait without expiring the access/session.
    with api.service.repository.transaction() as transaction:
        account = transaction.scalar(select(Account))
        assert account is not None
        account.nickname_changed_at = api.clock.now - timedelta(days=31)
    third = api.client.patch(
        "/v1/me/nickname",
        headers=api.headers(second.json()["access_token"]),
        json={"nickname": "ThirdFern"},
    )
    assert third.status_code == 200

    with api.service.repository.transaction() as transaction:
        claims = list(
            transaction.scalars(
                select(AccountNicknameClaim).order_by(
                    AccountNicknameClaim.normalized_nickname
                )
            )
        )
        assert [(claim.normalized_nickname, claim.state) for claim in claims] == [
            ("firstfern", "reserved"),
            ("secondfern", "reserved"),
            ("thirdfern", "current"),
        ]

    blocked_installation = uuid.uuid4()
    exchange = _exchange(
        api,
        provider="vk",
        provider_token="mock-vk-new",
        installation_id=blocked_installation,
    )
    blocked = api.client.post(
        "/v1/auth/signup/complete",
        json={
            "pending_signup_token": exchange.json()["pending_signup_token"],
            "completion_id": str(uuid.uuid4()),
            "nickname": "FirstFern",
            "installation_id": str(blocked_installation),
        },
    )
    assert blocked.status_code == 409
    assert _error(blocked) == "nickname_taken"

    api.service.nickname_dispatcher = lambda _actor, _nickname: True
    api.clock.advance(timedelta(seconds=60))
    assert api.service.process_nickname_jobs() == {"completed": 1, "retried": 0}
    with api.service.repository.transaction() as transaction:
        claims = list(transaction.scalars(select(AccountNicknameClaim)))
        assert [(claim.normalized_nickname, claim.state) for claim in claims] == [
            ("thirdfern", "current")
        ]


def test_refresh_rotation_lost_response_replays_the_same_successor(api: ApiHarness):
    tokens = _signup(api)
    first_refresh = tokens["refresh_token"]
    rotation_id = str(uuid.uuid4())
    rotated = api.client.post(
        "/v1/auth/refresh",
        json={"refresh_token": first_refresh, "rotation_id": rotation_id},
    )
    assert rotated.status_code == 200
    api.clock.advance(timedelta(minutes=5))

    retry = api.client.post(
        "/v1/auth/refresh",
        json={"refresh_token": first_refresh, "rotation_id": rotation_id},
    )

    assert retry.status_code == 200
    assert retry.json()["refresh_token"] == rotated.json()["refresh_token"]
    assert retry.json()["session"] == rotated.json()["session"]
    successor = api.client.post(
        "/v1/auth/refresh",
        json={
            "refresh_token": retry.json()["refresh_token"],
            "rotation_id": str(uuid.uuid4()),
        },
    )
    assert successor.status_code == 200


def test_refresh_requires_rotation_id(api: ApiHarness):
    tokens = _signup(api)
    missing = api.client.post(
        "/v1/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert missing.status_code == 422


def test_refresh_rotation_replay_after_successor_use_is_detected_immediately(
    api: ApiHarness,
):
    tokens = _signup(api)
    first_refresh = tokens["refresh_token"]
    first_rotation_id = str(uuid.uuid4())
    rotated = api.client.post(
        "/v1/auth/refresh",
        json={"refresh_token": first_refresh, "rotation_id": first_rotation_id},
    )
    replacement_refresh = rotated.json()["refresh_token"]
    successor = api.client.post(
        "/v1/auth/refresh",
        json={"refresh_token": replacement_refresh, "rotation_id": str(uuid.uuid4())},
    )
    assert successor.status_code == 200

    replay = api.client.post(
        "/v1/auth/refresh",
        json={"refresh_token": first_refresh, "rotation_id": first_rotation_id},
    )
    denied = api.client.post(
        "/v1/auth/refresh",
        json={
            "refresh_token": successor.json()["refresh_token"],
            "rotation_id": str(uuid.uuid4()),
        },
    )

    assert replay.status_code == 401
    assert _error(replay) == "refresh_reused"
    assert denied.status_code == 401
    assert _error(denied) == "session_revoked"


def test_refresh_rotation_different_id_is_reuse_not_a_retry(api: ApiHarness):
    tokens = _signup(api)
    first_refresh = tokens["refresh_token"]
    rotated = api.client.post(
        "/v1/auth/refresh",
        json={"refresh_token": first_refresh, "rotation_id": str(uuid.uuid4())},
    )
    assert rotated.status_code == 200

    replay = api.client.post(
        "/v1/auth/refresh",
        json={"refresh_token": first_refresh, "rotation_id": str(uuid.uuid4())},
    )

    assert replay.status_code == 401
    assert _error(replay) == "refresh_reused"


def test_refresh_rotation_matching_id_is_still_bounded(api: ApiHarness):
    tokens = _signup(api)
    first_refresh = tokens["refresh_token"]
    rotation_id = str(uuid.uuid4())
    rotated = api.client.post(
        "/v1/auth/refresh",
        json={"refresh_token": first_refresh, "rotation_id": rotation_id},
    )
    assert rotated.status_code == 200
    api.clock.advance(timedelta(days=7, seconds=1))

    replay = api.client.post(
        "/v1/auth/refresh",
        json={"refresh_token": first_refresh, "rotation_id": rotation_id},
    )
    successor = api.client.post(
        "/v1/auth/refresh",
        json={
            "refresh_token": rotated.json()["refresh_token"],
            "rotation_id": str(uuid.uuid4()),
        },
    )

    assert replay.status_code == 401
    assert _error(replay) == "refresh_reused"
    assert successor.status_code == 401
    assert _error(successor) == "session_revoked"


def test_refresh_idle_expiry_revokes_session(api: ApiHarness):
    tokens = _signup(api)
    api.clock.advance(timedelta(days=31))
    expired = api.client.post(
        "/v1/auth/refresh",
        json={
            "refresh_token": tokens["refresh_token"],
            "rotation_id": str(uuid.uuid4()),
        },
    )
    assert expired.status_code == 401
    assert _error(expired) == "refresh_expired"
    with api.service.repository.transaction() as transaction:
        assert transaction.scalar(select(Session)).revoked_at is not None


def test_multiple_sessions_can_be_listed_revoked_and_logged_out(api: ApiHarness):
    first_installation = uuid.uuid4()
    first = _signup(api, installation_id=first_installation)
    second_installation = uuid.uuid4()
    second_response = _exchange(
        api,
        provider="yandex",
        provider_token="mock-yandex-new",
        installation_id=second_installation,
    )
    assert second_response.status_code == 200
    second = second_response.json()
    assert second["status"] == "authenticated"

    sessions = api.client.get(
        "/v1/auth/sessions", headers=api.headers(first["access_token"])
    ).json()["sessions"]
    assert {item["installation_id"] for item in sessions} == {
        str(first_installation),
        str(second_installation),
    }
    second_session_id = second["session"]["session_id"]
    revoked = api.client.delete(
        f"/v1/auth/sessions/{second_session_id}",
        headers=api.headers(first["access_token"]),
    )
    assert revoked.status_code == 204
    second_denied = api.client.get("/v1/me", headers=api.headers(second["access_token"]))
    assert second_denied.status_code == 401
    assert _error(second_denied) == "session_revoked"

    logged_out = api.client.post(
        "/v1/auth/logout", json={"refresh_token": first["refresh_token"]}
    )
    assert logged_out.status_code == 204
    first_denied = api.client.get("/v1/me", headers=api.headers(first["access_token"]))
    assert first_denied.status_code == 401
    assert _error(first_denied) == "session_revoked"


def test_logout_refresh_credential_is_remote_idempotent_and_needs_no_live_jwt(
    api: ApiHarness,
):
    tokens = _signup(api)
    api.clock.advance(timedelta(minutes=16))

    first = api.client.post(
        "/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}
    )
    repeated = api.client.post(
        "/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}
    )
    unknown = api.client.post(
        "/v1/auth/logout", json={"refresh_token": "unknown-refresh-credential"}
    )

    assert first.status_code == repeated.status_code == unknown.status_code == 204
    with api.service.repository.transaction() as transaction:
        account_session = transaction.scalar(select(Session))
        assert account_session is not None and account_session.revoked_at is not None


def test_consumed_refresh_credential_can_revoke_its_replacement_family(api: ApiHarness):
    tokens = _signup(api)
    rotated = api.client.post(
        "/v1/auth/refresh",
        json={
            "refresh_token": tokens["refresh_token"],
            "rotation_id": str(uuid.uuid4()),
        },
    )
    assert rotated.status_code == 200

    logout = api.client.post(
        "/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}
    )
    denied = api.client.post(
        "/v1/auth/refresh",
        json={
            "refresh_token": rotated.json()["refresh_token"],
            "rotation_id": str(uuid.uuid4()),
        },
    )

    assert logout.status_code == 204
    assert denied.status_code == 401
    assert _error(denied) == "session_revoked"


def test_reauthentication_links_one_identity_once(api: ApiHarness):
    tokens = _signup(api)
    headers = api.headers(tokens["access_token"])
    unlinked_provider = api.client.post(
        "/v1/auth/reauth",
        headers=headers,
        json={
            "provider": "vk",
            "provider_access_token": "mock-vk-new",
            "scope": "link_vk",
        },
    )
    assert unlinked_provider.status_code == 401
    assert _error(unlinked_provider) == "reauth_required"

    reauth = api.client.post(
        "/v1/auth/reauth",
        headers=headers,
        json={
            "provider": "yandex",
            "provider_access_token": "mock-yandex-new",
            "scope": "link_vk",
        },
    )
    assert reauth.status_code == 200
    challenge = reauth.json()["reauth_token"]
    linked = api.client.post(
        "/v1/auth/vk/link",
        headers=headers,
        json={
            "provider_access_token": "mock-vk-new",
            "reauth_token": challenge,
        },
    )
    assert linked.status_code == 200
    assert linked.json() == {"status": "linked", "provider": "vk"}
    me = api.client.get("/v1/me", headers=headers)
    assert {item["provider"] for item in me.json()["linked_providers"]} == {"yandex", "vk"}

    replay = api.client.post(
        "/v1/auth/vk/link",
        headers=headers,
        json={
            "provider_access_token": "mock-vk-new",
            "reauth_token": challenge,
        },
    )
    assert replay.status_code == 401
    assert _error(replay) == "reauth_expired"

    already_linked = api.client.post(
        "/v1/auth/reauth",
        headers=headers,
        json={
            "provider": "yandex",
            "provider_access_token": "mock-yandex-new",
            "scope": "link_vk",
        },
    )
    assert already_linked.status_code == 409
    assert _error(already_linked) == "identity_already_linked"


def test_linking_cancels_same_subject_pending_before_identity_insert(api: ApiHarness):
    requester = _signup(
        api,
        provider="yandex",
        provider_token="mock-yandex-new",
        nickname="LinkOwner",
    )
    pending_installation = uuid.uuid4()
    pending_exchange = _exchange(
        api,
        provider="vk",
        provider_token="mock-vk-new",
        installation_id=pending_installation,
    )
    pending_token = pending_exchange.json()["pending_signup_token"]
    headers = api.headers(requester["access_token"])
    reauth = api.client.post(
        "/v1/auth/reauth",
        headers=headers,
        json={
            "provider": "yandex",
            "provider_access_token": "mock-yandex-new",
            "scope": "link_vk",
        },
    )

    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(" ".join(statement.lower().split()))

    event.listen(api.engine, "before_cursor_execute", capture)
    try:
        linked = api.client.post(
            "/v1/auth/vk/link",
            headers=headers,
            json={
                "provider_access_token": "mock-vk-new",
                "reauth_token": reauth.json()["reauth_token"],
            },
        )
    finally:
        event.remove(api.engine, "before_cursor_execute", capture)

    assert linked.status_code == 200
    pending_lock = next(
        index
        for index, statement in enumerate(statements)
        if "from pending_signups" in statement
        and "pending_signups.provider_subject =" in statement
    )
    identity_reread = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("select account_identities.account_id")
        and "account_identities.provider_subject =" in statement
    )
    account_lock = next(
        index
        for index, statement in enumerate(statements)
        if index > identity_reread
        if statement.startswith("select accounts.id") and "where accounts.id =" in statement
    )
    assert pending_lock < identity_reread < account_lock
    stale_completion = api.client.post(
        "/v1/auth/signup/complete",
        json={
            "pending_signup_token": pending_token,
            "completion_id": str(uuid.uuid4()),
            "nickname": "MustNotDuplicate",
            "installation_id": str(pending_installation),
        },
    )
    assert stale_completion.status_code == 409
    assert _error(stale_completion) == "pending_signup_replayed"
    linked_exchange = _exchange(
        api,
        provider="vk",
        provider_token="mock-vk-new",
        installation_id=uuid.uuid4(),
    )
    assert linked_exchange.status_code == 200
    assert linked_exchange.json()["status"] == "authenticated"


def test_linking_refuses_identity_owned_by_another_account(api: ApiHarness):
    owner = _signup(
        api,
        provider="vk",
        provider_token="mock-vk-owned-by-other",
        nickname="VkOwner",
    )
    assert owner["status"] == "confirmation_required"
    requester = _signup(
        api,
        provider="yandex",
        provider_token="mock-yandex-new",
        nickname="YandexOwner",
    )
    headers = api.headers(requester["access_token"])
    reauth = api.client.post(
        "/v1/auth/reauth",
        headers=headers,
        json={
            "provider": "yandex",
            "provider_access_token": "mock-yandex-new",
            "scope": "link_vk",
        },
    )
    refused = api.client.post(
        "/v1/auth/vk/link",
        headers=headers,
        json={
            "provider_access_token": "mock-vk-owned-by-other",
            "reauth_token": reauth.json()["reauth_token"],
        },
    )
    assert refused.status_code == 409
    assert _error(refused) == "identity_owned_by_another_account"


def test_reauth_challenge_expires_after_five_minutes(api: ApiHarness):
    tokens = _signup(api)
    headers = api.headers(tokens["access_token"])
    reauth = api.client.post(
        "/v1/auth/reauth",
        headers=headers,
        json={
            "provider": "yandex",
            "provider_access_token": "mock-yandex-new",
            "scope": "link_vk",
        },
    )
    api.clock.advance(timedelta(minutes=6))
    expired = api.client.post(
        "/v1/auth/vk/link",
        headers=headers,
        json={
            "provider_access_token": "mock-vk-new",
            "reauth_token": reauth.json()["reauth_token"],
        },
    )
    assert expired.status_code == 401
    assert _error(expired) == "reauth_expired"


def test_push_is_authenticated_encrypted_and_disabled_on_deletion(api: ApiHarness):
    guest = api.client.put(
        "/v1/push/device",
        json={"fcm_token": "synthetic-fcm-token-without-a-real-device-guest"},
    )
    assert guest.status_code == 401
    assert _error(guest) == "auth_required"

    tokens = _signup(api)
    headers = api.headers(tokens["access_token"])
    fcm_token = "synthetic-fcm-token-without-a-real-device-0001"
    registered = api.client.put(
        "/v1/push/device",
        headers=headers,
        json={"fcm_token": fcm_token, "platform": "android"},
    )
    assert registered.status_code == 200
    assert registered.json() == {"status": "registered", "platform": "android"}
    with api.service.repository.transaction() as transaction:
        device = transaction.scalar(select(PushDevice))
        assert device is not None
        assert fcm_token.encode() not in device.token_ciphertext
        assert api.service.push_protector.decrypt(device.token_ciphertext) == fcm_token
        assert len(device.token_fingerprint) == 32

    reauth = api.client.post(
        "/v1/auth/reauth",
        headers=headers,
        json={
            "provider": "yandex",
            "provider_access_token": "mock-yandex-new",
            "scope": "deletion",
        },
    )
    assert reauth.status_code == 200
    deletion = api.client.request(
        "DELETE",
        "/v1/me",
        headers=headers,
        json={"reauth_token": reauth.json()["reauth_token"]},
    )
    assert deletion.status_code == 202
    assert deletion.json() == {"status": "deletion_pending"}

    with api.service.repository.transaction() as transaction:
        account = transaction.scalar(select(Account))
        sessions = list(transaction.scalars(select(Session)))
        device = transaction.scalar(select(PushDevice))
        job = transaction.scalar(select(AccountDeletionJob))
        challenge = transaction.scalar(select(ReauthChallenge))
        event = transaction.scalar(
            select(AccountSecurityEvent).where(
                AccountSecurityEvent.event_type == "deletion_requested"
            )
        )
        assert account is not None and account.state == "deletion_pending"
        assert all(item.revoked_at is not None for item in sessions)
        assert device is not None and device.disabled_at is not None
        assert job is not None and job.community_actor_id == account.community_actor_id
        assert job.state == "retry"
        assert job.attempt_count == 1
        assert job.next_attempt_at is not None
        assert job.last_error_code == "community_unavailable"
        assert challenge is not None and challenge.consumed_at is not None
        assert event is not None

    denied = api.client.get("/v1/me", headers=headers)
    assert denied.status_code == 409
    assert _error(denied) == "deletion_pending"
    refresh_denied = api.client.post(
        "/v1/auth/refresh",
        json={
            "refresh_token": tokens["refresh_token"],
            "rotation_id": str(uuid.uuid4()),
        },
    )
    assert refresh_denied.status_code == 409
    assert _error(refresh_denied) == "deletion_pending"


def test_push_registration_replaces_the_only_device_for_a_session(api: ApiHarness):
    tokens = _signup(api)
    headers = api.headers(tokens["access_token"])
    first = "synthetic-fcm-token-without-a-real-device-first"
    second = "synthetic-fcm-token-without-a-real-device-second"

    assert api.client.put(
        "/v1/push/device", headers=headers, json={"fcm_token": first}
    ).status_code == 200
    assert api.client.put(
        "/v1/push/device", headers=headers, json={"fcm_token": second}
    ).status_code == 200

    with api.service.repository.transaction() as transaction:
        devices = list(transaction.scalars(select(PushDevice)))
        assert len(devices) == 1
        assert api.service.push_protector.decrypt(devices[0].token_ciphertext) == second


def test_push_path_fails_explicitly_without_a_delivery_sink(api: ApiHarness):
    tokens = _signup(api)
    api.service.notification_sink = None

    response = api.client.put(
        "/v1/push/device",
        headers=api.headers(tokens["access_token"]),
        json={"fcm_token": "synthetic-fcm-token-without-a-real-sink"},
    )

    assert response.status_code == 503
    assert _error(response) == "push_unavailable"
    with api.service.repository.transaction() as transaction:
        assert transaction.scalar(select(PushDevice)) is None


def test_database_identity_rows_never_escape_profile_contract(api: ApiHarness):
    tokens = _signup(api)
    with api.service.repository.transaction() as transaction:
        identity = transaction.scalar(select(AccountIdentity))
        assert identity is not None
        private_subject = identity.provider_subject

    response = api.client.get("/v1/me", headers=api.headers(tokens["access_token"]))
    assert response.status_code == 200
    assert private_subject not in response.text


def test_sensitive_mutation_rechecks_account_state_inside_its_transaction(api: ApiHarness):
    tokens = _signup(api)
    current = api.service.authenticate(tokens["access_token"])
    with api.service.repository.transaction() as transaction:
        account = transaction.get(Account, current.account_id)
        assert account is not None
        account.state = "deletion_pending"
        account.authorization_version += 1

    with pytest.raises(AccountAPIError, match="deletion_pending"):
        api.service.register_push(
            current,
            fcm_token="synthetic-fcm-token-after-deletion-race",
            platform="android",
        )
    with api.service.repository.transaction() as transaction:
        assert transaction.scalar(select(PushDevice)) is None


def test_deletion_rechecks_current_session_after_reauth_before_mutating(api: ApiHarness):
    tokens = _signup(api)
    current = api.service.authenticate(tokens["access_token"])
    reauth = api.client.post(
        "/v1/auth/reauth",
        headers=api.headers(tokens["access_token"]),
        json={
            "provider": "yandex",
            "provider_access_token": "mock-yandex-new",
            "scope": "deletion",
        },
    )
    assert reauth.status_code == 200

    # Models the other-device revocation that can land after the HTTP auth
    # dependency returned but before the deletion transaction starts.
    with api.service.repository.transaction() as transaction:
        account_session = transaction.get(Session, current.session_id)
        assert account_session is not None
        account_session.revoked_at = api.clock.now

    with pytest.raises(AccountAPIError, match="session_revoked"):
        api.service.request_deletion(
            current, reauth_token=reauth.json()["reauth_token"]
        )

    with api.service.repository.transaction() as transaction:
        account = transaction.get(Account, current.account_id)
        challenge = transaction.scalar(select(ReauthChallenge))
        assert account is not None and account.state == "active"
        assert challenge is not None and challenge.consumed_at is None


def test_private_state_pruning_respects_expiry_and_refresh_family_lifetime(api: ApiHarness):
    first = _signup(api)
    second = _exchange(
        api,
        provider="yandex",
        provider_token="mock-yandex-new",
    ).json()
    first_session_id = uuid.UUID(first["session"]["session_id"])
    second_session_id = uuid.UUID(second["session"]["session_id"])
    with api.service.repository.transaction() as transaction:
        account = transaction.scalar(select(Account))
        first_session = transaction.get(Session, first_session_id)
        assert account is not None and first_session is not None
        first_session.idle_expires_at = api.clock.now - timedelta(days=2)
        first_session.absolute_expires_at = api.clock.now - timedelta(days=1)
        transaction.add_all(
            [
                PendingSignup(
                    token_hash=hash_opaque_token("expired-pending"),
                    provider="vk",
                    provider_subject="vk-test-expired-retention",
                    provider_display_name="Synthetic Expired Pending",
                    expires_at=api.clock.now - timedelta(seconds=1),
                ),
                ReauthChallenge(
                    account_id=account.id,
                    session_id=second_session_id,
                    token_hash=hash_opaque_token("expired-reauth"),
                    provider="yandex",
                    scope="deletion",
                    issued_at=api.clock.now - timedelta(minutes=6),
                    expires_at=api.clock.now - timedelta(minutes=1),
                ),
                AccountSecurityEvent(
                    account_id=account.id,
                    session_id=second_session_id,
                    event_type="session_revoked",
                    outcome="accepted",
                    created_at=api.clock.now - timedelta(days=31),
                    expires_at=api.clock.now - timedelta(days=1),
                ),
                AccountDeletionJob(
                    account_id=None,
                    community_actor_id=None,
                    state="completed",
                    attempt_count=1,
                    next_attempt_at=api.clock.now - timedelta(days=31),
                    created_at=api.clock.now - timedelta(days=31),
                    updated_at=api.clock.now - timedelta(days=31),
                    completed_at=api.clock.now - timedelta(days=31),
                ),
            ]
        )

    counts = api.service.prune_expired_private_state()
    assert counts == {
        # The expired synthetic pending row plus the completion marker whose
        # deliberately expired session can no longer be replayed safely.
        "pending_signups": 2,
        "reauth_challenges": 1,
        "security_events": 1,
        "completed_deletion_jobs": 1,
        "sessions": 1,
    }
    with api.service.repository.transaction() as transaction:
        assert transaction.get(Session, first_session_id) is None
        assert transaction.get(Session, second_session_id) is not None
        assert transaction.scalar(
            select(RefreshToken).where(RefreshToken.session_id == first_session_id)
        ) is None
        assert transaction.scalar(
            select(RefreshToken).where(RefreshToken.session_id == second_session_id)
        ) is not None
        assert transaction.scalar(select(AccountDeletionJob)) is None


def test_internal_introspection_requires_service_secret_and_live_session(api: ApiHarness):
    tokens = _signup(api)
    internal_token = api.client.app.state.internal_service_token
    headers = {
        "Authorization": f"Bearer {tokens['access_token']}",
        "X-Internal-Service-Token": internal_token,
    }

    denied = api.client.post(
        "/v1/internal/auth/verify",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
        json={},
    )
    assert denied.status_code == 401

    verified = api.client.post("/v1/internal/auth/verify", headers=headers, json={})
    assert verified.status_code == 200
    assert set(verified.json()) == {
        "session_id",
        "actor_id",
        "nickname",
        "authorization_version",
    }
    assert tokens["access_token"] not in verified.text

    logout = api.client.post(
        "/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}
    )
    assert logout.status_code == 204
    revoked = api.client.post("/v1/internal/auth/verify", headers=headers, json={})
    assert revoked.status_code == 401
    assert _error(revoked) == "session_revoked"


def test_internal_opinion_notification_resolves_tokens_without_returning_them(
    api: ApiHarness,
):
    tokens = _signup(api)
    fcm_token = "synthetic-fcm-token-private-resolution-12345"
    registered = api.client.put(
        "/v1/push/device",
        headers=api.headers(tokens["access_token"]),
        json={"fcm_token": fcm_token, "platform": "android"},
    )
    assert registered.status_code == 200
    delivered: list[tuple[list[str], dict[str, str]]] = []
    api.service.notification_sink = lambda targets, payload: (
        delivered.append((targets, payload)) or len(targets)
    )
    current = api.service.authenticate(tokens["access_token"])
    response = api.client.post(
        "/v1/internal/notifications/opinion",
        headers={"X-Internal-Service-Token": api.client.app.state.internal_service_token},
        json={
            "actor_id": str(current.community_actor_id),
            "scan_id": "synthetic-scan",
            "disease": "leaf_spot",
            "note": "synthetic note",
            "nickname": "Synthetic Responder",
        },
    )
    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "target_count": 1}
    assert fcm_token not in response.text
    assert delivered == [
        (
            [fcm_token],
            {
                "type": "opinion",
                "community_actor_id": str(current.community_actor_id),
                "scan_id": "synthetic-scan",
                "disease": "leaf_spot",
                "note": "synthetic note",
                "nickname": "Synthetic Responder",
            },
        )
    ]


def test_corrupt_push_ciphertext_is_disabled_without_aborting_notification(
    api: ApiHarness,
):
    tokens = _signup(api)
    assert api.client.put(
        "/v1/push/device",
        headers=api.headers(tokens["access_token"]),
        json={"fcm_token": "synthetic-corrupt-target-token", "platform": "android"},
    ).status_code == 200
    current = api.service.authenticate(tokens["access_token"])
    with api.service.repository.transaction() as transaction:
        device = transaction.scalar(select(PushDevice))
        assert device is not None
        device.token_ciphertext = b"x" * 40

    response = api.client.post(
        "/v1/internal/notifications/opinion",
        headers={"X-Internal-Service-Token": api.client.app.state.internal_service_token},
        json={
            "actor_id": str(current.community_actor_id),
            "scan_id": "synthetic-scan",
            "disease": "leaf_spot",
            "note": "synthetic note",
            "nickname": "Synthetic Responder",
        },
    )
    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "target_count": 0}
    with api.service.repository.transaction() as transaction:
        device = transaction.scalar(select(PushDevice))
        assert device is not None
        assert device.disabled_at is not None


def test_successful_community_tombstone_completes_private_account_erasure(
    api: ApiHarness,
):
    delivered: list[uuid.UUID] = []
    api.service.deletion_dispatcher = lambda actor: delivered.append(actor) or True
    tokens = _signup(api)
    current = api.service.authenticate(tokens["access_token"])
    reauth = api.client.post(
        "/v1/auth/reauth",
        headers=api.headers(tokens["access_token"]),
        json={
            "provider": "yandex",
            "provider_access_token": "mock-yandex-new",
            "scope": "deletion",
        },
    )
    response = api.client.request(
        "DELETE",
        "/v1/me",
        headers=api.headers(tokens["access_token"]),
        json={"reauth_token": reauth.json()["reauth_token"]},
    )
    assert response.status_code == 202
    assert delivered == [current.community_actor_id]
    with api.service.repository.transaction() as transaction:
        assert transaction.scalar(select(Account)) is None
        assert transaction.scalar(select(AccountNicknameClaim)) is None
        assert transaction.scalar(select(PushDevice)) is None
        job = transaction.scalar(select(AccountDeletionJob))
        assert job is not None
        assert job.state == "completed"
        assert job.account_id is None
        assert job.community_actor_id is None


def test_deletion_stays_accepted_when_immediate_worker_crashes(
    api: ApiHarness, monkeypatch,
):
    tokens = _signup(api)
    current = api.service.authenticate(tokens["access_token"])
    reauth = api.client.post(
        "/v1/auth/reauth",
        headers=api.headers(tokens["access_token"]),
        json={
            "provider": "yandex",
            "provider_access_token": "mock-yandex-new",
            "scope": "deletion",
        },
    )
    monkeypatch.setattr(
        api.service,
        "process_deletion_jobs",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("synthetic worker failure")),
    )

    response = api.client.request(
        "DELETE",
        "/v1/me",
        headers=api.headers(tokens["access_token"]),
        json={"reauth_token": reauth.json()["reauth_token"]},
    )

    assert response.status_code == 202
    assert response.json() == {"status": "deletion_pending"}
    with api.service.repository.transaction() as transaction:
        account = transaction.get(Account, current.account_id)
        job = transaction.scalar(select(AccountDeletionJob))
        assert account is not None and account.state == "deletion_pending"
        assert job is not None and job.state == "pending"
