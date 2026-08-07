from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Iterator

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from app.api.errors import AccountAPIError
from app.config import Settings
from app.db.models import (
    Account,
    AccountDeletionJob,
    AccountIdentity,
    AccountSecurityEvent,
    PendingSignup,
    PushDevice,
    ReauthChallenge,
    RefreshToken,
    Session,
)
from app.db.repository import AccountRepository
from app.main import create_app
from app.providers.mock import build_provider_registry
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
    complete = api.client.post(
        "/v1/auth/signup/complete",
        json={
            "pending_signup_token": exchange.json()["pending_signup_token"],
            "nickname": nickname,
            "installation_id": str(installation),
        },
    )
    assert complete.status_code == 201, complete.text
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

    completed = api.client.post(
        "/v1/auth/signup/complete",
        json={
            "pending_signup_token": pending_token,
            "nickname": "SyntheticLeaf",
            "installation_id": str(installation_id),
        },
    )
    assert completed.status_code == 201
    tokens = completed.json()
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
    exchange = _exchange(api, provider="yandex", provider_token="mock-yandex-new")
    pending_token = exchange.json()["pending_signup_token"]
    cancelled = api.client.post(
        "/v1/auth/signup/cancel",
        json={"pending_signup_token": pending_token},
    )
    assert cancelled.status_code == 204
    replay = api.client.post(
        "/v1/auth/signup/complete",
        json={
            "pending_signup_token": pending_token,
            "nickname": "SyntheticLeaf",
            "installation_id": str(uuid.uuid4()),
        },
    )
    assert replay.status_code == 409
    assert _error(replay) == "pending_signup_replayed"

    replacement = _exchange(api, provider="yandex", provider_token="mock-yandex-new")
    api.clock.advance(timedelta(minutes=11))
    expired = api.client.post(
        "/v1/auth/signup/complete",
        json={
            "pending_signup_token": replacement.json()["pending_signup_token"],
            "nickname": "SyntheticLeaf",
            "installation_id": str(uuid.uuid4()),
        },
    )
    assert expired.status_code == 401
    assert _error(expired) == "pending_signup_expired"


def test_nickname_uniqueness_cooldown_and_authorization_version(api: ApiHarness):
    first = _signup(api, nickname="LeafOwner")
    second_exchange = _exchange(api, provider="vk", provider_token="mock-vk-new")
    duplicate = api.client.post(
        "/v1/auth/signup/complete",
        json={
            "pending_signup_token": second_exchange.json()["pending_signup_token"],
            "nickname": "leafowner",
            "installation_id": str(uuid.uuid4()),
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

    changed = api.client.patch(
        "/v1/me/nickname",
        headers=api.headers(access_token),
        json={"nickname": "NewLeafOwner"},
    )
    assert changed.status_code == 200
    assert changed.json()["nickname"] == "NewLeafOwner"
    new_access_token = changed.json()["access_token"]

    old_access = api.client.get("/v1/me", headers=api.headers(access_token))
    assert old_access.status_code == 401
    assert _error(old_access) == "session_revoked"
    assert api.client.get("/v1/me", headers=api.headers(new_access_token)).status_code == 200


def test_refresh_rotation_replay_revokes_the_token_family(api: ApiHarness):
    tokens = _signup(api)
    first_refresh = tokens["refresh_token"]
    rotated = api.client.post(
        "/v1/auth/refresh",
        json={"refresh_token": first_refresh},
    )
    assert rotated.status_code == 200
    replacement_refresh = rotated.json()["refresh_token"]
    assert replacement_refresh != first_refresh

    replay = api.client.post(
        "/v1/auth/refresh",
        json={"refresh_token": first_refresh},
    )
    assert replay.status_code == 401
    assert _error(replay) == "refresh_reused"

    family_revoked = api.client.post(
        "/v1/auth/refresh",
        json={"refresh_token": replacement_refresh},
    )
    assert family_revoked.status_code == 401
    assert _error(family_revoked) == "session_revoked"

    with api.service.repository.transaction() as transaction:
        account_session = transaction.scalar(select(Session))
        event = transaction.scalar(
            select(AccountSecurityEvent).where(
                AccountSecurityEvent.event_type == "refresh_reuse"
            )
        )
        assert account_session is not None and account_session.revoked_at is not None
        assert event is not None
        assert event.expires_at > event.created_at
        consumed = transaction.scalar(
            select(RefreshToken).where(
                RefreshToken.token_hash == hash_opaque_token(first_refresh)
            )
        )
        assert consumed is not None and consumed.replacement_token_id is not None


def test_refresh_idle_expiry_revokes_session(api: ApiHarness):
    tokens = _signup(api)
    api.clock.advance(timedelta(days=31))
    expired = api.client.post(
        "/v1/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
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
        "/v1/auth/logout", headers=api.headers(first["access_token"])
    )
    assert logged_out.status_code == 204
    first_denied = api.client.get("/v1/me", headers=api.headers(first["access_token"]))
    assert first_denied.status_code == 401
    assert _error(first_denied) == "session_revoked"


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


def test_linking_refuses_identity_owned_by_another_account(api: ApiHarness):
    owner = _signup(
        api,
        provider="vk",
        provider_token="mock-vk-owned-by-other",
        nickname="VkOwner",
    )
    assert owner["status"] == "authenticated"
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
        assert challenge is not None and challenge.consumed_at is not None
        assert event is not None

    denied = api.client.get("/v1/me", headers=headers)
    assert denied.status_code == 409
    assert _error(denied) == "deletion_pending"
    refresh_denied = api.client.post(
        "/v1/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert refresh_denied.status_code == 409
    assert _error(refresh_denied) == "deletion_pending"


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
            ]
        )

    counts = api.service.prune_expired_private_state()
    assert counts == {
        "pending_signups": 1,
        "reauth_challenges": 1,
        "security_events": 1,
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
