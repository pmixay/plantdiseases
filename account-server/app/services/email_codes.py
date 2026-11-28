"""Mailed single-use codes: sign-up, password reset and adding an address.

Rules shared by every flow:

- A code is six digits, works once, expires after 15 minutes and dies after
  five wrong guesses. Only a keyed digest of it is stored.
- Sending and guessing are limited per client address (IP) and per email
  address. The limits are checked before the database is asked whether the
  address exists, so a refusal says nothing about it.
- A request about an address that cannot be used answers exactly like one that
  can. Where a row must exist for later steps to look the same, it holds a
  random digest that no code matches.

The limiters live in process memory, like the service's other limits: they
assume one replica and start empty after a restart. The five-guess limit on
each code is stored in the database.
"""

from __future__ import annotations

import hmac
import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.api.errors import AccountAPIError
from app.db.models import (
    Account,
    EmailCode,
    PasswordCredential,
    PendingPasswordCredential,
    PendingSignup,
    ReauthChallenge,
)
from app.db.models import (
    Session as AccountSession,
)
from app.mail import MailMessage
from app.mail_texts import already_registered_message, code_message
from app.rate_limit import InMemoryRateLimiter
from app.security.passwords import normalize_email, validate_new_password
from app.security.tokens import hash_opaque_token

