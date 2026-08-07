"""Private account-service calls made by the community API."""

from __future__ import annotations

import json
import os
import secrets
import stat
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlparse

_ENVIRONMENTS = frozenset({"test", "development", "staging", "production"})


def _load_internal_token(token_path: str) -> str | None:
    if not token_path:
        return None
    path = Path(token_path)
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
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
        if not url or not token_path or not issuer or environment not in _ENVIRONMENTS:
            return None
        try:
            parsed = urlparse(url)
            issuer_parsed = urlparse(issuer)
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
                or (
                    parsed.scheme,
                    parsed.hostname.lower(),
                    parsed.port or (443 if parsed.scheme == "https" else 80),
                )
                != (
                    issuer_parsed.scheme,
                    issuer_parsed.hostname.lower(),
                    issuer_parsed.port
                    or (443 if issuer_parsed.scheme == "https" else 80),
                )
                or (environment == "production" and parsed.scheme != "https")
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
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                response.read(4097)
                return response.status == 202
        except (OSError, urllib.error.URLError):
            return False


account_internal_client = AccountInternalClient.from_env()
_internal_service_token = _load_internal_token(
    os.environ.get("INTERNAL_SERVICE_TOKEN_FILE", "").strip()
)


def internal_service_authorized(supplied: str) -> bool:
    return bool(
        _internal_service_token
        and supplied
        and len(supplied) <= 512
        and secrets.compare_digest(_internal_service_token, supplied)
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
