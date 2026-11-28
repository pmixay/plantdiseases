"""Verified email sign-in: encrypted address, mailed codes, adding email.

The keyed fingerprint stays the only lookup key. The address is stored
encrypted with a key derived from the account encryption key file, which the
database never sees. Existing credentials get NULL in both new columns: their
addresses were never kept, so they become usable again through password reset.

Revision ID: 0012_email_verification
Revises: 0011_community_roles_rewards
Create Date: 2026-11-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_email_verification"
down_revision: str | None = "0011_community_roles_rewards"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CREDENTIAL_TABLES = ("password_credentials", "pending_password_credentials")


def upgrade() -> None:
    for table in _CREDENTIAL_TABLES:
        op.add_column(table, sa.Column("email_ciphertext", sa.LargeBinary(length=512)))
        op.add_column(table, sa.Column("email_verified_at", sa.DateTime(timezone=True)))

    op.drop_constraint(
        op.f("ck_reauth_challenges_scope_allowed"),
        "reauth_challenges",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_reauth_challenges_scope_allowed"),
        "reauth_challenges",
        "scope IN ('deletion', 'link_yandex', 'link_vk', 'link_email')",
    )

    op.create_table(
        "email_codes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(length=8), nullable=False),
        sa.Column("email_fingerprint", sa.LargeBinary(length=32), nullable=False),
        sa.Column("code_mac", sa.LargeBinary(length=32), nullable=False),
        sa.Column("pending_signup_id", sa.Uuid(), nullable=True),
        sa.Column("account_id", sa.Uuid(), nullable=True),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("password_hash", sa.String(length=512), nullable=True),
        sa.Column("email_ciphertext", sa.LargeBinary(length=512), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "purpose IN ('signup', 'reset', 'add')",
            name=op.f("ck_email_codes_purpose_allowed"),
        ),
        sa.CheckConstraint(
            "length(email_fingerprint) = 32",
            name=op.f("ck_email_codes_email_fingerprint_sha256"),
        ),
        sa.CheckConstraint(
            "length(code_mac) = 32",
            name=op.f("ck_email_codes_code_mac_sha256"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_email_codes_attempts_nonnegative")),
        sa.ForeignKeyConstraint(
            ["pending_signup_id"],
            ["pending_signups.id"],
            name=op.f("fk_email_codes_pending_signup_id_pending_signups"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_email_codes_account_id_accounts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_email_codes_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_codes")),
    )
    for column in ("email_fingerprint", "pending_signup_id", "account_id", "expires_at"):
        op.create_index(op.f(f"ix_email_codes_{column}"), "email_codes", [column])


def downgrade() -> None:
    # Dropping the table drops its indexes. Codes live for minutes, and the
    # encrypted addresses cannot be represented by the older revision.
    op.drop_table("email_codes")
    op.execute(sa.text("DELETE FROM reauth_challenges WHERE scope = 'link_email'"))
    op.drop_constraint(
        op.f("ck_reauth_challenges_scope_allowed"),
        "reauth_challenges",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_reauth_challenges_scope_allowed"),
        "reauth_challenges",
        "scope IN ('deletion', 'link_yandex', 'link_vk')",
    )
    for table in reversed(_CREDENTIAL_TABLES):
        op.drop_column(table, "email_verified_at")
        op.drop_column(table, "email_ciphertext")
