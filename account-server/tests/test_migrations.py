from __future__ import annotations

import importlib.util
import io
from pathlib import Path
from types import ModuleType

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, UniqueConstraint, create_engine, inspect

from app.db.models import Base

ACCOUNT_SERVER_DIR = Path(__file__).resolve().parent.parent
MIGRATION_PATH = (
    ACCOUNT_SERVER_DIR / "migrations" / "versions" / "0001_private_account_schema.py"
)


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("account_schema_v1", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_single_linear_migration_head():
    config = Config(str(ACCOUNT_SERVER_DIR / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == ["0001_private_account_schema"]


def test_migration_upgrade_matches_model_columns_and_downgrades_cleanly():
    migration = _load_migration()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.begin() as connection:
            context = MigrationContext.configure(connection)
            migration.op = Operations(context)
            migration.upgrade()

            inspector = inspect(connection)
            assert set(inspector.get_table_names()) == set(Base.metadata.tables)
            for table_name, table in Base.metadata.tables.items():
                migrated_columns = {column["name"] for column in inspector.get_columns(table_name)}
                assert migrated_columns == set(table.columns.keys()), table_name
                migrated_checks = {
                    constraint["name"] for constraint in inspector.get_check_constraints(table_name)
                }
                model_checks = {
                    constraint.name
                    for constraint in table.constraints
                    if isinstance(constraint, CheckConstraint)
                }
                assert migrated_checks == model_checks, table_name
                migrated_uniques = {
                    constraint["name"] for constraint in inspector.get_unique_constraints(table_name)
                }
                model_uniques = {
                    constraint.name
                    for constraint in table.constraints
                    if isinstance(constraint, UniqueConstraint)
                }
                assert migrated_uniques == model_uniques, table_name

            migration.downgrade()
            assert inspect(connection).get_table_names() == []
    finally:
        engine.dispose()


def test_postgres_offline_migration_is_explicit_and_contains_no_forbidden_columns():
    config = Config(str(ACCOUNT_SERVER_DIR / "alembic.ini"))
    output = io.StringIO()
    config.output_buffer = output
    command.upgrade(config, "head", sql=True)
    sql = output.getvalue().lower()

    for table_name in Base.metadata.tables:
        assert f"create table {table_name}" in sql

    forbidden = {
        "email",
        "phone",
        "birthday",
        "gender",
        "contacts",
        "provider_access_token",
        "raw_ip",
        "ip_hash",
        "phone_model",
        "manufacturer",
        "android_id",
        "advertising_id",
        "imei",
        "user_pseudo_id",
        "scan_id",
    }
    for column_name in forbidden:
        assert column_name not in sql
    assert "synthetic-test-only" not in sql
