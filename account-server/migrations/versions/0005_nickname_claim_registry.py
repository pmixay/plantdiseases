"""Reserve current and not-yet-erased community nicknames.

Revision ID: 0005_nickname_claim_registry
Revises: 0004_nickname_delivery_outbox
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_nickname_claim_registry"
down_revision: str | None = "0004_nickname_delivery_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "account_nickname_claims",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("normalized_nickname", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
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
            "state IN ('current', 'reserved')",
            name=op.f("ck_account_nickname_claims_state_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_account_nickname_claims_account_id_accounts",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_account_nickname_claims"),
        sa.UniqueConstraint(
            "normalized_nickname",
            name="uq_nickname_claims_normalized_nickname",
        ),
    )
    op.create_index(
        "ix_nickname_claims_account_state",
        "account_nickname_claims",
        ["account_id", "state"],
        unique=False,
    )
    # Every existing account starts with one current claim. Reusing the
    # account UUID as this first row's UUID avoids a database-specific UUID
    # generator and is safe because claim ids have their own namespace.
    op.execute(
        sa.text(
            "INSERT INTO account_nickname_claims "
            "(id, account_id, normalized_nickname, state, created_at, updated_at) "
            "SELECT id, id, normalized_nickname, 'current', created_at, updated_at "
            "FROM accounts"
        )
    )


def downgrade() -> None:
    op.drop_index(
        "ix_nickname_claims_account_state",
        table_name="account_nickname_claims",
    )
    op.drop_table("account_nickname_claims")
