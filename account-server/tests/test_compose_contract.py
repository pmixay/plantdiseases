from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPOSITORY_ROOT / "deploy" / "account-beta-test" / "compose.local.yml"
INTEGRATION_CHECK_PATH = (
    REPOSITORY_ROOT / "deploy" / "account-beta-test" / "check_integration.py"
)
EXPECTED_SERVICES = {
    "beta-postgres",
    "beta-account-keygen",
    "beta-account-migrate",
    "beta-account-api",
    "beta-app-api",
}
POSTGRES_IMAGE = (
    "postgres:18.4-alpine3.23@"
    "sha256:996d0920e4ff9df1fc19dacb904492f3c1ec0ec1cc338f0ad7123be7731c5f5e"
)


def _load_compose() -> dict:
    with COMPOSE_PATH.open(encoding="utf-8") as compose_file:
        value = yaml.safe_load(compose_file)
    assert isinstance(value, dict)
    return value


def _mount_source(mount: str) -> str:
    return mount.split(":", maxsplit=1)[0]


def _mount_target(mount: str) -> str:
    return mount.split(":", maxsplit=2)[1]


def test_compose_has_only_isolated_beta_services_and_network():
    compose = _load_compose()

    assert compose["name"] == "${COMPOSE_PROJECT_NAME:-plantdisease_account_beta_test}"
    assert set(compose["services"]) == EXPECTED_SERVICES
    assert compose["networks"] == {
        "beta-private": {"internal": True},
        "beta-edge": {},
    }

    for service in compose["services"].values():
        assert "no-new-privileges:true" in service["security_opt"]

    services = compose["services"]
    assert services["beta-postgres"]["networks"] == ["beta-private"]
    assert services["beta-account-migrate"]["networks"] == ["beta-private"]
    assert services["beta-account-keygen"]["network_mode"] == "none"
    assert "networks" not in services["beta-account-keygen"]
    assert set(services["beta-account-api"]["networks"]) == {"beta-private", "beta-edge"}
    assert set(services["beta-app-api"]["networks"]) == {"beta-private", "beta-edge"}


def test_postgres_is_private_versioned_and_persistent():
    postgres = _load_compose()["services"]["beta-postgres"]

    assert postgres["image"] == POSTGRES_IMAGE
    assert "ports" not in postgres
    assert postgres["expose"] == ["5432"]
    assert postgres["environment"]["PGDATA"] == "/var/lib/postgresql/18/docker"
    assert postgres["volumes"] == ["beta_postgres:/var/lib/postgresql"]
    assert "pg_isready" in " ".join(postgres["healthcheck"]["test"])


def test_account_migration_and_readiness_gate_startup():
    services = _load_compose()["services"]
    migrate = services["beta-account-migrate"]
    account = services["beta-account-api"]
    app = services["beta-app-api"]

    assert migrate["command"] == ["python", "-m", "alembic", "upgrade", "head"]
    assert migrate["depends_on"] == {
        "beta-postgres": {"condition": "service_healthy"},
        "beta-account-keygen": {"condition": "service_completed_successfully"},
    }
    assert account["depends_on"] == {
        "beta-account-migrate": {"condition": "service_completed_successfully"}
    }
    assert app["depends_on"] == {"beta-account-api": {"condition": "service_healthy"}}
    assert "/ready" in " ".join(account["healthcheck"]["test"])
    assert "/api/health" in " ".join(app["healthcheck"]["test"])


