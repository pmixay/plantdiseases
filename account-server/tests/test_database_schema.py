from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from app.db.models import Base

EXPECTED_TABLES = {
    "accounts",
    "account_identities",
    "sessions",
    "refresh_tokens",
    "pending_signups",
    "signup_completions",
    "reauth_challenges",
    "account_deletion_jobs",
    "account_nickname_jobs",
    "account_nickname_claims",
    "push_devices",
    "account_consents",
    "account_security_events",
}

FORBIDDEN_COLUMN_NAMES = {
    "email",
    "phone",
    "birthday",
    "gender",
    "contacts",
    "provider_token",
    "provider_access_token",
    "raw_ip",
    "ip_hash",
    "manufacturer",
    "brand",
    "hardware_model",
    "product_name",
    "serial",
    "android_id",
    "advertising_id",
    "imei",
    "sim",
    "user_pseudo_id",
    "scan_id",
}


def test_metadata_contains_every_private_entity_and_no_extra_tables():
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_schema_contains_no_forbidden_identity_or_hardware_columns():
    for table in Base.metadata.sorted_tables:
        columns = set(table.columns.keys())
        assert columns.isdisjoint(FORBIDDEN_COLUMN_NAMES), (table.name, columns)


def test_session_and_push_columns_are_minimal_and_private():
    sessions = set(Base.metadata.tables["sessions"].columns.keys())
    assert "device_label" in sessions
    assert sessions.isdisjoint({"phone_model", "device", "model", "product"})

    push = set(Base.metadata.tables["push_devices"].columns.keys())
    assert "token" not in push
    assert {"token_ciphertext", "token_fingerprint", "platform"} <= push
    assert push.isdisjoint({"phone_model", "device_label", "provider_subject"})


def test_sqlite_fixture_materializes_all_constraints(db_engine: Engine):
    inspector = inspect(db_engine)
    assert set(inspector.get_table_names()) == EXPECTED_TABLES
    for table_name in EXPECTED_TABLES:
        assert inspector.get_pk_constraint(table_name)["constrained_columns"] == ["id"]
