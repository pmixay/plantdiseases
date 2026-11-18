from __future__ import annotations

from app.db.models import Base

EXPECTED_TABLES = {
    "accounts",
    "account_identities",
    "password_credentials",
    "sessions",
    "refresh_tokens",
    "pending_signups",
    "pending_password_credentials",
    "signup_completions",
    "reauth_challenges",
    "account_deletion_jobs",
    "account_nickname_jobs",
    "account_nickname_claims",
    "push_devices",
    "account_consents",
    "account_security_events",
    "billing_customers",
    "billing_purchase_fetches",
    "subscriptions",
    "billing_notification_receipts",
    "feature_usage",
    "feature_usage_receipts",
    "vendor_budget_authorities",
    "vendor_budget_daily",
    "vendor_budget_receipts",
    "community_roles",
    "community_rewards",
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


def test_password_tables_store_only_keyed_lookup_and_memory_hard_hashes():
    credentials = set(Base.metadata.tables["password_credentials"].columns.keys())
    pending = set(Base.metadata.tables["pending_password_credentials"].columns.keys())
    assert {"email_fingerprint", "password_hash"} <= credentials
    assert {"email_fingerprint", "password_hash"} <= pending
    assert "email" not in credentials | pending
    assert "password" not in credentials | pending


def test_billing_tables_use_opaque_bindings_and_minimal_provider_state():
    customers = set(Base.metadata.tables["billing_customers"].columns.keys())
    subscriptions = set(Base.metadata.tables["subscriptions"].columns.keys())
    fetches = set(Base.metadata.tables["billing_purchase_fetches"].columns.keys())
    receipts = set(
        Base.metadata.tables["billing_notification_receipts"].columns.keys()
    )
    assert customers == {
        "id",
        "account_id",
        "external_account_id",
        "created_at",
        "updated_at",
    }
    assert {
        "product_id",
        "purchase_id",
        "expires_at",
        "payment_state",
        "verified_at",
    } <= subscriptions
    assert fetches == {
        "purchase_id",
        "product_id",
        "sandbox",
        "generation",
        "applied_generation",
        "created_at",
        "updated_at",
    }
    assert {
        "notification_id",
        "payload_fingerprint",
        "kind",
        "event_at",
        "event_type",
        "status_new",
        "period_new",
        "outcome",
        "attempt_count",
        "next_attempt_at",
        "claim_token",
        "claim_expires_at",
        "last_error_code",
        "key_epoch",
        "manual_replay_count",
        "dead_lettered_at",
        "compacted_at",
    } <= receipts
    for columns in (customers, fetches, subscriptions, receipts):
        assert columns.isdisjoint({"email", "provider_subject", "provider_token"})


def test_vendor_budget_tables_contain_only_aggregate_cost_authority_state():
    authorities = set(
        Base.metadata.tables["vendor_budget_authorities"].columns.keys()
    )
    daily = set(Base.metadata.tables["vendor_budget_daily"].columns.keys())
    receipts = set(
        Base.metadata.tables["vendor_budget_receipts"].columns.keys()
    )
    assert authorities == {"id", "provider", "created_at"}
    assert {
        "usage_date",
        "attempts_used",
        "cost_exposure_micros",
    } <= daily
    assert {
        "request_id",
        "policy_version",
        "attempt_cost_micros",
        "daily_attempt_limit",
        "daily_cost_limit_micros",
        "allowed",
        "reason",
    } <= receipts
    forbidden = {
        "account_id",
        "actor_id",
        "session_id",
        "image",
        "payload",
        "provider_subject",
        "email",
        "ip",
    }
    assert forbidden.isdisjoint(authorities | daily | receipts)


