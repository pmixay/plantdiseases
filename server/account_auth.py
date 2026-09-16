"""Fail-closed validation for account access tokens used by the app API.

The app API verifies the Ed25519 JWT locally from a bounded JWKS document and
then asks the account service to confirm that the session and authorization
version are still current.  That second check is what makes logout, nickname
changes, and deletion effective immediately instead of waiting for the short
access-token lifetime to elapse.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import os
import re
import stat
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_ACCESS_CLAIMS = {"sid", "act", "nick", "iss", "aud", "iat", "nbf", "exp", "av"}
_KID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,64}\Z")
_MAX_TOKEN_BYTES = 8192
# The account service issues exactly ``ACCESS_TOKEN_TTL`` (15 minutes) in
# ``account-server/app/security/tokens.py``. Enforcing that bound here as well
# means this API honours the documented lifetime on its own evidence instead of
# trusting whatever ``exp`` a signed token happens to carry: a token minted with
# a far-future expiry — by a mistaken change, or by misuse of a signing key —
# cannot outlive its stated policy on this side of the trust boundary. The
# leeway matches the clock skew already allowed for the individual claims.
_MAX_ACCESS_TOKEN_LIFETIME_SECONDS = 15 * 60 + 5
_MAX_DOCUMENT_BYTES = 64 * 1024
_DEFAULT_TIMEOUT_SECONDS = 2.0
_DEFAULT_JWKS_TTL_SECONDS = 300
_UNKNOWN_KID_TTL_SECONDS = 5.0
_UNKNOWN_KID_CACHE_SIZE = 128
_REFRESH_FAILURE_BACKOFF_SECONDS = 2.0
_ENVIRONMENTS = frozenset({"test", "development", "staging", "production"})
_INTERNAL_SERVICE_HOST = re.compile(
    r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?\Z"
)


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
    configured = False

    def authenticate(self, _token: str) -> AccountPrincipal:
        raise AccountAuthenticationUnavailable


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects before urllib can replay credentialed headers."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _open_no_redirect(request: urllib.request.Request, *, timeout: float):
    return urllib.request.build_opener(_NoRedirectHandler()).open(
        request, timeout=timeout
    )


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


def _internal_base_url(value: str) -> str:
    """Validate an explicitly configured private account-service origin."""
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("ACCOUNT_INTERNAL_BASE_URL must be an HTTP(S) origin")
    hostname = parsed.hostname.lower()
    if parsed.scheme == "http":
        try:
            address = ipaddress.ip_address(hostname)
            private_host = address.is_private or address.is_loopback
        except ValueError:
            # A single-label hostname is service discovery inside the private
            # container network. Public DNS names must use HTTPS.
            private_host = bool(_INTERNAL_SERVICE_HOST.fullmatch(hostname))
        if not private_host:
            raise ValueError("internal account HTTP must use a private host")
    return value.rstrip("/")


def _read_private_token(path: Path) -> str:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o027:
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

    configured = True

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
        self._refresh_generation = 0
        self._refresh_failed_until = 0.0
        self._unknown_refresh_after = 0.0
        self._unknown_kids: OrderedDict[str, float] = OrderedDict()

    @classmethod
    def from_env(cls) -> AccountAuthenticator:
        environment = os.environ.get("ENVIRONMENT", "").strip().lower()
        issuer = os.environ.get("ACCOUNT_ISSUER", "").strip().rstrip("/")
        audience = os.environ.get("ACCOUNT_AUDIENCE", "").strip()
        jwks_url = os.environ.get("ACCOUNT_JWKS_URL", "").strip()
        introspection_url = os.environ.get("ACCOUNT_INTROSPECTION_URL", "").strip()
        internal_base_url = os.environ.get("ACCOUNT_INTERNAL_BASE_URL", "").strip()
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
                jwks_url,
                environment="staging" if internal_base_url else environment,
                name="ACCOUNT_JWKS_URL",
            )
            introspection_url = _validated_service_url(
                introspection_url,
                # An explicitly configured private service origin may use
                # plain HTTP inside the isolated container network.
                environment="staging" if internal_base_url else environment,
                name="ACCOUNT_INTROSPECTION_URL",
            )
            if internal_base_url:
                internal_base_url = _internal_base_url(internal_base_url)
            endpoint_base = internal_base_url or issuer
            endpoint_origin = _origin(endpoint_base)
            if _origin(jwks_url) != endpoint_origin:
                raise ValueError(
                    "ACCOUNT_JWKS_URL origin must match account endpoint base"
                )
            if _origin(introspection_url) != endpoint_origin:
                raise ValueError(
                    "ACCOUNT_INTROSPECTION_URL origin must match account endpoint base"
                )
            if jwks_url.rstrip("/") != f"{endpoint_base}/.well-known/jwks.json":
                raise ValueError("ACCOUNT_JWKS_URL must use the fixed JWKS path")
            if introspection_url.rstrip("/") != (
                f"{endpoint_base}/v1/internal/auth/verify"
            ):
                raise ValueError("ACCOUNT_INTROSPECTION_URL must use the fixed verify path")
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
            if claims["exp"] - claims["iat"] > _MAX_ACCESS_TOKEN_LIFETIME_SECONDS:
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
            self._refresh_keys(missing_kid=kid)
            refreshed = True
        with self._refresh_lock:
            key = self._keys.get(kid)
            miss_expires = self._unknown_kids.get(kid, 0.0)
            if miss_expires > now:
                raise ValueError("unknown signing key")
            self._unknown_kids.pop(kid, None)
            generation = self._refresh_generation
        if key is None and not refreshed:
            # A rotation may have introduced the key between cache refreshes.
            # Concurrent misses share one fetch, and a bounded negative cache
            # stops attacker-chosen kids from turning every request into I/O.
            self._refresh_keys(
                force=True,
                expected_generation=generation,
                missing_kid=kid,
            )
            with self._refresh_lock:
                key = self._keys.get(kid)
        if key is None:
            raise ValueError("unknown signing key")
        return key

    def _refresh_keys(
        self,
        *,
        force: bool = False,
        expected_generation: int | None = None,
        missing_kid: str | None = None,
    ) -> None:
        with self._refresh_lock:
            now = time.monotonic()
            if now < self._refresh_failed_until:
                raise AccountAuthenticationUnavailable
            if force and (
                expected_generation is not None
                and expected_generation != self._refresh_generation
            ):
                return
            if force and missing_kid is not None and now < self._unknown_refresh_after:
                # A per-kid cache alone is bypassable with random kid values.
                # Bound forced refreshes globally as well, while remembering
                # this miss for the remainder of the same short window.
                self._unknown_kids[missing_kid] = self._unknown_refresh_after
                self._unknown_kids.move_to_end(missing_kid)
                while len(self._unknown_kids) > _UNKNOWN_KID_CACHE_SIZE:
                    self._unknown_kids.popitem(last=False)
                return
            if not force and now - self._keys_loaded_at < self.jwks_ttl_seconds:
                return
            # Increment before I/O. Threads that were queued for the same cache
            # generation will observe the attempt and will not each fetch.
            self._refresh_generation += 1
            request = urllib.request.Request(
                self.jwks_url,
                headers={"Accept": "application/json", "User-Agent": "plantdiseases-app-api"},
                method="GET",
            )
            try:
                with _open_no_redirect(request, timeout=self.timeout_seconds) as response:
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
                self._refresh_failed_until = (
                    time.monotonic() + _REFRESH_FAILURE_BACKOFF_SECONDS
                )
                raise AccountAuthenticationUnavailable from None
            self._keys = parsed
            self._keys_loaded_at = time.monotonic()
            self._refresh_failed_until = 0.0
            self._unknown_kids.clear()
            if missing_kid is not None:
                self._unknown_refresh_after = (
                    self._keys_loaded_at + _UNKNOWN_KID_TTL_SECONDS
                )
            if missing_kid is not None and missing_kid not in parsed:
                self._unknown_kids[missing_kid] = self._unknown_refresh_after
                self._unknown_kids.move_to_end(missing_kid)
                while len(self._unknown_kids) > _UNKNOWN_KID_CACHE_SIZE:
                    self._unknown_kids.popitem(last=False)

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
            with _open_no_redirect(request, timeout=self.timeout_seconds) as response:
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


def build_account_authenticator(*, expected: bool = False) -> AccountAuthenticator:
    authenticator = RemoteAccountAuthenticator.from_env()
    if expected and not getattr(authenticator, "configured", False):
        raise RuntimeError("Account authentication is required but misconfigured")
    return authenticator


__all__ = [
    "AccountAuthenticationError",
    "AccountAuthenticationUnavailable",
    "AccountAuthenticator",
    "AccountPrincipal",
    "RemoteAccountAuthenticator",
    "UnconfiguredAccountAuthenticator",
    "build_account_authenticator",
]
