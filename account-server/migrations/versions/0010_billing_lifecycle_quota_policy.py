"""Harden RuStore lifecycle ordering and version feature quota decisions.

Revision ID: 0010_billing_lifecycle_quota_policy
Revises: 0009_plantnet_global_budget
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_billing_lifecycle_quota_policy"
down_revision: str | None = "0009_plantnet_global_budget"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _new_notification_payload_constraint() -> str:
    return (
        "(compacted_at IS NOT NULL AND outcome IN "
        "('test', 'updated', 'ignored', 'dead_letter') AND notification_id IS NULL "
        "AND product_id IS NULL AND purchase_id IS NULL AND event_at IS NULL "
        "AND event_type IS NULL AND status_new IS NULL AND period_new IS NULL "
        "AND next_attempt_at IS NULL AND claim_token IS NULL AND "
        "claim_expires_at IS NULL) OR "
        "(compacted_at IS NULL AND notification_id IS NOT NULL AND "
        "((kind = 'test' AND outcome = 'test' AND product_id IS NULL AND "
        "purchase_id IS NULL AND event_at IS NULL AND event_type IS NULL AND "
        "status_new IS NULL AND period_new IS NULL AND next_attempt_at IS NULL "
        "AND claim_token IS NULL AND claim_expires_at IS NULL) OR "
        "(kind = 'subscription' AND product_id IS NOT NULL AND purchase_id IS NOT NULL "
        "AND event_at IS NOT NULL AND event_type IS NOT NULL AND status_new IS NOT NULL "
        "AND period_new IS NOT NULL AND ((outcome = 'pending' AND "
        "next_attempt_at IS NOT NULL) OR (outcome IN "
        "('updated', 'ignored', 'dead_letter') AND next_attempt_at IS NULL AND "
        "claim_token IS NULL AND claim_expires_at IS NULL)))))"
    )


def _old_notification_payload_constraint() -> str:
    return (
        "(kind = 'test' AND outcome = 'test' AND product_id IS NULL AND "
        "purchase_id IS NULL AND event_at IS NULL AND event_type IS NULL AND "
        "status_new IS NULL AND period_new IS NULL AND next_attempt_at IS NULL "
        "AND claim_token IS NULL AND claim_expires_at IS NULL) OR "
        "(kind = 'subscription' AND product_id IS NOT NULL AND "
        "purchase_id IS NOT NULL AND event_at IS NOT NULL AND event_type IS NOT NULL "
        "AND status_new IS NOT NULL AND period_new IS NOT NULL AND "
        "((outcome = 'pending' AND next_attempt_at IS NOT NULL) OR "
        "(outcome IN ('updated', 'ignored') AND next_attempt_at IS NULL AND "
        "claim_token IS NULL AND claim_expires_at IS NULL)))"
    )


def upgrade() -> None:
    op.create_table(
        "billing_purchase_fetches",
        sa.Column("purchase_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.String(length=128), nullable=False),
        sa.Column("sandbox", sa.Boolean(), nullable=False),
        sa.Column(
            "generation",
            sa.BigInteger(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("applied_generation", sa.BigInteger(), nullable=True),
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
            "generation > 0",
            name=op.f("ck_billing_purchase_fetches_generation_positive"),
        ),
        sa.CheckConstraint(
            "applied_generation IS NULL OR "
            "(applied_generation > 0 AND applied_generation <= generation)",
            name=op.f("ck_billing_purchase_fetches_applied_generation_valid"),
        ),
        sa.PrimaryKeyConstraint(
            "purchase_id",
            name=op.f("pk_billing_purchase_fetches"),
        ),
    )
    op.execute(
        sa.text(
            "INSERT INTO billing_purchase_fetches "
            "(purchase_id, product_id, sandbox, generation, applied_generation, "
            "created_at, updated_at) "
            "SELECT purchase_id, product_id, sandbox, 1, 1, created_at, updated_at "
            "FROM subscriptions"
        )
    )

    with op.batch_alter_table("billing_notification_receipts") as batch:
        batch.add_column(
            sa.Column(
                "key_epoch",
                sa.String(length=64),
                server_default=sa.text("'legacy-v1'"),
                nullable=False,
            )
        )
        batch.add_column(
            sa.Column(
                "manual_replay_count",
                sa.Integer(),
                server_default=sa.text("0"),
                nullable=False,
            )
        )
        batch.add_column(
            sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column("compacted_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.alter_column(
            "notification_id",
            existing_type=sa.String(length=128),
            nullable=True,
        )
        batch.drop_constraint(
            op.f("uq_billing_notification_receipts_payload_fingerprint"),
            type_="unique",
        )
        batch.drop_constraint(
            op.f("ck_billing_notification_receipts_outcome_allowed"),
            type_="check",
        )
        batch.drop_constraint(
            op.f("ck_billing_notification_receipts_payload_consistent"),
            type_="check",
        )
        batch.create_unique_constraint(
            op.f("uq_billing_notification_receipts_epoch_fingerprint"),
            ["key_epoch", "payload_fingerprint"],
        )
        batch.create_check_constraint(
            op.f("ck_billing_notification_receipts_outcome_allowed"),
            "outcome IN ('pending', 'test', 'updated', 'ignored', 'dead_letter')",
        )
        batch.create_check_constraint(
            op.f("ck_billing_notification_receipts_manual_replay_count_nonnegative"),
            "manual_replay_count >= 0",
        )
        batch.create_check_constraint(
            op.f("ck_billing_notification_receipts_key_epoch_length"),
            "length(key_epoch) BETWEEN 1 AND 64",
        )
        batch.create_check_constraint(
            op.f("ck_billing_notification_receipts_payload_consistent"),
            _new_notification_payload_constraint(),
        )
        batch.create_check_constraint(
            op.f("ck_billing_notification_receipts_dead_letter_consistent"),
            "(outcome = 'dead_letter' AND dead_lettered_at IS NOT NULL) OR "
            "(outcome <> 'dead_letter' AND dead_lettered_at IS NULL)",
        )

    with op.batch_alter_table("feature_usage_receipts") as batch:
        batch.add_column(
            sa.Column(
                "policy_version",
                sa.String(length=64),
                server_default=sa.text("'legacy-hardcoded-v1'"),
                nullable=False,
            )
        )
        batch.add_column(
            sa.Column("released_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.create_check_constraint(
            op.f("ck_feature_usage_receipts_policy_version_length"),
            "length(policy_version) BETWEEN 1 AND 64",
        )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM billing_notification_receipts WHERE compacted_at IS NOT NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE billing_notification_receipts SET outcome = 'ignored', "
            "dead_lettered_at = NULL WHERE outcome = 'dead_letter'"
        )
    )
    # Revision 0010 scopes replay fingerprints by callback-key epoch. A
    # legitimate key rotation can therefore leave the same authenticated
    # payload in more than one epoch, while revision 0008 requires global
    # fingerprint uniqueness. Collapse those rows deterministically before
    # restoring the legacy constraint: an already applied/test result wins,
    # then any other terminal result, then the most recently touched pending
    # row. This avoids replaying an event merely because the schema rolled back.
    op.execute(
        sa.text(
            "DELETE FROM billing_notification_receipts WHERE id IN ("
            "SELECT id FROM ("
            "SELECT id, ROW_NUMBER() OVER ("
            "PARTITION BY payload_fingerprint ORDER BY "
            "CASE outcome WHEN 'updated' THEN 0 WHEN 'test' THEN 0 "
            "WHEN 'ignored' THEN 1 ELSE 2 END, "
            "updated_at DESC, created_at DESC, id DESC"
            ") AS duplicate_rank FROM billing_notification_receipts"
            ") AS ranked WHERE duplicate_rank > 1"
            ")"
        )
    )
    with op.batch_alter_table("feature_usage_receipts") as batch:
        batch.drop_constraint(
            op.f("ck_feature_usage_receipts_policy_version_length"),
            type_="check",
        )
        batch.drop_column("released_at")
        batch.drop_column("policy_version")

    with op.batch_alter_table("billing_notification_receipts") as batch:
        batch.drop_constraint(
            op.f("ck_billing_notification_receipts_dead_letter_consistent"),
            type_="check",
        )
        batch.drop_constraint(
            op.f("ck_billing_notification_receipts_payload_consistent"),
            type_="check",
        )
        batch.drop_constraint(
            op.f("ck_billing_notification_receipts_key_epoch_length"),
            type_="check",
        )
        batch.drop_constraint(
            op.f("ck_billing_notification_receipts_manual_replay_count_nonnegative"),
            type_="check",
        )
        batch.drop_constraint(
            op.f("ck_billing_notification_receipts_outcome_allowed"),
            type_="check",
        )
        batch.drop_constraint(
            op.f("uq_billing_notification_receipts_epoch_fingerprint"),
            type_="unique",
        )
        batch.create_unique_constraint(
            op.f("uq_billing_notification_receipts_payload_fingerprint"),
            ["payload_fingerprint"],
        )
        batch.create_check_constraint(
            op.f("ck_billing_notification_receipts_outcome_allowed"),
            "outcome IN ('pending', 'test', 'updated', 'ignored')",
        )
        batch.create_check_constraint(
            op.f("ck_billing_notification_receipts_payload_consistent"),
            _old_notification_payload_constraint(),
        )
        batch.alter_column(
            "notification_id",
            existing_type=sa.String(length=128),
            nullable=False,
        )
        batch.drop_column("compacted_at")
        batch.drop_column("dead_lettered_at")
        batch.drop_column("manual_replay_count")
        batch.drop_column("key_epoch")

    op.drop_table("billing_purchase_fetches")
