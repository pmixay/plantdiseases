from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.errors import AccountAPIError
from app.api.routes import _require_email_password_auth
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


def test_email_password_auth_is_fail_closed_and_beta_only(tmp_path: Path):
    disabled = Settings.from_env(_base_env())
    enabled = Settings.from_env(
        _base_env() | {"EMAIL_PASSWORD_AUTH_ENABLED": "true"}
    )
    assert disabled.email_password_auth_enabled is False
    assert enabled.email_password_auth_enabled is True

    disabled_request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=disabled))
    )
    enabled_request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=enabled))
    )
    with pytest.raises(AccountAPIError, match="email_auth_unavailable"):
        _require_email_password_auth(disabled_request)
    _require_email_password_auth(enabled_request)

    production = _production_env(tmp_path)
    production["EMAIL_PASSWORD_AUTH_ENABLED"] = "true"
    with pytest.raises(SettingsError, match="blocked in production"):
        Settings.from_env(production)


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


def test_rustore_billing_is_off_by_default_and_requires_complete_secret_config(
    tmp_path: Path,
):
    assert Settings.from_env(_base_env()).rustore_billing_enabled is False
    enabled = _base_env() | {"RUSTORE_BILLING_ENABLED": "true"}
    with pytest.raises(SettingsError, match="RUSTORE_APPLICATION_ID"):
        Settings.from_env(enabled)

    enabled |= {
        "RUSTORE_APPLICATION_ID": "12345",
        "RUSTORE_KEY_ID": "67890",
        "RUSTORE_PRIVATE_KEY_FILE": _secret(tmp_path / "rustore-private.b64"),
        "RUSTORE_NOTIFICATION_KEY_FILE": _secret(tmp_path / "rustore-aes.b64"),
    }
    settings = Settings.from_env(enabled)
    assert settings.rustore_billing_enabled is True
    assert settings.rustore_lifecycle_enabled is True
    assert settings.rustore_checkout_enabled is True
    assert settings.rustore_products == (
        "plantdiseases_pro_monthly",
        "plantdiseases_pro_yearly",
    )


def test_rustore_checkout_can_stop_while_lifecycle_configuration_remains_active(
    tmp_path: Path,
):
    env = _base_env() | {
        "RUSTORE_LIFECYCLE_ENABLED": "true",
        "RUSTORE_CHECKOUT_ENABLED": "false",
        "RUSTORE_APPLICATION_ID": "12345",
        "RUSTORE_KEY_ID": "67890",
        "RUSTORE_PRIVATE_KEY_FILE": _secret(tmp_path / "rustore-private.b64"),
        "RUSTORE_NOTIFICATION_KEY_FILE": _secret(tmp_path / "rustore-aes.b64"),
        "RUSTORE_NOTIFICATION_KEY_EPOCH": "staging-rotation-2",
    }

    settings = Settings.from_env(env)

    assert settings.rustore_lifecycle_enabled is True
    assert settings.rustore_checkout_enabled is False
    assert settings.rustore_billing_enabled is False
    assert settings.rustore_notification_key_epoch == "staging-rotation-2"


def test_rustore_checkout_requires_lifecycle_and_legacy_switch_cannot_be_mixed():
    with pytest.raises(SettingsError, match="requires RUSTORE_LIFECYCLE_ENABLED"):
        Settings.from_env(
            _base_env()
            | {
                "RUSTORE_LIFECYCLE_ENABLED": "false",
                "RUSTORE_CHECKOUT_ENABLED": "true",
            }
        )
    with pytest.raises(SettingsError, match="accepted only in test/development"):
        Settings.from_env(
            _base_env()
            | {
                "ENVIRONMENT": "staging",
                "RUSTORE_BILLING_ENABLED": "false",
            }
        )
    with pytest.raises(SettingsError, match="cannot be mixed"):
        Settings.from_env(
            _base_env()
            | {
                "RUSTORE_BILLING_ENABLED": "false",
                "RUSTORE_LIFECYCLE_ENABLED": "true",
            }
        )


def test_entitlement_quota_policy_is_exact_versioned_and_operator_configurable():
    settings = Settings.from_env(
        _base_env()
        | {
            "ENTITLEMENT_QUOTA_POLICY_VERSION": "cohort-b-v2",
            "ENTITLEMENT_FREE_DAILY_LIMITS": (
                "gamma_analysis=2,frontier_analysis=0,plant_identification=1,"
                "ai_doctor=2,care_plan=4"
            ),
            "ENTITLEMENT_PRO_DAILY_LIMITS": (
                "gamma_analysis=20,frontier_analysis=2,plant_identification=4,"
                "ai_doctor=12,care_plan=16"
            ),
        }
    )

    assert settings.entitlement_quota_policy_version == "cohort-b-v2"
    assert settings.entitlement_free_daily_limits == (2, 0, 1, 2, 4)
    assert settings.entitlement_pro_daily_limits == (20, 2, 4, 12, 16)

    with pytest.raises(SettingsError, match="each supported feature exactly once"):
        Settings.from_env(
            _base_env()
            | {"ENTITLEMENT_FREE_DAILY_LIMITS": "gamma_analysis=5"}
        )


