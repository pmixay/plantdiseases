"""Ed25519 access tokens and hashed high-entropy opaque tokens."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from app.api.errors import AccountAPIError
from app.config import Settings, SettingsError

ACCESS_TOKEN_TTL = timedelta(minutes=15)
_ACCESS_CLAIMS = {"sid", "act", "nick", "iss", "aud", "iat", "nbf", "exp", "av"}
_KID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,64}\Z")
_B64URL_PATTERN = re.compile(r"[A-Za-z0-9_-]+\Z")


def utc_now() -> datetime:
    return datetime.now(UTC)


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    if not _B64URL_PATTERN.fullmatch(value):
        raise ValueError("invalid base64url value")
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def new_opaque_token() -> str:
    """Return a URL-safe token backed by exactly 256 random bits."""
    return _b64url_encode(secrets.token_bytes(32))


def hash_opaque_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


@dataclass(frozen=True, slots=True)
class AccessPrincipal:
    session_id: uuid.UUID
    community_actor_id: uuid.UUID
    nickname: str
    authorization_version: int


class SigningKeyRing:
    """One active Ed25519 signer plus current/recent verification keys."""

    def __init__(
        self,
        *,
        active_kid: str,
        active_private_key: Ed25519PrivateKey,
        retired_public_keys: Mapping[str, Ed25519PublicKey] | None = None,
    ):
        self._validate_kid(active_kid)
        retired = dict(retired_public_keys or {})
        for kid in retired:
            self._validate_kid(kid)
        if active_kid in retired:
            raise SettingsError("Active signing kid is duplicated")
        self.active_kid = active_kid
        self._active_private_key = active_private_key
        self._public_keys = {
            active_kid: active_private_key.public_key(),
            **retired,
        }

    @classmethod
    def ephemeral(cls) -> SigningKeyRing:
        return cls(
            active_kid=f"test-{uuid.uuid4().hex[:16]}",
            active_private_key=Ed25519PrivateKey.generate(),
        )

    @classmethod
    def from_file(cls, path: Path) -> SigningKeyRing:
        if path.stat().st_size > 65_536:
            raise SettingsError("Signing key file is too large")
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            active_kid = document["active_kid"]
            entries = document["keys"]
        except (KeyError, TypeError, ValueError, OSError) as exc:
            raise SettingsError("Invalid signing key file") from exc
        if not isinstance(active_kid, str) or not isinstance(entries, list):
            raise SettingsError("Invalid signing key file")

        active_private: Ed25519PrivateKey | None = None
        retired: dict[str, Ed25519PublicKey] = {}
        seen: set[str] = set()
        try:
            for entry in entries:
                kid = entry["kid"]
                if not isinstance(kid, str) or kid in seen:
                    raise ValueError
                seen.add(kid)
                if kid == active_kid:
                    raw_private = _b64url_decode(entry["private_key"])
                    if len(raw_private) != 32:
                        raise ValueError
                    active_private = Ed25519PrivateKey.from_private_bytes(raw_private)
                else:
                    encoded_public = entry.get("public_key")
                    if encoded_public is None and "private_key" in entry:
                        private = Ed25519PrivateKey.from_private_bytes(
                            _b64url_decode(entry["private_key"])
                        )
                        public = private.public_key()
                    else:
                        raw_public = _b64url_decode(encoded_public)
                        if len(raw_public) != 32:
                            raise ValueError
                        public = Ed25519PublicKey.from_public_bytes(raw_public)
                    retired[kid] = public
        except (KeyError, TypeError, ValueError) as exc:
            raise SettingsError("Invalid signing key file") from exc
        if active_private is None:
            raise SettingsError("Signing key file has no active private key")
        return cls(
            active_kid=active_kid,
            active_private_key=active_private,
            retired_public_keys=retired,
        )

    @staticmethod
    def _validate_kid(kid: str) -> None:
        if not _KID_PATTERN.fullmatch(kid):
            raise SettingsError("Signing kid has an invalid format")

    def sign(self, claims: dict) -> str:
        return jwt.encode(
            claims,
            self._active_private_key,
            algorithm="EdDSA",
            headers={"kid": self.active_kid, "typ": "JWT"},
        )

    def verification_key(self, kid: str) -> Ed25519PublicKey | None:
        return self._public_keys.get(kid)

    def jwks(self) -> dict[str, list[dict[str, str]]]:
        ordered_kids = [self.active_kid] + sorted(
            kid for kid in self._public_keys if kid != self.active_kid
        )
        keys = []
        for kid in ordered_kids:
            raw = self._public_keys[kid].public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            keys.append(
                {
                    "kty": "OKP",
                    "crv": "Ed25519",
                    "use": "sig",
                    "alg": "EdDSA",
                    "kid": kid,
                    "x": _b64url_encode(raw),
                }
            )
        return {"keys": keys}


class TokenService:
    def __init__(self, *, issuer: str, audience: str, key_ring: SigningKeyRing):
        self.issuer = issuer
        self.audience = audience
        self.key_ring = key_ring

    @classmethod
    def from_settings(cls, settings: Settings) -> TokenService:
        if settings.signing_key_file is not None:
            key_ring = SigningKeyRing.from_file(settings.signing_key_file)
        elif settings.environment == "test" and settings.allow_ephemeral_keys:
            key_ring = SigningKeyRing.ephemeral()
        else:
            raise SettingsError("Account signing keys are not configured")
        return cls(issuer=settings.issuer, audience=settings.audience, key_ring=key_ring)

    @property
    def access_ttl_seconds(self) -> int:
        return int(ACCESS_TOKEN_TTL.total_seconds())

    def issue_access(
        self,
        *,
        session_id: uuid.UUID,
        community_actor_id: uuid.UUID,
        nickname: str,
        authorization_version: int,
        now: datetime | None = None,
    ) -> str:
        issued_at = now or utc_now()
        issued_timestamp = int(issued_at.timestamp())
        claims = {
            "sid": str(session_id),
            "act": str(community_actor_id),
            "nick": nickname,
            "iss": self.issuer,
            "aud": self.audience,
            "iat": issued_timestamp,
            "nbf": issued_timestamp,
            "exp": int((issued_at + ACCESS_TOKEN_TTL).timestamp()),
            "av": authorization_version,
        }
        return self.key_ring.sign(claims)

    def decode_access(
        self, token: str, *, now: datetime | None = None, leeway_seconds: int = 5
    ) -> AccessPrincipal:
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "EdDSA" or header.get("typ") != "JWT":
                raise ValueError
            kid = header.get("kid")
            if not isinstance(kid, str):
                raise ValueError
            verification_key = self.key_ring.verification_key(kid)
            if verification_key is None:
                raise ValueError
            claims = jwt.decode(
                token,
                verification_key,
                algorithms=["EdDSA"],
                issuer=self.issuer,
                audience=self.audience,
                options={
                    "require": sorted(_ACCESS_CLAIMS),
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                },
            )
            if set(claims) != _ACCESS_CLAIMS:
                raise ValueError
            timestamp_claims = (claims["iat"], claims["nbf"], claims["exp"])
            if any(type(value) is not int for value in timestamp_claims):
                raise ValueError
            current_timestamp = int((now or utc_now()).timestamp())
            if claims["iat"] > current_timestamp + leeway_seconds:
                raise ValueError
            if claims["nbf"] > current_timestamp + leeway_seconds:
                raise ValueError
            if claims["exp"] <= current_timestamp - leeway_seconds:
                raise ValueError
            if claims["exp"] <= claims["iat"]:
                raise ValueError
            # A token is only ever minted with ACCESS_TOKEN_TTL. Re-check the
            # bound on the way in so a long-lived token — from a mistaken
            # change, or from misuse of a signing key — cannot outlive the
            # policy this service publishes, and so revocation stays the only
            # variable a session's reach depends on.
            if (
                claims["exp"] - claims["iat"]
                > int(ACCESS_TOKEN_TTL.total_seconds()) + leeway_seconds
            ):
                raise ValueError
            if type(claims["av"]) is not int or claims["av"] < 1:
                raise ValueError
            if not isinstance(claims["nick"], str):
                raise ValueError
            return AccessPrincipal(
                session_id=uuid.UUID(claims["sid"]),
                community_actor_id=uuid.UUID(claims["act"]),
                nickname=claims["nick"],
                authorization_version=claims["av"],
            )
        except (jwt.PyJWTError, AttributeError, TypeError, ValueError, KeyError):
            raise AccountAPIError(
                "auth_required",
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            ) from None

    def jwks(self) -> dict[str, list[dict[str, str]]]:
        return self.key_ring.jwks()
