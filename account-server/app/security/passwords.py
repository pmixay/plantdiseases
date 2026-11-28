"""Email normalization and memory-hard password verification.

Accounts are looked up by a keyed fingerprint of the normalized address. The
address itself is stored only encrypted (see ``push_crypto.encrypt_email``).
"""

from __future__ import annotations

import re
import secrets
import threading
import unicodedata
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type

PASSWORD_MIN_LENGTH = 15
PASSWORD_MAX_LENGTH = 128
_PASSWORD_MAX_BYTES = 1024
_LOCAL_PART = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+\Z")
_DOMAIN_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


def normalize_email(value: str) -> str:
    """Return the stable, case-insensitive login form of a public email address."""

    candidate = unicodedata.normalize("NFKC", value.strip())
    if candidate.count("@") != 1:
        raise ValueError("invalid email")
    local, domain = candidate.rsplit("@", 1)
    if (
        not local
        or len(local) > 64
        or local.startswith(".")
        or local.endswith(".")
        or ".." in local
        or _LOCAL_PART.fullmatch(local) is None
    ):
        raise ValueError("invalid email")
    try:
        ascii_domain = domain.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("invalid email") from exc
    labels = ascii_domain.split(".")
    if (
        len(labels) < 2
        or len(ascii_domain) > 253
        or any(_DOMAIN_LABEL.fullmatch(label) is None for label in labels)
    ):
        raise ValueError("invalid email")
    normalized = f"{local.casefold()}@{ascii_domain}"
    if len(normalized.encode("ascii")) > 254:
        raise ValueError("invalid email")
    return normalized


def validate_new_password(password: str) -> None:
    """Apply length-only composition policy without transforming the secret."""

    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        raise ValueError("invalid password")
    if len(password.encode("utf-8")) > _PASSWORD_MAX_BYTES:
        raise ValueError("invalid password")


@dataclass(frozen=True, slots=True)
class PasswordVerification:
    valid: bool
    replacement_hash: str | None = None


class PasswordHashService:
    """Argon2id PHC hashes plus one indistinguishable dummy verification path."""

    def __init__(
        self,
        hasher: PasswordHasher | None = None,
        *,
        max_concurrent: int = 2,
    ):
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be positive")
        self._hasher = hasher or PasswordHasher(
            time_cost=3,
            memory_cost=65_536,
            parallelism=4,
            hash_len=32,
            salt_len=16,
            type=Type.ID,
        )
        # Argon2 intentionally allocates 64 MiB per production invocation.
        # Bound in-process work so an authentication burst cannot exhaust the
        # 512 MiB account-api container before the outer rate limit reacts.
        self._work_slots = threading.BoundedSemaphore(max_concurrent)
        self._dummy_hash = self._hasher.hash(secrets.token_urlsafe(48))

    def hash_new(self, password: str) -> str:
        validate_new_password(password)
        with self._work_slots:
            return self._hasher.hash(password)

    def verify(self, password: str, encoded_hash: str | None) -> PasswordVerification:
        candidate = encoded_hash or self._dummy_hash
        with self._work_slots:
            try:
                valid = bool(self._hasher.verify(candidate, password))
            except (VerificationError, InvalidHashError):
                valid = False
            if not valid or encoded_hash is None:
                return PasswordVerification(valid=False)
            replacement = (
                self._hasher.hash(password)
                if self._hasher.check_needs_rehash(candidate)
                else None
            )
        return PasswordVerification(valid=True, replacement_hash=replacement)
