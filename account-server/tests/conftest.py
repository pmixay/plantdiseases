"""Synthetic, fail-closed settings shared by account-api tests."""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

ACCOUNT_SERVER_DIR = Path(__file__).resolve().parent.parent
if str(ACCOUNT_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(ACCOUNT_SERVER_DIR))

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("AUTH_PROVIDER_MODE", "mock")
os.environ.setdefault("ACCOUNT_ISSUER", "http://beta-account-api:8000")
os.environ.setdefault("ACCOUNT_AUDIENCE", "plantdiseases-app")
os.environ.setdefault("ACCOUNT_ALLOW_EPHEMERAL_KEYS", "true")
os.environ.setdefault(
    "ACCOUNT_DATABASE_URL",
    "postgresql+psycopg://beta:synthetic-test-only@beta-postgres/plantdisease_beta",
)


@pytest.fixture
def db_engine() -> Engine:
    from app.db.models import Base

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        hide_parameters=True,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection: sqlite3.Connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()
