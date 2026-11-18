"""Community roles (expert, moderator, admin) and achievement rewards.

Revision ID: 0011_community_roles_rewards
Revises: 0010_billing_lifecycle_quota_policy
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_community_roles_rewards"
down_revision: str | None = "0010_billing_lifecycle_quota_policy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "community_roles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("scope", sa.String(length=32), server_default=sa.text("'*'"), nullable=False),
        sa.Column("granted_by_account_id", sa.Uuid(), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "role IN ('expert', 'moderator', 'admin')",
            name=op.f("ck_community_roles_role_allowed"),
        ),
        sa.CheckConstraint(
            "length(scope) BETWEEN 1 AND 32", name=op.f("ck_community_roles_scope_length")
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"],
            name=op.f("fk_community_roles_account_id_accounts"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by_account_id"], ["accounts.id"],
            name=op.f("fk_community_roles_granted_by_account_id_accounts"), ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_community_roles")),
    )
    op.create_index(
        "ix_community_roles_account", "community_roles", ["account_id", "revoked_at"]
    )
    op.create_table(
        "community_rewards",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("achievement_count", sa.Integer(), nullable=False),
        sa.Column("discount_percent", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "achievement_count >= 0",
            name=op.f("ck_community_rewards_achievement_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "discount_percent BETWEEN 0 AND 100",
            name=op.f("ck_community_rewards_discount_percent_range"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"],
            name=op.f("fk_community_rewards_account_id_accounts"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("account_id", name=op.f("pk_community_rewards")),
    )


def downgrade() -> None:
    op.drop_table("community_rewards")
    op.drop_index("ix_community_roles_account", table_name="community_roles")
    op.drop_table("community_roles")
