"""Authenticated encryption and keyed deduplication for FCM tokens."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets

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
