#!/bin/sh
# Safe both when sourced by docker-entrypoint.sh and when invoked explicitly by
# the idempotent role-provision service.
(
set -eu

validate_url_password() {
    value=$1
    label=$2
    case "$value" in
        *[!0123456789abcdefABCDEF]*|'')
            echo "$label must be exactly 64 hexadecimal characters" >&2
            exit 1
            ;;
    esac
    if [ "${#value}" -ne 64 ]; then
        echo "$label must be exactly 64 hexadecimal characters" >&2
        exit 1
    fi
}

validate_url_password "$RU_POSTGRES_RUNTIME_PASSWORD" RU_POSTGRES_RUNTIME_PASSWORD
validate_url_password "$RU_POSTGRES_MIGRATOR_PASSWORD" RU_POSTGRES_MIGRATOR_PASSWORD
validate_url_password "$RU_POSTGRES_BACKUP_PASSWORD" RU_POSTGRES_BACKUP_PASSWORD

# Runs only during first initialization of the dedicated Russian PostgreSQL
# volume. Fixed role names avoid identifier injection; psql variables safely
# quote password values. The bootstrap superuser is never mounted into an API.
psql --set=ON_ERROR_STOP=1 \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    --set=runtime_password="$RU_POSTGRES_RUNTIME_PASSWORD" \
    --set=migrator_password="$RU_POSTGRES_MIGRATOR_PASSWORD" \
    --set=backup_password="$RU_POSTGRES_BACKUP_PASSWORD" <<'SQL'
REVOKE ALL ON DATABASE plantdisease FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

SELECT 'CREATE ROLE account_migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'account_migrator') \gexec
SELECT 'CREATE ROLE account_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'account_runtime') \gexec
SELECT 'CREATE ROLE account_backup LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'account_backup') \gexec

ALTER ROLE account_migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
ALTER ROLE account_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
ALTER ROLE account_backup LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;

SELECT format('REVOKE %I FROM %I', granted.rolname, member.rolname)
FROM pg_auth_members membership
JOIN pg_roles granted ON granted.oid = membership.roleid
JOIN pg_roles member ON member.oid = membership.member
WHERE member.rolname IN (
    'account_migrator', 'account_runtime', 'account_backup'
) \gexec

SELECT format('ALTER ROLE account_migrator PASSWORD %L', :'migrator_password') \gexec
SELECT format('ALTER ROLE account_runtime PASSWORD %L', :'runtime_password') \gexec
SELECT format('ALTER ROLE account_backup PASSWORD %L', :'backup_password') \gexec

REVOKE ALL ON DATABASE plantdisease
    FROM account_migrator, account_runtime, account_backup;
GRANT CONNECT, TEMPORARY ON DATABASE plantdisease TO account_migrator;
GRANT CONNECT ON DATABASE plantdisease TO account_runtime, account_backup;

REASSIGN OWNED BY account_bootstrap TO account_migrator;
ALTER SCHEMA public OWNER TO account_migrator;
REVOKE ALL ON SCHEMA public FROM account_runtime, account_backup;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM account_runtime, account_backup;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM account_runtime, account_backup;
GRANT USAGE ON SCHEMA public TO account_runtime, account_backup;
WITH runtime_tables(tablename) AS (
    VALUES
        ('account_consents'),
        ('account_deletion_jobs'),
        ('account_identities'),
        ('account_nickname_claims'),
        ('account_nickname_jobs'),
        ('account_security_events'),
        ('accounts'),
        ('pending_signups'),
        ('push_devices'),
        ('reauth_challenges'),
        ('refresh_tokens'),
        ('sessions'),
        ('signup_completions')
)
SELECT format(
    'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE %I.%I TO account_runtime',
    tables.schemaname,
    tables.tablename
)
FROM pg_tables AS tables
JOIN runtime_tables USING (tablename)
WHERE tables.schemaname = 'public'
ORDER BY tables.tablename
\gexec
SELECT 'GRANT SELECT ON TABLE public.alembic_version TO account_runtime'
WHERE to_regclass('public.alembic_version') IS NOT NULL
\gexec
GRANT SELECT ON ALL TABLES IN SCHEMA public TO account_backup;

ALTER DEFAULT PRIVILEGES FOR ROLE account_migrator IN SCHEMA public
    REVOKE ALL ON TABLES FROM account_runtime, account_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE account_migrator IN SCHEMA public
    REVOKE ALL ON SEQUENCES FROM account_runtime, account_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE account_migrator IN SCHEMA public
    REVOKE ALL ON TYPES FROM account_runtime, account_backup;
ALTER DEFAULT PRIVILEGES FOR ROLE account_migrator IN SCHEMA public
    GRANT USAGE ON TYPES TO account_runtime;

ALTER DEFAULT PRIVILEGES FOR ROLE account_migrator IN SCHEMA public
    GRANT SELECT ON TABLES TO account_backup;
SQL
)