def test_mock_and_weight_free_modes_are_explicitly_test_only():
    services = _load_compose()["services"]
    account_environment = services["beta-account-api"]["environment"]
    migrate_environment = services["beta-account-migrate"]["environment"]
    app_environment = services["beta-app-api"]["environment"]

    assert account_environment == migrate_environment
    assert account_environment["ENVIRONMENT"] == "test"
    assert account_environment["AUTH_PROVIDER_MODE"] == "mock"
    assert account_environment["ACCOUNT_ISSUER"] == "http://beta-account-api:8000"
    assert "@beta-postgres/plantdisease_beta" in account_environment["ACCOUNT_DATABASE_URL"]
    assert account_environment["ACCOUNT_DATABASE_LOCAL"] == "true"
    assert account_environment["ACCOUNT_ALLOW_EPHEMERAL_KEYS"] == "false"
    assert account_environment["ACCOUNT_SIGNING_KEY_FILE"] == "/run/account-secrets/signing.json"
    assert (
        account_environment["ACCOUNT_ENCRYPTION_KEY_FILE"]
        == "/run/account-secrets/encryption.key"
    )
    assert account_environment["INTERNAL_SERVICE_TOKEN_FILE"] == (
        "/run/account-secrets/internal.token"
    )
    assert account_environment["COMMUNITY_DELETION_URL"].endswith(
        "/api/internal/account-deletions"
    )

    assert app_environment["ENVIRONMENT"] == "test"
    assert app_environment["ACCOUNT_BETA_SYNTHETIC_PIPELINE"] == "true"
    assert app_environment["POPULAR_WARM_ENABLED"] == "false"
    assert app_environment["FRONTIER_ENABLED"] == "false"
    assert app_environment["SCAN_RECORDING_ENABLED"] == "false"
    assert app_environment["TELEMETRY_ENABLED"] == "false"
    assert app_environment["ACCOUNT_JWKS_URL"].startswith("http://beta-account-api:8000/")
    assert app_environment["ACCOUNT_INTROSPECTION_URL"].endswith(
        "/v1/internal/auth/verify"
    )
    assert app_environment["ACCOUNT_NOTIFICATION_URL"].endswith(
        "/v1/internal/notifications/opinion"
    )
    assert app_environment["INTERNAL_SERVICE_TOKEN_FILE"] == (
        "/run/account-secrets/internal.token"
    )
    assert "PUSH_DB_PATH" not in app_environment


def test_local_keys_are_generated_once_without_network_or_committed_values():
    keygen = _load_compose()["services"]["beta-account-keygen"]

    assert keygen["environment"] == {"ENVIRONMENT": "test"}
    assert keygen["command"] == [
        "python",
        "-m",
        "app.generate_test_keys",
        "/run/account-secrets",
    ]
    assert keygen["network_mode"] == "none"
    assert keygen["volumes"] == ["beta_account_keys:/run/account-secrets"]


def test_host_ports_are_loopback_only_and_do_not_publish_postgres():
    services = _load_compose()["services"]
    published = {
        service_name: service["ports"]
        for service_name, service in services.items()
        if "ports" in service
    }

    assert set(published) == {"beta-account-api", "beta-app-api"}
    for ports in published.values():
        assert len(ports) == 1
        assert ports[0]["host_ip"] == "127.0.0.1"
        assert ports[0]["target"] == 8000
        assert ports[0]["protocol"] == "tcp"
    assert published["beta-account-api"][0]["published"] == "${ACCOUNT_API_PORT:-18001}"
    assert published["beta-app-api"][0]["published"] == "${APP_API_PORT:-18000}"


def test_every_persistent_mount_is_a_declared_beta_named_volume():
    compose = _load_compose()
    declared_volumes = compose["volumes"]
    mounted_sources: set[str] = set()

    for service in compose["services"].values():
        for mount in service.get("volumes", []):
            assert isinstance(mount, str)
            source = _mount_source(mount)
            assert source in declared_volumes
            mounted_sources.add(source)

    assert mounted_sources == set(declared_volumes)
    app_targets = {
        _mount_target(mount) for mount in compose["services"]["beta-app-api"]["volumes"]
    }
    assert {"/app/models", "/app/data"}.issubset(app_targets)
    assert "/run/account-secrets" in app_targets
    assert "/data/legacy-push" not in app_targets
    for volume in declared_volumes.values():
        assert volume["name"].startswith(
            "${BETA_VOLUME_PREFIX:-plantdisease_account_beta_test}_"
        )


def test_compose_contains_no_production_paths_or_mutable_image_tags():
    text = COMPOSE_PATH.read_text(encoding="utf-8")

    for forbidden in (
        ":latest",
        "server/admin_backups",
        "/home/",
        "/opt/plant",
        "community.db:/",
        "feedback.db:/",
        "labeling.db:/",
        "telemetry.db:/",
    ):
        assert forbidden not in text

    for service_name in (
        "beta-account-keygen",
        "beta-account-migrate",
        "beta-account-api",
        "beta-app-api",
    ):
        service = _load_compose()["services"][service_name]
        assert service["read_only"] is True
        assert service["tmpfs"]


def test_integration_checker_refuses_every_non_loopback_target():
    spec = importlib.util.spec_from_file_location(
        "account_beta_check_integration", INTEGRATION_CHECK_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module._loopback_url("http://127.0.0.1:18000/") == (
        "http://127.0.0.1:18000"
    )
    for unsafe in (
        "https://127.0.0.1:18000",
        "http://beta-app-api:8000",
        "http://192.0.2.1:8000",
        "https://production.invalid",
    ):
        try:
            module._loopback_url(unsafe)
        except ValueError:
            pass
        else:
            raise AssertionError(f"integration checker accepted unsafe URL: {unsafe}")
