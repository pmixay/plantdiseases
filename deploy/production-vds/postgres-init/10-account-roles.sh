#!/bin/sh
# The official Postgres entrypoint sources non-executable *.sh files. Keep all
# options and variables inside a subshell so this file is safe in either mode.
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

validate_url_password "$ACCOUNT_POSTGRES_RUNTIME_PASSWORD" ACCOUNT_POSTGRES_RUNTIME_PASSWORD
validate_url_password "$ACCOUNT_POSTGRES_MIGRATOR_PASSWORD" ACCOUNT_POSTGRES_MIGRATOR_PASSWORD
validate_url_password "$ACCOUNT_POSTGRES_BACKUP_PASSWORD" ACCOUNT_POSTGRES_BACKUP_PASSWORD

psql --set=ON_ERROR_STOP=1 \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    --set=runtime_password="$ACCOUNT_POSTGRES_RUNTIME_PASSWORD" \
    --set=migrator_password="$ACCOUNT_POSTGRES_MIGRATOR_PASSWORD" \
    --set=backup_password="$ACCOUNT_POSTGRES_BACKUP_PASSWORD" <<'SQL'
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

-- Existing roles are converged, not merely created. Remove any group-role
-- membership that could still permit SET ROLE into broader cluster powers.
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

-- The official image creates POSTGRES_USER as PostgreSQL's bootstrap role
-- (OID 10). That role is pinned, so REASSIGN OWNED rejects it even when the
-- only owned objects belong to this dedicated database. Transfer the concrete
-- database objects first; indexes and table row/array types follow their
-- parent table automatically. Keep REASSIGN OWNED for non-bootstrap upgrades.
SELECT format('ALTER TABLE %I.%I OWNER TO account_migrator',
              schemaname, tablename)
FROM pg_tables
WHERE schemaname = 'public' AND tableowner = 'account_bootstrap'
ORDER BY tablename
\gexec
SELECT format('ALTER SEQUENCE %I.%I OWNER TO account_migrator',
              schemaname, sequencename)
FROM pg_sequences
WHERE schemaname = 'public' AND sequenceowner = 'account_bootstrap'
ORDER BY sequencename
\gexec
SELECT format('ALTER DATABASE %I OWNER TO account_migrator', current_database())
WHERE (SELECT datdba FROM pg_database WHERE datname = current_database()) =
      (SELECT oid FROM pg_roles WHERE rolname = 'account_bootstrap')
\gexec
SELECT 'REASSIGN OWNED BY account_bootstrap TO account_migrator'
WHERE (SELECT oid FROM pg_roles WHERE rolname = 'account_bootstrap') <> 10
\gexec
ALTER SCHEMA public OWNER TO account_migrator;
REVOKE ALL ON SCHEMA public
    FROM account_runtime, account_backup;
REVOKE ALL ON ALL TABLES IN SCHEMA public
    FROM account_runtime, account_backup;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public
    FROM account_runtime, account_backup;
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
        ('billing_customers'),
        ('billing_purchase_fetches'),
        ('billing_notification_receipts'),
        ('feature_usage'),
        ('feature_usage_receipts'),
        ('password_credentials'),
        ('pending_password_credentials'),
        ('pending_signups'),
        ('push_devices'),
        ('reauth_challenges'),
        ('refresh_tokens'),
        ('sessions'),
        ('signup_completions'),
        ('subscriptions'),
        ('vendor_budget_authorities'),
        ('vendor_budget_daily'),
        ('vendor_budget_receipts'),
        ('community_roles'),
        ('community_rewards')
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
