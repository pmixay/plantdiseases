from __future__ import annotations

import stat
from pathlib import Path

import pytest

from app.config import Settings
from app.generate_test_keys import ensure_test_keys
from app.security.push_crypto import PushTokenProtector
from app.security.tokens import SigningKeyRing


def test_generator_creates_private_valid_keys_and_never_overwrites(tmp_path: Path):
    directory = tmp_path / "account-keys"
    signing_path, encryption_path = ensure_test_keys(directory, environment="test")
    first_signing = signing_path.read_bytes()
    first_encryption = encryption_path.read_bytes()

    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(signing_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(encryption_path.stat().st_mode) == 0o600
    assert SigningKeyRing.from_file(signing_path).jwks()["keys"][0]["kid"] == "local-beta-v1"

    ensure_test_keys(directory, environment="test")
    assert signing_path.read_bytes() == first_signing
    assert encryption_path.read_bytes() == first_encryption

    settings = Settings.from_env(
        {
            "ENVIRONMENT": "test",
            "AUTH_PROVIDER_MODE": "mock",
            "ACCOUNT_ISSUER": "http://beta-account-api:8000",
            "ACCOUNT_AUDIENCE": "plantdiseases-app",
            "ACCOUNT_DATABASE_URL": (
                "postgresql+psycopg://beta:synthetic@beta-postgres/plantdisease_beta"
            ),
            "ACCOUNT_SIGNING_KEY_FILE": str(signing_path),
            "ACCOUNT_ENCRYPTION_KEY_FILE": str(encryption_path),
        }
    )
    first_process = PushTokenProtector.from_settings(settings)
    ciphertext = first_process.encrypt("synthetic-fcm-token-persistence-check")
    restarted_process = PushTokenProtector.from_settings(settings)
    assert restarted_process.decrypt(ciphertext) == "synthetic-fcm-token-persistence-check"


@pytest.mark.parametrize("environment", ["", "development", "staging", "production"])
def test_generator_refuses_every_non_test_environment(tmp_path: Path, environment: str):
    with pytest.raises(RuntimeError, match="only in ENVIRONMENT=test"):
        ensure_test_keys(tmp_path / "keys", environment=environment)


def test_generator_rejects_relaxed_existing_key_permissions(tmp_path: Path):
    directory = tmp_path / "keys"
    signing_path, _ = ensure_test_keys(directory, environment="test")
    signing_path.chmod(0o640)

    with pytest.raises(RuntimeError, match="Unsafe generated key file"):
        ensure_test_keys(directory, environment="test")


def test_generator_refuses_symlinked_key_files(tmp_path: Path):
    directory = tmp_path / "keys"
    directory.mkdir()
    outside = tmp_path / "outside-signing.json"
    outside.write_text("synthetic-outside-file", encoding="utf-8")
    outside.chmod(0o600)
    (directory / "signing.json").symlink_to(outside)

    with pytest.raises(RuntimeError, match="Unsafe generated key file"):
        ensure_test_keys(directory, environment="test")
