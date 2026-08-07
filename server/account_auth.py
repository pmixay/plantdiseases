"""Fail-closed validation for account access tokens used by the app API.

The app API verifies the Ed25519 JWT locally from a bounded JWKS document and
then asks the account service to confirm that the session and authorization
version are still current.  That second check is what makes logout, nickname
changes, and deletion effective immediately instead of waiting for the short
access-token lifetime to elapse.
"""

from __future__ import annotations

import base64
import json
import os
import re
import stat
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_ACCESS_CLAIMS = {"sid", "act", "nick", "iss", "aud", "iat", "nbf", "exp", "av"}
_KID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,64}\Z")
_MAX_TOKEN_BYTES = 8192
_MAX_DOCUMENT_BYTES = 64 * 1024
_DEFAULT_TIMEOUT_SECONDS = 2.0
_DEFAULT_JWKS_TTL_SECONDS = 300
_ENVIRONMENTS = frozenset({"test", "development", "staging", "production"})


class AccountAuthenticationError(Exception):
    """The caller did not present a valid, currently authorized account."""


class AccountAuthenticationUnavailable(Exception):
    """The private account validation dependency is unavailable or unconfigured."""


@dataclass(frozen=True, slots=True)
class AccountPrincipal:
    session_id: str
    actor_id: str
    nickname: str
    authorization_version: int


class AccountAuthenticator(Protocol):
    def authenticate(self, token: str) -> AccountPrincipal: ...


class UnconfiguredAccountAuthenticator:
    def authenticate(self, _token: str) -> AccountPrincipal:
        raise AccountAuthenticationUnavailable


def _decode_b64url(value: str) -> bytes:
    if not value or re.fullmatch(r"[A-Za-z0-9_-]+", value) is None:
        raise ValueError("invalid base64url")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _validated_service_url(value: str, *, environment: str, name: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{name} must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(f"{name} must not contain credentials, query, or a fragment")
    if environment == "production" and parsed.scheme != "https":
        raise ValueError(f"production {name} must use HTTPS")
    return value


def _origin(value: str) -> tuple[str, str, int]:
    parsed = urlparse(value)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), port


def _read_private_token(path: Path) -> str:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError("internal service token file permissions are unsafe")
        if info.st_size > 4096:
            raise ValueError("internal service token file is too large")
        token = path.read_text(encoding="ascii").strip()
    except OSError as exc:
        raise ValueError("cannot read internal service token file") from exc
    if not 32 <= len(token) <= 512:
        raise ValueError("internal service token has an invalid length")
    return token