def test_rustore_configuration_rejects_package_product_and_environment_mixups(
    tmp_path: Path,
):
    enabled = _base_env() | {
        "RUSTORE_BILLING_ENABLED": "true",
        "RUSTORE_APPLICATION_ID": "12345",
        "RUSTORE_KEY_ID": "67890",
        "RUSTORE_PRIVATE_KEY_FILE": _secret(tmp_path / "rustore-private.b64"),
        "RUSTORE_NOTIFICATION_KEY_FILE": _secret(tmp_path / "rustore-aes.b64"),
    }
    with pytest.raises(SettingsError, match="RUSTORE_PACKAGE_NAME"):
        Settings.from_env(enabled | {"RUSTORE_PACKAGE_NAME": "com.other.app"})
    with pytest.raises(SettingsError, match="SUBSCRIPTION_PRODUCTS"):
        Settings.from_env(
            enabled
            | {
                "RUSTORE_SUBSCRIPTION_PRODUCTS": (
                    "plantdiseases_pro_monthly,plantdiseases_pro_monthly"
                )
            }
        )
    for invalid_application_id in ("0", "١٢٣", "1" * 20):
        with pytest.raises(SettingsError, match="RUSTORE_APPLICATION_ID"):
            Settings.from_env(
                enabled | {"RUSTORE_APPLICATION_ID": invalid_application_id}
            )
    for invalid_key_id in ("0", "１２３", "1" * 33):
        with pytest.raises(SettingsError, match="RUSTORE_KEY_ID"):
            Settings.from_env(enabled | {"RUSTORE_KEY_ID": invalid_key_id})

    explicit_enabled = {
        key: value
        for key, value in enabled.items()
        if key != "RUSTORE_BILLING_ENABLED"
    } | {
        "RUSTORE_LIFECYCLE_ENABLED": "true",
        "RUSTORE_CHECKOUT_ENABLED": "true",
    }
    production = _production_env(tmp_path) | explicit_enabled | {
        "ENVIRONMENT": "production",
        "ACCOUNT_ISSUER": "https://id.example.invalid",
        "RUSTORE_ALLOW_SANDBOX": "true",
    }
    with pytest.raises(SettingsError, match="forbidden in production"):
        Settings.from_env(production)


def test_plantnet_budget_is_disabled_by_default_and_requires_versioned_dual_caps():
    assert Settings.from_env(_base_env()).plantnet_budget_enabled is False
    enabled = _base_env() | {"PLANTNET_BUDGET_ENABLED": "true"}
    with pytest.raises(SettingsError, match="POLICY_VERSION"):
        Settings.from_env(enabled)

    enabled |= {
        "PLANTNET_BUDGET_POLICY_VERSION": "plantnet-pro-2026-08",
        "PLANTNET_DAILY_ATTEMPT_LIMIT": "100",
        "PLANTNET_ATTEMPT_COST_MICROS": "5000",
        "PLANTNET_DAILY_COST_LIMIT_MICROS": "500000",
    }
    settings = Settings.from_env(enabled)
    assert settings.plantnet_budget_enabled is True
    assert settings.plantnet_budget_policy_version == "plantnet-pro-2026-08"
    assert settings.plantnet_daily_attempt_limit == 100
    assert settings.plantnet_attempt_cost_micros == 5_000
    assert settings.plantnet_daily_cost_limit_micros == 500_000


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("PLANTNET_BUDGET_POLICY_VERSION", "bad version", "POLICY_VERSION"),
        ("PLANTNET_DAILY_ATTEMPT_LIMIT", "0", "ATTEMPT_LIMIT"),
        ("PLANTNET_ATTEMPT_COST_MICROS", "0", "ATTEMPT_COST"),
        ("PLANTNET_DAILY_COST_LIMIT_MICROS", "0", "COST_LIMIT"),
        ("PLANTNET_ATTEMPT_COST_MICROS", "500001", "must not exceed"),
    ],
)
def test_plantnet_budget_rejects_unversioned_or_nonpositive_policy(
    name: str,
    value: str,
    message: str,
):
    enabled = _base_env() | {
        "PLANTNET_BUDGET_ENABLED": "true",
        "PLANTNET_BUDGET_POLICY_VERSION": "plantnet-pro-2026-08",
        "PLANTNET_DAILY_ATTEMPT_LIMIT": "100",
        "PLANTNET_ATTEMPT_COST_MICROS": "5000",
        "PLANTNET_DAILY_COST_LIMIT_MICROS": "500000",
        name: value,
    }
    with pytest.raises(SettingsError, match=message):
        Settings.from_env(enabled)


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
