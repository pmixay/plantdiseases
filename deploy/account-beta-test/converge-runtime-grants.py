"""Converge account database grants after Alembic, using the migrator owner."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

SCHEMA_HEAD = "0010_billing_lifecycle_quota_policy"
APPLICATION_TABLES = (
    "account_consents",
    "account_deletion_jobs",
    "account_identities",
    "account_nickname_claims",
    "account_nickname_jobs",
    "account_security_events",
    "accounts",
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
)
DATABASE_TABLES = frozenset((*APPLICATION_TABLES, "alembic_version"))


@dataclass(frozen=True)
class RoleContract:
    database: str
    migrator: str
    runtime: str
    backup: str


CONTRACTS = {
    "german-beta": RoleContract(
        database="plantdisease_beta",
        migrator="beta_account_migrator",
        runtime="beta_account_runtime",
        backup="beta_account_backup",
    ),
    "production": RoleContract(
        database="plantdisease",
        migrator="account_migrator",
        runtime="account_runtime",
        backup="account_backup",
    ),
}


class ContractError(RuntimeError):
    """A safe-to-report privilege-contract mismatch."""


def _require_scalar(connection, statement: str, **parameters: str) -> str:
    value = connection.execute(text(statement), parameters).scalar_one_or_none()
    if not isinstance(value, str) or not value:
        raise ContractError("required database metadata is absent")
    return value


def converge(database_url: str, contract: RoleContract) -> None:
    engine = create_engine(database_url, poolclass=NullPool)
    application_table_sql = ",\n    ".join(
        f"public.{table_name}" for table_name in APPLICATION_TABLES
    )
    try:
        with engine.begin() as connection:
            current_database, current_user, is_superuser = connection.execute(
                text(
                    "SELECT current_database(), current_user, "
                    "(SELECT rolsuper FROM pg_roles WHERE rolname = current_user)"
                )
            ).one()
            if (
                current_database != contract.database
                or current_user != contract.migrator
                or is_superuser is not False
            ):
                raise ContractError("grant convergence is not running as the reviewed migrator")

            for role in (contract.runtime, contract.backup):
                exists = connection.execute(
                    text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :role)"),
                    {"role": role},
                ).scalar_one()
                if not exists:
                    raise ContractError("a reviewed database role is absent")

            revision = _require_scalar(
                connection,
                "SELECT version_num FROM public.alembic_version",
            )
            if revision != SCHEMA_HEAD:
                raise ContractError("database is not at the reviewed Alembic revision")

            tables = set(
                connection.execute(
                    text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname = 'public' ORDER BY tablename COLLATE \"C\""
                    )
                ).scalars()
            )
            if tables != DATABASE_TABLES:
                raise ContractError("database table inventory differs from the reviewed schema")

            sequences = tuple(
                connection.execute(
                    text(
                        "SELECT sequence_name FROM information_schema.sequences "
                        "WHERE sequence_schema = 'public'"
                    )
                ).scalars()
            )
            if sequences:
                raise ContractError("database has an unreviewed runtime sequence")

            connection.exec_driver_sql(
                f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {contract.runtime}"
            )
            connection.exec_driver_sql(
                f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {contract.runtime}"
            )
            connection.exec_driver_sql(
                f"GRANT USAGE ON SCHEMA public TO {contract.runtime}"
            )
            connection.exec_driver_sql(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE\n"
                f"    {application_table_sql}\n"
                f"TO {contract.runtime}"
            )
            connection.exec_driver_sql(
                f"GRANT SELECT ON TABLE public.alembic_version TO {contract.runtime}"
            )

            connection.exec_driver_sql(
                f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {contract.backup}"
            )
            connection.exec_driver_sql(
                f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {contract.backup}"
            )
            connection.exec_driver_sql(
                f"GRANT USAGE ON SCHEMA public TO {contract.backup}"
            )
            connection.exec_driver_sql(
                f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {contract.backup}"
            )

            connection.exec_driver_sql(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                f"REVOKE ALL ON TABLES FROM {contract.runtime}, {contract.backup}"
            )
            connection.exec_driver_sql(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                f"REVOKE ALL ON SEQUENCES FROM {contract.runtime}, {contract.backup}"
            )
            connection.exec_driver_sql(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                f"GRANT SELECT ON TABLES TO {contract.backup}"
            )

            runtime_table_contract = connection.execute(
                text(
                    "SELECT count(*) = 24 AND bool_and("
                    "has_table_privilege(:runtime, format('%I.%I', schemaname, tablename), 'SELECT') "
                    "AND has_table_privilege(:runtime, format('%I.%I', schemaname, tablename), "
                    "'INSERT') "
                    "AND has_table_privilege(:runtime, format('%I.%I', schemaname, tablename), "
                    "'UPDATE') "
                    "AND has_table_privilege(:runtime, format('%I.%I', schemaname, tablename), "
                    "'DELETE')) "
                    "FROM pg_tables WHERE schemaname = 'public' "
                    "AND tablename <> 'alembic_version'"
                ),
                {"runtime": contract.runtime},
            ).scalar_one()
            runtime_revision_contract = connection.execute(
                text(
                    "SELECT has_table_privilege(:runtime, 'public.alembic_version', 'SELECT') "
                    "AND NOT has_table_privilege(:runtime, 'public.alembic_version', 'INSERT') "
                    "AND NOT has_table_privilege(:runtime, 'public.alembic_version', 'UPDATE') "
                    "AND NOT has_table_privilege(:runtime, 'public.alembic_version', 'DELETE')"
                ),
                {"runtime": contract.runtime},
            ).scalar_one()
            if not runtime_table_contract or not runtime_revision_contract:
                raise ContractError("runtime grants do not match the reviewed privilege inventory")
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", choices=tuple(CONTRACTS), required=True)
    arguments = parser.parse_args()
    database_url = os.environ.get("ACCOUNT_DATABASE_URL", "").strip()
    if not database_url:
        print("role convergence failed: ACCOUNT_DATABASE_URL is absent", file=sys.stderr)
        return 1
    try:
        converge(database_url, CONTRACTS[arguments.contract])
    except ContractError as exc:
        print(f"role convergence failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - redact driver and connection details
        print(f"role convergence failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(f"role convergence passed at {SCHEMA_HEAD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
