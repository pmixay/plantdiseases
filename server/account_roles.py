"""Fail-closed client for community roles and rewards in the account service.

Roles (expert, moderator, admin) live in the account service. The app API asks
for an actor's roles before every moderation action and caches the answer for
``ROLE_CACHE_SECONDS``, so a revoked role stops working within that window.
Achievement counts are reported back so the account service can derive the
PRO discount. Nothing here is trusted from a client.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from urllib.parse import urlparse

try:
    from .account_auth import _open_no_redirect
    from .account_entitlements import _ENVIRONMENTS, _private_endpoint_base
    from .account_internal import _load_internal_token
except ImportError:  # flat Docker/module import
    from account_auth import _open_no_redirect
    from account_entitlements import _ENVIRONMENTS, _private_endpoint_base
    from account_internal import _load_internal_token

ROLE_CACHE_SECONDS = 60
_MAX_RESPONSE_BYTES = 64 * 1024
_ROLES = frozenset({"expert", "moderator", "admin"})


class RolesUnavailable(Exception):
    """The account service could not be asked; moderation fails closed."""


class RoleChangeRefused(Exception):
    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True, slots=True)
class Role:
    role: str
    scope: str


def can_act(roles: list[Role], allowed: set[str], space: str | None = None) -> bool:
    """Admins act everywhere; other roles only in their scope (``*`` = all)."""
    for item in roles:
        if item.role == "admin":
            return True
        if item.role in allowed and (item.scope == "*" or space is None or item.scope == space):
            return True
    return False


def scopes_for(roles: list[Role], allowed: set[str]) -> set[str] | None:
    """Spaces an actor may moderate; ``None`` means every space."""
    scopes: set[str] = set()
    for item in roles:
        if item.role == "admin" or (item.role in allowed and item.scope == "*"):
            return None
        if item.role in allowed:
            scopes.add(item.scope)
    return scopes


class CommunityRolesClient:
    def __init__(self, *, base_url: str, service_token: str, timeout: float = 2.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.service_token = service_token
        self.timeout = max(0.1, min(float(timeout), 10.0))
        self._cache: dict[str, tuple[float, list[Role]]] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> CommunityRolesClient | None:
        environment = os.environ.get("ENVIRONMENT", "").strip().lower()
        issuer = os.environ.get("ACCOUNT_ISSUER", "").strip().rstrip("/")
        internal_base = os.environ.get("ACCOUNT_INTERNAL_BASE_URL", "").strip()
        token_path = os.environ.get("INTERNAL_SERVICE_TOKEN_FILE", "").strip()
        if environment not in _ENVIRONMENTS or not issuer or not token_path:
            return None
        try:
            issuer_parsed = urlparse(issuer)
            if (
                issuer_parsed.scheme not in {"http", "https"}
                or not issuer_parsed.hostname
                or (environment == "production" and issuer_parsed.scheme != "https")
            ):
                return None
            base = _private_endpoint_base(internal_base or issuer)
        except ValueError:
            return None
        token = _load_internal_token(token_path)
        if token is None:
            return None
        return cls(base_url=base, service_token=token)

    def _post(self, path: str, document: dict) -> dict:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
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
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
                status = response.status
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read(_MAX_RESPONSE_BYTES))
                code = str(body["error"]["code"])[:64]
            except (ValueError, KeyError, TypeError, OSError):
                raise RolesUnavailable from None
            if exc.code in {403, 404, 409, 422}:
                raise RoleChangeRefused(code, exc.code) from None
            raise RolesUnavailable from None
        except (OSError, urllib.error.URLError):
            raise RolesUnavailable from None
        if status != 200 or len(raw) > _MAX_RESPONSE_BYTES:
            raise RolesUnavailable
        try:
            payload = json.loads(raw)
        except ValueError:
            raise RolesUnavailable from None
        if not isinstance(payload, dict):
            raise RolesUnavailable
        return payload

    def roles_for(self, actor_id: str) -> list[Role]:
        actor = str(uuid.UUID(actor_id))
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(actor)
            if cached and cached[0] > now:
                return list(cached[1])
        payload = self._post("/v1/internal/community/roles/lookup", {"actor_id": actor})
        roles = []
        try:
            for item in payload["roles"]:
                role, scope = str(item["role"]), str(item["scope"])
                if role in _ROLES and 1 <= len(scope) <= 32:
                    roles.append(Role(role, scope))
        except (KeyError, TypeError):
            raise RolesUnavailable from None
        with self._lock:
            if len(self._cache) > 5_000:
                self._cache.clear()
            self._cache[actor] = (now + ROLE_CACHE_SECONDS, roles)
        return list(roles)

    def forget(self, actor_id: str) -> None:
        with self._lock:
            self._cache.pop(actor_id, None)

    def holders(self) -> list[dict]:
        payload = self._post("/v1/internal/community/roles/list", {})
        try:
            return [
                {
                    "actor_id": str(uuid.UUID(item["actor_id"])),
                    "nickname": str(item["nickname"])[:48],
                    "role": str(item["role"]),
                    "scope": str(item["scope"])[:32],
                    "granted_at": str(item["granted_at"])[:40],
                }
                for item in payload["items"]
            ]
        except (KeyError, TypeError, ValueError):
            raise RolesUnavailable from None

    def change(self, *, grant: bool, actor_id: str, role: str, scope: str, by_actor_id: str) -> dict:
        path = "/v1/internal/community/roles/" + ("grant" if grant else "revoke")
        payload = self._post(
            path,
            {"actor_id": actor_id, "role": role, "scope": scope, "by_actor_id": by_actor_id},
        )
        self.forget(actor_id)
        return payload

    def report_achievements(self, actor_id: str, achievement_count: int) -> dict:
        return self._post(
            "/v1/internal/community/rewards",
            {"actor_id": str(uuid.UUID(actor_id)), "achievement_count": int(achievement_count)},
        )


community_roles_client = CommunityRolesClient.from_env()
