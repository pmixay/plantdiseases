"""Transactional account, session, linking, deletion, and push workflows."""

from __future__ import annotations

import hashlib
import hmac
import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cryptography.exceptions import InvalidTag
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from app.api.errors import AccountAPIError
from app.db.models import (
    Account,
    AccountDeletionJob,
    AccountIdentity,
    AccountNicknameClaim,
    AccountNicknameJob,
    AccountSecurityEvent,
    PendingSignup,
    PushDevice,
    ReauthChallenge,
    RefreshToken,
    SignupCompletion,
)
from app.db.models import (
    Session as AccountSession,
)
from app.db.repository import AccountRepository
from app.nicknames import normalize_nickname, validate_nickname
from app.providers.base import (
    ProviderAdapter,
    ProviderName,
    ProviderVerificationError,
    VerifiedProviderIdentity,
)
from app.rate_limit import InMemoryRateLimiter
from app.security.push_crypto import PushTokenProtector
from app.security.tokens import (
    AccessPrincipal,
    TokenService,
    hash_opaque_token,
    new_opaque_token,
    utc_now,
)

ACCESS_IDLE_WRITE_INTERVAL = timedelta(minutes=5)
PENDING_SIGNUP_TTL = timedelta(minutes=10)
REAUTH_TTL = timedelta(minutes=5)
REFRESH_IDLE_TTL = timedelta(days=30)
SESSION_ABSOLUTE_TTL = timedelta(days=180)
NICKNAME_CHANGE_COOLDOWN = timedelta(days=30)
SECURITY_EVENT_TTL = timedelta(days=30)
COMPLETED_DELETION_JOB_TTL = timedelta(days=30)
MAX_ACTIVE_SESSIONS_PER_ACCOUNT = 8
MAX_RETAINED_SESSIONS_PER_ACCOUNT = 64
MAX_REFRESH_TOKENS_PER_SESSION = 4096
IDEMPOTENT_REFRESH_RETRY_GRACE = timedelta(days=7)
IDEMPOTENT_SIGNUP_RETRY_GRACE = timedelta(days=7)

logger = logging.getLogger("plantdiseases.account")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _as_utc_optional(value: datetime | None) -> datetime | None:
    return _as_utc(value) if value is not None else None


