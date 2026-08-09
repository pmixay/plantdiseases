"""Keep at most one encrypted push credential per session.

Revision ID: 0003_push_session_unique
Revises: 0002_account_retention_index
Create Date: 2026-08-08
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_push_session_unique"
down_revision: str | None = "0002_account_retention_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Retain the most recently updated row if a beta database already contains
    # multiple registrations for one session.
    op.execute(
        """
        DELETE FROM push_devices
        WHERE id IN (
            SELECT id FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY session_id
                           ORDER BY updated_at DESC, created_at DESC, id DESC
                       ) AS row_number
                FROM push_devices
            ) AS ranked
            WHERE row_number > 1
        )
        """
    )
    op.create_unique_constraint(
        "uq_push_devices_session_id",
        "push_devices",
        ["session_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_push_devices_session_id",
        "push_devices",
        type_="unique",
    )
