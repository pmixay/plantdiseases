"""Static deployment contracts; no host, provider, or production system is called."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEPLOY = REPOSITORY_ROOT / "deploy" / "account-beta-test"
GERMAN_COMPOSE = DEPLOY / "compose.german-vds.yml"
GERMAN_BUILD_COMPOSE = DEPLOY / "compose.german-vds.build.yml"
GERMAN_ENV = DEPLOY / "env.german.example"
NGINX = DEPLOY / "nginx.location.conf"
NGINX_TESTER_ALLOWLIST = DEPLOY / "nginx.testers.allow.example"
DEPLOY_README = DEPLOY / "README.md"
SECRETS_INVENTORY = DEPLOY / "SECRETS.md"
EMAIL_AUTH_HOLD = REPOSITORY_ROOT / "docs" / "EMAIL_PASSWORD_AUTH_RELEASE_BLOCKERS.md"
BACKUP = DEPLOY / "backup-test.sh"
HANDOFF = DEPLOY / "handoff"
RU_NGINX = HANDOFF / "nginx.russia.location.conf"
RU_COMPOSE = HANDOFF / "compose.russia-template.yml"
DE_COMPOSE = HANDOFF / "compose.germany-worker-template.yml"
SERVICE_MANIFEST = HANDOFF / "ru-de-service-manifest.yml"
ML_OPENAPI = HANDOFF / "ml-worker.openapi.json"
PRODUCTION_ENV = HANDOFF / "env.production.example"
RU_EGRESS_POLICY = HANDOFF / "egress-proxy" / "squid.conf.example"
PRIVACY = REPOSITORY_ROOT / "privacy.html"
RELEASE_POLICY_GATES = DEPLOY / "release-policy-gates.json"
ANDROID_RELEASE_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "android-release.yml"
CI_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"
ANDROID_BUILD_GRADLE = REPOSITORY_ROOT / "android" / "app" / "build.gradle.kts"
ANDROID_GRADLE_PROPERTIES = REPOSITORY_ROOT / "android" / "gradle.properties"
GERMAN_ROLE_INIT = DEPLOY / "postgres-init" / "10-account-roles.sh"
RU_ROLE_INIT = HANDOFF / "postgres-init" / "10-account-roles.sh"
GRANT_CONVERGENCE = DEPLOY / "converge-runtime-grants.py"

ACCOUNT_SCHEMA_HEAD = "0010_billing_lifecycle_quota_policy"
ACCOUNT_TABLES = {
    "account_consents",
    "account_deletion_jobs",
    "account_identities",
    "account_nickname_claims",
    "account_nickname_jobs",
    "account_security_events",
    "accounts",
    "alembic_version",
    "billing_customers",
    "billing_purchase_fetches",
    "billing_notification_receipts",
    "feature_usage",
    "feature_usage_receipts",
    "password_credentials",
    "pending_password_credentials",
    "pending_signups",
    "push_devices",
    "reauth_challenges",
    "refresh_tokens",
    "sessions",
    "signup_completions",
    "subscriptions",
    "vendor_budget_authorities",
    "vendor_budget_daily",
    "vendor_budget_receipts",
}


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


def _release_policy_validator_source() -> str:
    workflow = _yaml(ANDROID_RELEASE_WORKFLOW)
    step = next(
        step
        for step in workflow["jobs"]["build_unsigned"]["steps"]
        if step.get("name") == "Block unresolved identity-data policy releases"
    )
    script = step["run"]
    prefix = "python3 - <<'PY'\n"
    start = script.index(prefix) + len(prefix)
    return script[start : script.index("\nPY\n", start)]


def _run_release_policy_validator(tmp_path: Path, gates: dict, policy: str):
    deployment = tmp_path / "deploy" / "account-beta-test"
    deployment.mkdir(parents=True, exist_ok=True)
    (deployment / "release-policy-gates.json").write_text(
        json.dumps(gates),
        encoding="utf-8",
    )
    (tmp_path / "privacy.html").write_text(policy, encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-c", _release_policy_validator_source()],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )


def _mount_source(mount: str) -> str:
    return mount.split(":", maxsplit=1)[0]


def test_german_staging_uses_real_providers_and_model_with_isolated_state():
    compose = _yaml(GERMAN_COMPOSE)
    services = compose["services"]
    assert set(services) == {
        "beta-postgres",
        "beta-account-role-upgrade",
        "beta-account-keygen",
        "beta-account-migrate",
        "beta-account-api",
        "beta-app-api",
    }
    assert all("build" not in service for service in services.values())
    assert services["beta-postgres"]["networks"] == ["beta-private"]
    assert services["beta-app-api"]["networks"] == ["beta-edge"]
    assert set(services["beta-account-api"]["networks"]) == {
        "beta-private",
        "beta-edge",
    }
    assert services["beta-postgres"]["environment"]["PGDATA"] == (
        "/var/lib/postgresql/18/docker"
    )
    assert services["beta-postgres"]["volumes"] == [
        "beta_postgres:/var/lib/postgresql",
        {
            "type": "bind",
            "source": "./postgres-init",
            "target": "/docker-entrypoint-initdb.d",
            "read_only": True,
        },
    ]
    assert services["beta-account-keygen"]["network_mode"] == "none"
    assert services["beta-account-migrate"]["depends_on"][
        "beta-account-role-upgrade"
    ]["condition"] == "service_completed_successfully"
    assert services["beta-account-role-upgrade"]["environment"]["POSTGRES_USER"] == (
        "beta_account"
    )
    assert services["beta-account-api"]["depends_on"] == {
        "beta-account-migrate": {"condition": "service_completed_successfully"}
    }
    migrate_command = services["beta-account-migrate"]["command"]
    assert migrate_command[:2] == ["/bin/sh", "-ec"]
    assert migrate_command[2].index("python -m alembic upgrade head") < migrate_command[2].index(
        "python /role-contract/converge-runtime-grants.py --contract german-beta"
    )
    assert (
        "./converge-runtime-grants.py:/role-contract/converge-runtime-grants.py:ro"
        in services["beta-account-migrate"]["volumes"]
    )
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
    assert account["YANDEX_CLIENT_ID"] == "${YANDEX_CLIENT_ID:-}"
    assert account["VKID_CLIENT_ID"] == "${VKID_CLIENT_ID:-}"
    assert account["EMAIL_PASSWORD_AUTH_ENABLED"] == (
        "${EMAIL_PASSWORD_AUTH_ENABLED:-false}"
    )
    assert account["ACCOUNT_ROOT_PATH"] == "/plantdisease-test/id"
    assert account["ACCOUNT_ISSUER"] == (
        "https://pdis.arbuz.lol/plantdisease-test/id"
    )
    assert account["RUSTORE_LIFECYCLE_ENABLED"] == (
        "${RUSTORE_LIFECYCLE_ENABLED:-false}"
    )
    assert account["RUSTORE_CHECKOUT_ENABLED"] == (
        "${RUSTORE_CHECKOUT_ENABLED:-false}"
    )
    assert account["RUSTORE_APPLICATION_ID"] == "${RUSTORE_APPLICATION_ID:-}"
    assert account["RUSTORE_KEY_ID"] == "${RUSTORE_KEY_ID:-}"
    assert account["RUSTORE_PRIVATE_KEY_FILE"] == "/run/rustore-secrets/private-key.b64"
    assert account["RUSTORE_NOTIFICATION_KEY_FILE"] == (
        "/run/rustore-secrets/notification-key.b64"
    )
    assert account["RUSTORE_ALLOW_SANDBOX"] == "${RUSTORE_ALLOW_SANDBOX:-false}"
    assert account["ENTITLEMENT_QUOTA_POLICY_VERSION"] == (
        "${ENTITLEMENT_QUOTA_POLICY_VERSION:-launch-2026-08-v1}"
    )
    assert "beta_account_runtime" in account["ACCOUNT_DATABASE_URL"]
    assert "beta_account_migrator" in services["beta-account-migrate"]["environment"][
        "ACCOUNT_DATABASE_URL"
    ]
    assert app["ENVIRONMENT"] == "staging"
    assert app["ACCOUNT_BETA_SYNTHETIC_PIPELINE"] == "false"
    assert app["ACCOUNT_FEATURE_EXPECTED"] == "true"
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
    assert app["ACCOUNT_NOTIFICATION_URL"] == ""
    assert app["INTERNAL_SERVICE_TOKEN_FILE"] == (
        "/run/internal-token/internal.token"
    )
    assert services["beta-app-api"]["group_add"] == ["10002"]
    assert services["beta-app-api"]["cpus"] == 1.0
    app_mounts = services["beta-app-api"]["volumes"]
    assert "beta_internal_token:/run/internal-token:ro" in app_mounts
    assert all(not str(mount).startswith("beta_account_keys:") for mount in app_mounts)

    declared = set(compose["volumes"])
    mounted: set[str] = set()
    for service in services.values():
        for mount in service.get("volumes", []):
            if isinstance(mount, dict):
                if mount.get("target") == "/run/rustore-secrets":
                    assert (
                        service is services["beta-account-migrate"]
                        or service is services["beta-account-api"]
                    )
                    assert mount == {
                        "type": "bind",
                        "source": (
                            "${BETA_RUSTORE_SECRETS_DIR:?Set the account-owned "
                            "RuStore beta secret directory}"
                        ),
                        "target": "/run/rustore-secrets",
                        "read_only": True,
                    }
                elif service is services["beta-app-api"]:
                    assert mount == {
                        "type": "bind",
                        "source": (
                            "${BETA_MODELS_DIR:?Set the absolute host directory "
                            "containing the PlantVision model bundle}"
                        ),
                        "target": "/app/models",
                        "read_only": True,
                    }
                else:
                    assert service is services["beta-postgres"]
                    assert mount == {
                        "type": "bind",
                        "source": "./postgres-init",
                        "target": "/docker-entrypoint-initdb.d",
                        "read_only": True,
                    }
                continue
            source = _mount_source(mount)
            if source in {"./postgres-init", "./converge-runtime-grants.py"}:
                continue
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


def test_german_runtime_images_are_digest_pinned_and_only_apis_bind_loopback():
    compose = _yaml(GERMAN_COMPOSE)
    environment = _environment(GERMAN_ENV)
    for key in ("BETA_ACCOUNT_IMAGE", "BETA_APP_IMAGE"):
        image = environment[key]
        repository, digest = image.split("@sha256:", maxsplit=1)
        assert repository.startswith("registry.invalid/plantdiseases/")
        assert len(digest) == 64
        assert set(digest) <= set("0123456789abcdef")
        assert ":latest" not in image
    assert environment["BETA_ACCOUNT_BUILD_IMAGE"].endswith(
        ":build-replace-with-git-sha"
    )
    assert environment["BETA_APP_BUILD_IMAGE"].endswith(
        ":build-replace-with-git-sha"
    )
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
    assert environment["YANDEX_CLIENT_ID"] == "2b439944bb4c4e33b85e20290aca6c7f"
    assert environment["VKID_CLIENT_ID"] == ""
    assert environment["EMAIL_PASSWORD_AUTH_ENABLED"] == "false"
    assert environment["BETA_MODELS_DIR"].startswith("/absolute/")
    for name in (
        "BETA_POSTGRES_ADMIN_PASSWORD",
        "BETA_POSTGRES_RUNTIME_PASSWORD",
        "BETA_POSTGRES_MIGRATOR_PASSWORD",
        "BETA_POSTGRES_BACKUP_PASSWORD",
    ):
        assert environment[name].startswith("replace-with-")

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


def test_deletion_journal_is_writable_only_by_the_account_api():
    """The journal must live outside every backed-up store and never reach the app."""
    compose = _yaml(GERMAN_COMPOSE)
    services = compose["services"]
    account = services["beta-account-api"]

    assert account["environment"]["ACCOUNT_DELETION_JOURNAL_DIR"] == "/run/deletion-journal"
    assert "beta_deletion_journal:/run/deletion-journal" in account["volumes"]  # read-write
    for name, service in services.items():
        if name == "beta-account-api":
            continue
        mounts = [m if isinstance(m, str) else m.get("source", "") for m in service.get("volumes", [])]
        assert not any("deletion_journal" in m or "deletion-journal" in m for m in mounts), name
    assert compose["volumes"]["beta_deletion_journal"]["name"].endswith("_deletion_journal")
    dockerfile = (REPOSITORY_ROOT / "account-server" / "Dockerfile").read_text()
    assert "/run/deletion-journal" in dockerfile and "chmod 0700 /run/deletion-journal" in dockerfile


def test_german_email_password_beta_is_opt_in_and_edge_restricted():
    compose = _yaml(GERMAN_COMPOSE)
    environment = _environment(GERMAN_ENV)
    account = compose["x-account-environment"]
    assert account["EMAIL_PASSWORD_AUTH_ENABLED"] == (
        "${EMAIL_PASSWORD_AUTH_ENABLED:-false}"
    )
    assert environment["EMAIL_PASSWORD_AUTH_ENABLED"] == "false"

    nginx = NGINX.read_text(encoding="utf-8")
    assert nginx.count("include /etc/nginx/plantdisease-beta-testers.allow;") == 2
    tester_allowlist = NGINX_TESTER_ALLOWLIST.read_text(encoding="utf-8")
    assert "allow 192.0.2.1/32;" in tester_allowlist
    assert "allow 2001:db8::1/128;" in tester_allowlist
    assert "\nallow all;" not in tester_allowlist
    assert tester_allowlist.rstrip().endswith("deny all;")

    operator_contract = DEPLOY_README.read_text(encoding="utf-8")
    hold_contract = EMAIL_AUTH_HOLD.read_text(encoding="utf-8")
    normalized_hold_contract = " ".join(hold_contract.split())
    assert "EMAIL_PASSWORD_AUTH_ENABLED=true" in operator_contract
    assert "named tester" in operator_contract
    assert "Disposable addresses are required" in normalized_hold_contract
    assert (
        "pending bearer cannot resume after a later re-enable"
        in normalized_hold_contract
    )


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
        assert services[name]["image"].startswith("${BETA_ACCOUNT_BUILD_IMAGE:?")
    assert services["beta-app-api"]["build"] == {
        "context": "../../server",
        "dockerfile": "Dockerfile",
    }
    assert services["beta-app-api"]["image"].startswith("${BETA_APP_BUILD_IMAGE:?")


def test_nginx_prefixes_strip_to_distinct_upstreams_without_fallback():
    text = NGINX.read_text(encoding="utf-8")
    account_location = "location ^~ /plantdisease-test/id/"
    app_location = "location ^~ /plantdisease-test/"
    account_index = text.index(account_location)
    app_index = text.index(app_location, account_index + len(account_location))
    assert account_index < app_index
    assert "proxy_pass http://127.0.0.1:18101/;" in text
    assert "proxy_pass http://127.0.0.1:18100/;" in text
    assert text.count("proxy_next_upstream off;") == 3
    assert text.count("access_log off;") == 3
    assert text.count("proxy_set_header X-Forwarded-For $remote_addr;") == 3
    assert text.count('proxy_set_header X-Request-ID "";') == 3
    assert text.count('proxy_set_header X-Internal-Service-Token "";') == 3
    assert (
        text.count("include /etc/nginx/plantdisease-beta-testers.allow;") == 2
    )
    assert "location ^~ /plantdisease-test/id/v1/internal/" in text
    assert "location ^~ /plantdisease-test/api/internal/" in text
    assert text.count("limit_req_status 429;") == 3
    assert "return 429 '{\"detail\":\"rate_limited\"}';" in text
    assert "X-Forwarded-Prefix /plantdisease-test/id" in text
    assert "X-Forwarded-Prefix /plantdisease-test;" in text
    assert "proxy_pass http://127.0.0.1:18000" not in text
    assert "proxy_pass http://127.0.0.1:18001" not in text

    tester_allowlist = NGINX_TESTER_ALLOWLIST.read_text(encoding="utf-8")
    assert "allow 192.0.2.1/32;" in tester_allowlist
    assert "allow 2001:db8::1/128;" in tester_allowlist
    assert "\nallow all;" not in tester_allowlist
    assert tester_allowlist.rstrip().endswith("deny all;")


def test_rustore_callbacks_have_exact_paths_source_cidrs_and_sufficient_body_cap():
    expected_ranges = {
        "allow 95.163.133.0/30;",
        "allow 85.198.107.0/24;",
        "allow 79.137.140.128/26;",
    }
    for path, public_path, upstream in (
        (
            NGINX,
            "/plantdisease-test/id/v1/billing/rustore/notifications",
            "http://127.0.0.1:18101/v1/billing/rustore/notifications",
        ),
        (
            RU_NGINX,
            "/id/v1/billing/rustore/notifications",
            "http://ru-account-api:8000/v1/billing/rustore/notifications",
        ),
    ):
        text = path.read_text(encoding="utf-8")
        marker = f"location = {public_path} {{"
        assert text.count(marker) == 1
        block = text.split(marker, maxsplit=1)[1].split("\n}", maxsplit=1)[0]
        assert expected_ranges.issubset(set(line.strip() for line in block.splitlines()))
        assert "deny all;" in block
        assert "client_max_body_size 96k;" in block
        assert f"proxy_pass {upstream};" in block
        assert "proxy_next_upstream off;" in block
        assert "proxy_read_timeout 5s;" in block


def test_backup_check_is_scoped_to_generated_restore_database():
    text = BACKUP.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash\n")
    assert "set -euo pipefail" in text
    assert "set -o noclobber" in text
    assert "plantdisease_account_beta_german" in text
    assert "grep -c '^COMPOSE_PROJECT_NAME='" in text
    assert "grep -c '^BETA_VOLUME_PREFIX='" in text
    assert 'restore_db="beta_restore_${timestamp}_$$"' in text
    assert "dropdb" in text
    assert '"$restore_db"' in text
    assert 'age --encrypt --recipient "$age_recipient"' in text
    assert 'age --decrypt --identity "$age_identity" "$partial_path"' in text
    assert "plantdisease-beta-$timestamp.dump.age" in text
    assert "stale plaintext restore database found" in text
    assert "left(datname, 13) = 'beta_restore_'" in text
    assert "trap cleanup EXIT" in text
    assert "trap cleanup EXIT HUP INT TERM" not in text
    assert "-mtime +27" in text
    assert '--project-name "$canonical_project"' in text
    assert 'export COMPOSE_PROJECT_NAME BETA_VOLUME_PREFIX' in text
    assert '.plantdisease-beta-backup.lock' in text
    assert '--owner=beta_account_migrator' in text
    assert '--role=beta_account_migrator' in text
    assert '-U beta_account_backup -d "$restore_db"' in text
    assert "account_nickname_jobs" in text
    assert "account_nickname_claims" in text
    assert "signup_completions" in text
    assert f"expected_schema_revision={ACCOUNT_SCHEMA_HEAD}" in text
    assert "expected_table_inventory=" in text
    assert "source or restored database table inventory differs" in text
    for table in ACCOUNT_TABLES:
        assert table in text
    assert "SELECT count(*) FROM signup_completions" in text
    assert "UPDATE signup_completions SET expires_at = expires_at WHERE false" in text
    assert "unreviewed runtime sequence" in text
    assert "UPDATE alembic_version SET version_num = version_num WHERE false" in text
    assert "DELETE FROM alembic_version WHERE false" in text
    assert "runtime role unexpectedly has UPDATE privilege on migration metadata" in text
    assert "runtime role unexpectedly has DELETE privilege on migration metadata" in text
    assert "-U beta_account_migrator" in text
    assert 'runtime role unexpectedly has schema DDL privileges' in text
    assert 'backup role unexpectedly has write privileges' in text
    assert 'backup role unexpectedly has schema DDL privileges' in text
    assert "NOT has_database_privilege('beta_account_backup'" in text
    assert "rolbypassrls" in text
    assert "FROM pg_auth_members membership" in text
    assert 'if [ "$output_dir" = / ]' in text
    assert "beta_account_bootstrap" not in text
    assert text.index('age --decrypt --identity "$age_identity" "$partial_path"') < text.index(
        'mv -- "$partial_path" "$backup_path"'
    )
    assert "down --volumes" not in text
    assert "docker volume" not in text
    assert "system prune" not in text
    assert "plantdisease_beta" in text
    assert "sha256sum" in text


def test_privacy_and_signing_gates_match_account_release():
    privacy = PRIVACY.read_text(encoding="utf-8")
    assert "fonts.googleapis.com" not in privacy
    assert "fonts.gstatic.com" not in privacy
    assert "retained for model improvement only if you separately consent" in privacy
    assert "сохраняется для улучшения моделей только если вы отдельно дали" in privacy
    assert "account-service endpoint scope, including its base path" in privacy
    assert "register with an email address and password" in privacy
    assert "non-reversible keyed lookup fingerprint" in privacy
    assert "RuStore processes checkout and payment details" in privacy
    assert "Deleting a PlantDiseases account does not cancel the RuStore subscription" in privacy
    assert "Publishing a scan or submitting an opinion requires sign-in" in privacy
    assert "It currently has no automatic expiry" not in privacy
    assert "It is kept for 30 days after deletion and then erased" in privacy
    assert "Do not post identifiers, tokens or account details in a public issue" in privacy

    gates = json.loads(RELEASE_POLICY_GATES.read_text(encoding="utf-8"))
    tombstone = gates["deleted_actor_tombstone"]
    assert tombstone["status"] == "approved"
    assert tombstone["mode"] == "fixed_days"
    assert tombstone["retention_days"] == 30
    assert tombstone["legal_basis"] is None
    assert tombstone["policy_marker_id"] == "deleted-actor-tombstone-policy"
    assert tombstone["decision_reference"].startswith("https://github.com/pmixay/plantdiseases/blob/")
    assert tombstone["approved_at_utc"].endswith("Z")
    assert "30" in tombstone["disclosure_en"] and "30" in tombstone["disclosure_ru"]
    assert tombstone["disclosure_en"] != tombstone["disclosure_ru"]
    contact = gates["private_contact"]
    assert contact["status"] == "approved"
    assert contact["uri"] == "mailto:gavel8980@gmail.com"
    assert contact["policy_link_id"] == "privacy-private-contact"
    assert contact["decision_reference"].startswith("https://github.com/pmixay/plantdiseases/blob/")
    assert contact["approved_at_utc"].endswith("Z")
    assert 'id="privacy-private-contact"' in privacy
    assert 'href="mailto:gavel8980@gmail.com"' in privacy
    assert 'data-policy-purpose="privacy-and-deletion"' in privacy
    assert gates == {
        "schema_version": 2,
        "private_contact": contact,
        "deleted_actor_tombstone": tombstone,
    }
    assert '<meta name="plantdiseases-release-policy-schema" content="2">' in privacy
    assert 'id="deleted-actor-tombstone-policy"' in privacy
    assert 'data-policy-status="approved"' in privacy
    assert 'data-retention-mode="fixed_days"' in privacy
    assert 'data-retention-days="30"' in privacy

    workflow = ANDROID_RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert "&& secrets.PRODUCTION_" not in workflow
    assert "PRODUCTION_RUSSIAN_CUTOVER_APPROVED" in workflow
    assert "Require successful CI for this exact commit" in workflow
    assert "APPLICATION_ID = \"com.plantdiseases.app\"" in workflow
    assert "versionCode='$expected_version_code'" in workflow
    assert "expected_yandex_client_id=2b439944bb4c4e33b85e20290aca6c7f" in workflow
    assert "STAGING_YANDEX_CLIENT_ID" not in workflow
    assert "PRODUCTION_YANDEX_CLIENT_ID" not in workflow
    assert "YANDEX_CLIENT_SECRET" not in workflow
    assert "STAGING_VKID_CLIENT_ID: ${{ vars.STAGING_VKID_CLIENT_ID }}" in workflow
    assert "Configured production and staging VK applications must be distinct" in workflow
    assert "VK ID and mobile value must be set together" in workflow
    assert "APPROVED_PRODUCTION_APP_API_BASE_URL" in workflow
    assert "APPROVED_PRODUCTION_ACCOUNT_API_BASE_URL" in workflow
    assert "Unsigned artifact endpoints differ from the protected approved" in workflow
    assert "Production and production-recovery endpoints must not use German staging" in workflow
    assert "Production origin must not use localhost or a reserved invalid" in workflow
    assert "Production origin must not use an IPv4 literal" in workflow
    assert "          - recovery\n" not in workflow
    assert "          - recovery-production\n" in workflow
    assert "          - recovery-staging\n" in workflow
    assert "STAGING_APP_API_BASE_URL: ${{ vars.STAGING_APP_API_BASE_URL }}" in workflow
    assert "STAGING_ACCOUNT_API_BASE_URL: ${{ vars.STAGING_ACCOUNT_API_BASE_URL }}" in workflow
    assert "Staging endpoints must equal the reviewed German staging pair" in workflow
    assert ":app:testRecoveryProductionUnitTest :app:assembleRecoveryProduction" in workflow
    assert ":app:testRecoveryStagingUnitTest :app:assembleRecoveryStaging" in workflow
    assert "RECOVERY_ENDPOINT_SCOPE = \"production\"" in workflow
    assert "RECOVERY_ENDPOINT_SCOPE = \"staging\"" in workflow
    assert "release-policy-gates.json" in workflow
    assert 'contact.get("status") != "approved"' in workflow
    assert 'tombstone.get("status") != "approved"' in workflow
    assert "approved private contact must use mailto: or HTTPS" in workflow
    assert "approved private contact does not match the policy link exactly" in workflow
    assert "distinct English and Russian tombstone disclosures are required" in workflow
    assert "both tombstone disclosures must contain the legal-basis reference" in workflow
    assert "tombstone JSON and bilingual policy marker do not match exactly" in workflow
    # The production stop was lifted with the deletion journal + backup epoch;
    # production must not be re-blocked by a leftover channel-specific stop.
    assert "Production distribution is blocked until the deferred backup epoch" not in workflow
    assert "docs/DELETION_JOURNAL_AND_BACKUP_EPOCH.md" in workflow
    assert "grep -Fq '**Deferred decision:**" not in workflow
    assert "refs/heads/account-beta-test" not in workflow
    assert "Every signing channel must use trusted workflow code from protected main" in workflow


def test_release_policy_validator_requires_exact_structured_bilingual_policy(tmp_path):
    disclosure_en = "The deleted-actor marker is retained for 30 days."
    disclosure_ru = "Маркер удалённого участника хранится 30 дней."
    gates = {
        "schema_version": 2,
        "private_contact": {
            "status": "approved",
            "uri": "mailto:privacy@example.test",
            "policy_link_id": "privacy-private-contact",
            "decision_reference": "https://decisions.example.test/contact",
            "approved_at_utc": "2026-08-09T12:00:00Z",
        },
        "deleted_actor_tombstone": {
            "status": "approved",
            "mode": "fixed_days",
            "retention_days": 30,
            "legal_basis": None,
            "policy_marker_id": "deleted-actor-tombstone-policy",
            "disclosure_en": disclosure_en,
            "disclosure_ru": disclosure_ru,
            "decision_reference": "https://decisions.example.test/tombstone",
            "approved_at_utc": "2026-08-09T12:00:00Z",
        },
    }
    policy = f"""<!doctype html>
    <html><head>
    <meta name="plantdiseases-release-policy-schema" content="2">
    </head><body><main>
    <a id="privacy-private-contact"
       data-policy-purpose="privacy-and-deletion"
       href="mailto:privacy@example.test">Private contact</a>
    <p id="deleted-actor-tombstone-policy"
       data-policy-status="approved"
       data-retention-mode="fixed_days"
       data-retention-days="30"
       data-legal-basis=""
       data-en="{disclosure_en}"
       data-ru="{disclosure_ru}">{disclosure_ru}</p>
    </main></body></html>"""

    accepted = _run_release_policy_validator(tmp_path, gates, policy)
    assert accepted.returncode == 0, accepted.stderr

    invalid_contact = copy.deepcopy(gates)
    invalid_contact["private_contact"]["uri"] = "account"
    rejected_contact = _run_release_policy_validator(
        tmp_path,
        invalid_contact,
        policy.replace("mailto:privacy@example.test", "account"),
    )
    assert rejected_contact.returncode != 0
    assert "must use mailto: or HTTPS" in rejected_contact.stderr

    contradictory_retention = copy.deepcopy(gates)
    contradictory_retention["deleted_actor_tombstone"]["retention_days"] = 1
    rejected_retention = _run_release_policy_validator(
        tmp_path,
        contradictory_retention,
        policy,
    )
    assert rejected_retention.returncode != 0
    assert "must contain the approved day count" in rejected_retention.stderr


def test_protected_android_signing_has_checkout_free_key_boundary():
    workflow = _yaml(ANDROID_RELEASE_WORKFLOW)
    build = workflow["jobs"]["build_unsigned"]
    sign = workflow["jobs"]["sign"]
    build_text = json.dumps(build)
    sign_text = json.dumps(sign)

    assert "environment" not in build
    assert "secrets." not in build_text
    assert "RELEASE_KEYSTORE" not in build_text
    assert "RELEASE_STORE_PASSWORD" not in build_text
    assert "RELEASE_KEY_PASSWORD" not in build_text
    assert "ANDROID_UNSIGNED_DISTRIBUTION" in build_text
    assert "EMAIL_PASSWORD_AUTH_ENABLED = true" in build_text
    assert "EMAIL_PASSWORD_AUTH_ENABLED = false" in build_text
    assert "Gradle output is already signed" in build_text
    assert "Require successful CI for this exact commit" in build_text
    branch_gate = next(
        step
        for step in build["steps"]
        if step.get("name") == "Validate requested source branch"
    )
    assert branch_gate["working-directory"] == "."

    assert sign["needs"] == "build_unsigned"
    assert sign["environment"] == "android-signing"
    assert sign["permissions"] == {"actions": "read", "contents": "none"}
    assert "actions/checkout@" not in sign_text
    assert "gradlew" not in sign_text
    assert "setup-gradle" not in sign_text
    assert "actions/download-artifact@37930b1c2abaa49bbe596cd826c3c89aef350131" in sign_text
    assert "artifact-ids" in sign_text
    assert "64 lowercase hex" in sign_text
    assert "unset RELEASE_KEYSTORE_BASE64 RELEASE_STORE_PASSWORD" in sign_text
    assert "EXPECTED_UNSIGNED_SHA256" in sign_text
    assert "Downloaded unsigned APK digest differs from the build-job output" in sign_text
    assert "EXPECTED_YANDEX_CLIENT_ID_SHA256" in sign_text
    assert "EXPECTED_VKID_CLIENT_ID_SHA256" in sign_text
    assert "EXPECTED_VKID_CLIENT_SECRET_SHA256" in sign_text
    assert "vkid_client_secret_sha256=$EXPECTED_VKID_CLIENT_SECRET_SHA256" in sign_text
    assert "unsigned_artifact_digest=$UNSIGNED_ARTIFACT_DIGEST" in sign_text
    assert "apksigner" in sign_text
    assert "zipalign" in sign_text
    assert "certificate_sha256=$actual_cert" in sign_text
    assert "signing_boundary=checkout-free" in sign_text

    gradle = ANDROID_BUILD_GRADLE.read_text(encoding="utf-8")
    assert (
        'providers.environmentVariable(name)\n        .orElse(providers.gradleProperty(name))'
        in gradle
    )
    assert 'configuredValue("ANDROID_UNSIGNED_DISTRIBUTION")' in gradle
    assert "unsignedDistributionBuild -> null" in gradle
    assert 'setOf("staging", "production", "recovery-production", "recovery-staging")' in gradle
    assert 'create("recoveryProduction")' in gradle
    assert 'create("recoveryStaging")' in gradle
    assert 'getByName("testRecoveryProduction").java.srcDir("src/testRecovery/java")' in gradle
    assert 'getByName("testRecoveryStaging").java.srcDir("src/testRecovery/java")' in gradle
    assert 'listOf("recoveryProduction", "recoveryStaging")' in gradle
    assert "Staging distribution endpoints must equal the reviewed German staging pair" in gradle
    assert 'buildConfigField("String", "YANDEX_CLIENT_ID_SHA256"' in gradle
    assert 'buildConfigField("String", "VKID_CLIENT_ID_SHA256"' in gradle
    assert 'buildConfigField("String", "VKID_CLIENT_SECRET_SHA256"' in gradle
    assert 'configuredValue("EMAIL_PASSWORD_AUTH_ENABLED")' in gradle
    assert 'distributionChannel == "production" && emailPasswordAuthOverride == true' in gradle
    assert '"production", "recovery-production", "recovery-staging" -> false' in gradle


def test_fresh_android_checkout_defaults_to_live_real_oauth_beta():
    properties = {
        key: value
        for raw_line in ANDROID_GRADLE_PROPERTIES.read_text(encoding="utf-8").splitlines()
        if (line := raw_line.strip()) and not line.startswith("#") and "=" in line
        for key, value in [line.split("=", maxsplit=1)]
    }
    assert properties["ACCOUNT_REAL_OAUTH"] == "true"
    assert properties["STAGING_APP_API_BASE_URL"] == (
        "https://pdis.arbuz.lol/plantdisease-test/"
    )
    assert properties["STAGING_ACCOUNT_API_BASE_URL"] == (
        "https://pdis.arbuz.lol/plantdisease-test/id/"
    )

    gradle = ANDROID_BUILD_GRADLE.read_text(encoding="utf-8")
    assert '"", "true" -> true' in gradle
    assert ".ifBlank { stagingAppApiBaseUrl }" in gradle
    assert ".ifBlank { stagingAccountApiBaseUrl }" in gradle
    assert 'buildConfigField("boolean", "ACCOUNT_UI_ENABLED", "true")' in gradle
    assert 'distributionChannel.ifBlank { "local-beta" }' in gradle
    assert 'debug {' in gradle
    assert "?.takeIf { it.storeFile != null }" in gradle

    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "Verify zero-configuration Yandex beta build mode" in workflow


def test_private_repository_android_ci_uses_github_basic_gradle_cache():
    workflow = _yaml(CI_WORKFLOW)
    android_steps = workflow["jobs"]["android"]["steps"]
    setup = next(
        step
        for step in android_steps
        if str(step.get("uses", "")).startswith("gradle/actions/setup-gradle@")
    )
    assert setup["with"] == {"cache-provider": "basic"}


def test_android_ci_does_not_receive_release_signing_secrets():
    workflow = _yaml(CI_WORKFLOW)
    workflow_text = json.dumps(workflow)
    assert "RELEASE_" not in workflow_text
    assert "environment" not in workflow["jobs"]["android"]


def test_database_role_provisioning_is_idempotent_and_url_safe():
    for path, bootstrap in (
        (GERMAN_ROLE_INIT, "beta_account"),
        (RU_ROLE_INIT, "account_bootstrap"),
    ):
        text = path.read_text(encoding="utf-8")
        assert text.startswith("#!/bin/sh\n#")
        assert "(\nset -eu" in text
        assert "must be exactly 64 hexadecimal characters" in text
        assert "WHERE NOT EXISTS (SELECT 1 FROM pg_roles" in text
        assert "NOBYPASSRLS" in text
        assert "FROM pg_auth_members membership" in text
        assert f"REASSIGN OWNED BY {bootstrap}" in text
        assert "REVOKE ALL ON DATABASE" in text
        assert "REVOKE ALL ON ALL TABLES IN SCHEMA public" in text
        assert "REVOKE ALL ON TABLES FROM" in text
        assert "WITH runtime_tables(tablename) AS" in text
        assert "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE %I.%I" in text
        assert "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES" not in text
        assert "GRANT SELECT ON TABLE public.alembic_version" in text
        assert "GRANT USAGE, SELECT ON ALL SEQUENCES" not in text
        assert "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO" not in text
        for table in ACCOUNT_TABLES - {"alembic_version"}:
            assert f"('{table}')" in text
        sql_body = text.split("<<'SQL'\n", maxsplit=1)[1].split("\nSQL", maxsplit=1)[0]
        assert not any(line.lstrip().startswith("#") for line in sql_body.splitlines())

    german_text = GERMAN_ROLE_INIT.read_text(encoding="utf-8")
    assert "ALTER TABLE %I.%I OWNER TO beta_account_migrator" in german_text
    assert "ALTER SEQUENCE %I.%I OWNER TO beta_account_migrator" in german_text
    assert "ALTER DATABASE %I OWNER TO beta_account_migrator" in german_text
    assert "rolname = 'beta_account') <> 10" in german_text


def test_post_migration_grant_convergence_is_owner_scoped_and_fail_closed():
    text = GRANT_CONVERGENCE.read_text(encoding="utf-8")
    assert f'SCHEMA_HEAD = "{ACCOUNT_SCHEMA_HEAD}"' in text
    assert "current_user != contract.migrator" in text
    assert "is_superuser is not False" in text
    assert "database table inventory differs from the reviewed schema" in text
    assert "database has an unreviewed runtime sequence" in text
    assert "REVOKE ALL ON ALL TABLES IN SCHEMA public" in text
    assert "GRANT SELECT ON TABLE public.alembic_version" in text
    assert "REVOKE ALL ON SEQUENCES" in text
    assert "database_url" not in text.split("except Exception", maxsplit=1)[1]
    for table in ACCOUNT_TABLES - {"alembic_version"}:
        assert f'"{table}"' in text

    german = _yaml(GERMAN_COMPOSE)["services"]["beta-account-migrate"]
    german_script = german["command"][2]
    assert german_script.index("alembic upgrade head") < german_script.index(
        "converge-runtime-grants.py --contract german-beta"
    )
    german_mount = next(
        mount for mount in german["volumes"] if "converge-runtime-grants.py" in mount
    )
    assert (DEPLOY / _mount_source(german_mount)).resolve() == GRANT_CONVERGENCE.resolve()

    russian = _yaml(RU_COMPOSE)["services"]["ru-account-migrate"]
    russian_script = russian["command"][2]
    assert russian_script.index("alembic upgrade head") < russian_script.index(
        "converge-runtime-grants.py --contract russian-production"
    )
    russian_mount = next(
        mount for mount in russian["volumes"] if "converge-runtime-grants.py" in mount
    )
    assert (HANDOFF / _mount_source(russian_mount)).resolve() == GRANT_CONVERGENCE.resolve()


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
    account = services["ru-account-api"]
    assert "nickname_propagation_jobs" in account["persists"]
    assert account["schema_revision"] == ACCOUNT_SCHEMA_HEAD
    assert set(account["database_tables"]) == ACCOUNT_TABLES
    assert set(account["account_features"]) == {
        "installation_bound_pending_signup",
        "two_phase_signup_completion",
        "bounded_signup_completion_idempotency",
        "explicit_signup_confirmation_or_cancellation",
        "email_password_backend_beta",
        "keyed_email_lookup_and_argon2id",
        "runtime_read_only_migration_metadata",
        "nickname_claim_registry",
        "durable_nickname_propagation",
        "server_verified_rustore_subscription_v5",
        "dedicated_billing_external_account_binding",
        "encrypted_billing_notification_deduplication",
        "purchase_scoped_canonical_fetch_generation",
        "bounded_billing_dead_letter_and_manual_replay",
        "atomic_retry_safe_daily_feature_quotas",
        "versioned_feature_quota_policy_and_exact_once_release",
        "atomic_global_plantnet_attempt_and_cost_budget",
    }
    assert {
        "email_address",
        "password",
        "billing_external_id",
        "rustore_purchase_id",
        "subscription_state",
        "feature_usage",
    } <= set(services["ru-ingress"]["must_not_send_to_de"])
    assert account["release_holds"] == {
        "email_password": [
            "inbox_ownership_verification",
            "password_recovery",
            "shared_multi_replica_abuse_controls",
        ],
        "rustore_pro": [
            "console_product_and_legal_approval",
            "production_rsa_and_aes_secret_provisioning",
            "live_v5_callback_and_reconciliation_evidence",
            "quota_cost_and_rollback_evidence",
        ],
    }
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
    openrouter = services["ru-app-api"]["optional_external_processors"]["openrouter"]
    assert openrouter["retention_requirement"] == "zero"
    assert openrouter["data_collection_requirement"] == "deny"
    assert openrouter["release_gate"] == (
        "disabled_until_privacy_region_legal_and_budget_approval"
    )
    assert {"authorization", "account_id", "billing_identifier"} <= set(
        openrouter["forbidden"]
    )
    plantnet = services["ru-app-api"]["optional_external_processors"]["plantnet"]
    assert plantnet["endpoint"] == "my-api.plantnet.org"
    assert plantnet["provider_image_retention"] == "volatile_memory_only"
    assert plantnet["release_gate"] == (
        "disabled_until_global_budget_contract_attribution_and_legal_approval"
    )
    assert {"account_id", "installation_id", "billing_identifier"} <= set(
        plantnet["forbidden"]
    )
    fcm = account["optional_external_processors"]["firebase_cloud_messaging"]
    assert fcm["location"] == "global Google infrastructure"
    assert fcm["release_gate"] == (
        "disabled_until_operator_privacy_transfer_and_delivery_sink_approval"
    )
    assert {"oauth_token", "email_address", "billing_identifier", "image"} <= set(
        fcm["forbidden"]
    )
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
    assert ru["networks"]["ru-ingress-backend"]["internal"] is True
    assert ru["networks"]["ru-db"]["internal"] is True
    assert ru["networks"]["ru-egress-clients"] == {"internal": True}
    assert ru["networks"]["ru-egress"] == {}
    assert "ports" not in ru["services"]["ru-account-api"]
    assert "ports" not in ru["services"]["ru-app-api"]
    ru_app = ru["services"]["ru-app-api"]["environment"]
    assert ru_app["FORWARDED_ALLOW_IPS"] == "${RU_INGRESS_BACKEND_IP:-172.30.0.10}"
    assert ru_app["FCM_CREDENTIALS_FILE"] == ""
    assert ru_app["ACCOUNT_NOTIFICATION_URL"] == ""
    assert ru_app["ACCOUNT_FEATURE_EXPECTED"] == "true"
    assert ru_app["ML_WORKER_FORWARD_AUTHORIZATION"] == "false"
    assert ru_app["ML_WORKER_ALLOWED_FIELDS"] == (
        "image,request_id,model_family,language,result_options"
    )
    assert ru_app["OPENROUTER_API_KEY_FILE"] == "${OPENROUTER_API_KEY_FILE:-}"
    assert "OPENROUTER_API_KEY" not in ru_app
    assert ru_app["OPENROUTER_BASE_URL"] == "https://openrouter.ai/api/v1"
    assert ru_app["COMMUNITY_DB_PATH"].startswith("/data/")
    assert ru_app["TRAINING_DB_PATH"].startswith("/data/")
    assert ru_app["TELEMETRY_DB_PATH"].startswith("/data/")
    assert ru_app["HTTPS_PROXY"] == "http://ru-egress-proxy:3128"
    assert "PLANTNET_API_KEY" not in ru_app
    assert ru_app["PLANTNET_API_KEY_FILE"] == "${PLANTNET_API_KEY_FILE:-}"

    egress = ru["services"]["ru-egress-proxy"]
    assert set(egress["networks"]) == {"ru-egress-clients", "ru-egress"}
    assert "ru-push-worker" not in ru["services"]
    for service_name in ("ru-account-api", "ru-app-api", "ru-backup"):
        assert "ru-egress" not in ru["services"][service_name]["networks"]
        assert "ru-egress-clients" in ru["services"][service_name]["networks"]
    assert "ru-db" not in ru["services"]["ru-ingress"]["networks"]
    assert "ru-ingress-backend" not in ru["services"]["ru-postgres"]["networks"]
    assert set(ru["services"]["ru-postgres"]["networks"]) == {"ru-db"}

    egress_policy = RU_EGRESS_POLICY.read_text(encoding="utf-8")
    for exact_content_host in (
        "en.wikipedia.org",
        "ru.wikipedia.org",
        "commons.wikimedia.org",
        "upload.wikimedia.org",
        "translate.googleapis.com",
    ):
        assert exact_content_host in egress_policy
    assert "http_access allow CONNECT TLS_port content_hosts" in egress_policy
    assert "acl billing_hosts dstdomain public-api.rustore.ru" in egress_policy
    assert "http_access allow CONNECT TLS_port billing_hosts" in egress_policy
    assert "acl plantnet_hosts dstdomain my-api.plantnet.org" in egress_policy
    assert "http_access allow CONNECT TLS_port plantnet_hosts" in egress_policy
    assert "acl llm_router_host dstdomain openrouter.ai" in egress_policy
    assert "http_access allow CONNECT TLS_port llm_router_host" in egress_policy
    assert ru["services"]["ru-account-migrate"]["depends_on"][
        "ru-account-role-provision"
    ]["condition"] == "service_completed_successfully"
    assert ru["services"]["ru-account-api"]["depends_on"] == {
        "ru-account-migrate": {"condition": "service_completed_successfully"}
    }

    account_environment = ru["services"]["ru-account-api"]["environment"]
    assert account_environment["EMAIL_PASSWORD_AUTH_ENABLED"] == "false"
    assert account_environment["PLANTNET_BUDGET_ENABLED"] == (
        "${PLANTNET_BUDGET_ENABLED:-false}"
    )
    assert account_environment["PLANTNET_BUDGET_POLICY_VERSION"] == (
        "${PLANTNET_BUDGET_POLICY_VERSION:-}"
    )
    assert account_environment["PLANTNET_DAILY_ATTEMPT_LIMIT"] == (
        "${PLANTNET_DAILY_ATTEMPT_LIMIT:-0}"
    )
    assert account_environment["PLANTNET_ATTEMPT_COST_MICROS"] == (
        "${PLANTNET_ATTEMPT_COST_MICROS:-0}"
    )
    assert account_environment["PLANTNET_DAILY_COST_LIMIT_MICROS"] == (
        "${PLANTNET_DAILY_COST_LIMIT_MICROS:-0}"
    )
    assert account_environment["COMMUNITY_DELETION_URL"] == (
        "http://ru-app-api:8000/api/internal/account-deletions"
    )
    assert "account_runtime" in account_environment["ACCOUNT_DATABASE_URL"]
    assert "account_migrator" in ru["services"]["ru-account-migrate"]["environment"][
        "ACCOUNT_DATABASE_URL"
    ]

    backup = ru["services"]["ru-backup"]
    assert backup["environment"]["BACKUP_POSTGRES_MODE"] == "pg-dump-custom"
    assert backup["environment"]["BACKUP_SQLITE_MODE"] == "online-backup-api"
    assert "account_backup" in backup["environment"]["BACKUP_DATABASE_URL"]
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
    assert environment["PLANTNET_API_KEY_FILE"] == ""
    assert environment["PLANTNET_BUDGET_ENABLED"] == "false"
    assert int(environment["PLANTNET_ATTEMPT_COST_MICROS"]) > 0
    assert int(environment["PLANTNET_DAILY_ATTEMPT_LIMIT"]) > 0
    assert int(environment["PLANTNET_DAILY_COST_LIMIT_MICROS"]) > 0
    secrets_inventory = SECRETS_INVENTORY.read_text(encoding="utf-8")
    assert "Optional `plantnet-api.key`" in secrets_inventory
    assert "${RU_APP_SECRETS_DIR}/plantnet-api.key" in secrets_inventory
    assert (
        "`PLANTNET_API_KEY_FILE=/run/app-secrets/plantnet-api.key`"
        in secrets_inventory
    )
    assert "first restore\n`PLANTNET_BUDGET_ENABLED=false`" in secrets_inventory
    assert environment["DE_ML_WORKER_URL"].split(":", maxsplit=1)[1].startswith(
        "//de-ml.private.invalid"
    )
    for name in (
        "RU_POSTGRES_ADMIN_PASSWORD",
        "RU_POSTGRES_RUNTIME_PASSWORD",
        "RU_POSTGRES_MIGRATOR_PASSWORD",
        "RU_POSTGRES_BACKUP_PASSWORD",
    ):
        assert environment[name].startswith("replace-")
