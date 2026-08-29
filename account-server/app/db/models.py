"""SQLAlchemy model for the private account PostgreSQL database."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
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
            "primary_provider IN ('yandex', 'vk', 'email')",
            name="primary_provider_allowed",
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


class PasswordCredential(Base):
    """A password login method with no recoverable email address."""

    __tablename__ = "password_credentials"
    __table_args__ = (
        UniqueConstraint("account_id", name="uq_password_credentials_account_id"),
        UniqueConstraint("email_fingerprint", name="uq_password_credentials_email_fingerprint"),
        CheckConstraint(
            "length(email_fingerprint) = 32",
            name="email_fingerprint_sha256",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    email_fingerprint: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    password_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
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
        CheckConstraint("provider IN ('yandex', 'vk', 'email')", name="provider_allowed"),
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


class PendingPasswordCredential(Base):
    """Password material staged behind the existing one-use signup bearer."""

    __tablename__ = "pending_password_credentials"
    __table_args__ = (
        UniqueConstraint(
            "pending_signup_id",
            name="uq_pending_password_credentials_pending_signup_id",
        ),
        CheckConstraint(
            "length(email_fingerprint) = 32",
            name="email_fingerprint_sha256",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pending_signup_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("pending_signups.id", ondelete="CASCADE"),
        nullable=False,
    )
    email_fingerprint: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


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
        CheckConstraint("provider IN ('yandex', 'vk', 'email')", name="provider_allowed"),
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


class BillingCustomer(TimestampMixin, Base):
    """Random app-scoped RuStore identity, never an account or OAuth identifier."""

    __tablename__ = "billing_customers"
    __table_args__ = (
        UniqueConstraint("account_id", name="uq_billing_customers_account_id"),
        UniqueConstraint(
            "external_account_id",
            name="uq_billing_customers_external_account_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    external_account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False, default=uuid.uuid4
    )


class BillingPurchaseFetch(Base):
    """Purchase-scoped generation reserved before each out-of-lock V5 fetch."""

    __tablename__ = "billing_purchase_fetches"
    __table_args__ = (
        CheckConstraint("generation > 0", name="generation_positive"),
        CheckConstraint(
            "applied_generation IS NULL OR "
            "(applied_generation > 0 AND applied_generation <= generation)",
            name="applied_generation_valid",
        ),
    )

    purchase_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True
    )
    product_id: Mapped[str] = mapped_column(String(128), nullable=False)
    sandbox: Mapped[bool] = mapped_column(Boolean, nullable=False)
    generation: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=1, server_default=text("1")
    )
    applied_generation: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Subscription(TimestampMixin, Base):
    """Last canonical RuStore state for one globally unique purchase."""

    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint("purchase_id", name="uq_subscriptions_purchase_id"),
        CheckConstraint("start_at < expires_at", name="expiry_order"),
        CheckConstraint(
            "payment_state IS NULL OR payment_state IN (0, 1, 2)",
            name="payment_state_allowed",
        ),
        CheckConstraint(
            "acknowledgement_state IN ('PENDING', 'ACKNOWLEDGED', 'UNKNOWN')",
            name="acknowledgement_state_allowed",
        ),
        CheckConstraint("price_amount_micros > 0", name="price_positive"),
        CheckConstraint("length(price_currency_code) = 3", name="currency_code_length"),
        CheckConstraint(
            "status IN ('pending', 'trial', 'active', 'grace', 'expired')",
            name="status_allowed",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    billing_customer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("billing_customers.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id: Mapped[str] = mapped_column(String(128), nullable=False)
    purchase_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    sandbox: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    auto_renewing: Mapped[bool] = mapped_column(Boolean, nullable=False)
    payment_state: Mapped[int | None] = mapped_column(Integer)
    acknowledgement_state: Mapped[str] = mapped_column(String(16), nullable=False)
    order_id: Mapped[str] = mapped_column(String(256), nullable=False)
    price_currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    price_amount_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reconcile_attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    provider_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BillingNotificationReceipt(Base):
    """Authenticated RuStore event inbox and durable deduplication record."""

    __tablename__ = "billing_notification_receipts"
    __table_args__ = (
        UniqueConstraint(
            "key_epoch",
            "payload_fingerprint",
            name="uq_billing_notification_receipts_epoch_fingerprint",
        ),
        CheckConstraint("kind IN ('test', 'subscription')", name="kind_allowed"),
        CheckConstraint(
            "outcome IN ('pending', 'test', 'updated', 'ignored', 'dead_letter')",
            name="outcome_allowed",
        ),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        CheckConstraint(
            "manual_replay_count >= 0",
            name="manual_replay_count_nonnegative",
        ),
        CheckConstraint("length(key_epoch) BETWEEN 1 AND 64", name="key_epoch_length"),
        CheckConstraint(
            "length(payload_fingerprint) = 64",
            name="payload_fingerprint_sha256",
        ),
        CheckConstraint(
            "(compacted_at IS NOT NULL AND outcome IN "
            "('test', 'updated', 'ignored', 'dead_letter') AND notification_id IS NULL "
            "AND product_id IS NULL AND purchase_id IS NULL AND event_at IS NULL "
            "AND event_type IS NULL AND status_new IS NULL AND period_new IS NULL "
            "AND next_attempt_at IS NULL AND claim_token IS NULL AND "
            "claim_expires_at IS NULL) OR "
            "(compacted_at IS NULL AND notification_id IS NOT NULL AND "
            "((kind = 'test' AND outcome = 'test' AND product_id IS NULL AND "
            "purchase_id IS NULL AND event_at IS NULL AND event_type IS NULL AND "
            "status_new IS NULL AND period_new IS NULL AND next_attempt_at IS NULL "
            "AND claim_token IS NULL AND claim_expires_at IS NULL) OR "
            "(kind = 'subscription' AND product_id IS NOT NULL AND purchase_id IS NOT NULL "
            "AND event_at IS NOT NULL AND event_type IS NOT NULL AND status_new IS NOT NULL "
            "AND period_new IS NOT NULL AND ((outcome = 'pending' AND next_attempt_at IS NOT NULL) "
            "OR (outcome IN ('updated', 'ignored', 'dead_letter') AND next_attempt_at IS NULL "
            "AND claim_token IS NULL AND claim_expires_at IS NULL)))))",
            name="payload_consistent",
        ),
        CheckConstraint(
            "(claim_token IS NULL AND claim_expires_at IS NULL) OR "
            "(claim_token IS NOT NULL AND claim_expires_at IS NOT NULL AND outcome = 'pending')",
            name="claim_consistent",
        ),
        CheckConstraint(
            "(outcome = 'dead_letter' AND dead_lettered_at IS NOT NULL) OR "
            "(outcome <> 'dead_letter' AND dead_lettered_at IS NULL)",
            name="dead_letter_consistent",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    notification_id: Mapped[str | None] = mapped_column(String(128))
    key_epoch: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    sandbox: Mapped[bool] = mapped_column(Boolean, nullable=False)
    product_id: Mapped[str | None] = mapped_column(String(128))
    purchase_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    event_type: Mapped[str | None] = mapped_column(String(32))
    status_new: Mapped[str | None] = mapped_column(String(16))
    period_new: Mapped[str | None] = mapped_column(String(16))
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    manual_replay_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_token: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    dead_lettered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    compacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class FeatureUsage(TimestampMixin, Base):
    """One UTC-day counter, serialized by locking its owning account."""

    __tablename__ = "feature_usage"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "feature",
            "usage_date",
            name="uq_feature_usage_account_feature_date",
        ),
        CheckConstraint(
            "feature IN ('gamma_analysis', 'frontier_analysis', "
            "'plant_identification', 'ai_doctor', 'care_plan')",
            name="feature_allowed",
        ),
        CheckConstraint("used >= 0", name="used_nonnegative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    feature: Mapped[str] = mapped_column(String(32), nullable=False)
    usage_date: Mapped[date] = mapped_column(Date, nullable=False)
    used: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )


class FeatureUsageReceipt(Base):
    """Frozen result for retry-safe cost authorization."""

    __tablename__ = "feature_usage_receipts"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "feature",
            "request_id",
            name="uq_feature_usage_receipts_account_feature_request",
        ),
        CheckConstraint(
            "feature IN ('gamma_analysis', 'frontier_analysis', "
            "'plant_identification', 'ai_doctor', 'care_plan')",
            name="feature_allowed",
        ),
        CheckConstraint("plan IN ('free', 'pro')", name="plan_allowed"),
        CheckConstraint(
            "length(policy_version) BETWEEN 1 AND 64",
            name="policy_version_length",
        ),
        CheckConstraint("daily_limit >= 0", name="daily_limit_nonnegative"),
        CheckConstraint(
            "remaining >= 0 AND remaining <= daily_limit",
            name="remaining_bounded",
        ),
        CheckConstraint(
            "(allowed AND reason IS NULL AND daily_limit > 0 AND "
            "remaining < daily_limit) OR "
            "(NOT allowed AND remaining = 0 AND "
            "((reason = 'pro_required' AND plan = 'free' AND daily_limit = 0) OR "
            "(reason = 'quota_exhausted' AND daily_limit > 0)))",
            name="decision_consistent",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    feature: Mapped[str] = mapped_column(String(32), nullable=False)
    request_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    usage_date: Mapped[date] = mapped_column(Date, nullable=False)
    allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    plan: Mapped[str] = mapped_column(String(8), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    daily_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    remaining: Mapped[int] = mapped_column(Integer, nullable=False)
    reset_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(24))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class VendorBudgetAuthority(Base):
    """One seeded row per cost-bearing vendor, used as the global lock."""

    __tablename__ = "vendor_budget_authorities"
    __table_args__ = (
        UniqueConstraint("provider", name="uq_vendor_budget_authorities_provider"),
        CheckConstraint("provider = 'plantnet'", name="provider_allowed"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class VendorBudgetDaily(Base):
    """Deployment-wide UTC-day attempt and marginal-cost exposure ledger."""

    __tablename__ = "vendor_budget_daily"
    __table_args__ = (
        UniqueConstraint(
            "authority_id",
            "usage_date",
            name="uq_vendor_budget_daily_authority_date",
        ),
        CheckConstraint("attempts_used >= 0", name="attempts_used_nonnegative"),
        CheckConstraint(
            "cost_exposure_micros >= 0",
            name="cost_exposure_nonnegative",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    authority_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("vendor_budget_authorities.id", ondelete="RESTRICT"),
        nullable=False,
    )
    usage_date: Mapped[date] = mapped_column(Date, nullable=False)
    attempts_used: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    cost_exposure_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class VendorBudgetReceipt(Base):
    """Immutable retry result for one anonymous upstream-attempt UUID."""

    __tablename__ = "vendor_budget_receipts"
    __table_args__ = (
        UniqueConstraint(
            "authority_id",
            "request_id",
            name="uq_vendor_budget_receipts_authority_request",
        ),
        CheckConstraint("length(policy_version) BETWEEN 1 AND 64", name="policy_version_length"),
        CheckConstraint("attempt_cost_micros > 0", name="attempt_cost_positive"),
        CheckConstraint("daily_attempt_limit > 0", name="attempt_limit_positive"),
        CheckConstraint("daily_cost_limit_micros > 0", name="cost_limit_positive"),
        CheckConstraint(
            "attempts_used >= 0 AND attempts_used <= daily_attempt_limit",
            name="attempts_used_bounded",
        ),
        CheckConstraint(
            "cost_exposure_micros >= 0 AND "
            "cost_exposure_micros <= daily_cost_limit_micros",
            name="cost_exposure_bounded",
        ),
        CheckConstraint(
            "remaining_attempts = daily_attempt_limit - attempts_used",
            name="remaining_attempts_consistent",
        ),
        CheckConstraint(
            "remaining_cost_micros = daily_cost_limit_micros - cost_exposure_micros",
            name="remaining_cost_consistent",
        ),
        CheckConstraint(
            "(allowed AND reason IS NULL) OR "
            "(NOT allowed AND reason IN ('attempt_limit', 'cost_limit'))",
            name="decision_consistent",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    authority_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("vendor_budget_authorities.id", ondelete="RESTRICT"),
        nullable=False,
    )
    request_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    usage_date: Mapped[date] = mapped_column(Date, nullable=False)
    allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_cost_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    daily_attempt_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    daily_cost_limit_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    attempts_used: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_exposure_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    remaining_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    remaining_cost_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


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
Index(
    "ix_subscriptions_customer_expires",
    Subscription.billing_customer_id,
    Subscription.expires_at,
)
Index("ix_subscriptions_reconcile_attempted_at", Subscription.reconcile_attempted_at)
Index(
    "ix_billing_notification_receipts_created_at",
    BillingNotificationReceipt.created_at,
)
Index(
    "ix_billing_notification_receipts_due",
    BillingNotificationReceipt.outcome,
    BillingNotificationReceipt.next_attempt_at,
    BillingNotificationReceipt.claim_expires_at,
)
Index("ix_feature_usage_date", FeatureUsage.usage_date)
Index("ix_feature_usage_receipts_created_at", FeatureUsageReceipt.created_at)
Index("ix_vendor_budget_daily_usage_date", VendorBudgetDaily.usage_date)
Index("ix_vendor_budget_receipts_created_at", VendorBudgetReceipt.created_at)
