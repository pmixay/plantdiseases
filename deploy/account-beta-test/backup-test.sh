#!/usr/bin/env bash
# Create and restore-check one encrypted German-beta PostgreSQL dump.
# This never stops services, deletes volumes, or touches an existing database.

set -euo pipefail
set -o noclobber
umask 077

if [ "$#" -ne 3 ]; then
    echo "usage: $0 /absolute/path/to/german.env /absolute/protected/output-dir /absolute/path/to/age-identity" >&2
    exit 64
fi

env_file=$1
output_dir=$2
age_identity=$3
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
compose_file="$script_dir/compose.german-vds.yml"

case "$env_file" in
    /*) ;;
    *) echo "environment file path must be absolute" >&2; exit 64 ;;
esac
case "$output_dir" in
    /*) ;;
    *) echo "output directory path must be absolute" >&2; exit 64 ;;
esac
case "$age_identity" in
    /*) ;;
    *) echo "age identity path must be absolute" >&2; exit 64 ;;
esac

if [ ! -f "$env_file" ] || [ ! -d "$output_dir" ]; then
    echo "environment file and existing output directory are required" >&2
    exit 66
fi
if [ -L "$output_dir" ]; then
    echo "output directory must not be a symbolic link" >&2
    exit 65
fi
output_dir=$(CDPATH= cd -- "$output_dir" && pwd -P)
if [ "$output_dir" = / ]; then
    echo "output directory must not be the filesystem root" >&2
    exit 65
fi
if [ ! -f "$age_identity" ] || [ -L "$age_identity" ]; then
    echo "an existing non-symlink age identity file is required" >&2
    exit 66
fi
identity_mode=$(stat -c '%a' "$age_identity")
case "$identity_mode" in
    400|600) ;;
    *) echo "age identity must have mode 0400 or 0600" >&2; exit 65 ;;
esac
command -v age >/dev/null 2>&1 || { echo "age is required" >&2; exit 69; }
command -v age-keygen >/dev/null 2>&1 || { echo "age-keygen is required" >&2; exit 69; }
age_recipient=$(age-keygen -y "$age_identity")
test -n "$age_recipient"
if [ "$(grep -c '^COMPOSE_PROJECT_NAME=' "$env_file")" -ne 1 ] || \
   ! grep -qx 'COMPOSE_PROJECT_NAME=plantdisease_account_beta_german' "$env_file"; then
    echo "refusing any Compose project except plantdisease_account_beta_german" >&2
    exit 65
fi
if [ "$(grep -c '^BETA_VOLUME_PREFIX=' "$env_file")" -ne 1 ] || \
   ! grep -qx 'BETA_VOLUME_PREFIX=plantdisease_account_beta_german' "$env_file"; then
    echo "refusing any non-dedicated German beta volume prefix" >&2
    exit 65
fi

# Shell environment variables outrank --env-file interpolation in Compose.
# Pin both values here and also pass --project-name so an ambient override can
# never redirect this script at a different stack or volume namespace.
canonical_project=plantdisease_account_beta_german
COMPOSE_PROJECT_NAME=$canonical_project
BETA_VOLUME_PREFIX=$canonical_project
export COMPOSE_PROJECT_NAME BETA_VOLUME_PREFIX

compose() {
    docker compose --project-name "$canonical_project" \
        --env-file "$env_file" -f "$compose_file" "$@"
}

rendered_compose=$(compose config)
for volume_suffix in \
    postgres account_keys internal_token app_data community thumbnails \
    training training_images telemetry app_cache app_logs; do
    rendered_name="name: ${canonical_project}_${volume_suffix}"
    if [ "$(printf '%s\n' "$rendered_compose" | grep -Fc "$rendered_name")" -ne 1 ]; then
        echo "rendered Compose volume is outside the dedicated beta namespace: $rendered_name" >&2
        exit 65
    fi
done
compose exec -T beta-postgres pg_isready -U beta_account -d plantdisease_beta >/dev/null

# A hard-killed earlier run can leave a plaintext restore database. Never
# create another copy or silently delete evidence; make the operator inspect
# and remove the exact stale database before retrying.
stale_restore=$(compose exec -T beta-postgres psql \
    -X -A -t -v ON_ERROR_STOP=1 -U beta_account -d postgres \
    -c "SELECT datname FROM pg_database WHERE left(datname, 13) = 'beta_restore_' ORDER BY 1;")
if [ -n "$stale_restore" ]; then
    echo "stale plaintext restore database found; inspect and remove it before retrying: $stale_restore" >&2
    exit 65
fi

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_name="plantdisease-beta-$timestamp.dump.age"
backup_path="$output_dir/$backup_name"
partial_path="$backup_path.partial"
checksum_path="$backup_path.sha256"
restore_db="beta_restore_${timestamp}_$$"
lock_dir="$output_dir/.plantdisease-beta-backup.lock"
lock_acquired=0

if [ -e "$backup_path" ] || [ -e "$partial_path" ] || [ -e "$checksum_path" ]; then
    echo "backup target already exists" >&2
    exit 73
fi
if ! mkdir -- "$lock_dir"; then
    echo "another backup check is active, or a stale backup lock needs inspection: $lock_dir" >&2
    exit 75
fi
lock_acquired=1

cleanup() {
    compose exec -T beta-postgres dropdb \
        --if-exists --force -U beta_account "$restore_db" >/dev/null 2>&1 || true
    if [ -f "$partial_path" ]; then
        rm -f -- "$partial_path"
    fi
    if [ "$lock_acquired" -eq 1 ]; then
        rmdir -- "$lock_dir" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

compose exec -T beta-postgres pg_dump \
    --format=custom --compress=9 --no-owner --no-privileges \
    -U beta_account_backup plantdisease_beta | \
    age --encrypt --recipient "$age_recipient" >"$partial_path"
test -s "$partial_path"

compose exec -T beta-postgres createdb \
    --template=template0 --owner=beta_account_migrator \
    -U beta_account "$restore_db"
age --decrypt --identity "$age_identity" "$partial_path" | \
compose exec -T beta-postgres pg_restore \
    --exit-on-error --no-owner --no-privileges \
    --role=beta_account_migrator -U beta_account -d "$restore_db"

# A portable dump deliberately carries neither cluster role definitions nor
# grants. Reconstruct the same least-privilege contract a disaster restore must
# apply before the API starts, then test through the real runtime/backup roles.
compose exec -T beta-postgres psql \
    -X -v ON_ERROR_STOP=1 -U beta_account -d postgres \
    -c "REVOKE ALL ON DATABASE \"$restore_db\" FROM PUBLIC, beta_account_migrator, beta_account_runtime, beta_account_backup; GRANT CONNECT, TEMPORARY ON DATABASE \"$restore_db\" TO beta_account_migrator; GRANT CONNECT ON DATABASE \"$restore_db\" TO beta_account_runtime, beta_account_backup;"
compose exec -T beta-postgres psql \
    -X -v ON_ERROR_STOP=1 -U beta_account -d "$restore_db" <<'SQL'
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO beta_account_migrator;
REVOKE ALL ON SCHEMA public FROM beta_account_runtime, beta_account_backup;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM beta_account_runtime, beta_account_backup;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM beta_account_runtime, beta_account_backup;
GRANT USAGE ON SCHEMA public TO beta_account_runtime, beta_account_backup;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE
    account_consents,
    account_deletion_jobs,
    account_identities,
    account_nickname_claims,
    account_nickname_jobs,
    account_security_events,
    accounts,
    billing_customers,
    billing_purchase_fetches,
    billing_notification_receipts,
    email_codes,
    feature_usage,
    feature_usage_receipts,
    password_credentials,
    pending_password_credentials,
    pending_signups,
    push_devices,
    reauth_challenges,
    refresh_tokens,
    sessions,
    signup_completions,
    subscriptions
TO beta_account_runtime;
GRANT SELECT ON TABLE alembic_version TO beta_account_runtime;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO beta_account_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE beta_account_migrator IN SCHEMA public
    REVOKE ALL ON TABLES FROM beta_account_runtime, beta_account_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE beta_account_migrator IN SCHEMA public
    REVOKE ALL ON SEQUENCES FROM beta_account_runtime, beta_account_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE beta_account_migrator IN SCHEMA public
    REVOKE ALL ON TYPES FROM beta_account_runtime, beta_account_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE beta_account_migrator IN SCHEMA public
    GRANT USAGE ON TYPES TO beta_account_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE beta_account_migrator IN SCHEMA public
    GRANT SELECT ON TABLES TO beta_account_backup;
SQL

inventory_sql="SELECT 'account_consents=' || count(*) FROM account_consents
UNION ALL SELECT 'account_deletion_jobs=' || count(*) FROM account_deletion_jobs
UNION ALL SELECT 'account_identities=' || count(*) FROM account_identities
UNION ALL SELECT 'account_nickname_claims=' || count(*) FROM account_nickname_claims
UNION ALL SELECT 'account_nickname_jobs=' || count(*) FROM account_nickname_jobs
UNION ALL SELECT 'account_security_events=' || count(*) FROM account_security_events
UNION ALL SELECT 'accounts=' || count(*) FROM accounts
UNION ALL SELECT 'billing_customers=' || count(*) FROM billing_customers
UNION ALL SELECT 'billing_purchase_fetches=' || count(*) FROM billing_purchase_fetches
UNION ALL SELECT 'billing_notification_receipts=' || count(*) FROM billing_notification_receipts
UNION ALL SELECT 'feature_usage=' || count(*) FROM feature_usage
UNION ALL SELECT 'feature_usage_receipts=' || count(*) FROM feature_usage_receipts
UNION ALL SELECT 'password_credentials=' || count(*) FROM password_credentials
UNION ALL SELECT 'pending_password_credentials=' || count(*) FROM pending_password_credentials
UNION ALL SELECT 'pending_signups=' || count(*) FROM pending_signups
UNION ALL SELECT 'push_devices=' || count(*) FROM push_devices
UNION ALL SELECT 'reauth_challenges=' || count(*) FROM reauth_challenges
UNION ALL SELECT 'refresh_tokens=' || count(*) FROM refresh_tokens
UNION ALL SELECT 'sessions=' || count(*) FROM sessions
UNION ALL SELECT 'signup_completions=' || count(*) FROM signup_completions
UNION ALL SELECT 'subscriptions=' || count(*) FROM subscriptions
UNION ALL SELECT 'vendor_budget_authorities=' || count(*) FROM vendor_budget_authorities
UNION ALL SELECT 'vendor_budget_daily=' || count(*) FROM vendor_budget_daily
UNION ALL SELECT 'vendor_budget_receipts=' || count(*) FROM vendor_budget_receipts
UNION ALL SELECT 'community_roles=' || count(*) FROM community_roles
UNION ALL SELECT 'community_rewards=' || count(*) FROM community_rewards
UNION ALL SELECT 'email_codes=' || count(*) FROM email_codes
ORDER BY 1;"

expected_schema_revision=0012_email_verification
source_revision=$(compose exec -T beta-postgres psql \
    -X -A -t -v ON_ERROR_STOP=1 -U beta_account_backup -d plantdisease_beta \
    -c "SELECT version_num FROM alembic_version;")
restored_revision=$(compose exec -T beta-postgres psql \
    -X -A -t -v ON_ERROR_STOP=1 -U beta_account_backup -d "$restore_db" \
    -c "SELECT version_num FROM alembic_version;")
if [ "$source_revision" != "$expected_schema_revision" ] || \
   [ "$restored_revision" != "$expected_schema_revision" ]; then
    echo "source or restored database is not at required schema revision $expected_schema_revision" >&2
    exit 1
fi

expected_table_inventory="account_consents
account_deletion_jobs
account_identities
account_nickname_claims
account_nickname_jobs
account_security_events
accounts
alembic_version
billing_customers
billing_notification_receipts
billing_purchase_fetches
community_rewards
community_roles
email_codes
feature_usage
feature_usage_receipts
password_credentials
pending_password_credentials
pending_signups
push_devices
reauth_challenges
refresh_tokens
sessions
signup_completions
subscriptions
vendor_budget_authorities
vendor_budget_daily
vendor_budget_receipts"
table_inventory_sql="SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
ORDER BY table_name COLLATE \"C\";"
source_tables=$(compose exec -T beta-postgres psql \
    -X -A -t -v ON_ERROR_STOP=1 -U beta_account_backup -d plantdisease_beta \
    -c "$table_inventory_sql")
restored_tables=$(compose exec -T beta-postgres psql \
    -X -A -t -v ON_ERROR_STOP=1 -U beta_account_backup -d "$restore_db" \
    -c "$table_inventory_sql")
if [ "$source_tables" != "$expected_table_inventory" ] || \
   [ "$restored_tables" != "$expected_table_inventory" ]; then
    echo "source or restored database table inventory differs from the reviewed schema" >&2
    exit 1
fi

source_sequences=$(compose exec -T beta-postgres psql \
    -X -A -t -v ON_ERROR_STOP=1 -U beta_account_backup -d plantdisease_beta \
    -c "SELECT sequence_name FROM information_schema.sequences WHERE sequence_schema = 'public' ORDER BY sequence_name COLLATE \"C\";")
restored_sequences=$(compose exec -T beta-postgres psql \
    -X -A -t -v ON_ERROR_STOP=1 -U beta_account_backup -d "$restore_db" \
    -c "SELECT sequence_name FROM information_schema.sequences WHERE sequence_schema = 'public' ORDER BY sequence_name COLLATE \"C\";")
if [ -n "$source_sequences" ] || [ -n "$restored_sequences" ]; then
    echo "source or restored database has an unreviewed runtime sequence" >&2
    exit 1
fi

source_inventory=$(compose exec -T beta-postgres psql \
    -X -A -t -v ON_ERROR_STOP=1 -U beta_account_backup -d plantdisease_beta \
    -c "$inventory_sql")
restored_inventory=$(compose exec -T beta-postgres psql \
    -X -A -t -v ON_ERROR_STOP=1 -U beta_account_backup -d "$restore_db" \
    -c "$inventory_sql")
if [ "$source_inventory" != "$restored_inventory" ]; then
    echo "restore row inventory differs from source" >&2
    exit 1
fi

role_contract_sql="SELECT
  (SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()),
  (SELECT schema_owner FROM information_schema.schemata WHERE schema_name = 'public'),
  COALESCE((SELECT bool_and(tableowner = 'beta_account_migrator') FROM pg_tables WHERE schemaname = 'public'), false),
  COALESCE((SELECT count(*) = 20 AND bool_and(
      has_table_privilege('beta_account_runtime', format('%I.%I', schemaname, tablename), 'SELECT') AND
      has_table_privilege('beta_account_runtime', format('%I.%I', schemaname, tablename), 'INSERT') AND
      has_table_privilege('beta_account_runtime', format('%I.%I', schemaname, tablename), 'UPDATE') AND
      has_table_privilege('beta_account_runtime', format('%I.%I', schemaname, tablename), 'DELETE')
  ) FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'alembic_version'), false),
  has_table_privilege('beta_account_runtime', 'public.alembic_version', 'SELECT') AND
  NOT has_table_privilege('beta_account_runtime', 'public.alembic_version', 'INSERT') AND
  NOT has_table_privilege('beta_account_runtime', 'public.alembic_version', 'UPDATE') AND
  NOT has_table_privilege('beta_account_runtime', 'public.alembic_version', 'DELETE'),
  COALESCE((SELECT bool_and(
      has_table_privilege('beta_account_backup', format('%I.%I', schemaname, tablename), 'SELECT') AND
      NOT has_table_privilege('beta_account_backup', format('%I.%I', schemaname, tablename), 'INSERT') AND
      NOT has_table_privilege('beta_account_backup', format('%I.%I', schemaname, tablename), 'UPDATE') AND
      NOT has_table_privilege('beta_account_backup', format('%I.%I', schemaname, tablename), 'DELETE')
  ) FROM pg_tables WHERE schemaname = 'public'), false),
  has_database_privilege('beta_account_runtime', current_database(), 'CONNECT') AND
  NOT has_database_privilege('beta_account_runtime', current_database(), 'CREATE') AND
  NOT has_database_privilege('beta_account_runtime', current_database(), 'TEMPORARY') AND
  has_database_privilege('beta_account_backup', current_database(), 'CONNECT') AND
  NOT has_database_privilege('beta_account_backup', current_database(), 'CREATE') AND
  NOT has_database_privilege('beta_account_backup', current_database(), 'TEMPORARY') AND
  has_schema_privilege('beta_account_runtime', 'public', 'USAGE') AND
  NOT has_schema_privilege('beta_account_runtime', 'public', 'CREATE') AND
  has_schema_privilege('beta_account_backup', 'public', 'USAGE') AND
  NOT has_schema_privilege('beta_account_backup', 'public', 'CREATE'),
  COALESCE((SELECT count(*) = 3 AND bool_and(
      rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole AND
      NOT rolinherit AND NOT rolreplication AND NOT rolbypassrls
  ) FROM pg_roles WHERE rolname IN (
      'beta_account_migrator', 'beta_account_runtime', 'beta_account_backup'
  )), false),
  NOT EXISTS (
      SELECT 1
      FROM pg_auth_members membership
      JOIN pg_roles member ON member.oid = membership.member
      WHERE member.rolname IN (
          'beta_account_migrator', 'beta_account_runtime', 'beta_account_backup'
      )
  );"
role_contract=$(compose exec -T beta-postgres psql \
    -X -A -t -F '|' -v ON_ERROR_STOP=1 -U beta_account_backup -d "$restore_db" \
    -c "$role_contract_sql")
if [ "$role_contract" != "beta_account_migrator|beta_account_migrator|t|t|t|t|t|t|t" ]; then
    echo "restored database ownership or grants differ from the runtime contract: $role_contract" >&2
    exit 1
fi

# Exercise DML privilege checks even when the restored account and two-phase
# signup tables are empty.
compose exec -T beta-postgres psql \
    -X -v ON_ERROR_STOP=1 -U beta_account_runtime -d "$restore_db" \
    -c "BEGIN; SELECT count(*) FROM accounts; SELECT count(*) FROM password_credentials; SELECT count(*) FROM subscriptions; SELECT count(*) FROM signup_completions; INSERT INTO accounts (id) SELECT NULL::uuid WHERE false; INSERT INTO signup_completions (id, pending_signup_id, installation_id, request_fingerprint, created_at, expires_at) SELECT NULL::uuid, NULL::uuid, NULL::uuid, NULL::bytea, now(), now() WHERE false; UPDATE signup_completions SET expires_at = expires_at WHERE false; DELETE FROM signup_completions WHERE false; ROLLBACK;" >/dev/null
if compose exec -T beta-postgres psql \
    -X -v ON_ERROR_STOP=1 -U beta_account_runtime -d "$restore_db" \
    -c "CREATE TABLE account_restore_privilege_escape (id integer);" >/dev/null 2>&1; then
    echo "runtime role unexpectedly has schema DDL privileges after restore" >&2
    exit 1
fi
if compose exec -T beta-postgres psql \
    -X -v ON_ERROR_STOP=1 -U beta_account_runtime -d "$restore_db" \
    -c "UPDATE alembic_version SET version_num = version_num WHERE false;" >/dev/null 2>&1; then
    echo "runtime role unexpectedly has UPDATE privilege on migration metadata" >&2
    exit 1
fi
if compose exec -T beta-postgres psql \
    -X -v ON_ERROR_STOP=1 -U beta_account_runtime -d "$restore_db" \
    -c "DELETE FROM alembic_version WHERE false;" >/dev/null 2>&1; then
    echo "runtime role unexpectedly has DELETE privilege on migration metadata" >&2
    exit 1
fi
compose exec -T beta-postgres psql \
    -X -v ON_ERROR_STOP=1 -U beta_account_migrator -d "$restore_db" \
    -c "BEGIN; UPDATE alembic_version SET version_num = version_num; ROLLBACK;" >/dev/null
if compose exec -T beta-postgres psql \
    -X -v ON_ERROR_STOP=1 -U beta_account_backup -d "$restore_db" \
    -c "UPDATE signup_completions SET expires_at = expires_at WHERE false;" >/dev/null 2>&1; then
    echo "backup role unexpectedly has write privileges after restore" >&2
    exit 1
fi
if compose exec -T beta-postgres psql \
    -X -v ON_ERROR_STOP=1 -U beta_account_backup -d "$restore_db" \
    -c "CREATE TABLE account_restore_backup_escape (id integer);" >/dev/null 2>&1; then
    echo "backup role unexpectedly has schema DDL privileges after restore" >&2
    exit 1
fi

mv -- "$partial_path" "$backup_path"
(cd -- "$output_dir" && sha256sum "$backup_name" >"$backup_name.sha256")
# The published privacy limit is four weeks. Restrict deletion to this
# canonical non-root directory and this script's exact filename namespace.
find "$output_dir" -maxdepth 1 -type f \
    -name 'plantdisease-beta-*.dump.age' -mtime +27 -exec rm -f -- {} \;
find "$output_dir" -maxdepth 1 -type f \
    -name 'plantdisease-beta-*.dump.age.sha256' -mtime +27 -exec rm -f -- {} \;
echo "encrypted beta backup restore check passed: $backup_path"
