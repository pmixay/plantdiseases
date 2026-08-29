"""Add the deployment-wide PlantNet attempt and cost-exposure budget.

Revision ID: 0009_plantnet_global_budget
Revises: 0008_pro_entitlements
Create Date: 2026-08-29
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_plantnet_global_budget"
down_revision: str | None = "0008_pro_entitlements"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PLANTNET_AUTHORITY_ID = uuid.UUID("00000000-0000-0000-0000-000000000009")


def upgrade() -> None:
    op.create_table(
        "vendor_budget_authorities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "provider = 'plantnet'",
            name=op.f("ck_vendor_budget_authorities_provider_allowed"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vendor_budget_authorities")),
        sa.UniqueConstraint(
            "provider",
            name=op.f("uq_vendor_budget_authorities_provider"),
        ),
    )
    authority = sa.table(
        "vendor_budget_authorities",
        sa.column("id", sa.Uuid()),
        sa.column("provider", sa.String()),
    )
    op.bulk_insert(
        authority,
        [{"id": _PLANTNET_AUTHORITY_ID, "provider": "plantnet"}],
    )

    op.create_table(
        "vendor_budget_daily",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("authority_id", sa.Uuid(), nullable=False),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column(
            "attempts_used",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "cost_exposure_micros",
            sa.BigInteger(),
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
            "attempts_used >= 0",
            name=op.f("ck_vendor_budget_daily_attempts_used_nonnegative"),
        ),
        sa.CheckConstraint(
            "cost_exposure_micros >= 0",
            name=op.f("ck_vendor_budget_daily_cost_exposure_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["authority_id"],
            ["vendor_budget_authorities.id"],
            name=op.f(
                "fk_vendor_budget_daily_authority_id_vendor_budget_authorities"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vendor_budget_daily")),
        sa.UniqueConstraint(
            "authority_id",
            "usage_date",
            name=op.f("uq_vendor_budget_daily_authority_date"),
        ),
    )
    op.create_index(
        "ix_vendor_budget_daily_usage_date",
        "vendor_budget_daily",
        ["usage_date"],
        unique=False,
    )

    op.create_table(
        "vendor_budget_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("authority_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("attempt_cost_micros", sa.BigInteger(), nullable=False),
        sa.Column("daily_attempt_limit", sa.Integer(), nullable=False),
        sa.Column("daily_cost_limit_micros", sa.BigInteger(), nullable=False),
        sa.Column("attempts_used", sa.Integer(), nullable=False),
        sa.Column("cost_exposure_micros", sa.BigInteger(), nullable=False),
        sa.Column("remaining_attempts", sa.Integer(), nullable=False),
        sa.Column("remaining_cost_micros", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(length=24), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "attempt_cost_micros > 0",
            name=op.f("ck_vendor_budget_receipts_attempt_cost_positive"),
        ),
        sa.CheckConstraint(
            "daily_attempt_limit > 0",
            name=op.f("ck_vendor_budget_receipts_attempt_limit_positive"),
        ),
        sa.CheckConstraint(
            "daily_cost_limit_micros > 0",
            name=op.f("ck_vendor_budget_receipts_cost_limit_positive"),
        ),
        sa.CheckConstraint(
            "attempts_used >= 0 AND attempts_used <= daily_attempt_limit",
            name=op.f("ck_vendor_budget_receipts_attempts_used_bounded"),
        ),
        sa.CheckConstraint(
            "cost_exposure_micros >= 0 AND "
            "cost_exposure_micros <= daily_cost_limit_micros",
            name=op.f("ck_vendor_budget_receipts_cost_exposure_bounded"),
        ),
        sa.CheckConstraint(
            "remaining_attempts = daily_attempt_limit - attempts_used",
            name=op.f("ck_vendor_budget_receipts_remaining_attempts_consistent"),
        ),
        sa.CheckConstraint(
            "remaining_cost_micros = daily_cost_limit_micros - cost_exposure_micros",
            name=op.f("ck_vendor_budget_receipts_remaining_cost_consistent"),
        ),
        sa.CheckConstraint(
            "(allowed AND reason IS NULL) OR "
            "(NOT allowed AND reason IN ('attempt_limit', 'cost_limit'))",
            name=op.f("ck_vendor_budget_receipts_decision_consistent"),
        ),
        sa.CheckConstraint(
            "length(policy_version) BETWEEN 1 AND 64",
            name=op.f("ck_vendor_budget_receipts_policy_version_length"),
        ),
        sa.ForeignKeyConstraint(
            ["authority_id"],
            ["vendor_budget_authorities.id"],
            name=op.f(
                "fk_vendor_budget_receipts_authority_id_vendor_budget_authorities"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vendor_budget_receipts")),
        sa.UniqueConstraint(
            "authority_id",
            "request_id",
            name=op.f("uq_vendor_budget_receipts_authority_request"),
        ),
    )
    op.create_index(
        "ix_vendor_budget_receipts_created_at",
        "vendor_budget_receipts",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_vendor_budget_receipts_created_at",
        table_name="vendor_budget_receipts",
    )
    op.drop_table("vendor_budget_receipts")
    op.drop_index(
        "ix_vendor_budget_daily_usage_date",
        table_name="vendor_budget_daily",
    )
    op.drop_table("vendor_budget_daily")
    op.drop_table("vendor_budget_authorities")
