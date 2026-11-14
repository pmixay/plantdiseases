from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.db.models import Account, AccountIdentity, RefreshToken, Session
from app.db.repository import AccountRepository
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker


def _repository(engine: Engine) -> AccountRepository:
    factory = sessionmaker(bind=engine, class_=OrmSession, expire_on_commit=False)
    return AccountRepository(factory)


def _account(nickname: str, normalized: str) -> Account:
    return Account(
        nickname=nickname,
        normalized_nickname=normalized,
        primary_provider="yandex",
        primary_provider_display_name=f"Synthetic profile {nickname}",
        primary_provider_avatar_url="https://profiles.invalid/yandex/avatar.png",
    )


def _session(account_id: uuid.UUID) -> Session:
    now = datetime.now(UTC)
    return Session(
        account_id=account_id,
        installation_id=uuid.uuid4(),
        last_used_at=now,
        idle_expires_at=now + timedelta(days=30),
        absolute_expires_at=now + timedelta(days=180),
    )


def test_transaction_commits_and_rolls_back(db_engine: Engine):
    repository = _repository(db_engine)
    with repository.transaction() as transaction:
        transaction.add(_account("SyntheticOne", "syntheticone"))

    with repository.transaction() as transaction:
        assert transaction.scalar(select(Account.nickname)) == "SyntheticOne"

    with pytest.raises(RuntimeError, match="synthetic rollback"):
        with repository.transaction() as transaction:
            transaction.add(_account("SyntheticTwo", "synthetictwo"))
            raise RuntimeError("synthetic rollback")

    with repository.transaction() as transaction:
        names = set(transaction.scalars(select(Account.nickname)))
        assert names == {"SyntheticOne"}


def test_normalized_nickname_and_actor_are_database_unique(db_engine: Engine):
    repository = _repository(db_engine)
    actor_id = uuid.uuid4()
    with repository.transaction() as transaction:
        first = _account("SyntheticOne", "collision")
        first.community_actor_id = actor_id
        transaction.add(first)

    with pytest.raises(IntegrityError):
        with repository.transaction() as transaction:
            transaction.add(_account("SyntheticTwo", "collision"))

    with pytest.raises(IntegrityError):
        with repository.transaction() as transaction:
            duplicate_actor = _account("SyntheticThree", "syntheticthree")
            duplicate_actor.community_actor_id = actor_id
            transaction.add(duplicate_actor)


def test_provider_subject_and_account_provider_are_database_unique(db_engine: Engine):
    repository = _repository(db_engine)
    with repository.transaction() as transaction:
        first = _account("SyntheticOne", "syntheticone")
        second = _account("SyntheticTwo", "synthetictwo")
        transaction.add_all([first, second])
        transaction.flush()
        transaction.add(
            AccountIdentity(
                account_id=first.id,
                provider="yandex",
                provider_subject="yandex-test-subject-001",
                display_name="Synthetic Yandex One",
                avatar_url="https://profiles.invalid/yandex/001.png",
                refreshed_at=datetime.now(UTC),
            )
        )

    with pytest.raises(IntegrityError):
        with repository.transaction() as transaction:
            second_id = transaction.scalar(
                select(Account.id).where(Account.normalized_nickname == "synthetictwo")
            )
            transaction.add(
                AccountIdentity(
                    account_id=second_id,
                    provider="yandex",
                    provider_subject="yandex-test-subject-001",
                    display_name="Synthetic Duplicate",
                    refreshed_at=datetime.now(UTC),
                )
            )

    with pytest.raises(IntegrityError):
        with repository.transaction() as transaction:
            first_id = transaction.scalar(
                select(Account.id).where(Account.normalized_nickname == "syntheticone")
            )
            transaction.add(
                AccountIdentity(
                    account_id=first_id,
                    provider="yandex",
                    provider_subject="yandex-test-subject-002",
                    display_name="Synthetic Second Identity",
                    refreshed_at=datetime.now(UTC),
                )
            )


def test_hash_length_checks_fail_closed(db_engine: Engine):
    repository = _repository(db_engine)
    now = datetime.now(UTC)
    with pytest.raises(IntegrityError):
        with repository.transaction() as transaction:
            account = _account("SyntheticOne", "syntheticone")
            transaction.add(account)
            transaction.flush()
            session = _session(account.id)
            transaction.add(session)
            transaction.flush()
            transaction.add(
                RefreshToken(
                    session_id=session.id,
                    token_hash=b"too-short",
                    issued_at=now,
                    idle_expires_at=now + timedelta(days=30),
                    absolute_expires_at=now + timedelta(days=180),
                )
            )
