"""Create the private account/session/push schema.

Revision ID: 0001_private_account_schema
Revises: None
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_private_account_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("community_actor_id", sa.Uuid(), nullable=False),
        sa.Column("nickname", sa.String(length=24), nullable=False),
        sa.Column("normalized_nickname", sa.String(length=64), nullable=False),
        sa.Column("primary_provider", sa.String(length=10), nullable=False),
        sa.Column("primary_provider_display_name", sa.String(length=256), nullable=False),
        sa.Column("primary_provider_avatar_url", sa.String(length=2048), nullable=True),
        sa.Column("nickname_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("authorization_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("state", sa.String(length=24), server_default=sa.text("'active'"), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "authorization_version >= 1",
            name=op.f("ck_accounts_authorization_version_positive"),
        ),
        sa.CheckConstraint(
            "primary_provider IN ('yandex', 'vk')",
            name=op.f("ck_accounts_primary_provider_allowed"),
        ),
        sa.CheckConstraint(
            "state IN ('active', 'deletion_pending')",
            name=op.f("ck_accounts_state_allowed"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_accounts"),
        sa.UniqueConstraint("community_actor_id", name="uq_accounts_community_actor_id"),
        sa.UniqueConstraint("normalized_nickname", name="uq_accounts_normalized_nickname"),
    )

    op.create_table(
        "account_identities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=10), nullable=False),
        sa.Column("provider_subject", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=False),
        sa.Column("avatar_url", sa.String(length=2048), nullable=True),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "provider IN ('yandex', 'vk')",
            name=op.f("ck_account_identities_provider_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_account_identities_account_id_accounts",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_account_identities"),
        sa.UniqueConstraint("account_id", "provider", name="uq_identity_account_provider"),
        sa.UniqueConstraint("provider", "provider_subject", name="uq_identity_provider_subject"),
    )
    op.create_index("ix_identity_account_id", "account_identities", ["account_id"])

    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("installation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "device_label",
            sa.String(length=64),
            server_default=sa.text("'Android device'"),
            nullable=False,
        ),
        sa.Column("token_family_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "idle_expires_at <= absolute_expires_at",
            name=op.f("ck_sessions_expiry_order"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name="fk_sessions_account_id_accounts", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sessions"),
        sa.UniqueConstraint("token_family_id", name="uq_sessions_token_family_id"),
    )
    op.create_index("ix_sessions_account_id", "sessions", ["account_id"])

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("replacement_token_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "idle_expires_at <= absolute_expires_at",
            name=op.f("ck_refresh_tokens_expiry_order"),
        ),
        sa.CheckConstraint(
            "length(token_hash) = 32",
            name=op.f("ck_refresh_tokens_token_hash_sha256"),
        ),
        sa.ForeignKeyConstraint(
            ["replacement_token_id"],
            ["refresh_tokens.id"],
            name="fk_refresh_tokens_replacement_token_id_refresh_tokens",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["sessions.id"], name="fk_refresh_tokens_session_id_sessions", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_refresh_tokens"),
        sa.UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
    )
    op.create_index("ix_refresh_tokens_session_id", "refresh_tokens", ["session_id"])

    op.create_table(
        "pending_signups",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("provider", sa.String(length=10), nullable=False),
        sa.Column("provider_subject", sa.String(length=255), nullable=False),
        sa.Column("provider_display_name", sa.String(length=256), nullable=False),
        sa.Column("provider_avatar_url", sa.String(length=2048), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "provider IN ('yandex', 'vk')",
            name=op.f("ck_pending_signups_provider_allowed"),
        ),
        sa.CheckConstraint(
            "length(token_hash) = 32",
            name=op.f("ck_pending_signups_token_hash_sha256"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_pending_signups"),
        sa.UniqueConstraint("token_hash", name="uq_pending_signups_token_hash"),
    )
    op.create_index("ix_pending_signups_expires_at", "pending_signups", ["expires_at"])

    op.create_table(
        "reauth_challenges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("provider", sa.String(length=10), nullable=False),
        sa.Column("scope", sa.String(length=24), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "provider IN ('yandex', 'vk')",
            name=op.f("ck_reauth_challenges_provider_allowed"),
        ),
        sa.CheckConstraint(
            "scope IN ('deletion', 'link_yandex', 'link_vk')",
            name=op.f("ck_reauth_challenges_scope_allowed"),
        ),
        sa.CheckConstraint(
            "length(token_hash) = 32",
            name=op.f("ck_reauth_challenges_token_hash_sha256"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_reauth_challenges_account_id_accounts",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name="fk_reauth_challenges_session_id_sessions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_reauth_challenges"),
        sa.UniqueConstraint("token_hash", name="uq_reauth_challenges_token_hash"),
    )
    op.create_index("ix_reauth_challenges_account_id", "reauth_challenges", ["account_id"])
    op.create_index("ix_reauth_challenges_expires_at", "reauth_challenges", ["expires_at"])

    op.create_table(
        "account_deletion_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=True),
        sa.Column("community_actor_id", sa.Uuid(), nullable=True),
        sa.Column(
            "state", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        *_timestamps(),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name=op.f("ck_account_deletion_jobs_attempt_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'retry', 'completed')",
            name=op.f("ck_account_deletion_jobs_state_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_account_deletion_jobs_account_id_accounts",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_account_deletion_jobs"),
        sa.UniqueConstraint("account_id", name="uq_deletion_jobs_account_id"),
    )
    op.create_index(
        "ix_account_deletion_jobs_next_attempt_at", "account_deletion_jobs", ["next_attempt_at"]
    )
    op.create_index(
        "ix_deletion_jobs_state_next_attempt",
        "account_deletion_jobs",
        ["state", "next_attempt_at"],
    )

    op.create_table(
        "push_devices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("token_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("token_fingerprint", sa.LargeBinary(length=32), nullable=False),
        sa.Column(
            "platform", sa.String(length=16), server_default=sa.text("'android'"), nullable=False
        ),
        *_timestamps(),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "platform = 'android'", name=op.f("ck_push_devices_platform_allowed")
        ),
        sa.CheckConstraint(
            "length(token_fingerprint) = 32",
            name=op.f("ck_push_devices_token_fingerprint_sha256"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name="fk_push_devices_account_id_accounts", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["sessions.id"], name="fk_push_devices_session_id_sessions", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_push_devices"),
        sa.UniqueConstraint("token_fingerprint", name="uq_push_devices_token_fingerprint"),
    )
    op.create_index("ix_push_devices_account_id", "push_devices", ["account_id"])

    op.create_table(
        "account_consents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("consent_type", sa.String(length=32), nullable=False),
        sa.Column("disclosure_version", sa.String(length=32), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "consent_type IN ('privacy', 'model_improvement', 'telemetry', 'push')",
            name=op.f("ck_account_consents_consent_type_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_account_consents_account_id_accounts",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_account_consents"),
        sa.UniqueConstraint(
            "account_id",
            "consent_type",
            "disclosure_version",
            name="uq_account_consent_version",
        ),
    )
    op.create_index("ix_account_consents_account_id", "account_consents", ["account_id"])

    op.create_table(
        "account_security_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=True),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('exchange_failed', 'refresh_reuse', 'session_revoked', "
            "'reauth_failed', 'linking_failed', 'deletion_requested')",
            name=op.f("ck_account_security_events_event_type_allowed"),
        ),
        sa.CheckConstraint(
            "outcome IN ('accepted', 'rejected')",
            name=op.f("ck_account_security_events_outcome_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_account_security_events_account_id_accounts",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name="fk_account_security_events_session_id_sessions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_account_security_events"),
    )
    op.create_index("ix_account_security_events_account_id", "account_security_events", ["account_id"])
    op.create_index("ix_account_security_events_created_at", "account_security_events", ["created_at"])
    op.create_index("ix_account_security_events_expires_at", "account_security_events", ["expires_at"])


def downgrade() -> None:
    op.drop_table("account_security_events")
    op.drop_table("account_consents")
    op.drop_table("push_devices")
    op.drop_table("account_deletion_jobs")
    op.drop_table("reauth_challenges")
    op.drop_table("pending_signups")
    op.drop_table("refresh_tokens")
    op.drop_table("sessions")
    op.drop_table("account_identities")
    op.drop_table("accounts")
