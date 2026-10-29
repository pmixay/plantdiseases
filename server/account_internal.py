"""Private account-service calls made by the community API."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import secrets
import stat
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlparse

try:
    from .account_auth import _open_no_redirect
except ImportError:  # flat Docker/module import
    from account_auth import _open_no_redirect

_ENVIRONMENTS = frozenset({"test", "development", "staging", "production"})
_INTERNAL_SERVICE_HOST = re.compile(
    r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?\Z"
)
_MAX_RESPONSE_BYTES = 4096


def _load_internal_token(token_path: str) -> str | None:
    if not token_path:
        return None
    path = Path(token_path)
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o027:
            return None
        token = path.read_text(encoding="ascii").strip()
    except OSError:
        return None
    return token if 32 <= len(token) <= 512 else None


class AccountInternalClient:
    def __init__(self, *, notification_url: str, service_token: str, timeout: float = 2.0):
        self.notification_url = notification_url
        self.service_token = service_token
        self.timeout = max(0.1, min(float(timeout), 10.0))

    @classmethod
    def from_env(cls) -> AccountInternalClient | None:
        url = os.environ.get("ACCOUNT_NOTIFICATION_URL", "").strip()
        token_path = os.environ.get("INTERNAL_SERVICE_TOKEN_FILE", "").strip()
        environment = os.environ.get("ENVIRONMENT", "").strip().lower()
        issuer = os.environ.get("ACCOUNT_ISSUER", "").strip().rstrip("/")
        internal_base = (
            os.environ.get("ACCOUNT_INTERNAL_BASE_URL", "").strip().rstrip("/")
        )
        if not url or not token_path or not issuer or environment not in _ENVIRONMENTS:
            return None
        try:
            parsed = urlparse(url)
            issuer_parsed = urlparse(issuer)
            endpoint_base = internal_base or issuer
            endpoint_parsed = urlparse(endpoint_base)
            private_internal_http = True
            if internal_base and endpoint_parsed.scheme == "http":
                hostname = (endpoint_parsed.hostname or "").lower()
                try:
                    address = ipaddress.ip_address(hostname)
                    private_internal_http = address.is_private or address.is_loopback
                except ValueError:
                    # Reject numeric legacy IP spellings and malformed labels;
                    # only a valid single-label service-discovery hostname may
                    # use cleartext on the isolated container network.
                    private_internal_http = bool(
                        _INTERNAL_SERVICE_HOST.fullmatch(hostname)
                    )
            invalid = (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or issuer_parsed.scheme not in {"http", "https"}
                or not issuer_parsed.hostname
                or issuer_parsed.username
                or issuer_parsed.password
                or issuer_parsed.query
                or issuer_parsed.fragment
                or (environment == "production" and issuer_parsed.scheme != "https")
                or endpoint_parsed.scheme not in {"http", "https"}
                or not endpoint_parsed.hostname
                or endpoint_parsed.username
                or endpoint_parsed.password
                or endpoint_parsed.query
                or endpoint_parsed.fragment
                or endpoint_parsed.path not in {"", "/"}
                or not private_internal_http
                or (
                    parsed.scheme,
                    parsed.hostname.lower(),
                    parsed.port or (443 if parsed.scheme == "https" else 80),
                )
                != (
                    endpoint_parsed.scheme,
                    endpoint_parsed.hostname.lower(),
                    endpoint_parsed.port
                    or (443 if endpoint_parsed.scheme == "https" else 80),
                )
                or url.rstrip("/")
                != f"{endpoint_base}/v1/internal/notifications/opinion"
                or (
                    environment == "production"
                    and not internal_base
                    and parsed.scheme != "https"
                )
            )
        except ValueError:
            return None
        if invalid:
            return None
        token = _load_internal_token(token_path)
        if token is None:
            return None
        return cls(notification_url=url, service_token=token)

    def send_opinion(
        self,
        owner_actor_id: str,
        scan_id: str,
        disease: str,
        note: str | None,
        nickname: str,
    ) -> bool:
        try:
            actor = str(uuid.UUID(owner_actor_id))
        except (TypeError, ValueError, AttributeError):
            return False
        document = {
            "actor_id": actor,
            "scan_id": str(scan_id)[:128],
            "disease": str(disease)[:64],
            "note": (note or "").strip()[:240],
            "nickname": str(nickname).strip()[:64],
        }
        request = urllib.request.Request(
            self.notification_url,
            data=json.dumps(document, separators=(",", ":")).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Internal-Service-Token": self.service_token,
                "User-Agent": "plantdiseases-app-api",
            },
            method="POST",
        )
        try:
            with _open_no_redirect(request, timeout=self.timeout) as response:
                body = response.read(_MAX_RESPONSE_BYTES + 1)
                return response.status == 202 and len(body) <= _MAX_RESPONSE_BYTES
        except (OSError, urllib.error.URLError):
            return False


account_internal_client = AccountInternalClient.from_env()
_internal_service_token = _load_internal_token(
    os.environ.get("INTERNAL_SERVICE_TOKEN_FILE", "").strip()
)


def internal_service_authorized(supplied: str) -> bool:
    """Constant-time check of the private service credential.

    ``compare_digest`` raises TypeError on non-ASCII *str* operands, and
    Starlette decodes header bytes before this sees them, so an unauthenticated
    request carrying one high byte in ``X-Internal-Service-Token`` used to
    escape as a 500 with a traceback rather than the stable 401 the internal
    routes promise. ``_load_internal_token`` reads the credential as ASCII, so
    a non-ASCII candidate can only ever be wrong: reject it first.
    """
    token = _internal_service_token
    return bool(
        token
        and supplied
        and len(supplied) <= 512
        and supplied.isascii()
        and token.isascii()
        and secrets.compare_digest(token, supplied)
    )


def send_account_opinion_notification(
    owner_actor_id: str,
    scan_id: str,
    disease: str,
    note: str | None,
    nickname: str,
) -> bool:
    if account_internal_client is None:
        return False
    return account_internal_client.send_opinion(
        owner_actor_id, scan_id, disease, note, nickname
    )


__all__ = [
    "AccountInternalClient",
    "account_internal_client",
    "internal_service_authorized",
    "send_account_opinion_notification",
]
