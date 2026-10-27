"""Add private email/password credentials alongside OAuth identities.

Revision ID: 0007_email_password_auth
Revises: 0006_signup_completion_idempotency
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_email_password_auth"
down_revision: str | None = "0006_signup_completion_idempotency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_accounts_primary_provider_allowed"),
        "accounts",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_accounts_primary_provider_allowed"),
        "accounts",
        "primary_provider IN ('yandex', 'vk', 'email')",
    )
    op.drop_constraint(
        op.f("ck_pending_signups_provider_allowed"),
        "pending_signups",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_pending_signups_provider_allowed"),
        "pending_signups",
        "provider IN ('yandex', 'vk', 'email')",
    )
    op.drop_constraint(
        op.f("ck_reauth_challenges_provider_allowed"),
        "reauth_challenges",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_reauth_challenges_provider_allowed"),
        "reauth_challenges",
        "provider IN ('yandex', 'vk', 'email')",
    )

    op.create_table(
        "password_credentials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("email_fingerprint", sa.LargeBinary(length=32), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(email_fingerprint) = 32",
            name=op.f("ck_password_credentials_email_fingerprint_sha256"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_password_credentials_account_id_accounts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_password_credentials")),
        sa.UniqueConstraint(
            "account_id",
            name=op.f("uq_password_credentials_account_id"),
        ),
        sa.UniqueConstraint(
            "email_fingerprint",
            name=op.f("uq_password_credentials_email_fingerprint"),
        ),
    )

    op.create_table(
        "pending_password_credentials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pending_signup_id", sa.Uuid(), nullable=False),
        sa.Column("email_fingerprint", sa.LargeBinary(length=32), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(email_fingerprint) = 32",
            name=op.f("ck_pending_password_credentials_email_fingerprint_sha256"),
        ),
        sa.ForeignKeyConstraint(
            ["pending_signup_id"],
            ["pending_signups.id"],
            name=op.f(
                "fk_pending_password_credentials_pending_signup_id_pending_signups"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pending_password_credentials")),
        sa.UniqueConstraint(
            "pending_signup_id",
            name=op.f("uq_pending_password_credentials_pending_signup_id"),
        ),
    )
    op.create_index(
        op.f("ix_pending_password_credentials_email_fingerprint"),
        "pending_password_credentials",
        ["email_fingerprint"],
        unique=False,
    )


def downgrade() -> None:
    # Older revisions cannot represent email-primary accounts or their pending
    # flows. Remove those private rows deliberately before restoring checks.
    op.execute(sa.text("DELETE FROM pending_signups WHERE provider = 'email'"))
    op.execute(sa.text("DELETE FROM accounts WHERE primary_provider = 'email'"))
    op.execute(sa.text("DELETE FROM reauth_challenges WHERE provider = 'email'"))

    op.drop_index(
        op.f("ix_pending_password_credentials_email_fingerprint"),
        table_name="pending_password_credentials",
    )
    op.drop_table("pending_password_credentials")
    op.drop_table("password_credentials")

    op.drop_constraint(
        op.f("ck_reauth_challenges_provider_allowed"),
        "reauth_challenges",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_reauth_challenges_provider_allowed"),
        "reauth_challenges",
        "provider IN ('yandex', 'vk')",
    )
    op.drop_constraint(
        op.f("ck_pending_signups_provider_allowed"),
        "pending_signups",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_pending_signups_provider_allowed"),
        "pending_signups",
        "provider IN ('yandex', 'vk')",
    )
    op.drop_constraint(
        op.f("ck_accounts_primary_provider_allowed"),
        "accounts",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_accounts_primary_provider_allowed"),
        "accounts",
        "primary_provider IN ('yandex', 'vk')",
    )