class RemoteAccountAuthenticator:
    """Verify locally, refresh an unknown key once, then introspect privately."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str,
        introspection_url: str,
        internal_service_token: str,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        jwks_ttl_seconds: int = _DEFAULT_JWKS_TTL_SECONDS,
    ):
        self.issuer = issuer.rstrip("/")
        self.audience = audience
        self.jwks_url = jwks_url
        self.introspection_url = introspection_url
        self.internal_service_token = internal_service_token
        self.timeout_seconds = max(0.1, min(float(timeout_seconds), 10.0))
        self.jwks_ttl_seconds = max(30, min(int(jwks_ttl_seconds), 3600))
        self._keys: dict[str, Ed25519PublicKey] = {}
        self._keys_loaded_at = 0.0
        self._refresh_lock = threading.Lock()

    @classmethod
    def from_env(cls) -> AccountAuthenticator:
        environment = os.environ.get("ENVIRONMENT", "").strip().lower()
        issuer = os.environ.get("ACCOUNT_ISSUER", "").strip().rstrip("/")
        audience = os.environ.get("ACCOUNT_AUDIENCE", "").strip()
        jwks_url = os.environ.get("ACCOUNT_JWKS_URL", "").strip()
        introspection_url = os.environ.get("ACCOUNT_INTROSPECTION_URL", "").strip()
        token_file = os.environ.get("INTERNAL_SERVICE_TOKEN_FILE", "").strip()
        if not all((environment, issuer, audience, jwks_url, introspection_url, token_file)):
            return UnconfiguredAccountAuthenticator()
        try:
            if environment not in _ENVIRONMENTS:
                raise ValueError("unsupported ENVIRONMENT")
            issuer = _validated_service_url(
                issuer, environment=environment, name="ACCOUNT_ISSUER"
            ).rstrip("/")
            jwks_url = _validated_service_url(
                jwks_url, environment=environment, name="ACCOUNT_JWKS_URL"
            )
            introspection_url = _validated_service_url(
                introspection_url,
                environment=environment,
                name="ACCOUNT_INTROSPECTION_URL",
            )
            issuer_origin = _origin(issuer)
            if _origin(jwks_url) != issuer_origin:
                raise ValueError("ACCOUNT_JWKS_URL origin must match ACCOUNT_ISSUER")
            if _origin(introspection_url) != issuer_origin:
                raise ValueError(
                    "ACCOUNT_INTROSPECTION_URL origin must match ACCOUNT_ISSUER"
                )
            return cls(
                issuer=issuer,
                audience=audience,
                jwks_url=jwks_url,
                introspection_url=introspection_url,
                internal_service_token=_read_private_token(Path(token_file)),
            )
        except ValueError:
            # Importing the inference API must remain possible for diagnostics,
            # but every protected operation stays closed.
            return UnconfiguredAccountAuthenticator()

    def authenticate(self, token: str) -> AccountPrincipal:
        if not token or len(token.encode("utf-8")) > _MAX_TOKEN_BYTES:
            raise AccountAuthenticationError
        principal = self._verify_local(token)
        confirmed = self._introspect(token)
        if confirmed != principal:
            raise AccountAuthenticationError
        return principal

    def _verify_local(self, token: str) -> AccountPrincipal:
        try:
            header = jwt.get_unverified_header(token)
            if set(header) - {"alg", "kid", "typ"}:
                raise ValueError
            if header.get("alg") != "EdDSA" or header.get("typ") != "JWT":
                raise ValueError
            kid = header.get("kid")
            if not isinstance(kid, str) or _KID_PATTERN.fullmatch(kid) is None:
                raise ValueError

            key = self._key_for(kid)
            claims = jwt.decode(
                token,
                key,
                algorithms=["EdDSA"],
                issuer=self.issuer,
                audience=self.audience,
                leeway=5,
                options={"require": sorted(_ACCESS_CLAIMS)},
            )
            if set(claims) != _ACCESS_CLAIMS:
                raise ValueError
            if any(type(claims[name]) is not int for name in ("iat", "nbf", "exp")):
                raise ValueError
            if claims["iss"] != self.issuer or claims["aud"] != self.audience:
                raise ValueError
            if claims["exp"] <= claims["iat"]:
                raise ValueError
            if type(claims["av"]) is not int or claims["av"] < 1:
                raise ValueError
            if not isinstance(claims["nick"], str) or not 1 <= len(claims["nick"]) <= 64:
                raise ValueError
            return AccountPrincipal(
                session_id=str(uuid.UUID(claims["sid"])),
                actor_id=str(uuid.UUID(claims["act"])),
                nickname=claims["nick"],
                authorization_version=claims["av"],
            )
        except AccountAuthenticationUnavailable:
            raise
        except (jwt.PyJWTError, KeyError, TypeError, ValueError, UnicodeError):
            raise AccountAuthenticationError from None

    def _key_for(self, kid: str) -> Ed25519PublicKey:
        now = time.monotonic()
        refreshed = False
        if now - self._keys_loaded_at >= self.jwks_ttl_seconds:
            self._refresh_keys()
            refreshed = True
        key = self._keys.get(kid)
        if key is None and not refreshed:
            # A rotation may have introduced the key between cache refreshes.
            # Exactly one forced, bounded refresh is attempted per validation.
            self._refresh_keys(force=True)
            key = self._keys.get(kid)
        if key is None:
            raise ValueError("unknown signing key")
        return key

    def _refresh_keys(self, *, force: bool = False) -> None:
        with self._refresh_lock:
            if not force and time.monotonic() - self._keys_loaded_at < self.jwks_ttl_seconds:
                return
            request = urllib.request.Request(
                self.jwks_url,
                headers={"Accept": "application/json", "User-Agent": "plantdiseases-app-api"},
                method="GET",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    if response.status != 200:
                        raise ValueError
                    raw = response.read(_MAX_DOCUMENT_BYTES + 1)
                if len(raw) > _MAX_DOCUMENT_BYTES:
                    raise ValueError
                document = json.loads(raw)
                entries = document["keys"]
                if not isinstance(entries, list) or not 1 <= len(entries) <= 16:
                    raise ValueError
                parsed: dict[str, Ed25519PublicKey] = {}
                for entry in entries:
                    if not isinstance(entry, dict):
                        raise ValueError
                    kid = entry.get("kid")
                    if (
                        not isinstance(kid, str)
                        or _KID_PATTERN.fullmatch(kid) is None
                        or kid in parsed
                        or entry.get("kty") != "OKP"
                        or entry.get("crv") != "Ed25519"
                        or entry.get("alg") != "EdDSA"
                        or entry.get("use") != "sig"
                    ):
                        raise ValueError
                    raw_key = _decode_b64url(entry.get("x", ""))
                    if len(raw_key) != 32:
                        raise ValueError
                    parsed[kid] = Ed25519PublicKey.from_public_bytes(raw_key)
            except (OSError, urllib.error.URLError, json.JSONDecodeError, KeyError, ValueError):
                raise AccountAuthenticationUnavailable from None
            self._keys = parsed
            self._keys_loaded_at = time.monotonic()

    def _introspect(self, token: str) -> AccountPrincipal:
        request = urllib.request.Request(
            self.introspection_url,
            data=b"{}",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "X-Internal-Service-Token": self.internal_service_token,
                "User-Agent": "plantdiseases-app-api",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read(_MAX_DOCUMENT_BYTES + 1)
                status = response.status
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403, 409}:
                raise AccountAuthenticationError from None
            raise AccountAuthenticationUnavailable from None
        except (OSError, urllib.error.URLError):
            raise AccountAuthenticationUnavailable from None
        if status != 200 or len(raw) > _MAX_DOCUMENT_BYTES:
            raise AccountAuthenticationUnavailable
        try:
            document = json.loads(raw)
            if set(document) != {"session_id", "actor_id", "nickname", "authorization_version"}:
                raise ValueError
            nickname = document["nickname"]
            authorization_version = document["authorization_version"]
            if not isinstance(nickname, str) or not 1 <= len(nickname) <= 64:
                raise ValueError
            if type(authorization_version) is not int or authorization_version < 1:
                raise ValueError
            return AccountPrincipal(
                session_id=str(uuid.UUID(document["session_id"])),
                actor_id=str(uuid.UUID(document["actor_id"])),
                nickname=nickname,
                authorization_version=authorization_version,
            )
        except (AttributeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            raise AccountAuthenticationUnavailable from None


def build_account_authenticator() -> AccountAuthenticator:
    return RemoteAccountAuthenticator.from_env()


__all__ = [
    "AccountAuthenticationError",
    "AccountAuthenticationUnavailable",
    "AccountAuthenticator",
    "AccountPrincipal",
    "RemoteAccountAuthenticator",
    "UnconfiguredAccountAuthenticator",
    "build_account_authenticator",
]
