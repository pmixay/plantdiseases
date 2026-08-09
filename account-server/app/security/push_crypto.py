"""Authenticated encryption and purpose-separated account token helpers."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import Settings, SettingsError

_AAD = b"plantdiseases-account-push-v1"


def _decode_key(value: str) -> bytes:
    try:
        padding = "=" * (-len(value) % 4)
        key = base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError, UnicodeEncodeError) as exc:
        raise SettingsError("Invalid account encryption key") from exc
    if len(key) != 32:
        raise SettingsError("Account encryption key must contain 256 bits")
    return key


class PushTokenProtector:
    def __init__(self, key: bytes):
        if len(key) != 32:
            raise ValueError("Push encryption key must contain 256 bits")
        self._key = key
        self._cipher = AESGCM(key)
        self._fingerprint_key = hmac.digest(key, b"fingerprint-v1", "sha256")
        self._refresh_rotation_key = hmac.digest(
            key, b"refresh-rotation-v1", "sha256"
        )
        self._signup_fingerprint_key = hmac.digest(
            key, b"signup-completion-fingerprint-v1", "sha256"
        )
        self._signup_refresh_key = hmac.digest(
            key, b"signup-completion-refresh-v1", "sha256"
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> PushTokenProtector:
        if settings.encryption_key_file is not None:
            try:
                encoded_key = settings.encryption_key_file.read_text(encoding="ascii").strip()
            except OSError as exc:
                raise SettingsError("Cannot read account encryption key") from exc
            key = _decode_key(encoded_key)
        elif settings.environment == "test" and settings.allow_ephemeral_keys:
            key = AESGCM.generate_key(bit_length=256)
        else:
            raise SettingsError("Account encryption key is not configured")
        return cls(key)

    def fingerprint(self, token: str) -> bytes:
        return hmac.digest(self._fingerprint_key, token.encode("utf-8"), hashlib.sha256)

    def encrypt(self, token: str) -> bytes:
        nonce = secrets.token_bytes(12)
        return nonce + self._cipher.encrypt(nonce, token.encode("utf-8"), _AAD)

    def decrypt(self, ciphertext: bytes) -> str:
        if len(ciphertext) < 29:
            raise ValueError("Invalid push token ciphertext")
        nonce, encrypted = ciphertext[:12], ciphertext[12:]
        return self._cipher.decrypt(nonce, encrypted, _AAD).decode("utf-8")

    def derive_refresh_replacement(
        self,
        refresh_token: str,
        *,
        rotation_id: uuid.UUID,
    ) -> str:
        """Derive the same opaque successor for a bounded refresh retry.

        The purpose-separated keyed derivation lets the service reproduce a
        replacement after a response is lost without storing that bearer
        credential in recoverable form. The database still stores only its
        SHA-256 lookup hash.
        """
        retry_context = b"\x01" + rotation_id.bytes
        digest = hmac.digest(
            self._refresh_rotation_key,
            retry_context + refresh_token.encode("utf-8"),
            "sha256",
        )
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    def signup_request_fingerprint(
        self,
        pending_token: str,
        *,
        completion_id: uuid.UUID,
        installation_id: uuid.UUID,
        normalized_nickname: str,
    ) -> bytes:
        """Bind one logical completion to all security-relevant inputs."""
        nickname = normalized_nickname.encode("utf-8")
        context = (
            b"\x01"
            + completion_id.bytes
            + installation_id.bytes
            + len(nickname).to_bytes(2, "big")
            + nickname
            + pending_token.encode("utf-8")
        )
        return hmac.digest(self._signup_fingerprint_key, context, "sha256")

    def derive_signup_refresh(
        self,
        pending_token: str,
        *,
        completion_id: uuid.UUID,
        installation_id: uuid.UUID,
    ) -> str:
        """Reconstruct a signup refresh bearer for a bounded exact retry."""
        context = (
            b"\x01"
            + completion_id.bytes
            + installation_id.bytes
            + pending_token.encode("utf-8")
        )
        digest = hmac.digest(self._signup_refresh_key, context, "sha256")
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
