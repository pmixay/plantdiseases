"""SQLAlchemy model for the private account PostgreSQL database."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Account(TimestampMixin, Base):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("community_actor_id", name="uq_accounts_community_actor_id"),
        UniqueConstraint("normalized_nickname", name="uq_accounts_normalized_nickname"),
        CheckConstraint(
            "primary_provider IN ('yandex', 'vk')", name="primary_provider_allowed"
        ),
        CheckConstraint(
            "state IN ('signup_pending', 'active', 'deletion_pending')",
            name="state_allowed",
        ),
        CheckConstraint("authorization_version >= 1", name="authorization_version_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    community_actor_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False, default=uuid.uuid4
    )
    nickname: Mapped[str] = mapped_column(String(24), nullable=False)
    normalized_nickname: Mapped[str] = mapped_column(String(64), nullable=False)
    primary_provider: Mapped[str] = mapped_column(String(10), nullable=False)
    primary_provider_display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    primary_provider_avatar_url: Mapped[str | None] = mapped_column(String(2048))
    nickname_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    authorization_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, default="active", server_default=text("'active'")
    )


class AccountIdentity(Base):
    __tablename__ = "account_identities"
    __table_args__ = (
        UniqueConstraint("provider", "provider_subject", name="uq_identity_provider_subject"),
        UniqueConstraint("account_id", "provider", name="uq_identity_account_provider"),
        CheckConstraint("provider IN ('yandex', 'vk')", name="provider_allowed"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(10), nullable=False)
    provider_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(2048))
    refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        UniqueConstraint("token_family_id", name="uq_sessions_token_family_id"),
        CheckConstraint("idle_expires_at <= absolute_expires_at", name="expiry_order"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    installation_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    device_label: Mapped[str] = mapped_column(
        String(64), nullable=False, default="Android device", server_default=text("'Android device'")
    )
    token_family_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False, default=uuid.uuid4
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
        CheckConstraint("length(token_hash) = 32", name="token_hash_sha256"),
        CheckConstraint("idle_expires_at <= absolute_expires_at", name="expiry_order"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    replacement_token_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("refresh_tokens.id", ondelete="SET NULL")
    )


class PendingSignup(Base):
    __tablename__ = "pending_signups"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_pending_signups_token_hash"),
        CheckConstraint("length(token_hash) = 32", name="token_hash_sha256"),
        CheckConstraint("provider IN ('yandex', 'vk')", name="provider_allowed"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(10), nullable=False)
    provider_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    provider_avatar_url: Mapped[str | None] = mapped_column(String(2048))
    # A random, issuer-scoped application installation identifier supplied in
    # the same provider-proof exchange. Older beta rows remain nullable and
    # deliberately cannot use the new completion contract.
    installation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SignupCompletion(Base):
    """Recoverable, two-phase signup completion without recoverable bearers."""

    __tablename__ = "signup_completions"
    __table_args__ = (
        UniqueConstraint("pending_signup_id", name="uq_signup_completions_pending_signup_id"),
        CheckConstraint("length(request_fingerprint) = 32", name="request_fingerprint_sha256"),
        CheckConstraint("created_at < expires_at", name="expiry_order"),
        CheckConstraint(
            "confirmed_at IS NULL OR cancelled_at IS NULL",
            name="single_terminal_state",
        ),
        CheckConstraint(
            "(account_id IS NULL AND session_id IS NULL) OR "
            "(account_id IS NOT NULL AND session_id IS NOT NULL)",
            name="resource_pair",
        ),
        CheckConstraint(
            "cancelled_at IS NULL OR (account_id IS NULL AND session_id IS NULL)",
            name="cancelled_resources_cleared",
        ),
        CheckConstraint(
            "confirmed_at IS NULL OR (account_id IS NOT NULL AND session_id IS NOT NULL)",
            name="confirmed_resources_present",
        ),
    )

    # This UUID is generated and durably stored by the client before its first
    # completion request. It is the logical operation identity, not a secret.
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    pending_signup_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("pending_signups.id", ondelete="CASCADE"),
        nullable=False,
    )
    installation_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    request_fingerprint: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    # Destructive service paths clear both references in one update before
    # deleting provisional resources. Restrictive FKs ensure an unreviewed
    # account/session delete cannot partially corrupt this credential marker.
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id"), index=True
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sessions.id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReauthChallenge(Base):
    __tablename__ = "reauth_challenges"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_reauth_challenges_token_hash"),
        CheckConstraint("length(token_hash) = 32", name="token_hash_sha256"),
        CheckConstraint("provider IN ('yandex', 'vk')", name="provider_allowed"),
        CheckConstraint(
            "scope IN ('deletion', 'link_yandex', 'link_vk')", name="scope_allowed"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(10), nullable=False)
    scope: Mapped[str] = mapped_column(String(24), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AccountDeletionJob(Base):
    __tablename__ = "account_deletion_jobs"
    __table_args__ = (
        UniqueConstraint("account_id", name="uq_deletion_jobs_account_id"),
        CheckConstraint(
            "state IN ('pending', 'retry', 'completed')", name="state_allowed"
        ),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="SET NULL")
    )
    community_actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default=text("'pending'")
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AccountNicknameJob(Base):
    """Durable latest-value delivery to denormalized community rows."""

    __tablename__ = "account_nickname_jobs"
    __table_args__ = (
        UniqueConstraint("account_id", name="uq_nickname_jobs_account_id"),
        CheckConstraint("state IN ('pending', 'retry')", name="state_allowed"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    community_actor_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    nickname: Mapped[str] = mapped_column(String(24), nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default=text("'pending'")
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AccountNicknameClaim(Base):
    """The unique nickname namespace, including pending remote aliases."""

    __tablename__ = "account_nickname_claims"
    __table_args__ = (
        UniqueConstraint(
            "normalized_nickname",
            name="uq_nickname_claims_normalized_nickname",
        ),
        CheckConstraint("state IN ('current', 'reserved')", name="state_allowed"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    normalized_nickname: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class PushDevice(Base):
    __tablename__ = "push_devices"
    __table_args__ = (
        UniqueConstraint("token_fingerprint", name="uq_push_devices_token_fingerprint"),
        UniqueConstraint("session_id", name="uq_push_devices_session_id"),
        CheckConstraint("length(token_fingerprint) = 32", name="token_fingerprint_sha256"),
        CheckConstraint("platform = 'android'", name="platform_allowed"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    token_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    token_fingerprint: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    platform: Mapped[str] = mapped_column(
        String(16), nullable=False, default="android", server_default=text("'android'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AccountConsent(Base):
    __tablename__ = "account_consents"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "consent_type", "disclosure_version", name="uq_account_consent_version"
        ),
        CheckConstraint(
            "consent_type IN ('privacy', 'model_improvement', 'telemetry', 'push')",
            name="consent_type_allowed",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    consent_type: Mapped[str] = mapped_column(String(32), nullable=False)
    disclosure_version: Mapped[str] = mapped_column(String(32), nullable=False)
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AccountSecurityEvent(Base):
    __tablename__ = "account_security_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('exchange_failed', 'refresh_reuse', 'session_revoked', "
            "'reauth_failed', 'linking_failed', 'deletion_requested')",
            name="event_type_allowed",
        ),
        CheckConstraint("outcome IN ('accepted', 'rejected')", name="outcome_allowed"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE")
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


Index("ix_identity_account_id", AccountIdentity.account_id)
Index("ix_sessions_absolute_expires_at", Session.absolute_expires_at)
Index("ix_deletion_jobs_state_next_attempt", AccountDeletionJob.state, AccountDeletionJob.next_attempt_at)
Index("ix_nickname_jobs_state_next_attempt", AccountNicknameJob.state, AccountNicknameJob.next_attempt_at)
Index("ix_nickname_claims_account_state", AccountNicknameClaim.account_id, AccountNicknameClaim.state)