# How long a verified or OAuth-proven sign-up has to pick a nickname.
PENDING_SIGNUP_TTL = timedelta(minutes=10)
EMAIL_CODE_TTL = timedelta(minutes=15)
EMAIL_CODE_MAX_ATTEMPTS = 5
_EMAIL_CODE_MINUTES = int(EMAIL_CODE_TTL.total_seconds() // 60)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class EmailAbuseLimits:
    """Sliding-window limits on sending codes and on guessing them."""

    def __init__(self, *, monotonic: Callable[[], float] | None = None):
        def limiter(requests: int, window_seconds: int) -> InMemoryRateLimiter:
            options = {} if monotonic is None else {"monotonic": monotonic}
            return InMemoryRateLimiter(
                requests=requests,
                window_seconds=window_seconds,
                max_buckets=50_000,
                **options,
            )

        self._send_address = limiter(3, 15 * 60)
        self._send_address_day = limiter(10, 24 * 60 * 60)
        self._send_ip = limiter(20, 60 * 60)
        self._guess_address = limiter(10, 15 * 60)
        self._guess_ip = limiter(30, 15 * 60)

    def check_send(self, *, address_key: str, client_address: str) -> None:
        # IP first: a client over its own limit must not use up an address's.
        self._send_ip.check("email-send-ip", client_address)
        self._send_address.check("email-send", address_key)
        self._send_address_day.check("email-send-day", address_key)

    def check_guess(self, *, address_key: str, client_address: str) -> None:
        self._guess_ip.check("email-guess-ip", client_address)
        self._guess_address.check("email-guess", address_key)


def _new_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


class EmailCodeFlows:
    """The code-based part of :class:`~app.services.accounts.AccountService`."""

    # Provided by AccountService.
    mail_sender: Callable[[MailMessage], None] | None
    email_limits: EmailAbuseLimits

    @property
    def mail_available(self) -> bool:
        return self.mail_sender is not None

    def _require_mail(self) -> Callable[[MailMessage], None]:
        if self.mail_sender is None:
            raise AccountAPIError("email_auth_unavailable", status_code=503)
        return self.mail_sender

    def _issue_email_code(
        self,
        transaction,
        *,
        purpose: str,
        fingerprint: bytes,
        address: str,
        now: datetime,
        usable: bool,
        pending_signup_id: uuid.UUID | None = None,
        account_id: uuid.UUID | None = None,
        session_id: uuid.UUID | None = None,
        password_hash: str | None = None,
        email_ciphertext: bytes | None = None,
    ) -> MailMessage:
        """Store a new code, retiring the previous one, and return its mail.

        With ``usable=False`` no code can ever match and the returned mail is
        the already-registered notice. Send the mail only after the commit.
        """

        # A sign-up belongs to its address. Reset and add belong to an account,
        # so one account can never retire another account's code.
        owner = (
            EmailCode.email_fingerprint == fingerprint
            if account_id is None
            else EmailCode.account_id == account_id
        )
        transaction.execute(
            update(EmailCode)
            .where(EmailCode.purpose == purpose, owner, EmailCode.consumed_at.is_(None))
            .values(consumed_at=now)
        )
        code_id = uuid.uuid4()
        code = _new_code()
        code_mac = (
            self.push_protector.email_code_mac(code, code_id=code_id, purpose=purpose)
            if usable
            else secrets.token_bytes(32)
        )
        transaction.add(
            EmailCode(
                id=code_id,
                purpose=purpose,
                email_fingerprint=fingerprint,
                code_mac=code_mac,
                pending_signup_id=pending_signup_id,
                account_id=account_id,
                session_id=session_id,
                password_hash=password_hash,
                email_ciphertext=email_ciphertext,
                attempts=0,
                created_at=now,
                expires_at=now + EMAIL_CODE_TTL,
            )
        )
        if not usable:
            return already_registered_message(to=address)
        return code_message(
            purpose=purpose, to=address, code=code, minutes=_EMAIL_CODE_MINUTES
        )

    def _redeem_email_code(
        self, record: EmailCode | None, *, code: str, now: datetime
    ) -> bool:
        """Spend one guess on a locked row. The caller must commit either way."""

        if (
            record is None
            or record.consumed_at is not None
            or record.attempts >= EMAIL_CODE_MAX_ATTEMPTS
            or now >= _as_utc(record.expires_at)
        ):
            return False
        record.attempts += 1
        expected = self.push_protector.email_code_mac(
            code, code_id=record.id, purpose=record.purpose
        )
        if hmac.compare_digest(record.code_mac, expected):
            record.consumed_at = now
            return True
        if record.attempts >= EMAIL_CODE_MAX_ATTEMPTS:
            record.consumed_at = now
        return False

    @staticmethod
    def _live_email_code(transaction, *criteria) -> EmailCode | None:
        return transaction.scalar(
            select(EmailCode)
            .where(EmailCode.consumed_at.is_(None), *criteria)
            .order_by(EmailCode.created_at.desc())
            .limit(1)
            .with_for_update()
        )

    def verify_email_signup(
        self,
        *,
        pending_token: str,
        code: str,
        installation_id: uuid.UUID,
        client_address: str = "unknown",
    ) -> dict:
        """Prove the address of a pending sign-up; nickname selection follows."""

        self._require_mail()
        now = self.clock()
        verified = False
        with self.repository.transaction() as transaction:
            pending = transaction.scalar(
                select(PendingSignup)
                .where(PendingSignup.token_hash == hash_opaque_token(pending_token))
                .with_for_update()
            )
            if (
                pending is None
                or pending.provider != "email"
                or pending.installation_id != installation_id
            ):
                raise AccountAPIError("pending_signup_expired", status_code=401)
            self._require_live_pending(pending, now)
            staged = transaction.scalar(
                select(PendingPasswordCredential)
                .where(PendingPasswordCredential.pending_signup_id == pending.id)
                .with_for_update()
            )
            if staged is None:
                raise AccountAPIError("pending_signup_expired", status_code=401)
            if staged.email_verified_at is not None:
                # A retry after a lost response, from the bearer's own install.
                verified = True
            else:
                self.email_limits.check_guess(
                    address_key=staged.email_fingerprint.hex(),
                    client_address=client_address,
                )
                record = self._live_email_code(
                    transaction,
                    EmailCode.purpose == "signup",
                    EmailCode.pending_signup_id == pending.id,
                )
                if self._redeem_email_code(record, code=code, now=now):
                    staged.email_verified_at = now
                    pending.expires_at = now + PENDING_SIGNUP_TTL
                    verified = True
        if not verified:
            raise AccountAPIError("invalid_code", status_code=400)
        return {
            "status": "profile_required",
            "provider": "email",
            "pending_signup_token": pending_token,
            "expires_in": int(PENDING_SIGNUP_TTL.total_seconds()),
        }

    def request_password_reset(self, *, email: str, client_address: str = "unknown") -> dict:
        """Mail a reset code if the address signs in; answer the same if not."""

        send = self._require_mail()
        try:
            address = normalize_email(email)
        except ValueError:
            raise AccountAPIError("invalid_email", status_code=400) from None
        fingerprint = self.push_protector.email_fingerprint(address)
        self.email_limits.check_send(
            address_key=fingerprint.hex(), client_address=client_address
        )
        now = self.clock()
        message: MailMessage | None = None
        with self.repository.transaction() as transaction:
            self._lock_email_fingerprint(transaction, fingerprint)
            account_id = transaction.scalar(
                select(Account.id)
                .join(PasswordCredential, PasswordCredential.account_id == Account.id)
                .where(
                    PasswordCredential.email_fingerprint == fingerprint,
                    Account.state != "deletion_pending",
                )
            )
            if account_id is not None:
                message = self._issue_email_code(
                    transaction,
                    purpose="reset",
                    fingerprint=fingerprint,
                    address=address,
                    account_id=account_id,
                    now=now,
                    usable=True,
                )
        if message is not None:
            send(message)
        return {
            "status": "code_sent_if_registered",
            "expires_in": int(EMAIL_CODE_TTL.total_seconds()),
        }

    def confirm_password_reset(
        self,
        *,
        email: str,
        code: str,
        new_password: str,
        client_address: str = "unknown",
    ) -> None:
        """Set a new password with a mailed code and sign every device out.

        The reset also proves the supplied address, which is how a credential
        stored before addresses were kept becomes usable again.
        """

        self._require_mail()
        try:
            address = normalize_email(email)
        except ValueError:
            raise AccountAPIError("invalid_code", status_code=400) from None
        try:
            validate_new_password(new_password)
        except ValueError:
            raise AccountAPIError("invalid_password", status_code=400) from None
        fingerprint = self.push_protector.email_fingerprint(address)
        self.email_limits.check_guess(
            address_key=fingerprint.hex(), client_address=client_address
        )
        # Hashed before any lookup so every address costs the same.
        password_hash = self.password_hash_service.hash_new(new_password)

        now = self.clock()
        changed = False
        with self.repository.transaction() as transaction:
            self._lock_email_fingerprint(transaction, fingerprint)
            account_id = transaction.scalar(
                select(PasswordCredential.account_id).where(
                    PasswordCredential.email_fingerprint == fingerprint
                )
            )
            # Account first, then its rows: the order every writer here uses.
            account = (
                transaction.scalar(
                    select(Account).where(Account.id == account_id).with_for_update()
                )
                if account_id is not None
                else None
            )
            if account is not None and account.state != "deletion_pending":
                credential = transaction.scalar(
                    select(PasswordCredential)
                    .where(
                        PasswordCredential.account_id == account.id,
                        PasswordCredential.email_fingerprint == fingerprint,
                    )
                    .with_for_update()
                )
                record = self._live_email_code(
                    transaction,
                    EmailCode.purpose == "reset",
                    EmailCode.account_id == account.id,
                    EmailCode.email_fingerprint == fingerprint,
                )
                if credential is not None and self._redeem_email_code(
                    record, code=code, now=now
                ):
                    credential.password_hash = password_hash
                    credential.password_changed_at = now
                    credential.email_verified_at = now
                    credential.email_ciphertext = self.push_protector.encrypt_email(address)
                    for account_session in transaction.scalars(
                        select(AccountSession).where(AccountSession.account_id == account.id)
                    ):
                        self._revoke_session(transaction, account_session, now=now)
                    transaction.execute(
                        update(ReauthChallenge)
                        .where(
                            ReauthChallenge.account_id == account.id,
                            ReauthChallenge.consumed_at.is_(None),
                        )
                        .values(consumed_at=now)
                    )
                    changed = True
        if not changed:
            raise AccountAPIError("invalid_code", status_code=400)

    def request_email_link(
        self,
        current,
        *,
        email: str,
        password: str,
        reauth_token: str,
        client_address: str = "unknown",
    ) -> dict:
        """Stage an email sign-in for a signed-in account and mail its code."""

        send = self._require_mail()
        try:
            address = normalize_email(email)
            validate_new_password(password)
        except ValueError:
            raise AccountAPIError("invalid_registration", status_code=400) from None
        fingerprint = self.push_protector.email_fingerprint(address)
        self.email_limits.check_send(
            address_key=fingerprint.hex(), client_address=client_address
        )
        password_hash = self.password_hash_service.hash_new(password)

        now = self.clock()
        with self.repository.transaction() as transaction:
            self._require_current(transaction, current=current, now=now, for_update=True)
            challenge = self._live_challenge(
                transaction,
                current=current,
                token=reauth_token,
                scope="link_email",
                now=now,
            )
            if self._has_password_credential(transaction, current.account_id):
                raise AccountAPIError("identity_already_linked", status_code=409)
            challenge.consumed_at = now
            self._lock_email_fingerprint(transaction, fingerprint)
            taken = transaction.scalar(
                select(PasswordCredential.id).where(
                    PasswordCredential.email_fingerprint == fingerprint
                )
            )
            message = self._issue_email_code(
                transaction,
                purpose="add",
                fingerprint=fingerprint,
                address=address,
                account_id=current.account_id,
                session_id=current.session_id,
                password_hash=password_hash,
                email_ciphertext=self.push_protector.encrypt_email(address),
                now=now,
                usable=taken is None,
            )
        send(message)
        return {
            "status": "verification_required",
            "provider": "email",
            "expires_in": int(EMAIL_CODE_TTL.total_seconds()),
        }

    def confirm_email_link(
        self, current, *, code: str, client_address: str = "unknown"
    ) -> dict:
        self._require_mail()
        # The address is not part of this request; the account stands in for it.
        self.email_limits.check_guess(
            address_key=f"account:{current.account_id}", client_address=client_address
        )
        now = self.clock()
        linked = False
        try:
            with self.repository.transaction() as transaction:
                self._require_current(transaction, current=current, now=now, for_update=True)
                record = self._live_email_code(
                    transaction,
                    EmailCode.purpose == "add",
                    EmailCode.account_id == current.account_id,
                    EmailCode.session_id == current.session_id,
                )
                if self._redeem_email_code(record, code=code, now=now):
                    if self._has_password_credential(transaction, current.account_id):
                        raise AccountAPIError("identity_already_linked", status_code=409)
                    transaction.add(
                        PasswordCredential(
                            account_id=current.account_id,
                            email_fingerprint=record.email_fingerprint,
                            email_ciphertext=record.email_ciphertext,
                            email_verified_at=now,
                            password_hash=record.password_hash,
                            password_changed_at=now,
                            created_at=now,
                        )
                    )
                    transaction.delete(record)
                    linked = True
        except IntegrityError:
            # Someone else verified the address first. Say no more than that
            # the code did not work.
            raise AccountAPIError("invalid_code", status_code=400) from None
        if not linked:
            raise AccountAPIError("invalid_code", status_code=400)
        return {"status": "linked", "provider": "email"}

    @staticmethod
    def _has_password_credential(transaction, account_id: uuid.UUID) -> bool:
        return (
            transaction.scalar(
                select(PasswordCredential.id).where(
                    PasswordCredential.account_id == account_id
                )
            )
            is not None
        )

    @staticmethod
    def _erase_email_address(transaction, *, account_id: uuid.UUID, now: datetime) -> None:
        """Erase every stored copy of an account's address at deletion request.

        The credential row keeps its fingerprint and hash until the account
        rows are erased after community cleanup; the readable address does not
        wait for that.
        """

        credentials = list(
            transaction.scalars(
                select(PasswordCredential)
                .where(PasswordCredential.account_id == account_id)
                .with_for_update()
            )
        )
        fingerprints = [credential.email_fingerprint for credential in credentials]
        for credential in credentials:
            credential.email_ciphertext = None
        transaction.execute(
            delete(EmailCode).where(
                or_(
                    EmailCode.account_id == account_id,
                    EmailCode.email_fingerprint.in_(fingerprints),
                )
            )
        )
        if not fingerprints:
            return
        # Unfinished sign-ups for the same address hold their own copy.
        pending_ids = select(PendingPasswordCredential.pending_signup_id).where(
            PendingPasswordCredential.email_fingerprint.in_(fingerprints)
        )
        transaction.execute(
            update(PendingSignup)
            .where(
                PendingSignup.id.in_(pending_ids),
                PendingSignup.consumed_at.is_(None),
                PendingSignup.cancelled_at.is_(None),
            )
            .values(cancelled_at=now)
        )
        transaction.execute(
            delete(PendingPasswordCredential).where(
                PendingPasswordCredential.email_fingerprint.in_(fingerprints)
            )
        )
