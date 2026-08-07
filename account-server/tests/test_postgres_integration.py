from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.db.database import Database
from app.db.models import Account, RefreshToken
from app.generate_test_keys import ensure_test_keys
from app.main import create_app
from app.security.tokens import hash_opaque_token

POSTGRES_INTEGRATION_URL = os.environ.get("ACCOUNT_POSTGRES_INTEGRATION_URL", "")

pytestmark = pytest.mark.skipif(
    not POSTGRES_INTEGRATION_URL,
    reason="set ACCOUNT_POSTGRES_INTEGRATION_URL for the isolated beta PostgreSQL test",
)


def _settings(key_directory: Path) -> Settings:
    return Settings.from_env(
        {
            "ENVIRONMENT": "test",
            "AUTH_PROVIDER_MODE": "mock",
            "ACCOUNT_ISSUER": "http://127.0.0.1:18001",
            "ACCOUNT_AUDIENCE": "plantdiseases-app",
            "ACCOUNT_DATABASE_URL": POSTGRES_INTEGRATION_URL,
            "ACCOUNT_DATABASE_LOCAL": "true",
            "ACCOUNT_ALLOW_EPHEMERAL_KEYS": "false",
            "ACCOUNT_SIGNING_KEY_FILE": str(key_directory / "signing.json"),
            "ACCOUNT_ENCRYPTION_KEY_FILE": str(key_directory / "encryption.key"),
            "ACCOUNT_RATE_LIMIT_REQUESTS": "1000",
        }
    )


def test_postgres_signup_rotation_and_process_restart_recovery(tmp_path: Path):
    key_directory = tmp_path / "keys"
    ensure_test_keys(key_directory, environment="test")
    settings = _settings(key_directory)
    fixture_id = uuid.uuid4().hex
    provider_token = f"mock-yandex-generated-{fixture_id}"
    nickname = f"PgLeaf{fixture_id[:12]}"
    installation_id = str(uuid.uuid4())

    first_database = Database(settings.database_url)
    with TestClient(create_app(settings, database=first_database)) as first_process:
        exchange = first_process.post(
            "/v1/auth/yandex/exchange",
            json={
                "provider_access_token": provider_token,
                "installation_id": installation_id,
            },
        )
        assert exchange.status_code == 200
        signup = first_process.post(
            "/v1/auth/signup/complete",
            json={
                "pending_signup_token": exchange.json()["pending_signup_token"],
                "nickname": nickname,
                "installation_id": installation_id,
            },
        )
        assert signup.status_code == 201
        first_jwks = first_process.get("/.well-known/jwks.json").json()
        refresh_token = signup.json()["refresh_token"]

    second_database = Database(settings.database_url)
    with TestClient(create_app(settings, database=second_database)) as restarted_process:
        assert restarted_process.get("/.well-known/jwks.json").json() == first_jwks
        recovered = restarted_process.post(
            "/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert recovered.status_code == 200
        profile = restarted_process.get(
            "/v1/me",
            headers={"Authorization": f"Bearer {recovered.json()['access_token']}"},
        )
        assert profile.status_code == 200
        assert profile.json()["nickname"] == nickname

        with second_database.session_factory() as transaction:
            account = transaction.scalar(
                select(Account).where(Account.normalized_nickname == nickname.casefold())
            )
            rotated = transaction.scalar(
                select(RefreshToken).where(
                    RefreshToken.token_hash
                    == hash_opaque_token(recovered.json()["refresh_token"])
                )
            )
            assert account is not None
            assert rotated is not None
