from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings, SettingsError


def _base_env() -> dict[str, str]:
    return {
        "ENVIRONMENT": "test",
        "AUTH_PROVIDER_MODE": "mock",
        "ACCOUNT_ISSUER": "http://beta-account-api:8000/",
        "ACCOUNT_AUDIENCE": "plantdiseases-app",
        "ACCOUNT_DATABASE_URL": (
            "postgresql+psycopg://beta:synthetic-test-only@beta-postgres/plantdisease_beta"
        ),
    }


def _secret(path: Path) -> str:
    path.write_text("synthetic-test-secret", encoding="utf-8")
    path.chmod(0o600)
    return str(path)


def test_required_settings_have_no_implicit_service_defaults():
    with pytest.raises(SettingsError, match="ENVIRONMENT"):
        Settings.from_env({})


def test_test_settings_normalize_urls_and_root_path():
    env = _base_env() | {
        "ACCOUNT_ROOT_PATH": "/plantdisease-test/id/",
        "CORS_ORIGINS": "https://one.invalid, https://two.invalid",
    }
    settings = Settings.from_env(env)
    assert settings.issuer == "http://beta-account-api:8000"
    assert settings.root_path == "/plantdisease-test/id"
    assert settings.cors_origins == ("https://one.invalid", "https://two.invalid")


@pytest.mark.parametrize("environment", ["prod", "qa", ""])
def test_unknown_or_blank_environment_is_rejected(environment: str):
    env = _base_env()
    env["ENVIRONMENT"] = environment
    with pytest.raises(SettingsError):
        Settings.from_env(env)


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite:///account.db",
        "postgresql://account:synthetic@localhost/accounts",
    ],
)
def test_non_psycopg_database_url_is_rejected(database_url: str):
    env = _base_env()
    env["ACCOUNT_DATABASE_URL"] = database_url
    with pytest.raises(SettingsError, match=r"postgresql\+psycopg"):
        Settings.from_env(env)


def test_mock_provider_is_rejected_in_production():
    env = _base_env() | {
        "ENVIRONMENT": "production",
        "ACCOUNT_ISSUER": "https://id.example.invalid",
    }
    with pytest.raises(SettingsError, match="mock is forbidden"):
        Settings.from_env(env)


def test_production_requires_https_issuer():
    env = _base_env() | {
        "ENVIRONMENT": "production",
        "AUTH_PROVIDER_MODE": "remote",
    }
    with pytest.raises(SettingsError, match="must use HTTPS"):
        Settings.from_env(env)


def test_production_rejects_unencrypted_remote_postgres(tmp_path: Path):
    env = _production_env(tmp_path)
    env["ACCOUNT_DATABASE_URL"] = "postgresql+psycopg://account:secret@db.invalid/accounts"
    with pytest.raises(SettingsError, match="must use TLS"):
        Settings.from_env(env)


def test_every_environment_rejects_wildcard_cors():
    env = _base_env() | {"CORS_ORIGINS": "*"}
    with pytest.raises(SettingsError, match="Wildcard CORS"):
        Settings.from_env(env)


def test_ephemeral_keys_are_rejected_outside_explicit_test_mode():
    env = _base_env() | {
        "ENVIRONMENT": "development",
        "ACCOUNT_ALLOW_EPHEMERAL_KEYS": "true",
    }
    with pytest.raises(SettingsError, match="only in test"):
        Settings.from_env(env)


def test_malformed_boolean_and_rate_limit_are_rejected():
    with pytest.raises(SettingsError, match="must be a boolean"):
        Settings.from_env(_base_env() | {"ACCOUNT_DATABASE_LOCAL": "sometimes"})
    with pytest.raises(SettingsError, match="between 1 and 10000"):
        Settings.from_env(_base_env() | {"ACCOUNT_RATE_LIMIT_REQUESTS": "0"})


def test_configured_test_secret_file_must_also_be_private(tmp_path: Path):
    signing_path = tmp_path / "signing.json"
    signing_path.write_text("synthetic-invalid-content-is-parsed-at-startup", encoding="utf-8")
    signing_path.chmod(0o644)
    with pytest.raises(SettingsError, match="group/world"):
        Settings.from_env(
            _base_env() | {"ACCOUNT_SIGNING_KEY_FILE": str(signing_path)}
        )


def test_production_requires_all_secret_files(tmp_path: Path):
    env = _production_env(tmp_path)
    env.pop("ACCOUNT_SIGNING_KEY_FILE")
    with pytest.raises(SettingsError, match="ACCOUNT_SIGNING_KEY_FILE"):
        Settings.from_env(env)


def test_production_rejects_group_readable_secret(tmp_path: Path):
    env = _production_env(tmp_path)
    signing_path = Path(env["ACCOUNT_SIGNING_KEY_FILE"])
    signing_path.chmod(0o640)
    with pytest.raises(SettingsError, match="group/world"):
        Settings.from_env(env)


def test_safe_production_settings_are_accepted(tmp_path: Path):
    settings = Settings.from_env(_production_env(tmp_path))
    assert settings.is_production is True
    assert settings.auth_provider_mode == "remote"


def _production_env(tmp_path: Path) -> dict[str, str]:
    return {
        "ENVIRONMENT": "production",
        "AUTH_PROVIDER_MODE": "remote",
        "ACCOUNT_ISSUER": "https://id.example.invalid",
        "ACCOUNT_AUDIENCE": "plantdiseases-app",
        # Loopback means the DB is on the same host; a remote hostname requires
        # sslmode=require/verify-* unless ACCOUNT_DATABASE_LOCAL is explicit.
        "ACCOUNT_DATABASE_URL": "postgresql+psycopg://account:secret@localhost/accounts",
        "ACCOUNT_SIGNING_KEY_FILE": _secret(tmp_path / "signing.key"),
        "ACCOUNT_ENCRYPTION_KEY_FILE": _secret(tmp_path / "encryption.key"),
        "INTERNAL_SERVICE_TOKEN_FILE": _secret(tmp_path / "internal.token"),
        "CORS_ORIGINS": "https://app.example.invalid",
    }
