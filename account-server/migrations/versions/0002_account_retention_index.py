"""Index absolute session expiry for bounded private-state pruning.

Revision ID: 0002_account_retention_index
Revises: 0001_private_account_schema
Create Date: 2026-08-07
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_account_retention_index"
down_revision: str | None = "0001_private_account_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_sessions_absolute_expires_at",
        "sessions",
        ["absolute_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_sessions_absolute_expires_at", table_name="sessions")
