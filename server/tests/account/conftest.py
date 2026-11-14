"""Synthetic, fail-closed settings shared by account-api tests."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

ACCOUNT_SERVER_DIR = Path(__file__).resolve().parents[3] / "account-server"
if str(ACCOUNT_SERVER_DIR) not in sys.path:
    sys.path.append(str(ACCOUNT_SERVER_DIR))

# Set per account test only: the app server reads ``ENVIRONMENT`` / ``ACCOUNT_ISSUER``
# itself with different expectations, so they must not leak into server tests.
_ACCOUNT_ENV = {
    "ENVIRONMENT": "test",
    "AUTH_PROVIDER_MODE": "mock",
    "ACCOUNT_ISSUER": "http://beta-account-api:8000",
    "ACCOUNT_AUDIENCE": "plantdiseases-app",
    "ACCOUNT_ALLOW_EPHEMERAL_KEYS": "true",
    "ACCOUNT_DATABASE_URL": (
        "postgresql+psycopg://beta:synthetic-test-only@beta-postgres/plantdisease_beta"
    ),
}


@pytest.fixture(scope="session")
def fast_password_hashes():
    """One Argon2id service with throwaway cost parameters for the whole run.

    ``PasswordHashService()`` hashes a dummy password at production cost
    (64 MiB, t=3, p=4) in its constructor, and every ``AccountService`` built
    without an explicit service constructs one — which made password hashing
    the single largest cost of the account tests although almost none of them
    are about passwords. The production parameters stay pinned by
    ``test_password_security.py``, which builds its own service from
    ``app.security.passwords`` and is not affected by the default below.
    """
    from app.security.passwords import PasswordHashService
    from argon2 import PasswordHasher
    from argon2.low_level import Type

    return PasswordHashService(
        PasswordHasher(
            time_cost=1,
            memory_cost=8,
            parallelism=1,
            hash_len=32,
            salt_len=16,
            type=Type.ID,
        )
    )


@pytest.fixture(autouse=True)
def _account_env(monkeypatch, fast_password_hashes):
    for key, value in _ACCOUNT_ENV.items():
        monkeypatch.setenv(key, value)
    # The default an AccountService falls back to when none is injected.
    import app.services.accounts as accounts

    monkeypatch.setattr(accounts, "PasswordHashService", lambda: fast_password_hashes)


def _enable_foreign_keys(connection: sqlite3.Connection, _record) -> None:
    connection.execute("PRAGMA foreign_keys=ON")


@pytest.fixture(scope="session")
def _schema_image() -> bytes:
    """The empty account schema, built once and handed to each test as a copy.

    ``create_all`` over the whole metadata costs ~10 ms; restoring a serialised
    in-memory database costs under 1 ms and gives every test the same private,
    empty database it had before.
    """
    from app.db.models import Base

    engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool)
    try:
        Base.metadata.create_all(engine)
        with engine.connect() as connection:
            return connection.connection.driver_connection.serialize()
    finally:
        engine.dispose()


@pytest.fixture
def db_engine(_schema_image: bytes) -> Engine:
    def _connect() -> sqlite3.Connection:
        connection = sqlite3.connect(":memory:", check_same_thread=False)
        connection.deserialize(_schema_image)
        return connection

    engine = create_engine(
        "sqlite+pysqlite://",
        creator=_connect,
        poolclass=StaticPool,
        hide_parameters=True,
    )
    event.listen(engine, "connect", _enable_foreign_keys)
    try:
        yield engine
    finally:
        engine.dispose()
