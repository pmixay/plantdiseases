from __future__ import annotations

import importlib.util
import io
import uuid
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
RETENTION_MIGRATION_PATH = (
    ACCOUNT_SERVER_DIR / "migrations" / "versions" / "0002_account_retention_index.py"
)
NICKNAME_MIGRATION_PATH = (
    ACCOUNT_SERVER_DIR / "migrations" / "versions" / "0004_nickname_delivery_outbox.py"
)
NICKNAME_CLAIMS_MIGRATION_PATH = (
    ACCOUNT_SERVER_DIR / "migrations" / "versions" / "0005_nickname_claim_registry.py"
)
SIGNUP_COMPLETION_MIGRATION_PATH = (
    ACCOUNT_SERVER_DIR / "migrations" / "versions" / "0006_signup_completion_idempotency.py"
)
EMAIL_PASSWORD_MIGRATION_PATH = (
    ACCOUNT_SERVER_DIR / "migrations" / "versions" / "0007_email_password_auth.py"
)
PRO_ENTITLEMENTS_MIGRATION_PATH = (
    ACCOUNT_SERVER_DIR / "migrations" / "versions" / "0008_pro_entitlements.py"
)
PLANTNET_BUDGET_MIGRATION_PATH = (
    ACCOUNT_SERVER_DIR
    / "migrations"
    / "versions"
    / "0009_plantnet_global_budget.py"
)
BILLING_LIFECYCLE_MIGRATION_PATH = (
    ACCOUNT_SERVER_DIR
    / "migrations"
    / "versions"
    / "0010_billing_lifecycle_quota_policy.py"
)


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("account_schema_v1", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_retention_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "account_retention_v2", RETENTION_MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_nickname_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "account_nickname_v4", NICKNAME_MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_nickname_claims_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "account_nickname_claims_v5", NICKNAME_CLAIMS_MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_signup_completion_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "signup_completion_v6", SIGNUP_COMPLETION_MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_email_password_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "email_password_v7", EMAIL_PASSWORD_MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_pro_entitlements_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "pro_entitlements_v8", PRO_ENTITLEMENTS_MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_plantnet_budget_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "plantnet_global_budget_v9", PLANTNET_BUDGET_MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_billing_lifecycle_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "billing_lifecycle_quota_v10", BILLING_LIFECYCLE_MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_single_linear_migration_head():
    config = Config(str(ACCOUNT_SERVER_DIR / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == ["0010_billing_lifecycle_quota_policy"]


def test_long_signup_revision_widens_alembic_version_and_freezes_check_name():
    migration = _load_signup_completion_migration()

    class RecordingOperations:
        def __init__(self):
            self.calls: list[tuple[str, tuple, dict]] = []
            self.frozen_names: list[str] = []

        def f(self, name: str) -> str:
            self.frozen_names.append(name)
            return name

        def __getattr__(self, name: str):
            def record(*args, **kwargs):
                self.calls.append((name, args, kwargs))

            return record

    operations = RecordingOperations()
    migration.op = operations
    migration.upgrade()

    assert len(migration.revision) > 32
    name, args, kwargs = operations.calls[0]
    assert name == "alter_column"
    assert args == ("alembic_version", "version_num")
    assert kwargs["existing_type"].length == 32
    assert kwargs["type_"].length == 64
    assert kwargs["existing_nullable"] is False
    assert operations.frozen_names.count("ck_accounts_state_allowed") == 2


def test_migration_upgrade_matches_model_columns_and_downgrades_cleanly():
    migration = _load_migration()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.begin() as connection:
            context = MigrationContext.configure(connection)
            migration.op = Operations(context)
            migration.upgrade()

            inspector = inspect(connection)
            baseline_tables = set(Base.metadata.tables) - {
                "account_nickname_jobs",
                "account_nickname_claims",
                "signup_completions",
                "password_credentials",
                "pending_password_credentials",
                "billing_customers",
                "billing_purchase_fetches",
                "subscriptions",
                "billing_notification_receipts",
                "feature_usage",
                "feature_usage_receipts",
                "vendor_budget_authorities",
                "vendor_budget_daily",
                "vendor_budget_receipts",
            }
            assert set(inspector.get_table_names()) == baseline_tables
            for table_name in baseline_tables:
                table = Base.metadata.tables[table_name]
                migrated_columns = {column["name"] for column in inspector.get_columns(table_name)}
                model_columns = set(table.columns.keys())
                if table_name == "pending_signups":
                    # Added to already-created beta databases by revision 0006.
                    model_columns.remove("installation_id")
                assert migrated_columns == model_columns, table_name
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
                if table_name == "push_devices":
                    # Added to already-created beta databases by revision 0003.
                    model_uniques.remove("uq_push_devices_session_id")
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
        "normalized_email",
        "email_ciphertext",
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
    assert "ix_sessions_absolute_expires_at" in sql
    assert "uq_push_devices_session_id" in sql
    assert "create table password_credentials" in sql
    assert "create table pending_password_credentials" in sql


def test_retention_migration_adds_and_removes_the_session_expiry_index():
    schema = _load_migration()
    retention = _load_retention_migration()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.begin() as connection:
            context = MigrationContext.configure(connection)
            operations = Operations(context)
            schema.op = operations
            retention.op = operations
            schema.upgrade()
            retention.upgrade()
            index_names = {
                index["name"] for index in inspect(connection).get_indexes("sessions")
            }
            assert "ix_sessions_absolute_expires_at" in index_names

            retention.downgrade()
            index_names = {
                index["name"] for index in inspect(connection).get_indexes("sessions")
            }
            assert "ix_sessions_absolute_expires_at" not in index_names
            schema.downgrade()
    finally:
        engine.dispose()


def test_nickname_outbox_migration_adds_and_removes_latest_value_job_table():
    schema = _load_migration()
    nickname = _load_nickname_migration()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.begin() as connection:
            context = MigrationContext.configure(connection)
            operations = Operations(context)
            schema.op = operations
            nickname.op = operations
            schema.upgrade()
            nickname.upgrade()

            inspector = inspect(connection)
            assert "account_nickname_jobs" in inspector.get_table_names()
            assert {column["name"] for column in inspector.get_columns(
                "account_nickname_jobs"
            )} == set(Base.metadata.tables["account_nickname_jobs"].columns.keys())
            assert {
                index["name"]
                for index in inspector.get_indexes("account_nickname_jobs")
            } == {"ix_nickname_jobs_state_next_attempt"}

            nickname.downgrade()
            assert "account_nickname_jobs" not in inspect(connection).get_table_names()
            schema.downgrade()
    finally:
        engine.dispose()


def test_nickname_claim_migration_backfills_and_downgrades_cleanly():
    schema = _load_migration()
    nickname = _load_nickname_migration()
    claims = _load_nickname_claims_migration()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.begin() as connection:
            context = MigrationContext.configure(connection)
            operations = Operations(context)
            schema.op = operations
            nickname.op = operations
            claims.op = operations
            schema.upgrade()
            nickname.upgrade()
            connection.exec_driver_sql(
                "INSERT INTO accounts ("
                "id, community_actor_id, nickname, normalized_nickname, "
                "primary_provider, primary_provider_display_name, "
                "authorization_version, state, created_at, updated_at"
                ") VALUES ("
                "'00000000000000000000000000000001', "
                "'00000000000000000000000000000002', "
                "'MigrationFern', 'migrationfern', 'yandex', 'Migration', "
                "1, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
            claims.upgrade()

            inspector = inspect(connection)
            assert "account_nickname_claims" in inspector.get_table_names()
            assert {column["name"] for column in inspector.get_columns(
                "account_nickname_claims"
            )} == set(Base.metadata.tables["account_nickname_claims"].columns.keys())
            row = connection.exec_driver_sql(
                "SELECT account_id, normalized_nickname, state "
                "FROM account_nickname_claims"
            ).one()
            assert row[1:] == ("migrationfern", "current")

            claims.downgrade()
            assert "account_nickname_claims" not in inspect(connection).get_table_names()
            nickname.downgrade()
            schema.downgrade()
    finally:
        engine.dispose()


def test_signup_completion_downgrade_converges_provisional_accounts_before_old_check():
    migration = _load_signup_completion_migration()

    class RecordingOperations:
        def __init__(self):
            self.calls: list[tuple[str, str]] = []

        @staticmethod
        def f(name: str) -> str:
            return name

        def drop_index(self, name: str, **_kwargs) -> None:
            self.calls.append(("drop_index", name))

        def drop_table(self, name: str) -> None:
            self.calls.append(("drop_table", name))

        def execute(self, statement) -> None:
            self.calls.append(("execute", str(statement)))

        def drop_constraint(self, name: str, *_args, **_kwargs) -> None:
            self.calls.append(("drop_constraint", name))

        def create_check_constraint(self, name: str, *_args, **_kwargs) -> None:
            self.calls.append(("create_check_constraint", name))

        def drop_column(self, _table: str, name: str) -> None:
            self.calls.append(("drop_column", name))

    operations = RecordingOperations()
    migration.op = operations
    migration.downgrade()

    drop_completion = operations.calls.index(("drop_table", "signup_completions"))
    delete_provisional = next(
        index
        for index, call in enumerate(operations.calls)
        if call[0] == "execute" and "state = 'signup_pending'" in call[1]
    )
    restore_old_check = operations.calls.index(
        ("create_check_constraint", "ck_accounts_state_allowed")
    )
    assert drop_completion < delete_provisional < restore_old_check


def test_pro_entitlement_migration_matches_models_and_downgrades_cleanly():
    migration = _load_pro_entitlements_migration()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    expected_tables = {
        "billing_customers",
        "subscriptions",
        "billing_notification_receipts",
        "feature_usage",
        "feature_usage_receipts",
    }
    try:
        Base.metadata.tables["accounts"].create(engine)
        with engine.begin() as connection:
            context = MigrationContext.configure(connection)
            migration.op = Operations(context)
            migration.upgrade()
            inspector = inspect(connection)
            assert set(inspector.get_table_names()) == expected_tables | {"accounts"}
            for table_name in expected_tables:
                model = Base.metadata.tables[table_name]
                migrated_columns = {
                    column["name"] for column in inspector.get_columns(table_name)
                }
                model_columns = set(model.columns.keys())
                if table_name == "billing_notification_receipts":
                    model_columns -= {
                        "key_epoch",
                        "manual_replay_count",
                        "dead_lettered_at",
                        "compacted_at",
                    }
                elif table_name == "feature_usage_receipts":
                    model_columns -= {"policy_version", "released_at"}
                assert migrated_columns == model_columns
                assert {
                    index["name"] for index in inspector.get_indexes(table_name)
                } == {index.name for index in model.indexes}

            migration.downgrade()
            assert inspect(connection).get_table_names() == ["accounts"]
    finally:
        engine.dispose()


def test_plantnet_budget_migration_seeds_lock_and_matches_models():
    migration = _load_plantnet_budget_migration()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    expected_tables = {
        "vendor_budget_authorities",
        "vendor_budget_daily",
        "vendor_budget_receipts",
    }
    try:
        with engine.begin() as connection:
            context = MigrationContext.configure(connection)
            migration.op = Operations(context)
            migration.upgrade()
            inspector = inspect(connection)
            assert set(inspector.get_table_names()) == expected_tables
            for table_name in expected_tables:
                model = Base.metadata.tables[table_name]
                assert {
                    column["name"] for column in inspector.get_columns(table_name)
                } == set(model.columns.keys())
                assert {
                    constraint["name"]
                    for constraint in inspector.get_check_constraints(table_name)
                } == {
                    constraint.name
                    for constraint in model.constraints
                    if isinstance(constraint, CheckConstraint)
                }
                assert {
                    constraint["name"]
                    for constraint in inspector.get_unique_constraints(table_name)
                } == {
                    constraint.name
                    for constraint in model.constraints
                    if isinstance(constraint, UniqueConstraint)
                }
                assert {
                    index["name"] for index in inspector.get_indexes(table_name)
                } == {index.name for index in model.indexes}

            authority = connection.exec_driver_sql(
                "SELECT id, provider FROM vendor_budget_authorities"
            ).one()
            assert authority[1] == "plantnet"
            assert str(authority[0]).replace("-", "") == "0" * 31 + "9"

            migration.downgrade()
            assert inspect(connection).get_table_names() == []
    finally:
        engine.dispose()


def test_billing_lifecycle_migration_matches_hardened_models_and_downgrades():
    pro = _load_pro_entitlements_migration()
    plantnet = _load_plantnet_budget_migration()
    lifecycle = _load_billing_lifecycle_migration()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    affected_tables = {
        "billing_purchase_fetches",
        "billing_notification_receipts",
        "feature_usage_receipts",
    }
    try:
        Base.metadata.tables["accounts"].create(engine)
        with engine.begin() as connection:
            context = MigrationContext.configure(connection)
            operations = Operations(context)
            pro.op = operations
            plantnet.op = operations
            lifecycle.op = operations
            pro.upgrade()
            plantnet.upgrade()
            lifecycle.upgrade()

            inspector = inspect(connection)
            for table_name in affected_tables:
                model = Base.metadata.tables[table_name]
                assert {
                    column["name"] for column in inspector.get_columns(table_name)
                } == set(model.columns.keys())
                assert {
                    constraint["name"]
                    for constraint in inspector.get_check_constraints(table_name)
                } == {
                    constraint.name
                    for constraint in model.constraints
                    if isinstance(constraint, CheckConstraint)
                }
                assert {
                    constraint["name"]
                    for constraint in inspector.get_unique_constraints(table_name)
                } == {
                    constraint.name
                    for constraint in model.constraints
                    if isinstance(constraint, UniqueConstraint)
                }

            # The same authenticated payload may legitimately be accepted
            # under two callback-key epochs. Revision 0008 cannot represent
            # both rows because its fingerprint constraint is global.
            fingerprint = "a" * 64
            purchase_id = uuid.uuid4().hex
            connection.exec_driver_sql(
                "INSERT INTO billing_notification_receipts ("
                "id, notification_id, key_epoch, payload_fingerprint, kind, "
                "sandbox, product_id, purchase_id, event_at, event_type, "
                "status_new, period_new, outcome, attempt_count, "
                "manual_replay_count, next_attempt_at, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, 'subscription', 1, ?, ?, ?, ?, ?, ?, "
                "?, ?, 0, ?, ?, ?)",
                (
                    uuid.uuid4().hex,
                    "already-applied",
                    "rotation-v1",
                    fingerprint,
                    "plantdiseases_pro_monthly",
                    purchase_id,
                    "2026-08-26 12:00:00+00:00",
                    "ACTIVATED",
                    "ACTIVE",
                    "MAIN",
                    "updated",
                    1,
                    None,
                    "2026-08-26 12:00:00+00:00",
                    "2026-08-26 12:01:00+00:00",
                ),
            )
            connection.exec_driver_sql(
                "INSERT INTO billing_notification_receipts ("
                "id, notification_id, key_epoch, payload_fingerprint, kind, "
                "sandbox, product_id, purchase_id, event_at, event_type, "
                "status_new, period_new, outcome, attempt_count, "
                "manual_replay_count, next_attempt_at, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, 'subscription', 1, ?, ?, ?, ?, ?, ?, "
                "?, ?, 0, ?, ?, ?)",
                (
                    uuid.uuid4().hex,
                    "rotated-pending",
                    "rotation-v2",
                    fingerprint,
                    "plantdiseases_pro_monthly",
                    purchase_id,
                    "2026-08-26 12:00:00+00:00",
                    "ACTIVATED",
                    "ACTIVE",
                    "MAIN",
                    "pending",
                    0,
                    "2026-08-26 12:02:00+00:00",
                    "2026-08-26 12:02:00+00:00",
                    "2026-08-26 12:02:00+00:00",
                ),
            )

            lifecycle.downgrade()
            assert "billing_purchase_fetches" not in inspect(connection).get_table_names()
            survivor = connection.exec_driver_sql(
                "SELECT notification_id, outcome FROM billing_notification_receipts "
                "WHERE payload_fingerprint = ?",
                (fingerprint,),
            ).one()
            assert survivor == ("already-applied", "updated")
            plantnet.downgrade()
            pro.downgrade()
            assert inspect(connection).get_table_names() == ["accounts"]
    finally:
        engine.dispose()
