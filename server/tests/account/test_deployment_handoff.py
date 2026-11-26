"""Static deployment contracts; no host, provider, or production system is called."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEPLOY = REPOSITORY_ROOT / "deploy" / "account-beta-test"
GERMAN_COMPOSE = DEPLOY / "compose.german-vds.yml"
GERMAN_BUILD_COMPOSE = DEPLOY / "compose.german-vds.build.yml"
GERMAN_ENV = DEPLOY / "env.german.example"
NGINX = DEPLOY / "nginx.location.conf"
NGINX_TESTER_ALLOWLIST = DEPLOY / "nginx.testers.allow.example"
DEPLOY_README = DEPLOY / "README.md"
BACKUP = DEPLOY / "backup-test.sh"
PRIVACY = REPOSITORY_ROOT / "privacy.html"
RELEASE_POLICY_GATES = DEPLOY / "release-policy-gates.json"
CI_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"
ANDROID_BUILD_GRADLE = REPOSITORY_ROOT / "android" / "app" / "build.gradle.kts"
ANDROID_GRADLE_PROPERTIES = REPOSITORY_ROOT / "android" / "gradle.properties"
GERMAN_ROLE_INIT = DEPLOY / "postgres-init" / "10-account-roles.sh"
GRANT_CONVERGENCE = DEPLOY / "converge-runtime-grants.py"
PRODUCTION_COMPOSE = (
    REPOSITORY_ROOT / "deploy" / "production-vds" / "compose.production.yml"
)

ACCOUNT_SCHEMA_HEAD = "0011_community_roles_rewards"
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
    "community_roles",
    "community_rewards",
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
        "${ENTITLEMENT_QUOTA_POLICY_VERSION:-launch-2026-11-v2}"
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


def test_production_records_consented_error_reports():
    # Production was cut from the staging file, which keeps telemetry off, and
    # silently dropped every opted-in app error report until this was pinned.
    app_service = _yaml(PRODUCTION_COMPOSE)["services"]["prod-app-api"]
    app = app_service["environment"]
    assert app["TELEMETRY_ENABLED"] == "true"
    consent_prefs = (
        REPOSITORY_ROOT / "android" / "app" / "src" / "main" / "java" / "com"
        / "plantdiseases" / "app" / "util" / "PrivacyConsentPrefs.kt"
    ).read_text(encoding="utf-8")
    app_version = re.search(r'TELEMETRY_CONSENT_VERSION = "([^"]+)"', consent_prefs)
    assert app_version, "PrivacyConsentPrefs.TELEMETRY_CONSENT_VERSION not found"
    # A mismatch answers every report with 422 telemetry_consent_version_invalid.
    assert app["TELEMETRY_CONSENT_VERSION"] == app_version.group(1)
    assert app["TELEMETRY_DB_PATH"] == "/data/telemetry/telemetry.db"
    assert "prod_telemetry:/data/telemetry" in app_service["volumes"]


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
    assert "EMAIL_PASSWORD_AUTH_ENABLED=true" in operator_contract
    assert "named tester" in operator_contract


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


def test_privacy_policy_matches_account_release():
    privacy = PRIVACY.read_text(encoding="utf-8")
    assert "fonts.googleapis.com" not in privacy
    assert "fonts.gstatic.com" not in privacy
    assert "retained for model improvement, a use you accept when you first open the app" in privacy
    assert "сохраняется для улучшения моделей — это использование вы принимаете при первом запуске" in privacy
    assert "account-service endpoint scope, including its base path" in privacy
    # The first release is Yandex-only with push and billing off (plan decision 3);
    # the policy must not offer or describe sign-in methods or services that are not live.
    assert "You can sign in with Yandex ID. We store" in privacy
    for not_live in (
        "VK ID",
        "email address and password",
        "Argon2id",
        "Firebase Cloud Messaging",
        "RuStore",
    ):
        assert not_live not in privacy, f"policy still describes {not_live}"
    assert "Publishing a scan or a forum post requires sign-in" in privacy
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
    assert tombstone["approved_at_utc"].endswith("Z")
    assert "30" in tombstone["disclosure_en"] and "30" in tombstone["disclosure_ru"]
    assert tombstone["disclosure_en"] != tombstone["disclosure_ru"]
    contact = gates["private_contact"]
    assert contact["status"] == "approved"
    assert contact["uri"] == "mailto:gavel8980@gmail.com"
    assert contact["policy_link_id"] == "privacy-private-contact"
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


def test_android_gradle_keeps_distribution_channel_contract():
    gradle = ANDROID_BUILD_GRADLE.read_text(encoding="utf-8")
    assert (
        'providers.environmentVariable(name)\n        .orElse(providers.gradleProperty(name))'
        in gradle
    )
    assert 'configuredValue("ANDROID_UNSIGNED_DISTRIBUTION")' in gradle
    assert "unsignedDistributionBuild -> null" in gradle
    assert 'setOf("production", "recovery-production")' in gradle
    assert 'create("recoveryProduction")' in gradle
    # Every build targets production; there is no staging build type or URL.
    assert "plantdisease-test" not in gradle
    assert 'create("staging")' not in gradle
    assert 'create("recoveryStaging")' not in gradle
    # The identity tests must live in AGP's standard per-variant source sets: a
    # custom java.srcDir for a .kt file compiled nothing (javac NO-SOURCE) and
    # the test silently never ran.
    identity_test = (
        ANDROID_BUILD_GRADLE.parent
        / "src"
        / "testRecoveryProduction"
        / "java"
        / "com"
        / "plantdiseases"
        / "app"
        / "RecoveryBuildIdentityTest.kt"
    )
    assert identity_test.is_file()
    assert "testRecovery/java" not in gradle
    assert 'selector().withBuildType("recoveryProduction")' in gradle
    assert 'buildConfigField("String", "YANDEX_CLIENT_ID_SHA256"' in gradle
    assert 'buildConfigField("String", "VKID_CLIENT_ID_SHA256"' in gradle
    assert 'buildConfigField("String", "VKID_CLIENT_SECRET_SHA256"' in gradle
    assert 'configuredValue("EMAIL_PASSWORD_AUTH_ENABLED")' in gradle
    assert 'distributionChannel == "production" && emailPasswordAuthOverride == true' in gradle
    assert '"production", "recovery-production" -> false' in gradle
    assert "else -> emailPasswordAuthOverride ?: false" in gradle


def test_fresh_android_checkout_defaults_to_production_with_real_oauth():
    properties = {
        key: value
        for raw_line in ANDROID_GRADLE_PROPERTIES.read_text(encoding="utf-8").splitlines()
        if (line := raw_line.strip()) and not line.startswith("#") and "=" in line
        for key, value in [line.split("=", maxsplit=1)]
    }
    assert properties["ACCOUNT_REAL_OAUTH"] == "true"
    assert not any("API_BASE_URL" in key for key in properties)

    gradle = ANDROID_BUILD_GRADLE.read_text(encoding="utf-8")
    assert '"", "true" -> true' in gradle
    assert 'val productionAppApiBaseUrl = "https://pdis.arbuz.lol/"' in gradle
    assert 'val productionAccountApiBaseUrl = "https://pdis.arbuz.lol/id/"' in gradle
    assert ".ifBlank { productionAppApiBaseUrl }" in gradle
    assert ".ifBlank { productionAccountApiBaseUrl }" in gradle
    assert 'buildConfigField("boolean", "ACCOUNT_UI_ENABLED", "true")' in gradle
    assert 'debug {' in gradle
    assert "?.takeIf { it.storeFile != null }" in gradle


def test_private_repository_android_ci_uses_github_basic_gradle_cache():
    workflow = _yaml(CI_WORKFLOW)
    android_steps = workflow["jobs"]["android"]["steps"]
    setup = next(
        step
        for step in android_steps
        if str(step.get("uses", "")).startswith("gradle/actions/setup-gradle@")
    )
    assert setup["with"] == {"cache-provider": "basic"}


def test_android_ci_signs_with_the_permanent_key_and_proves_it():
    workflow = _yaml(CI_WORKFLOW)
    for name, job in workflow["jobs"].items():
        if name != "android":
            assert "RELEASE_" not in json.dumps(job)
    steps = workflow["jobs"]["android"]["steps"]
    # The key is env for `run:` steps only; no third-party action receives it.
    for step in steps:
        if "uses" in step:
            assert "RELEASE_" not in json.dumps(step)
    names = [step.get("name", "") for step in steps]
    build = names.index("Build, test and sign (production server, permanent key)")
    verify = names.index("Verify the signing certificate")
    upload = names.index("Upload signed APK (release)")
    assert build < verify < upload
    assert steps[verify]["env"]["EXPECTED_CERT_SHA256"] == (
        "ef9563c5db397924c1f81239b9ed5fa1e9b233f034ce10eb75f16efd36a26367"
    )
    assert "if" not in steps[verify] and "if" not in steps[upload]
    command = steps[build]["run"]
    assert "assembleRelease" in command
    assert "assembleRecoveryProduction" not in command
    assert "Staging" not in command


def test_database_role_provisioning_is_idempotent_and_url_safe():
    for path, bootstrap in (
        (GERMAN_ROLE_INIT, "beta_account"),
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

    production_script = PRODUCTION_COMPOSE.read_text(encoding="utf-8")
    assert production_script.index("alembic upgrade head") < production_script.index(
        "converge-runtime-grants.py --contract production"
    )