def _auth_error(code: str = "auth_required") -> AccountAPIError:
    return AccountAPIError(
        code,
        status_code=401,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _integrity_constraint(exc: IntegrityError) -> str:
    diagnostic = getattr(exc.orig, "diag", None)
    constraint_name = getattr(diagnostic, "constraint_name", None)
    if isinstance(constraint_name, str):
        return constraint_name.lower()
    # SQLite has no structured constraint diagnostic. This string is used only
    # for local test-dialect classification and is never logged or returned.
    return str(exc.orig).lower()


@dataclass(frozen=True, slots=True)
class CurrentAccount:
    account_id: uuid.UUID
    session_id: uuid.UUID
    community_actor_id: uuid.UUID
    nickname: str
    authorization_version: int


class AccountService:
    def __init__(
        self,
        *,
        repository: AccountRepository,
        providers: Mapping[ProviderName, ProviderAdapter],
        token_service: TokenService,
        push_protector: PushTokenProtector,
        limiter: InMemoryRateLimiter,
        blocked_nicknames: tuple[str, ...] = (),
        clock: Callable[[], datetime] = utc_now,
        notification_sink: Callable[[list[str], dict[str, str]], int] | None = None,
        deletion_dispatcher: Callable[[uuid.UUID], bool] | None = None,
        nickname_dispatcher: Callable[[uuid.UUID, str], bool] | None = None,
    ):
        self.repository = repository
        self.providers = dict(providers)
        self.token_service = token_service
        self.push_protector = push_protector
        self.limiter = limiter
        self.blocked_nicknames = blocked_nicknames
        self.clock = clock
        # A missing sink means push is unavailable. Silently accepting device
        # registrations made the production API claim a delivery capability it
        # did not have and accumulated credentials that could never be used.
        self.notification_sink = notification_sink
        self.deletion_dispatcher = deletion_dispatcher or (lambda _actor_id: False)
        self.nickname_dispatcher = nickname_dispatcher or (
            lambda _actor_id, _nickname: True
        )

    def limit_ip(self, operation: str, address: str) -> None:
        self.limiter.check(f"ip:{operation}", address)

    async def exchange(
        self,
        *,
        provider: ProviderName,
        provider_access_token: str,
        installation_id: uuid.UUID,
    ) -> dict:
        identity = await self._verify_provider(provider, provider_access_token)
        self._limit_subject("exchange", identity)
        now = self.clock()
        with self.repository.transaction() as transaction:
            self._lock_provider_subject(
                transaction,
                provider=provider,
                provider_subject=identity.subject,
            )
            # Lock the provider subject's pending lifecycle before discovering
            # identity ownership. A concurrent completion can turn a pending
            # row into an identity while this exchange waits; the subsequent
            # identity read must see and handle that committed state instead
            # of cancelling a consumed token and minting a replacement.
            subject_pending = list(
                transaction.scalars(
                    select(PendingSignup)
                    .where(
                        PendingSignup.provider == provider,
                        PendingSignup.provider_subject == identity.subject,
                        PendingSignup.cancelled_at.is_(None),
                    )
                    .order_by(PendingSignup.id)
                    .with_for_update()
                )
            )
            identity_reference = transaction.execute(
                select(AccountIdentity.id, AccountIdentity.account_id).where(
                    AccountIdentity.provider == provider,
                    AccountIdentity.provider_subject == identity.subject,
                )
            ).one_or_none()
            if identity_reference is None:
                for pending in subject_pending:
                    if pending.consumed_at is None:
                        pending.cancelled_at = now
                pending_token = new_opaque_token()
                transaction.add(
                    PendingSignup(
                        token_hash=hash_opaque_token(pending_token),
                        provider=provider,
                        provider_subject=identity.subject,
                        provider_display_name=identity.display_name,
                        provider_avatar_url=identity.avatar_url,
                        installation_id=installation_id,
                        created_at=now,
                        expires_at=now + PENDING_SIGNUP_TTL,
                    )
                )
                return {
                    "status": "profile_required",
                    "provider": provider,
                    "pending_signup_token": pending_token,
                    "expires_in": int(PENDING_SIGNUP_TTL.total_seconds()),
                }

            identity_id, account_id = identity_reference
            # Completion paths use pending -> completion -> account -> session.
            # Discover without a lock, then acquire the same canonical order
            # before touching the account. This prevents a reinstall exchange
            # (and session compaction below) from deadlocking an exact confirm.
            completion_reference = transaction.execute(
                select(SignupCompletion.id, SignupCompletion.pending_signup_id).where(
                    SignupCompletion.account_id == account_id
                )
            ).one_or_none()
            locked_pending: PendingSignup | None = None
            locked_completion: SignupCompletion | None = None
            if completion_reference is not None:
                completion_id, completion_pending_id = completion_reference
                locked_pending = transaction.scalar(
                    select(PendingSignup)
                    .where(PendingSignup.id == completion_pending_id)
                    .with_for_update()
                )
                locked_completion = transaction.scalar(
                    select(SignupCompletion)
                    .where(SignupCompletion.id == completion_id)
                    .with_for_update()
                )
            account = transaction.scalar(
                select(Account).where(Account.id == account_id).with_for_update()
            )
            stored_identity = transaction.scalar(
                select(AccountIdentity)
                .where(
                    AccountIdentity.id == identity_id,
                    AccountIdentity.account_id == account_id,
                    AccountIdentity.provider == provider,
                    AccountIdentity.provider_subject == identity.subject,
                )
                .with_for_update()
            )
            if account is None or stored_identity is None:
                raise _auth_error()
            if account.state == "signup_pending":
                # A valid fresh provider proof is the recovery path for a
                # reinstalled client that no longer has the encrypted pending
                # token. Roll back only the still-unconfirmed provisional
                # account, retain its bounded cancellation marker, and issue a
                # fresh installation-bound pending flow atomically.
                completion = locked_completion
                old_pending = locked_pending
                if (
                    completion is None
                    or old_pending is None
                    or completion.account_id != account.id
                    or completion.pending_signup_id != old_pending.id
                    or completion.confirmed_at is not None
                ):
                    raise AccountAPIError("signup_confirmation_required", status_code=409)
                completion.account_id = None
                completion.session_id = None
                completion.cancelled_at = now
                old_pending.cancelled_at = now
                transaction.flush()
                transaction.delete(account)
                transaction.flush()
                pending_token = new_opaque_token()
                transaction.add(
                    PendingSignup(
                        token_hash=hash_opaque_token(pending_token),
                        provider=provider,
                        provider_subject=identity.subject,
                        provider_display_name=identity.display_name,
                        provider_avatar_url=identity.avatar_url,
                        installation_id=installation_id,
                        created_at=now,
                        expires_at=now + PENDING_SIGNUP_TTL,
                    )
                )
                return {
                    "status": "profile_required",
                    "provider": provider,
                    "pending_signup_token": pending_token,
                    "expires_in": int(PENDING_SIGNUP_TTL.total_seconds()),
                }
            if account.state == "deletion_pending":
                raise AccountAPIError("deletion_pending", status_code=409)
            self._refresh_provider_profile(account, stored_identity, identity)
            account_session, refresh_token = self._new_session(
                transaction,
                account=account,
                installation_id=installation_id,
                now=now,
            )
            return self._token_bundle(
                account=account,
                account_session=account_session,
                refresh_token=refresh_token,
                now=now,
                status="authenticated",
            )

    def complete_signup(
        self,
        *,
        pending_token: str,
        completion_id: uuid.UUID,
        nickname: str,
        installation_id: uuid.UUID,
    ) -> dict:
        token_hash = hash_opaque_token(pending_token)
        now = self.clock()
        try:
            with self.repository.transaction() as transaction:
                pending = transaction.scalar(
                    select(PendingSignup)
                    .where(PendingSignup.token_hash == token_hash)
                    .with_for_update()
                )
                if pending is None:
                    raise AccountAPIError("pending_signup_expired", status_code=401)
                existing = transaction.scalar(
                    select(SignupCompletion)
                    .where(SignupCompletion.pending_signup_id == pending.id)
                    .with_for_update()
                )
                if existing is not None:
                    return self._retry_signup_completion(
                        transaction,
                        pending=pending,
                        completion=existing,
                        pending_token=pending_token,
                        completion_id=completion_id,
                        installation_id=installation_id,
                        # Mutable moderation policy applies only before the
                        # first commit. Exact lost-response retries compare the
                        # original HMAC-bound normalization and cannot be
                        # stranded by a later blocked-name configuration.
                        normalized_nickname=normalize_nickname(nickname),
                        now=now,
                    )
                self._require_live_pending(pending, now)
                if pending.installation_id is None or pending.installation_id != installation_id:
                    raise AccountAPIError("signup_completion_conflict", status_code=409)
                validated = validate_nickname(nickname, blocked=self.blocked_nicknames)
                self.limiter.check(
                    "subject:signup", f"{pending.provider}:{pending.provider_subject}"
                )
                nickname_owner = transaction.scalar(
                    select(AccountNicknameClaim.account_id).where(
                        AccountNicknameClaim.normalized_nickname == validated.normalized
                    )
                )
                if nickname_owner is not None:
                    raise AccountAPIError("nickname_taken", status_code=409)

                account = Account(
                    community_actor_id=uuid.uuid4(),
                    nickname=validated.display,
                    normalized_nickname=validated.normalized,
                    primary_provider=pending.provider,
                    primary_provider_display_name=pending.provider_display_name,
                    primary_provider_avatar_url=pending.provider_avatar_url,
                    nickname_changed_at=now,
                    authorization_version=1,
                    state="signup_pending",
                    created_at=now,
                    updated_at=now,
                )
                transaction.add(account)
                transaction.flush()
                transaction.add(
                    AccountNicknameClaim(
                        account_id=account.id,
                        normalized_nickname=validated.normalized,
                        state="current",
                        created_at=now,
                        updated_at=now,
                    )
                )
                transaction.add(
                    AccountIdentity(
                        account_id=account.id,
                        provider=pending.provider,
                        provider_subject=pending.provider_subject,
                        display_name=pending.provider_display_name,
                        avatar_url=pending.provider_avatar_url,
                        refreshed_at=now,
                        created_at=now,
                    )
                )
                pending.consumed_at = now
                refresh_token = self.push_protector.derive_signup_refresh(
                    pending_token,
                    completion_id=completion_id,
                    installation_id=installation_id,
                )
                account_session, refresh_token = self._new_session(
                    transaction,
                    account=account,
                    installation_id=installation_id,
                    now=now,
                    refresh_token=refresh_token,
                )
                transaction.add(
                    SignupCompletion(
                        id=completion_id,
                        pending_signup_id=pending.id,
                        installation_id=installation_id,
                        request_fingerprint=self.push_protector.signup_request_fingerprint(
                            pending_token,
                            completion_id=completion_id,
                            installation_id=installation_id,
                            normalized_nickname=validated.normalized,
                        ),
                        account_id=account.id,
                        session_id=account_session.id,
                        created_at=now,
                        expires_at=now + IDEMPOTENT_SIGNUP_RETRY_GRACE,
                    )
                )
                return self._token_bundle(
                    account=account,
                    account_session=account_session,
                    refresh_token=refresh_token,
                    now=now,
                    status="confirmation_required",
                )
        except IntegrityError as exc:
            constraint = _integrity_constraint(exc)
            if "normalized_nickname" in constraint:
                raise AccountAPIError("nickname_taken", status_code=409) from None
            raise AccountAPIError("signup_completion_conflict", status_code=409) from None

    def confirm_signup(
        self,
        *,
        pending_token: str,
        completion_id: uuid.UUID,
        installation_id: uuid.UUID,
    ) -> None:
        now = self.clock()
        with self.repository.transaction() as transaction:
            pending, completion = self._locked_signup_completion(
                transaction,
                pending_token=pending_token,
                completion_id=completion_id,
                installation_id=installation_id,
            )
            if completion.cancelled_at is not None or pending.cancelled_at is not None:
                raise AccountAPIError("signup_completion_cancelled", status_code=409)
            if now >= _as_utc(completion.expires_at):
                raise AccountAPIError("signup_completion_expired", status_code=409)
            account, account_session, refresh_token = self._completion_resources(
                transaction,
                completion=completion,
                pending_token=pending_token,
                now=now,
            )
            if completion.confirmed_at is not None:
                if account.state != "active":
                    raise AccountAPIError("signup_completion_expired", status_code=409)
                return
            if account.state != "signup_pending":
                raise AccountAPIError("signup_completion_conflict", status_code=409)
            account.state = "active"
            account.updated_at = now
            completion.confirmed_at = now

    def cancel_signup(
        self,
        *,
        pending_token: str,
        completion_id: uuid.UUID,
        installation_id: uuid.UUID,
    ) -> None:
        token_hash = hash_opaque_token(pending_token)
        now = self.clock()
        with self.repository.transaction() as transaction:
            pending = transaction.scalar(
                select(PendingSignup)
                .where(PendingSignup.token_hash == token_hash)
                .with_for_update()
            )
            if pending is None or pending.installation_id != installation_id:
                raise AccountAPIError("pending_signup_expired", status_code=401)
            completion = transaction.scalar(
                select(SignupCompletion)
                .where(SignupCompletion.pending_signup_id == pending.id)
                .with_for_update()
            )
            if completion is None:
                if pending.cancelled_at is not None:
                    return
                self._require_live_pending(pending, now)
                pending.cancelled_at = now
                return
            if completion.id != completion_id or completion.installation_id != installation_id:
                raise AccountAPIError("signup_completion_conflict", status_code=409)
            if completion.confirmed_at is not None:
                raise AccountAPIError("signup_already_confirmed", status_code=409)
            if completion.cancelled_at is not None:
                return
            # Destructive cancellation remains available after the retry
            # window while the exact pending/completion/installation tuple is
            # still present. This closes the race with scheduled pruning and
            # lets a client explicitly roll back the provisional account
            # instead of waiting for maintenance to discover it.
            self.limiter.check(
                "subject:signup-cancel", f"{pending.provider}:{pending.provider_subject}"
            )
            account = (
                transaction.get(Account, completion.account_id)
                if completion.account_id is not None
                else None
            )
            if account is None or account.state != "signup_pending":
                raise AccountAPIError("signup_completion_conflict", status_code=409)
            completion.account_id = None
            completion.session_id = None
            completion.cancelled_at = now
            pending.cancelled_at = now
            transaction.flush()
            transaction.delete(account)

    def refresh(
        self,
        *,
        refresh_token: str,
        rotation_id: uuid.UUID,
    ) -> dict:
        token_hash = hash_opaque_token(refresh_token)
        now = self.clock()
        error: AccountAPIError | None = None
        response: dict | None = None
        with self.repository.transaction() as transaction:
            token_reference = transaction.execute(
                select(RefreshToken.id, RefreshToken.session_id)
                .where(RefreshToken.token_hash == token_hash)
            ).one_or_none()
            if token_reference is None:
                raise AccountAPIError("refresh_expired", status_code=401)
            token_id, session_id = token_reference
            # Serialize the whole family before locking an individual token.
            # A consumed-token retry may inspect its successor; session-first
            # ordering prevents a deadlock with a concurrent successor refresh.
            account_session = transaction.scalar(
                select(AccountSession)
                .where(AccountSession.id == session_id)
                .with_for_update()
            )
            if account_session is None:
                raise AccountAPIError("session_revoked", status_code=401)
            stored_token = transaction.scalar(
                select(RefreshToken)
                .where(
                    RefreshToken.id == token_id,
                    RefreshToken.session_id == account_session.id,
                )
                .with_for_update()
            )
            if stored_token is None:
                raise AccountAPIError("refresh_expired", status_code=401)
            account = transaction.get(Account, account_session.account_id)
            if account is None:
                raise AccountAPIError("session_revoked", status_code=401)
            self.limiter.check("session:refresh", str(account_session.id))

            if account.state == "signup_pending":
                error = AccountAPIError("signup_confirmation_required", status_code=409)
            elif account.state == "deletion_pending":
                error = AccountAPIError("deletion_pending", status_code=409)
            elif account_session.revoked_at is not None:
                error = AccountAPIError("session_revoked", status_code=401)
            elif self._refresh_is_expired(stored_token, account_session, now):
                self._revoke_session(transaction, account_session, now=now)
                error = AccountAPIError("refresh_expired", status_code=401)
            elif stored_token.consumed_at is not None:
                replacement = self._refresh_retry_replacement(
                    transaction,
                    stored_token=stored_token,
                    account_session=account_session,
                    refresh_token=refresh_token,
                    rotation_id=rotation_id,
                    now=now,
                )
                if replacement is not None:
                    # This is the same logical rotation after a lost response:
                    # return the same successor without extending the session.
                    response = self._token_bundle(
                        account=account,
                        account_session=account_session,
                        refresh_token=replacement,
                        now=now,
                        status="authenticated",
                    )
                else:
                    self._revoke_session(transaction, account_session, now=now)
                    self._security_event(
                        transaction,
                        account_id=account.id,
                        session_id=account_session.id,
                        event_type="refresh_reuse",
                        outcome="rejected",
                        now=now,
                    )
                    error = AccountAPIError("refresh_reused", status_code=401)
            else:
                token_count = len(
                    transaction.scalars(
                        select(RefreshToken.id).where(
                            RefreshToken.session_id == account_session.id
                        )
                    ).all()
                )
                if token_count >= MAX_REFRESH_TOKENS_PER_SESSION:
                    # Force a fresh provider exchange before an otherwise
                    # unbounded token family can become a storage attack.
                    self._revoke_session(transaction, account_session, now=now)
                    error = AccountAPIError("refresh_expired", status_code=401)
                else:
                    replacement = self.push_protector.derive_refresh_replacement(
                        refresh_token,
                        rotation_id=rotation_id,
                    )
                    new_idle_expiry = min(
                        now + REFRESH_IDLE_TTL,
                        _as_utc(account_session.absolute_expires_at),
                    )
                    replacement_row = RefreshToken(
                        session_id=account_session.id,
                        token_hash=hash_opaque_token(replacement),
                        issued_at=now,
                        idle_expires_at=new_idle_expiry,
                        absolute_expires_at=account_session.absolute_expires_at,
                    )
                    transaction.add(replacement_row)
                    transaction.flush()
                    stored_token.consumed_at = now
                    stored_token.replacement_token_id = replacement_row.id
                    account_session.last_used_at = now
                    account_session.idle_expires_at = new_idle_expiry
                    response = self._token_bundle(
                        account=account,
                        account_session=account_session,
                        refresh_token=replacement,
                        now=now,
                        status="authenticated",
                    )
        if error is not None:
            raise error
        if response is None:
            raise RuntimeError("Refresh transaction produced no result")
        return response

    def authenticate(self, access_token: str) -> CurrentAccount:
        principal = self.token_service.decode_access(access_token, now=self.clock())
        now = self.clock()
        error: AccountAPIError | None = None
        current: CurrentAccount | None = None
        with self.repository.transaction() as transaction:
            account_session = transaction.get(AccountSession, principal.session_id)
            account = (
                transaction.get(Account, account_session.account_id)
                if account_session is not None
                else None
            )
            if account is None or account_session is None:
                error = _auth_error("session_revoked")
            elif account.state == "signup_pending":
                error = AccountAPIError("signup_confirmation_required", status_code=409)
            elif account.state == "deletion_pending":
                error = AccountAPIError("deletion_pending", status_code=409)
            elif account_session.revoked_at is not None:
                error = _auth_error("session_revoked")
            elif now >= _as_utc(account_session.idle_expires_at) or now >= _as_utc(
                account_session.absolute_expires_at
            ):
                self._revoke_session(transaction, account_session, now=now)
                error = _auth_error("session_revoked")
            elif not self._claims_match(account, account_session, principal):
                error = _auth_error("session_revoked")
            else:
                if now - _as_utc(account_session.last_used_at) >= ACCESS_IDLE_WRITE_INTERVAL:
                    account_session.last_used_at = now
                current = CurrentAccount(
                    account_id=account.id,
                    session_id=account_session.id,
                    community_actor_id=account.community_actor_id,
                    nickname=account.nickname,
                    authorization_version=account.authorization_version,
                )
        if error is not None:
            raise error
        if current is None:
            raise _auth_error()
        return current

    def logout_with_refresh(self, *, refresh_token: str) -> None:
        """Idempotently revoke the session named by any family credential.

        Logout cannot depend on a short-lived access JWT: the common reason a
        client is logging out is that its access credential already expired.
        Unknown credentials deliberately receive the same successful response
        so this endpoint is not a refresh-token oracle.
        """
        token_hash = hash_opaque_token(refresh_token)
        now = self.clock()
        with self.repository.transaction() as transaction:
            token_reference = transaction.execute(
                select(
                    RefreshToken.id,
                    RefreshToken.session_id,
                    AccountSession.account_id,
                )
                .join(AccountSession, AccountSession.id == RefreshToken.session_id)
                .where(RefreshToken.token_hash == token_hash)
            ).one_or_none()
            if token_reference is None:
                return
            token_id, session_id, account_id = token_reference
            # Match the account -> session -> token order used by destructive
            # account operations. Logging out while deletion is starting must
            # not form a session/account lock cycle around the audit-event FK.
            account = transaction.scalar(
                select(Account).where(Account.id == account_id).with_for_update()
            )
            if account is None:
                return
            account_session = transaction.scalar(
                select(AccountSession)
                .where(
                    AccountSession.id == session_id,
                    AccountSession.account_id == account.id,
                )
                .with_for_update()
            )
            if account_session is None:
                return
            # Re-read under the family lock so a stale unlocked lookup cannot
            # authorize revocation after concurrent family cleanup.
            stored_token = transaction.scalar(
                select(RefreshToken)
                .where(
                    RefreshToken.id == token_id,
                    RefreshToken.session_id == account_session.id,
                    RefreshToken.token_hash == token_hash,
                )
                .with_for_update()
            )
            if stored_token is None:
                return
            was_live = account_session.revoked_at is None
            self._revoke_session(transaction, account_session, now=now)
            if was_live:
                self._security_event(
                    transaction,
                    account_id=account.id,
                    session_id=account_session.id,
                    event_type="session_revoked",
                    outcome="accepted",
                    now=now,
                )

    def list_sessions(self, current: CurrentAccount) -> dict[str, list[dict]]:
        now = self.clock()
        with self.repository.transaction() as transaction:
            self._require_current(transaction, current=current, now=now)
            sessions = list(
                transaction.scalars(
                    select(AccountSession)
                    .where(
                        AccountSession.account_id == current.account_id,
                        AccountSession.revoked_at.is_(None),
                        AccountSession.idle_expires_at > now,
                        AccountSession.absolute_expires_at > now,
                    )
                    .order_by(AccountSession.created_at.desc())
                )
            )
            return {
                "sessions": [
                    self._session_payload(item, current_session_id=current.session_id)
                    for item in sessions
                ]
            }

    def revoke_owned_session(
        self, current: CurrentAccount, *, session_id: uuid.UUID
    ) -> None:
        now = self.clock()
        with self.repository.transaction() as transaction:
            self._require_current(
                transaction,
                current=current,
                now=now,
                for_update=True,
            )
            account_session = transaction.scalar(
                select(AccountSession)
                .where(
                    AccountSession.id == session_id,
                    AccountSession.account_id == current.account_id,
                )
                .with_for_update()
            )
            if account_session is None or account_session.revoked_at is not None:
                raise AccountAPIError("session_revoked", status_code=404)
            self._revoke_session(transaction, account_session, now=now)
            self._security_event(
                transaction,
                account_id=current.account_id,
                session_id=session_id,
                event_type="session_revoked",
                outcome="accepted",
                now=now,
            )

    async def create_reauth_challenge(
        self,
        current: CurrentAccount,
        *,
        provider: ProviderName,
        provider_access_token: str,
        scope: str,
    ) -> dict:
        identity = await self._verify_provider(provider, provider_access_token)
        self._limit_subject("reauth", identity)
        now = self.clock()
        with self.repository.transaction() as transaction:
            self._require_current(
                transaction,
                current=current,
                now=now,
                for_update=True,
            )
            linked_identity = transaction.scalar(
                select(AccountIdentity).where(
                    AccountIdentity.account_id == current.account_id,
                    AccountIdentity.provider == provider,
                    AccountIdentity.provider_subject == identity.subject,
                )
            )
            if linked_identity is None:
                raise AccountAPIError("reauth_required", status_code=401)
            target_provider = scope.removeprefix("link_") if scope.startswith("link_") else None
            if target_provider is not None:
                existing_target = transaction.scalar(
                    select(AccountIdentity.id).where(
                        AccountIdentity.account_id == current.account_id,
                        AccountIdentity.provider == target_provider,
                    )
                )
                if existing_target is not None:
                    raise AccountAPIError("identity_already_linked", status_code=409)
            old_challenges = transaction.scalars(
                select(ReauthChallenge).where(
                    ReauthChallenge.account_id == current.account_id,
                    ReauthChallenge.session_id == current.session_id,
                    ReauthChallenge.scope == scope,
                    ReauthChallenge.consumed_at.is_(None),
                )
            )
            for old_challenge in old_challenges:
                old_challenge.consumed_at = now
            challenge_token = new_opaque_token()
            transaction.add(
                ReauthChallenge(
                    account_id=current.account_id,
                    session_id=current.session_id,
                    token_hash=hash_opaque_token(challenge_token),
                    provider=provider,
                    scope=scope,
                    issued_at=now,
                    expires_at=now + REAUTH_TTL,
                )
            )
            return {
                "reauth_token": challenge_token,
                "scope": scope,
                "expires_in": int(REAUTH_TTL.total_seconds()),
            }

    async def link_identity(
        self,
        current: CurrentAccount,
        *,
        target_provider: ProviderName,
        provider_access_token: str,
        reauth_token: str,
    ) -> dict:
        identity = await self._verify_provider(target_provider, provider_access_token)
        self._limit_subject("link", identity)
        now = self.clock()
        try:
            with self.repository.transaction() as transaction:
                self._lock_provider_subject(
                    transaction,
                    provider=target_provider,
                    provider_subject=identity.subject,
                )
                subject_pending = list(
                    transaction.scalars(
                        select(PendingSignup)
                        .where(
                            PendingSignup.provider == target_provider,
                            PendingSignup.provider_subject == identity.subject,
                            PendingSignup.cancelled_at.is_(None),
                        )
                        .order_by(PendingSignup.id)
                        .with_for_update()
                    )
                )
                # Re-read only after the pending lifecycle lock. Completion can
                # create this identity while Link waits on the same pending row.
                existing_owner_id = transaction.scalar(
                    select(AccountIdentity.account_id).where(
                        AccountIdentity.provider == target_provider,
                        AccountIdentity.provider_subject == identity.subject,
                    )
                )
                self._require_current(
                    transaction,
                    current=current,
                    now=now,
                    for_update=True,
                )
                challenge = self._live_challenge(
                    transaction,
                    current=current,
                    token=reauth_token,
                    scope=f"link_{target_provider}",
                    now=now,
                )
                existing_for_account = transaction.scalar(
                    select(AccountIdentity).where(
                        AccountIdentity.account_id == current.account_id,
                        AccountIdentity.provider == target_provider,
                    )
                )
                if existing_for_account is not None:
                    raise AccountAPIError("identity_already_linked", status_code=409)
                if existing_owner_id is not None:
                    raise AccountAPIError(
                        "identity_owned_by_another_account", status_code=409
                    )
                for pending in subject_pending:
                    if pending.consumed_at is None:
                        pending.cancelled_at = now
                transaction.add(
                    AccountIdentity(
                        account_id=current.account_id,
                        provider=target_provider,
                        provider_subject=identity.subject,
                        display_name=identity.display_name,
                        avatar_url=identity.avatar_url,
                        refreshed_at=identity.refreshed_at,
                        created_at=now,
                    )
                )
                challenge.consumed_at = now
                return {"status": "linked", "provider": target_provider}
        except IntegrityError as exc:
            constraint = _integrity_constraint(exc)
            if "account_provider" in constraint:
                raise AccountAPIError("identity_already_linked", status_code=409) from None
            raise AccountAPIError(
                "identity_owned_by_another_account", status_code=409
            ) from None

    def get_me(self, current: CurrentAccount) -> dict:
        with self.repository.transaction() as transaction:
            account, _ = self._require_current(
                transaction,
                current=current,
                now=self.clock(),
            )
            identities = list(
                transaction.scalars(
                    select(AccountIdentity)
                    .where(AccountIdentity.account_id == current.account_id)
                    .order_by(AccountIdentity.provider)
                )
            )
            primary_identity = next(
                (item for item in identities if item.provider == account.primary_provider),
                None,
            )
            return {
                "nickname": account.nickname,
                "community_actor_id": str(account.community_actor_id),
                "primary_provider": {
                    "provider": account.primary_provider,
                    "display_name": account.primary_provider_display_name,
                    "avatar_url": account.primary_provider_avatar_url,
                    "refreshed_at": (
                        _as_utc(primary_identity.refreshed_at)
                        if primary_identity is not None
                        else None
                    ),
                },
                "linked_providers": [
                    {"provider": item.provider, "refreshed_at": _as_utc(item.refreshed_at)}
                    for item in identities
                ],
                "nickname_changed_at": _as_utc_optional(account.nickname_changed_at),
                "created_at": _as_utc(account.created_at),
                "updated_at": _as_utc(account.updated_at),
                "state": account.state,
            }

    def change_nickname(self, current: CurrentAccount, *, nickname: str) -> dict:
        validated = validate_nickname(nickname, blocked=self.blocked_nicknames)
        now = self.clock()
        response: dict | None = None
        actor_id: uuid.UUID | None = None
        try:
            with self.repository.transaction() as transaction:
                account, _ = self._require_current(
                    transaction,
                    current=current,
                    now=now,
                    for_update=True,
                )
                # Match the worker's job -> claims lock order. The account row
                # is already locked, so concurrent changes for this account are
                # serialized without deadlocking a delivery that is releasing
                # old claims after its callback succeeds.
                nickname_job = transaction.scalar(
                    select(AccountNicknameJob)
                    .where(AccountNicknameJob.account_id == account.id)
                    .with_for_update()
                )
                if account.nickname == validated.display:
                    self._queue_nickname_delivery(
                        transaction,
                        account=account,
                        now=now,
                        existing_job=nickname_job,
                    )
                else:
                    if account.nickname_changed_at is not None and now < (
                        _as_utc(account.nickname_changed_at) + NICKNAME_CHANGE_COOLDOWN
                    ):
                        raise AccountAPIError("nickname_cooldown", status_code=409)
                    desired_claim = transaction.scalar(
                        select(AccountNicknameClaim)
                        .where(
                            AccountNicknameClaim.normalized_nickname
                            == validated.normalized
                        )
                        .with_for_update()
                    )
                    if (
                        desired_claim is not None
                        and desired_claim.account_id != account.id
                    ):
                        raise AccountAPIError("nickname_taken", status_code=409)

                    old_normalized = account.normalized_nickname
                    if old_normalized != validated.normalized:
                        current_claim = transaction.scalar(
                            select(AccountNicknameClaim)
                            .where(
                                AccountNicknameClaim.normalized_nickname
                                == old_normalized
                            )
                            .with_for_update()
                        )
                        if current_claim is None:
                            current_claim = AccountNicknameClaim(
                                account_id=account.id,
                                normalized_nickname=old_normalized,
                                state="reserved",
                                created_at=now,
                                updated_at=now,
                            )
                            transaction.add(current_claim)
                        elif current_claim.account_id != account.id:
                            raise RuntimeError("Nickname claim ownership mismatch")
                        else:
                            current_claim.state = "reserved"
                            current_claim.updated_at = now

                        if desired_claim is None:
                            transaction.add(
                                AccountNicknameClaim(
                                    account_id=account.id,
                                    normalized_nickname=validated.normalized,
                                    state="current",
                                    created_at=now,
                                    updated_at=now,
                                )
                            )
                        else:
                            desired_claim.state = "current"
                            desired_claim.updated_at = now
                    elif desired_claim is not None:
                        desired_claim.state = "current"
                        desired_claim.updated_at = now
                    account.nickname = validated.display
                    account.normalized_nickname = validated.normalized
                    account.nickname_changed_at = now
                    account.updated_at = now
                    account.authorization_version += 1
                    # Commit the account mutation and its latest-value outbox row
                    # atomically. Delivery happens after commit and is retried, so
                    # an HTTP response loss after the community update can no
                    # longer roll PostgreSQL back to the old nickname.
                    self._queue_nickname_delivery(
                        transaction,
                        account=account,
                        now=now,
                        existing_job=nickname_job,
                    )
                actor_id = account.community_actor_id
                response = self._nickname_response(account, current.session_id, now)
        except IntegrityError as exc:
            if "normalized_nickname" in _integrity_constraint(exc):
                raise AccountAPIError("nickname_taken", status_code=409) from None
            raise
        if actor_id is None or response is None:
            raise RuntimeError("Nickname transaction produced no result")
        try:
            self.process_nickname_jobs(actor_ids=(actor_id,), limit=1)
        except Exception:  # noqa: BLE001 - mutation is committed; worker will retry
            logger.warning("Nickname delivery maintenance failed")
        return response

    def process_nickname_jobs(
        self,
        *,
        actor_ids: tuple[uuid.UUID, ...] | None = None,
        limit: int = 20,
    ) -> dict[str, int]:
        """Deliver latest nicknames idempotently and retain failures for retry.

        The row lock is deliberately held across the bounded internal request.
        This serializes delivery with a later nickname mutation, preventing a
        slow older callback from overwriting a newer nickname after it lands.
        """
        now = self.clock()
        bounded_limit = max(1, min(int(limit), 100))
        with self.repository.transaction() as transaction:
            statement = (
                select(AccountNicknameJob.id)
                .where(
                    AccountNicknameJob.state.in_(("pending", "retry")),
                    AccountNicknameJob.next_attempt_at <= now,
                )
                .order_by(
                    AccountNicknameJob.next_attempt_at,
                    AccountNicknameJob.created_at,
                )
                .limit(bounded_limit)
            )
            if actor_ids:
                statement = statement.where(
                    AccountNicknameJob.community_actor_id.in_(actor_ids)
                )
            job_ids = list(transaction.scalars(statement))

        completed = 0
        retried = 0
        for job_id in job_ids:
            with self.repository.transaction() as transaction:
                job = transaction.scalar(
                    select(AccountNicknameJob)
                    .where(
                        AccountNicknameJob.id == job_id,
                        AccountNicknameJob.state.in_(("pending", "retry")),
                        AccountNicknameJob.next_attempt_at <= now,
                    )
                    .with_for_update()
                )
                if job is None:
                    continue
                try:
                    delivered = bool(
                        self.nickname_dispatcher(
                            job.community_actor_id,
                            job.nickname,
                        )
                    )
                except Exception:  # noqa: BLE001 - fixed retry code, no payload logging
                    delivered = False
                if delivered:
                    transaction.execute(
                        delete(AccountNicknameClaim).where(
                            AccountNicknameClaim.account_id == job.account_id,
                            AccountNicknameClaim.state == "reserved",
                        )
                    )
                    transaction.delete(job)
                    completed += 1
                    continue
                attempts = job.attempt_count + 1
                delay_seconds = min(86_400, 60 * (2 ** min(attempts - 1, 10)))
                job.state = "retry"
                job.attempt_count = attempts
                job.next_attempt_at = now + timedelta(seconds=delay_seconds)
                job.last_error_code = "community_unavailable"
                job.updated_at = now
                retried += 1
        return {"completed": completed, "retried": retried}

    def request_deletion(self, current: CurrentAccount, *, reauth_token: str) -> dict:
        now = self.clock()
        with self.repository.transaction() as transaction:
            account, _ = self._require_current(
                transaction,
                current=current,
                now=now,
                for_update=True,
            )
            challenge = self._live_challenge(
                transaction,
                current=current,
                token=reauth_token,
                scope="deletion",
                now=now,
            )
            account.state = "deletion_pending"
            account.authorization_version += 1
            account.updated_at = now
            challenge.consumed_at = now
            sessions = transaction.scalars(
                select(AccountSession).where(AccountSession.account_id == current.account_id)
            )
            for account_session in sessions:
                self._revoke_session(transaction, account_session, now=now)
            existing_job = transaction.scalar(
                select(AccountDeletionJob).where(
                    AccountDeletionJob.account_id == current.account_id
                )
            )
            if existing_job is None:
                transaction.add(
                    AccountDeletionJob(
                        account_id=current.account_id,
                        community_actor_id=current.community_actor_id,
                        state="pending",
                        attempt_count=0,
                        next_attempt_at=now,
                        created_at=now,
                        updated_at=now,
                    )
                )
            self._security_event(
                transaction,
                account_id=current.account_id,
                session_id=current.session_id,
                event_type="deletion_requested",
                outcome="accepted",
                now=now,
            )
        # The committed tombstone is delivered synchronously so community
        # content disappears before this request returns when both local
        # services are healthy. A durable job retries safely if delivery fails.
        try:
            self.process_deletion_jobs(actor_ids=(current.community_actor_id,), limit=1)
        except Exception:  # noqa: BLE001 - acceptance already committed durably
            logger.warning("Immediate account-deletion delivery failed")
        return {"status": "deletion_pending"}

    def process_deletion_jobs(
        self,
        *,
        actor_ids: tuple[uuid.UUID, ...] | None = None,
        limit: int = 20,
    ) -> dict[str, int]:
        """Deliver community tombstones, then erase private account rows."""
        now = self.clock()
        bounded_limit = max(1, min(int(limit), 100))
        with self.repository.transaction() as transaction:
            statement = (
                select(AccountDeletionJob)
                .where(
                    AccountDeletionJob.state.in_(("pending", "retry")),
                    AccountDeletionJob.next_attempt_at <= now,
                )
                .order_by(AccountDeletionJob.next_attempt_at, AccountDeletionJob.created_at)
                .limit(bounded_limit)
            )
            if actor_ids:
                statement = statement.where(
                    AccountDeletionJob.community_actor_id.in_(actor_ids)
                )
            due = [
                (job.id, job.community_actor_id, job.attempt_count)
                for job in transaction.scalars(statement).all()
            ]

        completed = 0
        retried = 0
        for job_id, actor_id, previous_attempts in due:
            delivered = False
            if actor_id is not None:
                try:
                    delivered = bool(self.deletion_dispatcher(actor_id))
                except Exception:  # noqa: BLE001 - fixed retry code, no payload logging
                    delivered = False
            with self.repository.transaction() as transaction:
                job = transaction.scalar(
                    select(AccountDeletionJob)
                    .where(AccountDeletionJob.id == job_id)
                    .with_for_update()
                )
                if job is None or job.state == "completed":
                    continue
                attempts = max(int(previous_attempts), job.attempt_count) + 1
                job.attempt_count = attempts
                job.updated_at = now
                if not delivered:
                    delay_seconds = min(86_400, 60 * (2 ** min(attempts - 1, 10)))
                    job.state = "retry"
                    job.next_attempt_at = now + timedelta(seconds=delay_seconds)
                    job.last_error_code = "community_unavailable"
                    retried += 1
                    continue

                if job.account_id is not None:
                    completion_pending_ids = select(
                        SignupCompletion.pending_signup_id
                    ).where(SignupCompletion.account_id == job.account_id)
                    transaction.execute(
                        delete(PendingSignup).where(
                            PendingSignup.id.in_(completion_pending_ids)
                        )
                    )
                    transaction.execute(
                        delete(Account).where(Account.id == job.account_id)
                    )
                    job.account_id = None
                job.state = "completed"
                job.community_actor_id = None
                job.next_attempt_at = now
                job.last_error_code = None
                job.completed_at = now
                completed += 1
        return {"completed": completed, "retried": retried}

    def register_push(
        self, current: CurrentAccount, *, fcm_token: str, platform: str
    ) -> dict:
        if self.notification_sink is None:
            raise AccountAPIError("push_unavailable", status_code=503)
        now = self.clock()
        fingerprint = self.push_protector.fingerprint(fcm_token)
        ciphertext = self.push_protector.encrypt(fcm_token)
        try:
            with self.repository.transaction() as transaction:
                self._require_current(
                    transaction,
                    current=current,
                    now=now,
                    for_update=True,
                )
                self.limiter.check("account:push", str(current.account_id))
                session_device = transaction.scalar(
                    select(PushDevice)
                    .where(PushDevice.session_id == current.session_id)
                    .with_for_update()
                )
                token_device = transaction.scalar(
                    select(PushDevice)
                    .where(PushDevice.token_fingerprint == fingerprint)
                    .with_for_update()
                )
                if token_device is not None:
                    if session_device is not None and session_device.id != token_device.id:
                        transaction.delete(session_device)
                        # Free the per-session unique key before moving an existing
                        # token registration onto this session. SQLAlchemy otherwise
                        # may issue the UPDATE before the DELETE during autoflush.
                        transaction.flush()
                    device = token_device
                    device.account_id = current.account_id
                    device.session_id = current.session_id
                    device.token_ciphertext = ciphertext
                    device.platform = platform
                    device.updated_at = now
                    device.disabled_at = None
                elif session_device is not None:
                    session_device.token_ciphertext = ciphertext
                    session_device.token_fingerprint = fingerprint
                    session_device.platform = platform
                    session_device.updated_at = now
                    session_device.disabled_at = None
                else:
                    transaction.add(
                        PushDevice(
                            account_id=current.account_id,
                            session_id=current.session_id,
                            token_ciphertext=ciphertext,
                            token_fingerprint=fingerprint,
                            platform=platform,
                            created_at=now,
                            updated_at=now,
                        )
                    )
        except IntegrityError:
            # A concurrent registration won either the token-fingerprint or
            # one-device-per-session race. Resolve both without ever storing or
            # returning plaintext.
            with self.repository.transaction() as transaction:
                self._require_current(
                    transaction,
                    current=current,
                    now=now,
                    for_update=True,
                )
                session_device = transaction.scalar(
                    select(PushDevice)
                    .where(PushDevice.session_id == current.session_id)
                    .with_for_update()
                )
                token_device = transaction.scalar(
                    select(PushDevice)
                    .where(PushDevice.token_fingerprint == fingerprint)
                    .with_for_update()
                )
                device = token_device or session_device
                if device is None:
                    raise RuntimeError(
                        "Push registration race could not be resolved"
                    ) from None
                if session_device is not None and session_device.id != device.id:
                    transaction.delete(session_device)
                    transaction.flush()
                device.account_id = current.account_id
                device.session_id = current.session_id
                device.token_ciphertext = ciphertext
                device.token_fingerprint = fingerprint
                device.platform = platform
                device.updated_at = now
                device.disabled_at = None
        return {"status": "registered", "platform": platform}

    def disable_current_push(self, current: CurrentAccount) -> None:
        now = self.clock()
        with self.repository.transaction() as transaction:
            transaction.execute(
                update(PushDevice)
                .where(
                    PushDevice.account_id == current.account_id,
                    PushDevice.session_id == current.session_id,
                    PushDevice.disabled_at.is_(None),
                )
                .values(disabled_at=now, updated_at=now)
            )

    def dispatch_opinion_notification(
        self,
        *,
        community_actor_id: uuid.UUID,
        scan_id: str,
        disease: str,
        note: str,
        nickname: str,
    ) -> dict[str, int | str]:
        """Resolve an actor to live encrypted registrations and deliver privately."""
        if self.notification_sink is None:
            raise AccountAPIError("push_unavailable", status_code=503)
        now = self.clock()
        with self.repository.transaction() as transaction:
            account = transaction.scalar(
                select(Account).where(
                    Account.community_actor_id == community_actor_id,
                    Account.state == "active",
                )
            )
            if account is None:
                return {"status": "accepted", "target_count": 0}
            devices = transaction.scalars(
                select(PushDevice)
                .join(AccountSession, AccountSession.id == PushDevice.session_id)
                .where(
                    PushDevice.account_id == account.id,
                    PushDevice.disabled_at.is_(None),
                    AccountSession.revoked_at.is_(None),
                    AccountSession.idle_expires_at > now,
                    AccountSession.absolute_expires_at > now,
                )
            ).all()
            tokens: list[str] = []
            for device in devices:
                try:
                    tokens.append(self.push_protector.decrypt(device.token_ciphertext))
                except (InvalidTag, UnicodeDecodeError, ValueError):
                    device.disabled_at = now
                    device.updated_at = now
        payload = {
            "type": "opinion",
            "community_actor_id": str(community_actor_id),
            "scan_id": scan_id[:128],
            "disease": disease[:64],
            "note": note[:240],
            "nickname": nickname[:64],
        }
        if tokens:
            try:
                self.notification_sink(tokens, payload)
            except Exception:  # noqa: BLE001 - best effort; never log plaintext tokens
                pass
        return {"status": "accepted", "target_count": len(tokens)}

    def prune_expired_private_state(self) -> dict[str, int]:
        """Apply expiry policy without deleting a still-valid token family."""
        now = self.clock()
        with self.repository.transaction() as transaction:
            expired_completion_references = list(
                transaction.execute(
                    select(
                        SignupCompletion.id,
                        SignupCompletion.pending_signup_id,
                    )
                    .where(SignupCompletion.expires_at <= now)
                )
            )
            expired_completion_ids = [row[0] for row in expired_completion_references]
            expired_completion_pending_ids = [row[1] for row in expired_completion_references]
            if expired_completion_pending_ids:
                list(
                    transaction.scalars(
                        select(PendingSignup)
                        .where(PendingSignup.id.in_(expired_completion_pending_ids))
                        .order_by(PendingSignup.id)
                        .with_for_update()
                    )
                )
            expired_completions = (
                list(
                    transaction.scalars(
                        select(SignupCompletion)
                        .where(SignupCompletion.id.in_(expired_completion_ids))
                        .order_by(SignupCompletion.pending_signup_id)
                        .with_for_update()
                    )
                )
                if expired_completion_ids
                else []
            )
            provisional_account_ids = [
                completion.account_id
                for completion in expired_completions
                if completion.account_id is not None
                and completion.confirmed_at is None
                and completion.cancelled_at is None
            ]
            completed_pending = 0
            if expired_completion_pending_ids:
                # Deleting the pending bearer marker cascades only the bounded
                # completion metadata. Confirmed accounts remain; stale
                # unconfirmed accounts were rolled back immediately above.
                completed_pending = transaction.execute(
                    delete(PendingSignup).where(
                        PendingSignup.id.in_(expired_completion_pending_ids)
                    )
                ).rowcount
                transaction.flush()
            for account_id in provisional_account_ids:
                provisional = transaction.get(Account, account_id)
                if provisional is not None and provisional.state == "signup_pending":
                    transaction.delete(provisional)
            # Lock and re-evaluate expired, never-consumed rows before delete.
            # A completion that held this pending row may commit while prune
            # waits; SELECT FOR UPDATE then rechecks consumed_at on the new row
            # version, so it cannot cascade-delete the just-created marker.
            ordinary_expired_pending = list(
                transaction.scalars(
                    select(PendingSignup)
                    .where(
                        PendingSignup.expires_at <= now,
                        PendingSignup.consumed_at.is_(None),
                    )
                    .order_by(PendingSignup.id)
                    .with_for_update()
                )
            )
            ordinary_pending = len(ordinary_expired_pending)
            for expired_pending in ordinary_expired_pending:
                transaction.delete(expired_pending)
            pending = completed_pending + ordinary_pending
            reauth = transaction.execute(
                delete(ReauthChallenge).where(ReauthChallenge.expires_at <= now)
            ).rowcount
            security_events = transaction.execute(
                delete(AccountSecurityEvent).where(AccountSecurityEvent.expires_at <= now)
            ).rowcount
            completed_deletion_jobs = transaction.execute(
                delete(AccountDeletionJob).where(
                    AccountDeletionJob.state == "completed",
                    AccountDeletionJob.completed_at.is_not(None),
                    AccountDeletionJob.completed_at <= now - COMPLETED_DELETION_JOB_TTL,
                )
            ).rowcount
            expiring_completion_references = list(
                transaction.execute(
                    select(
                        SignupCompletion.id,
                        SignupCompletion.pending_signup_id,
                    )
                    .join(
                        AccountSession,
                        AccountSession.id == SignupCompletion.session_id,
                    )
                    .where(AccountSession.absolute_expires_at <= now)
                )
            )
            expiring_completion_ids = [row[0] for row in expiring_completion_references]
            expiring_completion_pending_ids = [
                row[1] for row in expiring_completion_references
            ]
            if expiring_completion_pending_ids:
                list(
                    transaction.scalars(
                        select(PendingSignup)
                        .where(PendingSignup.id.in_(expiring_completion_pending_ids))
                        .order_by(PendingSignup.id)
                        .with_for_update()
                    )
                )
                list(
                    transaction.scalars(
                        select(SignupCompletion)
                        .where(SignupCompletion.id.in_(expiring_completion_ids))
                        .order_by(SignupCompletion.pending_signup_id)
                        .with_for_update()
                    )
                )
                pending += transaction.execute(
                    delete(PendingSignup).where(
                        PendingSignup.id.in_(expiring_completion_pending_ids)
                    )
                ).rowcount
            # Session cascade removes its refresh family, reauth rows, and push
            # rows only after the 180-day absolute validity window ends.
            sessions = transaction.execute(
                delete(AccountSession).where(AccountSession.absolute_expires_at <= now)
            ).rowcount
        return {
            "pending_signups": pending,
            "reauth_challenges": reauth,
            "security_events": security_events,
            "completed_deletion_jobs": completed_deletion_jobs,
            "sessions": sessions,
        }

    async def _verify_provider(
        self, provider: ProviderName, access_token: str
    ) -> VerifiedProviderIdentity:
        adapter = self.providers.get(provider)
        if adapter is None:
            raise AccountAPIError("provider_unavailable", status_code=503)
        try:
            identity = await adapter.verify(access_token)
        except ProviderVerificationError as exc:
            status_code = 503 if exc.code.value == "provider_unavailable" else 401
            raise AccountAPIError(exc.code.value, status_code=status_code) from None
        if identity.provider != provider:
            raise AccountAPIError("invalid_provider_token", status_code=401)
        return identity

    def _limit_subject(self, operation: str, identity: VerifiedProviderIdentity) -> None:
        self.limiter.check(
            f"subject:{operation}", f"{identity.provider}:{identity.subject}"
        )

    @staticmethod
    def _lock_provider_subject(
        transaction,
        *,
        provider: ProviderName,
        provider_subject: str,
    ) -> None:
        """Serialize creation of the first pending row for a provider subject.

        Existing pending rows provide the row lock shared with completion, but
        two first-time exchanges have no row to lock. PostgreSQL's transaction
        advisory lock closes that empty-set race without persisting or exposing
        the provider subject. SQLite tests already serialize writers and do not
        implement PostgreSQL advisory locks.
        """
        bind = transaction.get_bind()
        if bind.dialect.name != "postgresql":
            return
        digest = hashlib.sha256(
            f"signup-provider-subject\0{provider}\0{provider_subject}".encode()
        ).digest()
        advisory_key = int.from_bytes(digest[:8], byteorder="big", signed=True)
        transaction.execute(select(func.pg_advisory_xact_lock(advisory_key)))

    @staticmethod
    def _refresh_provider_profile(
        account: Account,
        stored_identity: AccountIdentity,
        verified_identity: VerifiedProviderIdentity,
    ) -> None:
        stored_identity.display_name = verified_identity.display_name
        stored_identity.avatar_url = verified_identity.avatar_url
        stored_identity.refreshed_at = verified_identity.refreshed_at
        if account.primary_provider == verified_identity.provider:
            account.primary_provider_display_name = verified_identity.display_name
            account.primary_provider_avatar_url = verified_identity.avatar_url
            account.updated_at = verified_identity.refreshed_at

    @staticmethod
    def _require_live_pending(pending: PendingSignup | None, now: datetime) -> None:
        if pending is None or now >= _as_utc(pending.expires_at):
            raise AccountAPIError("pending_signup_expired", status_code=401)
        if pending.consumed_at is not None or pending.cancelled_at is not None:
            raise AccountAPIError("pending_signup_replayed", status_code=409)

    def _locked_signup_completion(
        self,
        transaction,
        *,
        pending_token: str,
        completion_id: uuid.UUID,
        installation_id: uuid.UUID,
    ) -> tuple[PendingSignup, SignupCompletion]:
        pending = transaction.scalar(
            select(PendingSignup)
            .where(PendingSignup.token_hash == hash_opaque_token(pending_token))
            .with_for_update()
        )
        if pending is None:
            raise AccountAPIError("pending_signup_expired", status_code=401)
        completion = transaction.scalar(
            select(SignupCompletion)
            .where(SignupCompletion.pending_signup_id == pending.id)
            .with_for_update()
        )
        if (
            completion is None
            or completion.id != completion_id
            or completion.installation_id != installation_id
            or pending.installation_id != installation_id
        ):
            raise AccountAPIError("signup_completion_conflict", status_code=409)
        return pending, completion

    def _completion_resources(
        self,
        transaction,
        *,
        completion: SignupCompletion,
        pending_token: str,
        now: datetime,
    ) -> tuple[Account, AccountSession, str]:
        if completion.account_id is None or completion.session_id is None:
            raise AccountAPIError("signup_completion_expired", status_code=409)
        account = transaction.scalar(
            select(Account)
            .where(Account.id == completion.account_id)
            .with_for_update()
        )
        account_session = transaction.scalar(
            select(AccountSession)
            .where(
                AccountSession.id == completion.session_id,
                AccountSession.account_id == completion.account_id,
            )
            .with_for_update()
        )
        if account is None or account_session is None or account_session.revoked_at is not None:
            raise AccountAPIError("signup_completion_expired", status_code=409)
        if now >= _as_utc(account_session.idle_expires_at) or now >= _as_utc(
            account_session.absolute_expires_at
        ):
            raise AccountAPIError("signup_completion_expired", status_code=409)
        refresh_token = self.push_protector.derive_signup_refresh(
            pending_token,
            completion_id=completion.id,
            installation_id=completion.installation_id,
        )
        stored_refresh = transaction.scalar(
            select(RefreshToken).where(
                RefreshToken.session_id == account_session.id,
                RefreshToken.token_hash == hash_opaque_token(refresh_token),
                RefreshToken.consumed_at.is_(None),
            )
        )
        if stored_refresh is None or self._refresh_is_expired(
            stored_refresh, account_session, now
        ):
            raise AccountAPIError("signup_completion_expired", status_code=409)
        return account, account_session, refresh_token

    def _retry_signup_completion(
        self,
        transaction,
        *,
        pending: PendingSignup,
        completion: SignupCompletion,
        pending_token: str,
        completion_id: uuid.UUID,
        installation_id: uuid.UUID,
        normalized_nickname: str,
        now: datetime,
    ) -> dict:
        expected_fingerprint = self.push_protector.signup_request_fingerprint(
            pending_token,
            completion_id=completion_id,
            installation_id=installation_id,
            normalized_nickname=normalized_nickname,
        )
        if (
            completion.id != completion_id
            or completion.installation_id != installation_id
            or pending.installation_id != installation_id
            or not hmac.compare_digest(
                completion.request_fingerprint,
                expected_fingerprint,
            )
        ):
            raise AccountAPIError("signup_completion_conflict", status_code=409)
        if completion.cancelled_at is not None or pending.cancelled_at is not None:
            raise AccountAPIError("signup_completion_cancelled", status_code=409)
        if now >= _as_utc(completion.expires_at):
            raise AccountAPIError("signup_completion_expired", status_code=409)
        account, account_session, refresh_token = self._completion_resources(
            transaction,
            completion=completion,
            pending_token=pending_token,
            now=now,
        )
        expected_state = "active" if completion.confirmed_at is not None else "signup_pending"
        if account.state != expected_state:
            raise AccountAPIError("signup_completion_conflict", status_code=409)
        return self._token_bundle(
            account=account,
            account_session=account_session,
            refresh_token=refresh_token,
            now=now,
            status="confirmation_required",
        )

    @staticmethod
    def _queue_nickname_delivery(
        transaction,
        *,
        account: Account,
        now: datetime,
        existing_job: AccountNicknameJob | None,
    ) -> None:
        job = existing_job
        if job is None:
            transaction.add(
                AccountNicknameJob(
                    account_id=account.id,
                    community_actor_id=account.community_actor_id,
                    nickname=account.nickname,
                    state="pending",
                    attempt_count=0,
                    next_attempt_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            return
        # Only the latest value matters. Reset backoff so a user-driven update
        # gets one immediate attempt even when an older delivery was retrying.
        job.community_actor_id = account.community_actor_id
        job.nickname = account.nickname
        job.state = "pending"
        job.attempt_count = 0
        job.next_attempt_at = now
        job.last_error_code = None
        job.updated_at = now

    def _new_session(
        self,
        transaction,
        *,
        account: Account,
        installation_id: uuid.UUID,
        now: datetime,
        refresh_token: str | None = None,
    ):
        retained = list(
            transaction.scalars(
                select(AccountSession)
                .where(AccountSession.account_id == account.id)
                .order_by(AccountSession.created_at.desc())
                .with_for_update()
            )
        )
        protected_session_ids = set(
            transaction.scalars(
                select(SignupCompletion.session_id).where(
                    SignupCompletion.account_id == account.id,
                    SignupCompletion.session_id.is_not(None),
                    SignupCompletion.expires_at > now,
                )
            )
        )
        active: list[AccountSession] = []
        for item in retained:
            expired = now >= _as_utc(item.idle_expires_at) or now >= _as_utc(
                item.absolute_expires_at
            )
            if item.revoked_at is None and (
                expired or item.installation_id == installation_id
            ) and item.id not in protected_session_ids:
                self._revoke_session(transaction, item, now=now)
            if item.revoked_at is None:
                active.append(item)

        # Keep the newest sessions and revoke older ones before admitting the
        # replacement. This makes the active-session bound deterministic even
        # when callers invent installation identifiers.
        active_excess = max(0, len(active) - MAX_ACTIVE_SESSIONS_PER_ACCOUNT + 1)
        active_victims = [
            item for item in reversed(active) if item.id not in protected_session_ids
        ][:active_excess]
        if len(active_victims) < active_excess:
            raise AccountAPIError("rate_limited", status_code=429)
        for item in active_victims:
            self._revoke_session(transaction, item, now=now)

        overflow = len(retained) - MAX_RETAINED_SESSIONS_PER_ACCOUNT + 1
        if overflow > 0:
            removable = [
                item
                for item in reversed(retained)
                if item.revoked_at is not None and item.id not in protected_session_ids
            ]
            victims = removable[:overflow]
            if len(victims) < overflow:
                raise AccountAPIError("rate_limited", status_code=429)
            victim_ids = [item.id for item in victims]
            completion_pending_ids = select(
                SignupCompletion.pending_signup_id
            ).where(SignupCompletion.session_id.in_(victim_ids))
            transaction.execute(
                delete(PendingSignup).where(
                    PendingSignup.id.in_(completion_pending_ids)
                )
            )
            # Preserve account-level security events while allowing their old
            # session families to be compacted.
            transaction.execute(
                update(AccountSecurityEvent)
                .where(AccountSecurityEvent.session_id.in_(victim_ids))
                .values(session_id=None)
            )
            transaction.execute(
                delete(AccountSession).where(AccountSession.id.in_(victim_ids))
            )

        absolute_expiry = now + SESSION_ABSOLUTE_TTL
        idle_expiry = now + REFRESH_IDLE_TTL
        account_session = AccountSession(
            account_id=account.id,
            installation_id=installation_id,
            device_label="Android device",
            token_family_id=uuid.uuid4(),
            created_at=now,
            last_used_at=now,
            idle_expires_at=idle_expiry,
            absolute_expires_at=absolute_expiry,
        )
        transaction.add(account_session)
        transaction.flush()
        refresh_token = refresh_token or new_opaque_token()
        transaction.add(
            RefreshToken(
                session_id=account_session.id,
                token_hash=hash_opaque_token(refresh_token),
                issued_at=now,
                idle_expires_at=idle_expiry,
                absolute_expires_at=absolute_expiry,
            )
        )
        return account_session, refresh_token

    def _token_bundle(
        self,
        *,
        account: Account,
        account_session: AccountSession,
        refresh_token: str,
        now: datetime,
        status: str,
    ) -> dict:
        access_token = self.token_service.issue_access(
            session_id=account_session.id,
            community_actor_id=account.community_actor_id,
            nickname=account.nickname,
            authorization_version=account.authorization_version,
            now=now,
        )
        return {
            "status": status,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "Bearer",
            "expires_in": self.token_service.access_ttl_seconds,
            "profile": {
                "nickname": account.nickname,
                "community_actor_id": str(account.community_actor_id),
            },
            "session": self._session_payload(
                account_session, current_session_id=account_session.id
            ),
        }

    @staticmethod
    def _session_payload(
        account_session: AccountSession, *, current_session_id: uuid.UUID
    ) -> dict:
        return {
            "session_id": str(account_session.id),
            "installation_id": str(account_session.installation_id),
            "device_label": account_session.device_label,
            "created_at": _as_utc(account_session.created_at),
            "last_used_at": _as_utc(account_session.last_used_at),
            "idle_expires_at": _as_utc(account_session.idle_expires_at),
            "absolute_expires_at": _as_utc(account_session.absolute_expires_at),
            "current": account_session.id == current_session_id,
        }

    @staticmethod
    def _refresh_is_expired(
        token: RefreshToken, account_session: AccountSession, now: datetime
    ) -> bool:
        return any(
            now >= _as_utc(expiry)
            for expiry in (
                token.idle_expires_at,
                token.absolute_expires_at,
                account_session.idle_expires_at,
                account_session.absolute_expires_at,
            )
        )

    def _refresh_retry_replacement(
        self,
        transaction,
        *,
        stored_token: RefreshToken,
        account_session: AccountSession,
        refresh_token: str,
        rotation_id: uuid.UUID,
        now: datetime,
    ) -> str | None:
        consumed_at = _as_utc_optional(stored_token.consumed_at)
        if (
            consumed_at is None
            or stored_token.replacement_token_id is None
        ):
            return None
        replacement_row = transaction.scalar(
            select(RefreshToken)
            .where(RefreshToken.id == stored_token.replacement_token_id)
            .with_for_update()
        )
        if (
            replacement_row is None
            or replacement_row.session_id != account_session.id
            or replacement_row.consumed_at is not None
            or self._refresh_is_expired(replacement_row, account_session, now)
        ):
            return None
        replacement = self.push_protector.derive_refresh_replacement(
            refresh_token,
            rotation_id=rotation_id,
        )
        if hash_opaque_token(replacement) != replacement_row.token_hash:
            return None
        if now > consumed_at + IDEMPOTENT_REFRESH_RETRY_GRACE:
            return None
        return replacement

    @staticmethod
    def _claims_match(
        account: Account, account_session: AccountSession, principal: AccessPrincipal
    ) -> bool:
        return (
            account_session.id == principal.session_id
            and account.community_actor_id == principal.community_actor_id
            and account.nickname == principal.nickname
            and account.authorization_version == principal.authorization_version
        )

    def _require_current(
        self,
        transaction,
        *,
        current: CurrentAccount,
        now: datetime,
        for_update: bool = False,
    ) -> tuple[Account, AccountSession]:
        statement = (
            select(Account, AccountSession)
            .join(AccountSession, AccountSession.account_id == Account.id)
            .where(
                Account.id == current.account_id,
                AccountSession.id == current.session_id,
            )
        )
        if for_update:
            statement = statement.with_for_update()
        row = transaction.execute(statement).one_or_none()
        if row is None:
            raise _auth_error("session_revoked")
        account, account_session = row
        if account.state == "signup_pending":
            raise AccountAPIError("signup_confirmation_required", status_code=409)
        if account.state == "deletion_pending":
            raise AccountAPIError("deletion_pending", status_code=409)
        if account_session.revoked_at is not None:
            raise _auth_error("session_revoked")
        if now >= _as_utc(account_session.idle_expires_at) or now >= _as_utc(
            account_session.absolute_expires_at
        ):
            raise _auth_error("session_revoked")
        if (
            account.community_actor_id != current.community_actor_id
            or account.nickname != current.nickname
            or account.authorization_version != current.authorization_version
        ):
            raise _auth_error("session_revoked")
        return account, account_session

    @staticmethod
    def _revoke_session(transaction, account_session: AccountSession, *, now: datetime) -> None:
        if account_session.revoked_at is None:
            account_session.revoked_at = now
        transaction.execute(
            update(PushDevice)
            .where(
                PushDevice.session_id == account_session.id,
                PushDevice.disabled_at.is_(None),
            )
            .values(disabled_at=now, updated_at=now)
        )

    @staticmethod
    def _security_event(
        transaction,
        *,
        account_id: uuid.UUID,
        session_id: uuid.UUID,
        event_type: str,
        outcome: str,
        now: datetime,
    ) -> None:
        transaction.add(
            AccountSecurityEvent(
                account_id=account_id,
                session_id=session_id,
                event_type=event_type,
                outcome=outcome,
                created_at=now,
                expires_at=now + SECURITY_EVENT_TTL,
            )
        )

    @staticmethod
    def _live_challenge(
        transaction,
        *,
        current: CurrentAccount,
        token: str,
        scope: str,
        now: datetime,
    ) -> ReauthChallenge:
        challenge = transaction.scalar(
            select(ReauthChallenge)
            .where(ReauthChallenge.token_hash == hash_opaque_token(token))
            .with_for_update()
        )
        if challenge is None:
            raise AccountAPIError("reauth_required", status_code=401)
        if (
            challenge.account_id != current.account_id
            or challenge.session_id != current.session_id
            or challenge.scope != scope
        ):
            raise AccountAPIError("reauth_required", status_code=401)
        if challenge.consumed_at is not None or now >= _as_utc(challenge.expires_at):
            raise AccountAPIError("reauth_expired", status_code=401)
        return challenge

    def _nickname_response(
        self, account: Account, session_id: uuid.UUID, now: datetime
    ) -> dict:
        return {
            "nickname": account.nickname,
            "access_token": self.token_service.issue_access(
                session_id=session_id,
                community_actor_id=account.community_actor_id,
                nickname=account.nickname,
                authorization_version=account.authorization_version,
                now=now,
            ),
            "token_type": "Bearer",
            "expires_in": self.token_service.access_ttl_seconds,
        }
