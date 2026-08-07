"""Static deployment contracts; no host, provider, or production system is called."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEPLOY = REPOSITORY_ROOT / "deploy" / "account-beta-test"
GERMAN_COMPOSE = DEPLOY / "compose.german-vds.yml"
GERMAN_BUILD_COMPOSE = DEPLOY / "compose.german-vds.build.yml"
GERMAN_ENV = DEPLOY / "env.german.example"
NGINX = DEPLOY / "nginx.location.conf"
BACKUP = DEPLOY / "backup-test.sh"
HANDOFF = DEPLOY / "handoff"
RU_COMPOSE = HANDOFF / "compose.russia.template.yml"
DE_COMPOSE = HANDOFF / "compose.germany-worker.template.yml"
SERVICE_MANIFEST = HANDOFF / "ru-de-service-manifest.yml"
ML_OPENAPI = HANDOFF / "ml-worker.openapi.json"
PRODUCTION_ENV = HANDOFF / "env.production.example"


def _yaml(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _environment(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, value = line.split("=", maxsplit=1)
        values[key] = value
    return values


def _mount_source(mount: str) -> str:
    return mount.split(":", maxsplit=1)[0]


def test_german_staging_uses_real_providers_and_model_with_isolated_state():
    compose = _yaml(GERMAN_COMPOSE)
    services = compose["services"]
    assert set(services) == {
        "beta-postgres",
        "beta-account-keygen",
        "beta-account-migrate",
        "beta-account-api",
        "beta-app-api",
    }
    assert all("build" not in service for service in services.values())
    assert services["beta-postgres"]["networks"] == ["beta-private"]
    assert services["beta-postgres"]["environment"]["PGDATA"] == (
        "/var/lib/postgresql/18/docker"
    )
    assert services["beta-postgres"]["volumes"] == [
        "beta_postgres:/var/lib/postgresql"
    ]
    assert services["beta-account-keygen"]["network_mode"] == "none"
    assert services["beta-account-keygen"]["command"][-2:] == [
        "--internal-token-directory",
        "/run/internal-token",
    ]
    assert compose["networks"]["beta-private"] == {"internal": True}
    assert compose["networks"]["beta-edge"] == {
        "ipam": {
            "config": [
                {
                    "subnet": "${BETA_EDGE_SUBNET:?Set the dedicated "
                    "non-conflicting beta edge subnet}",
                    "gateway": "${BETA_EDGE_GATEWAY:?Set the beta edge gateway address}",
                }
            ]
        }
    }

    account = services["beta-account-api"]["environment"]
    app = services["beta-app-api"]["environment"]
    assert account["ENVIRONMENT"] == "staging"
    assert account["AUTH_PROVIDER_MODE"] == "remote"
    assert account["YANDEX_CLIENT_ID"].startswith("${YANDEX_CLIENT_ID:?")
    assert account["VKID_CLIENT_ID"].startswith("${VKID_CLIENT_ID:?")
    assert account["ACCOUNT_ROOT_PATH"] == "/plantdisease-test/id"
    assert account["ACCOUNT_ISSUER"] == (
        "https://pdis.arbuz.lol/plantdisease-test/id"
    )
    assert app["ENVIRONMENT"] == "staging"
    assert app["ACCOUNT_BETA_SYNTHETIC_PIPELINE"] == "false"
    assert "ACCOUNT_BETA_SYNTHETIC_DATA_ONLY" not in app
    assert app["APP_ROOT_PATH"] == "/plantdisease-test"
    assert app["ACCOUNT_INTERNAL_BASE_URL"] == "http://beta-account-api:8000"
    assert app["MODELS_DIR"] == "/app/models"
    assert app["AQUA_STATIC_DIR"] == "/app/models/aqua"
    assert app["ONNX_INTRA_OP_THREADS"] == "1"
    assert app["INFER_WORKERS"] == "1"
    assert app["TRAINING_CAPTURE_ENABLED"] == "false"
    assert app["TELEMETRY_ENABLED"] == "false"
    assert app["PLANTNET_API_KEY"] == ""
    assert app["OPENROUTER_API_KEY"] == ""
    assert app["FCM_CREDENTIALS_FILE"] == ""
    assert app["INTERNAL_SERVICE_TOKEN_FILE"] == (
        "/run/internal-token/internal.token"
    )
    assert services["beta-app-api"]["group_add"] == ["10002"]
    app_mounts = services["beta-app-api"]["volumes"]
    assert "beta_internal_token:/run/internal-token:ro" in app_mounts
    assert all(not str(mount).startswith("beta_account_keys:") for mount in app_mounts)

    declared = set(compose["volumes"])
    mounted: set[str] = set()
    for service in services.values():
        for mount in service.get("volumes", []):
            if isinstance(mount, dict):
                assert service is services["beta-app-api"]
                assert mount == {
                    "type": "bind",
                    "source": (
                        "${BETA_MODELS_DIR:?Set the absolute host directory "
                        "containing the PlantVision model bundle}"
                    ),
                    "target": "/app/models",
                    "read_only": True,
                }
                continue
            source = _mount_source(mount)
            assert source in declared
            mounted.add(source)
    assert mounted == declared
    text = GERMAN_COMPOSE.read_text(encoding="utf-8")
    assert "server/admin_backups" not in text
    assert "/home/" not in text
    assert "feedback.db" not in text
    assert "labeling.db" not in text
    for volume in compose["volumes"].values():
        assert str(volume["name"]).startswith("${BETA_VOLUME_PREFIX:?")


def test_german_images_are_branch_tagged_and_only_apis_bind_loopback():
    compose = _yaml(GERMAN_COMPOSE)
    environment = _environment(GERMAN_ENV)
    for key in ("BETA_ACCOUNT_IMAGE", "BETA_APP_IMAGE"):
        image = environment[key]
        assert image.startswith("plantdiseases-account-beta-")
        assert image.endswith(":replace-with-git-sha")
        assert ":latest" not in image
    assert environment["COMPOSE_PROJECT_NAME"] == (
        "plantdisease_account_beta_german"
    )
    assert environment["BETA_VOLUME_PREFIX"] == (
        "plantdisease_account_beta_german"
    )
    assert environment["BETA_FORWARDED_ALLOW_IPS"] != "*"
    assert environment["BETA_TRUSTED_PROXIES"] != "*"
    assert environment["BETA_FORWARDED_ALLOW_IPS"] == environment["BETA_EDGE_GATEWAY"]
    assert environment["BETA_TRUSTED_PROXIES"] == environment["BETA_EDGE_GATEWAY"]
    assert environment["YANDEX_CLIENT_ID"].startswith("replace-with-")
    assert environment["VKID_CLIENT_ID"].isdecimal()
    assert environment["BETA_MODELS_DIR"].startswith("/absolute/")

    published = {
        name: service["ports"]
        for name, service in compose["services"].items()
        if "ports" in service
    }
    assert set(published) == {"beta-account-api", "beta-app-api"}
    for ports in published.values():
        assert ports[0]["host_ip"] == "127.0.0.1"
        assert ports[0]["target"] == 8000
    assert "ports" not in compose["services"]["beta-postgres"]


def test_german_build_overlay_builds_only_branch_application_images():
    overlay = _yaml(GERMAN_BUILD_COMPOSE)
    services = overlay["services"]
    assert set(services) == {
        "beta-account-keygen",
        "beta-account-migrate",
        "beta-account-api",
        "beta-app-api",
    }
    for name in (
        "beta-account-keygen",
        "beta-account-migrate",
        "beta-account-api",
    ):
        assert services[name]["build"] == {
            "context": "../../account-server",
            "dockerfile": "Dockerfile",
        }
    assert services["beta-app-api"]["build"] == {
        "context": "../../server",
        "dockerfile": "Dockerfile",
    }


def test_nginx_prefixes_strip_to_distinct_upstreams_without_fallback():
    text = NGINX.read_text(encoding="utf-8")
    account_location = "location ^~ /plantdisease-test/id/"
    app_location = "location ^~ /plantdisease-test/"
    account_index = text.index(account_location)
    app_index = text.index(app_location, account_index + len(account_location))
    assert account_index < app_index
    assert "proxy_pass http://127.0.0.1:18101/;" in text
    assert "proxy_pass http://127.0.0.1:18100/;" in text
    assert text.count("proxy_next_upstream off;") == 2
    assert text.count("access_log off;") == 2
    assert text.count("proxy_set_header X-Forwarded-For $remote_addr;") == 2
    assert text.count('proxy_set_header X-Request-ID "";') == 2
    assert "X-Forwarded-Prefix /plantdisease-test/id" in text
    assert "X-Forwarded-Prefix /plantdisease-test;" in text
    assert "proxy_pass http://127.0.0.1:18000" not in text
    assert "proxy_pass http://127.0.0.1:18001" not in text


def test_backup_check_is_scoped_to_generated_restore_database():
    text = BACKUP.read_text(encoding="utf-8")
    assert "set -euC" in text
    assert "plantdisease_account_beta_german" in text
    assert "grep -c '^COMPOSE_PROJECT_NAME='" in text
    assert "grep -c '^BETA_VOLUME_PREFIX='" in text
    assert 'restore_db="beta_restore_${timestamp}_$$"' in text
    assert "dropdb" in text
    assert '"$restore_db"' in text
    assert text.index('<"$partial_path"') < text.index(
        'mv -- "$partial_path" "$backup_path"'
    )
    assert "down --volumes" not in text
    assert "docker volume" not in text
    assert "system prune" not in text
    assert "plantdisease_beta" in text
    assert "sha256sum" in text


def test_final_service_manifest_keeps_every_user_state_store_in_russia():
    manifest = _yaml(SERVICE_MANIFEST)
    services = manifest["services"]
    worker = services["de-ml-worker"]
    assert manifest["status"] == "deployment-blocked-until-legal-and-operator-gates"
    assert worker["country"] == "DE"
    assert worker["persists"] == []
    assert worker["public_android_endpoint"] is False
    assert worker["runtime"]["request_storage"] == "memory-only"
    assert worker["runtime"]["temporary_storage"] == "tmpfs"
    assert worker["runtime"]["model_storage"] == "read-only"
    assert set(worker["forbidden_import_domains"]) == {
        "account",
        "community",
        "push",
        "telemetry",
        "feedback",
        "training_store",
    }
    for name, service in services.items():
        if name.startswith("ru-"):
            assert service["country"] == "RU"
    flow = manifest["flows"]["ru_to_de_inference"]
    assert flow["transport"] == "mTLS"
    assert flow["direct_android_access"] == "forbidden"


def test_worker_openapi_has_an_exact_identity_free_request_contract():
    document = json.loads(ML_OPENAPI.read_text(encoding="utf-8"))
    operation = document["paths"]["/v1/infer"]["post"]
    assert operation["security"] == [{"mutualTLS": []}]
    request = document["components"]["schemas"]["InferenceRequest"]
    assert request["additionalProperties"] is False
    assert set(request["properties"]) == {
        "image",
        "request_id",
        "model_family",
        "language",
        "result_options",
    }
    assert set(request["required"]) == set(request["properties"])
    forbidden = {
        "authorization",
        "jwt",
        "account_id",
        "actor_id",
        "session_id",
        "nickname",
        "provider",
        "installation_id",
        "fcm_token",
        "filename",
        "metadata",
    }
    assert forbidden.isdisjoint(request["properties"])


def test_final_compose_templates_encode_the_ru_de_boundary():
    ru = _yaml(RU_COMPOSE)
    de = _yaml(DE_COMPOSE)
    assert ru["networks"]["ru-private"] == {"internal": True}
    assert "ports" not in ru["services"]["ru-account-api"]
    assert "ports" not in ru["services"]["ru-app-api"]
    ru_app = ru["services"]["ru-app-api"]["environment"]
    assert ru_app["ML_WORKER_FORWARD_AUTHORIZATION"] == "false"
    assert ru_app["ML_WORKER_ALLOWED_FIELDS"] == (
        "image,request_id,model_family,language,result_options"
    )
    assert ru_app["COMMUNITY_DB_PATH"].startswith("/data/")
    assert ru_app["TRAINING_DB_PATH"].startswith("/data/")
    assert ru_app["TELEMETRY_DB_PATH"].startswith("/data/")

    backup = ru["services"]["ru-backup"]
    assert backup["environment"]["BACKUP_POSTGRES_MODE"] == "pg-dump-custom"
    assert backup["environment"]["BACKUP_SQLITE_MODE"] == "online-backup-api"
    assert all("/source/postgres" not in mount for mount in backup["volumes"])
    assert all(not mount.startswith("ru_postgres:") for mount in backup["volumes"])

    worker = de["services"]["de-ml-worker"]
    assert set(de["services"]) == {"de-ml-worker"}
    assert worker["read_only"] is True
    assert worker["environment"]["PERSISTENCE_ENABLED"] == "false"
    assert worker["environment"]["ACCESS_LOG_ENABLED"] == "false"
    assert worker["environment"]["ALLOWED_REQUEST_FIELDS"] == (
        "image,request_id,model_family,language,result_options"
    )
    assert worker["ports"][0]["host_ip"].startswith("${DE_ML_BIND_IP:?")
    assert all(mount.endswith(":ro") for mount in worker["volumes"])
    assert all("postgres" not in mount.lower() for mount in worker["volumes"])

    environment = _environment(PRODUCTION_ENV)
    image_values = [value for key, value in environment.items() if key.endswith("IMAGE")]
    assert image_values
    assert all("@sha256:" in image for image in image_values)
    assert all(image.startswith("registry.invalid/") for image in image_values)
    assert all(image.endswith("0" * 64) for image in image_values)
    assert environment["RU_PUBLIC_HOST"].endswith(".invalid")
    assert environment["DE_ML_WORKER_URL"].split(":", maxsplit=1)[1].startswith(
        "//de-ml.private.invalid"
    )
    assert environment["RU_POSTGRES_PASSWORD"].startswith("replace-")
