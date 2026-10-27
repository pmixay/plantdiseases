from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from argon2 import PasswordHasher
from argon2.low_level import Type
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from app.api.errors import AccountAPIError
from app.db.models import Account, AccountIdentity, PasswordCredential
from app.db.repository import AccountRepository
from app.providers.mock import MockProviderAdapter
from app.rate_limit import InMemoryRateLimiter
from app.security.passwords import PasswordHashService
from app.security.push_crypto import PushTokenProtector
from app.security.tokens import SigningKeyRing, TokenService
from app.services.accounts import AccountService


@pytest.fixture
def email_service(db_engine: Engine) -> AccountService:
    factory = sessionmaker(
        bind=db_engine,
        class_=OrmSession,
        autoflush=False,
        expire_on_commit=False,
    )
    password_hasher = PasswordHasher(
        time_cost=1,
        memory_cost=8_192,
        parallelism=1,
        hash_len=32,
        salt_len=16,
        type=Type.ID,
    )
    return AccountService(
        repository=AccountRepository(factory),
        providers={
            "yandex": MockProviderAdapter("yandex", environment="test"),
            "vk": MockProviderAdapter("vk", environment="test"),
        },
        token_service=TokenService(
            issuer="http://beta-account-api:8000",
            audience="plantdiseases-app",
            key_ring=SigningKeyRing.ephemeral(),
        ),
        push_protector=PushTokenProtector(b"p" * 32),
        limiter=InMemoryRateLimiter(requests=1_000, window_seconds=60),
        password_hash_service=PasswordHashService(password_hasher),
        clock=lambda: datetime(2030, 1, 2, 3, 4, 5, tzinfo=UTC),
        deletion_dispatcher=lambda _actor_id: True,
    )


def _complete_email_account(
    service: AccountService,
    *,
    email: str,
    password: str,
) -> dict:
    installation_id = uuid.uuid4()
    pending = service.register_with_email(
        email=email,
        password=password,
        installation_id=installation_id,
    )
    completion_id = uuid.uuid4()
    completed = service.complete_signup(
        pending_token=pending["pending_signup_token"],
        completion_id=completion_id,
        nickname="EmailLeaf",
        installation_id=installation_id,
        email_password_auth_enabled=True,
    )
    service.confirm_signup(
        pending_token=pending["pending_signup_token"],
        completion_id=completion_id,
        installation_id=installation_id,
        email_password_auth_enabled=True,
    )
    return completed


def test_email_service_registration_login_link_and_deletion(email_service: AccountService):
    password = "correct horse garden staple"
    _complete_email_account(
        email_service,
        email="Garden.Fern@Example.COM",
        password=password,
    )

    with email_service.repository.transaction() as transaction:
        account = transaction.scalar(select(Account))
        credential = transaction.scalar(select(PasswordCredential))
        assert account is not None and account.primary_provider == "email"
        assert credential is not None and credential.account_id == account.id
        assert credential.password_hash.startswith("$argon2id$")
        assert list(transaction.scalars(select(AccountIdentity))) == []

    login = email_service.login_with_email(
        email="garden.fern@example.com",
        password=password,
        installation_id=uuid.uuid4(),
    )
    assert login["status"] == "authenticated"
    current = email_service.authenticate(login["access_token"])

    challenge = email_service.create_email_reauth_challenge(
        current,
        password=password,
        scope="link_vk",
    )
    asyncio.run(
        email_service.link_identity(
            current,
            target_provider="vk",
            provider_access_token="mock-vk-new",
            reauth_token=challenge["reauth_token"],
        )
    )
    assert {item["provider"] for item in email_service.get_me(current)["linked_providers"]} == {
        "email",
        "vk",
    }

    delete_challenge = email_service.create_email_reauth_challenge(
        current,
        password=password,
        scope="deletion",
    )
    assert email_service.request_deletion(
        current,
        reauth_token=delete_challenge["reauth_token"],
    ) == {"status": "deletion_pending"}
    with email_service.repository.transaction() as transaction:
        assert transaction.scalar(select(Account)) is None
        assert transaction.scalar(select(PasswordCredential)) is None


def test_email_login_errors_do_not_enumerate_accounts(email_service: AccountService):
    password = "correct horse garden staple"
    _complete_email_account(
        email_service,
        email="known@example.com",
        password=password,
    )

    errors: list[tuple[str, int]] = []
    for email, candidate in (
        ("known@example.com", "a long but incorrect garden password"),
        ("unknown@example.com", "a long but incorrect garden password"),
        ("not-an-address", "a long but incorrect garden password"),
    ):
        with pytest.raises(AccountAPIError) as caught:
            email_service.login_with_email(
                email=email,
                password=candidate,
                installation_id=uuid.uuid4(),
            )
        errors.append((caught.value.code, caught.value.status_code))

    assert errors == [("invalid_credentials", 401)] * 3


def test_email_login_restarts_unconfirmed_signup_on_new_installation(
    email_service: AccountService,
):
    password = "correct horse garden staple"
    first_installation = uuid.uuid4()
    pending = email_service.register_with_email(
        email="recover@example.com",
        password=password,
        installation_id=first_installation,
    )
    email_service.complete_signup(
        pending_token=pending["pending_signup_token"],
        completion_id=uuid.uuid4(),
        nickname="RecoverLeaf",
        installation_id=first_installation,
        email_password_auth_enabled=True,
    )

    recovered = email_service.login_with_email(
        email="recover@example.com",
        password=password,
        installation_id=uuid.uuid4(),
    )
    assert recovered["status"] == "profile_required"
    assert recovered["provider"] == "email"
    with email_service.repository.transaction() as transaction:
        assert transaction.scalar(select(Account)) is None
        assert transaction.scalar(select(PasswordCredential)) is None
