"""Transactional account, session, linking, deletion, and push workflows."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cryptography.exceptions import InvalidTag
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app.api.errors import AccountAPIError
from app.db.models import (
    Account,
    AccountDeletionJob,
    AccountIdentity,
    AccountSecurityEvent,
    PendingSignup,
    PushDevice,
    ReauthChallenge,
    RefreshToken,
)
from app.db.models import (
    Session as AccountSession,
)
from app.db.repository import AccountRepository
from app.nicknames import validate_nickname
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
    ):
        self.repository = repository
        self.providers = dict(providers)
        self.token_service = token_service
        self.push_protector = push_protector
        self.limiter = limiter
        self.blocked_nicknames = blocked_nicknames
        self.clock = clock
        self.notification_sink = notification_sink or (lambda _tokens, _payload: 0)
        self.deletion_dispatcher = deletion_dispatcher or (lambda _actor_id: False)

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
            stored_identity = transaction.scalar(
                select(AccountIdentity)
                .where(
                    AccountIdentity.provider == provider,
                    AccountIdentity.provider_subject == identity.subject,
                )
                .with_for_update()
            )
            if stored_identity is None:
                active_pending = transaction.scalars(
                    select(PendingSignup).where(
                        PendingSignup.provider == provider,
                        PendingSignup.provider_subject == identity.subject,
                        PendingSignup.consumed_at.is_(None),
                        PendingSignup.cancelled_at.is_(None),
                    )
                )
                for pending in active_pending:
                    pending.cancelled_at = now
                pending_token = new_opaque_token()
                transaction.add(
                    PendingSignup(
                        token_hash=hash_opaque_token(pending_token),
                        provider=provider,
                        provider_subject=identity.subject,
                        provider_display_name=identity.display_name,
                        provider_avatar_url=identity.avatar_url,
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

            account = transaction.get(Account, stored_identity.account_id)
            if account is None:
                raise _auth_error()
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
        self, *, pending_token: str, nickname: str, installation_id: uuid.UUID
    ) -> dict:
        validated = validate_nickname(nickname, blocked=self.blocked_nicknames)
        token_hash = hash_opaque_token(pending_token)
        now = self.clock()
        try:
            with self.repository.transaction() as transaction:
                pending = transaction.scalar(
                    select(PendingSignup)
                    .where(PendingSignup.token_hash == token_hash)
                    .with_for_update()
                )
                self._require_live_pending(pending, now)
                self.limiter.check(
                    "subject:signup", f"{pending.provider}:{pending.provider_subject}"
                )
                nickname_owner = transaction.scalar(
                    select(Account.id).where(
                        Account.normalized_nickname == validated.normalized
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
                    state="active",
                    created_at=now,
                    updated_at=now,
                )
                transaction.add(account)
                transaction.flush()
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
        except IntegrityError as exc:
            constraint = _integrity_constraint(exc)
            if "normalized_nickname" in constraint:
                raise AccountAPIError("nickname_taken", status_code=409) from None
            raise AccountAPIError("pending_signup_replayed", status_code=409) from None

    def cancel_signup(self, *, pending_token: str) -> None:
        token_hash = hash_opaque_token(pending_token)
        now = self.clock()
        with self.repository.transaction() as transaction:
            pending = transaction.scalar(
                select(PendingSignup)
                .where(PendingSignup.token_hash == token_hash)
                .with_for_update()
            )
            self._require_live_pending(pending, now)
            self.limiter.check(
                "subject:signup-cancel", f"{pending.provider}:{pending.provider_subject}"
            )
            pending.cancelled_at = now

    def refresh(self, *, refresh_token: str) -> dict:
        token_hash = hash_opaque_token(refresh_token)
        now = self.clock()
        error: AccountAPIError | None = None
        response: dict | None = None
        with self.repository.transaction() as transaction:
            stored_token = transaction.scalar(
                select(RefreshToken)
                .where(RefreshToken.token_hash == token_hash)
                .with_for_update()
            )
            if stored_token is None:
                raise AccountAPIError("refresh_expired", status_code=401)
            account_session = transaction.get(AccountSession, stored_token.session_id)
            if account_session is None:
                raise AccountAPIError("session_revoked", status_code=401)
            account = transaction.get(Account, account_session.account_id)
            if account is None:
                raise AccountAPIError("session_revoked", status_code=401)
            self.limiter.check("session:refresh", str(account_session.id))

            if stored_token.consumed_at is not None:
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
            elif account.state == "deletion_pending":
                error = AccountAPIError("deletion_pending", status_code=409)
            elif account_session.revoked_at is not None:
                error = AccountAPIError("session_revoked", status_code=401)
            elif self._refresh_is_expired(stored_token, account_session, now):
                self._revoke_session(transaction, account_session, now=now)
                error = AccountAPIError("refresh_expired", status_code=401)
            else:
                replacement = new_opaque_token()
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

    def logout(self, current: CurrentAccount) -> None:
        now = self.clock()
        with self.repository.transaction() as transaction:
            account_session = transaction.get(AccountSession, current.session_id)
            if account_session is not None and account_session.account_id == current.account_id:
                self._revoke_session(transaction, account_session, now=now)
                self._security_event(
                    transaction,
                    account_id=current.account_id,
                    session_id=current.session_id,
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
                existing_owner = transaction.scalar(
                    select(AccountIdentity).where(
                        AccountIdentity.provider == target_provider,
                        AccountIdentity.provider_subject == identity.subject,
                    )
                )
                if existing_owner is not None:
                    raise AccountAPIError(
                        "identity_owned_by_another_account", status_code=409
                    )
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
        try:
            with self.repository.transaction() as transaction:
                account, _ = self._require_current(
                    transaction,
                    current=current,
                    now=now,
                    for_update=True,
                )
                if account.nickname == validated.display:
                    return self._nickname_response(account, current.session_id, now)
                if account.nickname_changed_at is not None and now < (
                    _as_utc(account.nickname_changed_at) + NICKNAME_CHANGE_COOLDOWN
                ):
                    raise AccountAPIError("nickname_cooldown", status_code=409)
                owner = transaction.scalar(
                    select(Account.id).where(
                        Account.normalized_nickname == validated.normalized,
                        Account.id != current.account_id,
                    )
                )
                if owner is not None:
                    raise AccountAPIError("nickname_taken", status_code=409)
                account.nickname = validated.display
                account.normalized_nickname = validated.normalized
                account.nickname_changed_at = now
                account.updated_at = now
                account.authorization_version += 1
                return self._nickname_response(account, current.session_id, now)
        except IntegrityError as exc:
            if "normalized_nickname" in _integrity_constraint(exc):
                raise AccountAPIError("nickname_taken", status_code=409) from None
            raise

    def request_deletion(self, current: CurrentAccount, *, reauth_token: str) -> dict:
        now = self.clock()
        with self.repository.transaction() as transaction:
            challenge = self._live_challenge(
                transaction,
                current=current,
                token=reauth_token,
                scope="deletion",
                now=now,
            )
            account = transaction.scalar(
                select(Account).where(Account.id == current.account_id).with_for_update()
            )
            if account is None:
                raise _auth_error()
            if account.state == "deletion_pending":
                raise AccountAPIError("deletion_pending", status_code=409)
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
        self.process_deletion_jobs(actor_ids=(current.community_actor_id,), limit=1)
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
                device = transaction.scalar(
                    select(PushDevice)
                    .where(PushDevice.token_fingerprint == fingerprint)
                    .with_for_update()
                )
                if device is None:
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
                else:
                    device.account_id = current.account_id
                    device.session_id = current.session_id
                    device.token_ciphertext = ciphertext
                    device.platform = platform
                    device.updated_at = now
                    device.disabled_at = None
        except IntegrityError:
            # A concurrent first registration won the unique fingerprint race.
            # Update that row without ever storing or returning the plaintext.
            with self.repository.transaction() as transaction:
                self._require_current(
                    transaction,
                    current=current,
                    now=now,
                    for_update=True,
                )
                result = transaction.execute(
                    update(PushDevice)
                    .where(PushDevice.token_fingerprint == fingerprint)
                    .values(
                        account_id=current.account_id,
                        session_id=current.session_id,
                        token_ciphertext=ciphertext,
                        platform=platform,
                        updated_at=now,
                        disabled_at=None,
                    )
                )
                if result.rowcount != 1:
                    raise RuntimeError(
                        "Push registration race could not be resolved"
                    ) from None
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
            pending = transaction.execute(
                delete(PendingSignup).where(PendingSignup.expires_at <= now)
            ).rowcount
            reauth = transaction.execute(
                delete(ReauthChallenge).where(ReauthChallenge.expires_at <= now)
            ).rowcount
            security_events = transaction.execute(
                delete(AccountSecurityEvent).where(AccountSecurityEvent.expires_at <= now)
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
            "sessions": sessions,
        }

    async def _verify_provider(
        self, provider: ProviderName, access_token: str
    ) -> VerifiedProviderIdentity:
        try:
            identity = await self.providers[provider].verify(access_token)
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

    @staticmethod
    def _new_session(transaction, *, account: Account, installation_id: uuid.UUID, now: datetime):
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
        refresh_token = new_opaque_token()
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
