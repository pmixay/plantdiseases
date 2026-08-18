"""Add a durable latest-nickname delivery outbox.

Revision ID: 0004_nickname_delivery_outbox
Revises: 0003_push_session_unique
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_nickname_delivery_outbox"
down_revision: str | None = "0003_push_session_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "account_nickname_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("community_actor_id", sa.Uuid(), nullable=False),
        sa.Column("nickname", sa.String(length=24), nullable=False),
        sa.Column(
            "state",
            sa.String(length=16),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
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
            "attempt_count >= 0",
            name=op.f("ck_account_nickname_jobs_attempt_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'retry')",
            name=op.f("ck_account_nickname_jobs_state_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_account_nickname_jobs_account_id_accounts",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_account_nickname_jobs"),
        sa.UniqueConstraint("account_id", name="uq_nickname_jobs_account_id"),
    )
    op.create_index(
        "ix_nickname_jobs_state_next_attempt",
        "account_nickname_jobs",
        ["state", "next_attempt_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_nickname_jobs_state_next_attempt",
        table_name="account_nickname_jobs",
    )
    op.drop_table("account_nickname_jobs")
