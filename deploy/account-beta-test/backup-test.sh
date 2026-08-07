#!/bin/sh
# Create and restore-check one synthetic German-beta PostgreSQL dump.
# This never stops services, deletes volumes, or touches an existing database.

set -euC
umask 077

if [ "$#" -ne 2 ]; then
    echo "usage: $0 /absolute/path/to/german.env /absolute/protected/output-dir" >&2
    exit 64
fi

env_file=$1
output_dir=$2
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

if [ ! -f "$env_file" ] || [ ! -d "$output_dir" ]; then
    echo "environment file and existing output directory are required" >&2
    exit 66
fi
if [ -L "$output_dir" ]; then
    echo "output directory must not be a symbolic link" >&2
    exit 65
fi
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

compose() {
    docker compose --env-file "$env_file" -f "$compose_file" "$@"
}

compose config --quiet
compose exec -T beta-postgres pg_isready -U beta_account -d plantdisease_beta >/dev/null

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_name="plantdisease-beta-$timestamp.dump"
backup_path="$output_dir/$backup_name"
partial_path="$backup_path.partial"
checksum_path="$backup_path.sha256"
restore_db="beta_restore_${timestamp}_$$"

if [ -e "$backup_path" ] || [ -e "$partial_path" ] || [ -e "$checksum_path" ]; then
    echo "backup target already exists" >&2
    exit 73
fi

cleanup() {
    compose exec -T beta-postgres dropdb \
        --if-exists --force -U beta_account "$restore_db" >/dev/null 2>&1 || true
    if [ -f "$partial_path" ]; then
        rm -f -- "$partial_path"
    fi
}
trap cleanup EXIT HUP INT TERM

compose exec -T beta-postgres pg_dump \
    --format=custom --compress=9 --no-owner --no-privileges \
    -U beta_account plantdisease_beta >"$partial_path"
test -s "$partial_path"

compose exec -T beta-postgres createdb \
    --template=template0 -U beta_account "$restore_db"
compose exec -T beta-postgres pg_restore \
    --exit-on-error --no-owner --no-privileges \
    -U beta_account -d "$restore_db" <"$partial_path"

inventory_sql="SELECT 'account_consents=' || count(*) FROM account_consents
UNION ALL SELECT 'account_deletion_jobs=' || count(*) FROM account_deletion_jobs
UNION ALL SELECT 'account_identities=' || count(*) FROM account_identities
UNION ALL SELECT 'account_security_events=' || count(*) FROM account_security_events
UNION ALL SELECT 'accounts=' || count(*) FROM accounts
UNION ALL SELECT 'pending_signups=' || count(*) FROM pending_signups
UNION ALL SELECT 'push_devices=' || count(*) FROM push_devices
UNION ALL SELECT 'reauth_challenges=' || count(*) FROM reauth_challenges
UNION ALL SELECT 'refresh_tokens=' || count(*) FROM refresh_tokens
UNION ALL SELECT 'sessions=' || count(*) FROM sessions
ORDER BY 1;"

source_inventory=$(compose exec -T beta-postgres psql \
    -X -A -t -v ON_ERROR_STOP=1 -U beta_account -d plantdisease_beta \
    -c "$inventory_sql")
restored_inventory=$(compose exec -T beta-postgres psql \
    -X -A -t -v ON_ERROR_STOP=1 -U beta_account -d "$restore_db" \
    -c "$inventory_sql")
if [ "$source_inventory" != "$restored_inventory" ]; then
    echo "restore row inventory differs from source" >&2
    exit 1
fi

mv -- "$partial_path" "$backup_path"
(cd -- "$output_dir" && sha256sum "$backup_name" >"$backup_name.sha256")
echo "synthetic beta backup restore check passed: $backup_path"
