"""Fail-closed client for account-owned subscription quotas.

The inference service never trusts an Android billing result.  For signed-in
requests it asks the private account service to atomically authorize and
consume one unit before doing cost-bearing work.  The account service is the
only component that knows the canonical RuStore subscription state.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

try:
    from .account_auth import _open_no_redirect
    from .account_internal import _load_internal_token
except ImportError:  # flat Docker/module import
    from account_auth import _open_no_redirect
    from account_internal import _load_internal_token

ALLOWED_METERED_FEATURES = frozenset(
    {
        "gamma_analysis",
        "frontier_analysis",
        "plant_identification",
        "ai_doctor",
        "care_plan",
    }
)
_ENVIRONMENTS = frozenset({"test", "development", "staging", "production"})
_INTERNAL_SERVICE_HOST = re.compile(r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_MAX_RESPONSE_BYTES = 4096


class EntitlementUnavailable(Exception):
    """The authoritative account quota service could not be consulted."""


@dataclass(frozen=True, slots=True)
class EntitlementDecision:
    allowed: bool
    replayed: bool
    plan: str
    feature: str
    limit: int
    remaining: int
    reset_at: str
    reason: str | None = None
    policy_version: str = "legacy-hardcoded-v1"
    released: bool = False


def _private_endpoint_base(value: str) -> str:
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
        raise ValueError("invalid account service origin")
    hostname = parsed.hostname.lower()
    if parsed.scheme == "http":
        try:
            address = ipaddress.ip_address(hostname)
            private_host = address.is_private or address.is_loopback
        except ValueError:
            private_host = bool(_INTERNAL_SERVICE_HOST.fullmatch(hostname))
        if not private_host:
            raise ValueError("cleartext account service origin is not private")
    return value.rstrip("/")


class AccountEntitlementClient:
    def __init__(
        self,
        *,
        consume_url: str,
        service_token: str,
        timeout: float = 2.0,
        release_url: str | None = None,
    ):
        self.consume_url = consume_url
        self.release_url = release_url or consume_url.removesuffix("/consume") + "/release"
        self.service_token = service_token
        self.timeout = max(0.1, min(float(timeout), 10.0))

    @classmethod
    def from_env(cls) -> AccountEntitlementClient | None:
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
                or issuer_parsed.username
                or issuer_parsed.password
                or issuer_parsed.query
                or issuer_parsed.fragment
                or (environment == "production" and issuer_parsed.scheme != "https")
            ):
                return None
            endpoint_base = _private_endpoint_base(internal_base or issuer)
        except ValueError:
            return None
        token = _load_internal_token(token_path)
        if token is None:
            return None
        return cls(
            consume_url=f"{endpoint_base}/v1/internal/entitlements/consume",
            service_token=token,
        )

    def consume(
        self,
        *,
        actor_id: str,
        feature: str,
        request_id: str,
    ) -> EntitlementDecision:
        if feature not in ALLOWED_METERED_FEATURES:
            raise ValueError("unsupported metered feature")
        try:
            actor = str(uuid.UUID(actor_id))
            operation = str(uuid.UUID(request_id))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("invalid quota identity") from exc
        return self._request_decision(
            url=self.consume_url,
            actor_id=actor,
            feature=feature,
            request_id=operation,
        )

    def release(
        self,
        *,
        actor_id: str,
        feature: str,
        request_id: str,
    ) -> EntitlementDecision:
        """Idempotently return an unused account allowance reservation."""

        if feature not in ALLOWED_METERED_FEATURES:
            raise ValueError("unsupported metered feature")
        try:
            actor = str(uuid.UUID(actor_id))
            operation = str(uuid.UUID(request_id))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("invalid quota identity") from exc
        return self._request_decision(
            url=self.release_url,
            actor_id=actor,
            feature=feature,
            request_id=operation,
        )

    def _request_decision(
        self,
        *,
        url: str,
        actor_id: str,
        feature: str,
        request_id: str,
    ) -> EntitlementDecision:
        document = {
            "actor_id": actor_id,
            "feature": feature,
            "request_id": request_id,
        }
        request = urllib.request.Request(
            url,
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
                if response.status != 200 or len(raw) > _MAX_RESPONSE_BYTES:
                    raise EntitlementUnavailable
            payload = json.loads(raw)
            return self._parse_decision(payload, expected_feature=feature)
        except EntitlementUnavailable:
            raise
        except (
            OSError,
            UnicodeError,
            ValueError,
            KeyError,
            TypeError,
            json.JSONDecodeError,
            urllib.error.URLError,
        ):
            raise EntitlementUnavailable from None

    @staticmethod
    def _parse_decision(payload: object, *, expected_feature: str) -> EntitlementDecision:
        required_keys = {
            "allowed",
            "replayed",
            "plan",
            "feature",
            "limit",
            "remaining",
            "reset_at",
            "reason",
        }
        optional_keys = {"policy_version", "released"}
        if (
            not isinstance(payload, dict)
            or not required_keys.issubset(payload)
            or set(payload) - required_keys - optional_keys
        ):
            raise ValueError("invalid entitlement response")
        allowed = payload["allowed"]
        replayed = payload["replayed"]
        plan = payload["plan"]
        feature = payload["feature"]
        limit = payload["limit"]
        remaining = payload["remaining"]
        reset_at = payload["reset_at"]
        reason = payload["reason"]
        policy_version = payload.get("policy_version", "legacy-hardcoded-v1")
        released = payload.get("released", False)
        if (
            type(allowed) is not bool
            or type(replayed) is not bool
            or plan not in {"free", "pro"}
            or feature != expected_feature
            or type(limit) is not int
            or type(remaining) is not int
            or limit < 0
            or remaining < 0
            or remaining > limit
            or not isinstance(reset_at, str)
            or not 1 <= len(reset_at) <= 64
            or reason not in {None, "pro_required", "quota_exhausted"}
            or not isinstance(policy_version, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", policy_version)
            is None
            or type(released) is not bool
            or (released and (not allowed or not replayed))
            or (allowed and reason is not None)
            or (not allowed and reason is None)
            or (allowed and (limit == 0 or remaining >= limit))
            or (
                reason == "pro_required"
                and (plan != "free" or limit != 0 or remaining != 0)
            )
            or (reason == "quota_exhausted" and (limit == 0 or remaining != 0))
        ):
            raise ValueError("invalid entitlement response")
        try:
            reset = datetime.fromisoformat(reset_at.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("invalid entitlement response") from None
        if reset.tzinfo is None:
            raise ValueError("invalid entitlement response")
        return EntitlementDecision(
            allowed=allowed,
            replayed=replayed,
            plan=plan,
            feature=feature,
            limit=limit,
            remaining=remaining,
            reset_at=reset_at,
            reason=reason,
            policy_version=policy_version,
            released=released,
        )


account_entitlement_client = AccountEntitlementClient.from_env()


__all__ = [
    "ALLOWED_METERED_FEATURES",
    "AccountEntitlementClient",
    "EntitlementDecision",
    "EntitlementUnavailable",
    "account_entitlement_client",
]
