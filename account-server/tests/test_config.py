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
        "YANDEX_CLIENT_ID": "yandex-public-client",
        "VKID_CLIENT_ID": "12345",
    }
    with pytest.raises(SettingsError, match="must use HTTPS"):
        Settings.from_env(env)


def test_production_requires_community_deletion_url(tmp_path: Path):
    env = _production_env(tmp_path)
    env.pop("COMMUNITY_DELETION_URL")
    with pytest.raises(SettingsError, match="requires COMMUNITY_DELETION_URL"):
        Settings.from_env(env)


def test_production_rejects_http_public_community_deletion_url(tmp_path: Path):
    env = _production_env(tmp_path)
    env["COMMUNITY_DELETION_URL"] = (
        "http://app.example.invalid/api/internal/account-deletions"
    )
    with pytest.raises(SettingsError, match="private service hostname"):
        Settings.from_env(env)


def test_production_accepts_fixed_path_on_explicit_private_http_service(
    tmp_path: Path,
):
    env = _production_env(tmp_path)
    env["COMMUNITY_DELETION_URL"] = (
        "http://ru-app-api:8000/api/internal/account-deletions"
    )

    settings = Settings.from_env(env)

    assert settings.community_deletion_url.startswith("http://ru-app-api:8000/")


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8000/api/internal/account-deletions",
        "http://2130706433:8000/api/internal/account-deletions",
        "http://ru-app-api:8000/api/internal/other",
        "http://ru-app-api/api/internal/account-deletions",
    ],
)
def test_production_private_http_deletion_url_is_narrowly_scoped(
    tmp_path: Path, url: str,
):
    env = _production_env(tmp_path)
    env["COMMUNITY_DELETION_URL"] = url
    with pytest.raises(SettingsError):
        Settings.from_env(env)


def test_community_deletion_url_rejects_query_parameters():
    env = _base_env() | {
        "COMMUNITY_DELETION_URL": (
            "http://beta-app-api:8000/api/internal/account-deletions?token=unsafe"
        )
    }
    with pytest.raises(SettingsError, match="absolute safe URL"):
        Settings.from_env(env)


def test_production_rejects_unencrypted_remote_postgres(tmp_path: Path):
    env = _production_env(tmp_path)
    env["ACCOUNT_DATABASE_URL"] = "postgresql+psycopg://account:secret@db.invalid/accounts"
    with pytest.raises(SettingsError, match="must use TLS"):
        Settings.from_env(env)


def test_database_local_flag_only_accepts_internal_service_hostnames(tmp_path: Path):
    env = _production_env(tmp_path) | {
        "ACCOUNT_DATABASE_URL": (
            "postgresql+psycopg://account:secret@db.example.invalid/accounts"
        ),
        "ACCOUNT_DATABASE_LOCAL": "true",
    }
    with pytest.raises(SettingsError, match="single-label"):
        Settings.from_env(env)

    env["ACCOUNT_DATABASE_URL"] = (
        "postgresql+psycopg://account:secret@2130706433/accounts"
    )
    with pytest.raises(SettingsError, match="single-label"):
        Settings.from_env(env)

    env["ACCOUNT_DATABASE_URL"] = (
        "postgresql+psycopg://account:secret@ru-postgres/accounts"
    )
    assert Settings.from_env(env).database_is_local is True


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
    with pytest.raises(SettingsError, match="between 1 and 15"):
        Settings.from_env(_base_env() | {"PROVIDER_HTTP_TIMEOUT_SECONDS": "30"})


def test_remote_provider_mode_accepts_each_public_application_id_independently():
    remote = _base_env() | {"AUTH_PROVIDER_MODE": "remote"}
    with pytest.raises(SettingsError, match="YANDEX_CLIENT_ID or VKID_CLIENT_ID"):
        Settings.from_env(remote)

    remote["YANDEX_CLIENT_ID"] = "yandex-public-client"
    assert Settings.from_env(remote).yandex_client_id == "yandex-public-client"

    remote.pop("YANDEX_CLIENT_ID")
    remote["VKID_CLIENT_ID"] = "12345"
    assert Settings.from_env(remote).vk_client_id == "12345"

    remote["VKID_CLIENT_ID"] = "not-numeric"
    with pytest.raises(SettingsError, match="decimal application ID"):
        Settings.from_env(remote)


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


def test_internal_service_token_may_be_group_readable_but_not_group_writable(
    tmp_path: Path,
):
    env = _production_env(tmp_path)
    internal_token = Path(env["INTERNAL_SERVICE_TOKEN_FILE"])
    internal_token.chmod(0o640)
    assert Settings.from_env(env).internal_service_token_file == internal_token

    internal_token.chmod(0o660)
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
        "YANDEX_CLIENT_ID": "yandex-public-client",
        "VKID_CLIENT_ID": "12345",
        "ACCOUNT_ISSUER": "https://id.example.invalid",
        "ACCOUNT_AUDIENCE": "plantdiseases-app",
        # Loopback means the DB is on the same host; a remote hostname requires
        # sslmode=require/verify-* unless ACCOUNT_DATABASE_LOCAL is explicit.
        "ACCOUNT_DATABASE_URL": "postgresql+psycopg://account:secret@localhost/accounts",
        "ACCOUNT_SIGNING_KEY_FILE": _secret(tmp_path / "signing.key"),
        "ACCOUNT_ENCRYPTION_KEY_FILE": _secret(tmp_path / "encryption.key"),
        "INTERNAL_SERVICE_TOKEN_FILE": _secret(tmp_path / "internal.token"),
        "COMMUNITY_DELETION_URL": (
            "https://app.example.invalid/api/internal/account-deletions"
        ),
        "CORS_ORIGINS": "https://app.example.invalid",
    }
