"""Add recoverable two-phase signup completion.

Revision ID: 0006_signup_completion_idempotency
Revises: 0005_nickname_claim_registry
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_signup_completion_idempotency"
down_revision: str | None = "0005_nickname_claim_registry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing ten-minute pending rows cannot be bound retroactively to a
    # trustworthy installation. They remain nullable and expire normally.
    op.add_column(
        "pending_signups",
        sa.Column("installation_id", sa.Uuid(), nullable=True),
    )

    op.drop_constraint("ck_accounts_state_allowed", "accounts", type_="check")
    op.create_check_constraint(
        "ck_accounts_state_allowed",
        "accounts",
        "state IN ('signup_pending', 'active', 'deletion_pending')",
    )

    op.create_table(
        "signup_completions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pending_signup_id", sa.Uuid(), nullable=False),
        sa.Column("installation_id", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.LargeBinary(length=32), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=True),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "length(request_fingerprint) = 32",
            name=op.f("ck_signup_completions_request_fingerprint_sha256"),
        ),
        sa.CheckConstraint(
            "created_at < expires_at",
            name=op.f("ck_signup_completions_expiry_order"),
        ),
        sa.CheckConstraint(
            "confirmed_at IS NULL OR cancelled_at IS NULL",
            name=op.f("ck_signup_completions_single_terminal_state"),
        ),
        sa.CheckConstraint(
            "(account_id IS NULL AND session_id IS NULL) OR "
            "(account_id IS NOT NULL AND session_id IS NOT NULL)",
            name=op.f("ck_signup_completions_resource_pair"),
        ),
        sa.CheckConstraint(
            "cancelled_at IS NULL OR (account_id IS NULL AND session_id IS NULL)",
            name=op.f("ck_signup_completions_cancelled_resources_cleared"),
        ),
        sa.CheckConstraint(
            "confirmed_at IS NULL OR (account_id IS NOT NULL AND session_id IS NOT NULL)",
            name=op.f("ck_signup_completions_confirmed_resources_present"),
        ),
        sa.ForeignKeyConstraint(
            ["pending_signup_id"],
            ["pending_signups.id"],
            name=op.f("fk_signup_completions_pending_signup_id_pending_signups"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_signup_completions_account_id_accounts"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_signup_completions_session_id_sessions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_signup_completions")),
        sa.UniqueConstraint(
            "pending_signup_id",
            name=op.f("uq_signup_completions_pending_signup_id"),
        ),
    )
    op.create_index(
        op.f("ix_signup_completions_account_id"),
        "signup_completions",
        ["account_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_signup_completions_expires_at"),
        "signup_completions",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_signup_completions_expires_at"),
        table_name="signup_completions",
    )
    op.drop_index(
        op.f("ix_signup_completions_account_id"),
        table_name="signup_completions",
    )
    op.drop_table("signup_completions")

    # v5 has no representation for an unconfirmed account. Converge those
    # rows (and their cascade-owned identities/sessions/claims) after removing
    # the restrictive completion FKs but before restoring the old state check.
    # Confirmed active accounts are kept.
    op.execute(sa.text("DELETE FROM accounts WHERE state = 'signup_pending'"))

    op.drop_constraint("ck_accounts_state_allowed", "accounts", type_="check")
    op.create_check_constraint(
        "ck_accounts_state_allowed",
        "accounts",
        "state IN ('active', 'deletion_pending')",
    )
    op.drop_column("pending_signups", "installation_id")
