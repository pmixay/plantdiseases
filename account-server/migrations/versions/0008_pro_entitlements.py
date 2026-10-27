"""Add server-verified PRO subscriptions and daily feature quotas.

Revision ID: 0008_pro_entitlements
Revises: 0007_email_password_auth
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_pro_entitlements"
down_revision: str | None = "0007_email_password_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "billing_customers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("external_account_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_billing_customers_account_id_accounts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_billing_customers")),
        sa.UniqueConstraint(
            "account_id",
            name=op.f("uq_billing_customers_account_id"),
        ),
        sa.UniqueConstraint(
            "external_account_id",
            name=op.f("uq_billing_customers_external_account_id"),
        ),
    )

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("billing_customer_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.String(length=128), nullable=False),
        sa.Column("purchase_id", sa.Uuid(), nullable=False),
        sa.Column(
            "sandbox",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("auto_renewing", sa.Boolean(), nullable=False),
        sa.Column("payment_state", sa.Integer(), nullable=True),
        sa.Column("acknowledgement_state", sa.String(length=16), nullable=False),
        sa.Column("order_id", sa.String(length=256), nullable=False),
        sa.Column("price_currency_code", sa.String(length=3), nullable=False),
        sa.Column("price_amount_micros", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "reconcile_attempted_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("provider_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "acknowledgement_state IN ('PENDING', 'ACKNOWLEDGED', 'UNKNOWN')",
            name=op.f("ck_subscriptions_acknowledgement_state_allowed"),
        ),
        sa.CheckConstraint(
            "length(price_currency_code) = 3",
            name=op.f("ck_subscriptions_currency_code_length"),
        ),
        sa.CheckConstraint(
            "start_at < expires_at",
            name=op.f("ck_subscriptions_expiry_order"),
        ),
        sa.CheckConstraint(
            "payment_state IS NULL OR payment_state IN (0, 1, 2)",
            name=op.f("ck_subscriptions_payment_state_allowed"),
        ),
        sa.CheckConstraint(
            "price_amount_micros > 0",
            name=op.f("ck_subscriptions_price_positive"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'trial', 'active', 'grace', 'expired')",
            name=op.f("ck_subscriptions_status_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["billing_customer_id"],
            ["billing_customers.id"],
            name=op.f("fk_subscriptions_billing_customer_id_billing_customers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subscriptions")),
        sa.UniqueConstraint(
            "purchase_id",
            name=op.f("uq_subscriptions_purchase_id"),
        ),
    )
    op.create_index(
        "ix_subscriptions_customer_expires",
        "subscriptions",
        ["billing_customer_id", "expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_subscriptions_reconcile_attempted_at",
        "subscriptions",
        ["reconcile_attempted_at"],
        unique=False,
    )

    op.create_table(
        "billing_notification_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("notification_id", sa.String(length=128), nullable=False),
        sa.Column("payload_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("sandbox", sa.Boolean(), nullable=False),
        sa.Column("product_id", sa.String(length=128), nullable=True),
        sa.Column("purchase_id", sa.Uuid(), nullable=True),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_type", sa.String(length=32), nullable=True),
        sa.Column("status_new", sa.String(length=16), nullable=True),
        sa.Column("period_new", sa.String(length=16), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_token", sa.Uuid(), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('test', 'subscription')",
            name=op.f("ck_billing_notification_receipts_kind_allowed"),
        ),
        sa.CheckConstraint(
            "outcome IN ('pending', 'test', 'updated', 'ignored')",
            name=op.f("ck_billing_notification_receipts_outcome_allowed"),
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name=op.f(
                "ck_billing_notification_receipts_attempt_count_nonnegative"
            ),
        ),
        sa.CheckConstraint(
            "length(payload_fingerprint) = 64",
            name=op.f(
                "ck_billing_notification_receipts_payload_fingerprint_sha256"
            ),
        ),
        sa.CheckConstraint(
            "(kind = 'test' AND outcome = 'test' AND product_id IS NULL AND "
            "purchase_id IS NULL AND event_at IS NULL AND event_type IS NULL AND "
            "status_new IS NULL AND period_new IS NULL AND next_attempt_at IS NULL "
            "AND claim_token IS NULL AND claim_expires_at IS NULL) OR "
            "(kind = 'subscription' AND product_id IS NOT NULL AND "
            "purchase_id IS NOT NULL AND event_at IS NOT NULL AND event_type IS NOT NULL "
            "AND status_new IS NOT NULL AND period_new IS NOT NULL AND "
            "((outcome = 'pending' AND next_attempt_at IS NOT NULL) OR "
            "(outcome IN ('updated', 'ignored') AND next_attempt_at IS NULL AND "
            "claim_token IS NULL AND claim_expires_at IS NULL)))",
            name=op.f("ck_billing_notification_receipts_payload_consistent"),
        ),
        sa.CheckConstraint(
            "(claim_token IS NULL AND claim_expires_at IS NULL) OR "
            "(claim_token IS NOT NULL AND claim_expires_at IS NOT NULL AND "
            "outcome = 'pending')",
            name=op.f("ck_billing_notification_receipts_claim_consistent"),
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_billing_notification_receipts"),
        ),
        sa.UniqueConstraint(
            "payload_fingerprint",
            name=op.f("uq_billing_notification_receipts_payload_fingerprint"),
        ),
    )
    op.create_index(
        "ix_billing_notification_receipts_created_at",
        "billing_notification_receipts",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_billing_notification_receipts_due",
        "billing_notification_receipts",
        ["outcome", "next_attempt_at", "claim_expires_at"],
        unique=False,
    )

    op.create_table(
        "feature_usage",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("feature", sa.String(length=32), nullable=False),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column(
            "used",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "feature IN ('gamma_analysis', 'frontier_analysis', "
            "'plant_identification', 'ai_doctor', 'care_plan')",
            name=op.f("ck_feature_usage_feature_allowed"),
        ),
        sa.CheckConstraint(
            "used >= 0",
            name=op.f("ck_feature_usage_used_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_feature_usage_account_id_accounts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_feature_usage")),
        sa.UniqueConstraint(
            "account_id",
            "feature",
            "usage_date",
            name=op.f("uq_feature_usage_account_feature_date"),
        ),
    )
    op.create_index(
        "ix_feature_usage_date",
        "feature_usage",
        ["usage_date"],
        unique=False,
    )

    op.create_table(
        "feature_usage_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("feature", sa.String(length=32), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False),
        sa.Column("plan", sa.String(length=8), nullable=False),
        sa.Column("daily_limit", sa.Integer(), nullable=False),
        sa.Column("remaining", sa.Integer(), nullable=False),
        sa.Column("reset_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=24), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(allowed AND reason IS NULL AND daily_limit > 0 AND "
            "remaining < daily_limit) OR "
            "(NOT allowed AND remaining = 0 AND "
            "((reason = 'pro_required' AND plan = 'free' AND daily_limit = 0) OR "
            "(reason = 'quota_exhausted' AND daily_limit > 0)))",
            name=op.f("ck_feature_usage_receipts_decision_consistent"),
        ),
        sa.CheckConstraint(
            "daily_limit >= 0",
            name=op.f("ck_feature_usage_receipts_daily_limit_nonnegative"),
        ),
        sa.CheckConstraint(
            "feature IN ('gamma_analysis', 'frontier_analysis', "
            "'plant_identification', 'ai_doctor', 'care_plan')",
            name=op.f("ck_feature_usage_receipts_feature_allowed"),
        ),
        sa.CheckConstraint(
            "plan IN ('free', 'pro')",
            name=op.f("ck_feature_usage_receipts_plan_allowed"),
        ),
        sa.CheckConstraint(
            "remaining >= 0 AND remaining <= daily_limit",
            name=op.f("ck_feature_usage_receipts_remaining_bounded"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_feature_usage_receipts_account_id_accounts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_feature_usage_receipts")),
        sa.UniqueConstraint(
            "account_id",
            "feature",
            "request_id",
            name=op.f("uq_feature_usage_receipts_account_feature_request"),
        ),
    )
    op.create_index(
        "ix_feature_usage_receipts_created_at",
        "feature_usage_receipts",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_feature_usage_receipts_created_at",
        table_name="feature_usage_receipts",
    )
    op.drop_table("feature_usage_receipts")
    op.drop_index("ix_feature_usage_date", table_name="feature_usage")
    op.drop_table("feature_usage")
    op.drop_index(
        "ix_billing_notification_receipts_due",
        table_name="billing_notification_receipts",
    )
    op.drop_index(
        "ix_billing_notification_receipts_created_at",
        table_name="billing_notification_receipts",
    )
    op.drop_table("billing_notification_receipts")
    op.drop_index(
        "ix_subscriptions_reconcile_attempted_at",
        table_name="subscriptions",
    )
    op.drop_index("ix_subscriptions_customer_expires", table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_table("billing_customers")
