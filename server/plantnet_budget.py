"""Fail-closed client for the shared PlantNet attempt/cost authority."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlparse

try:
    from .account_auth import _open_no_redirect
    from .account_internal import _load_internal_token
except ImportError:  # flat Docker/module import
    from account_auth import _open_no_redirect
    from account_internal import _load_internal_token

_ENVIRONMENTS = frozenset({"test", "development", "staging", "production"})
_INTERNAL_SERVICE_HOST = re.compile(r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_POLICY_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_MAX_RESPONSE_BYTES = 4096


class PlantNetBudgetUnavailable(Exception):
    """No authoritative reservation decision could be obtained."""


@dataclass(frozen=True, slots=True)
class PlantNetBudgetDecision:
    allowed: bool
    replayed: bool
    policy_version: str
    usage_date: str
    attempt_limit: int
    attempts_used: int
    remaining_attempts: int
    attempt_cost_micros: int
    cost_limit_micros: int
    cost_exposure_micros: int
    remaining_cost_micros: int
    reason: str | None


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


class PlantNetBudgetClient:
    def __init__(self, *, reserve_url: str, service_token: str, timeout: float = 2.0):
        self.reserve_url = reserve_url
        self.service_token = service_token
        self.timeout = max(0.1, min(float(timeout), 10.0))

    @classmethod
    def from_env(cls) -> PlantNetBudgetClient | None:
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
            reserve_url=(
                f"{endpoint_base}/v1/internal/vendor-budgets/plantnet/reserve"
            ),
            service_token=token,
        )

    def reserve(self, *, request_id: str) -> PlantNetBudgetDecision:
        try:
            operation = str(uuid.UUID(request_id))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("invalid PlantNet attempt identity") from exc
        request = urllib.request.Request(
            self.reserve_url,
            data=json.dumps(
                {"request_id": operation}, separators=(",", ":")
            ).encode("utf-8"),
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
                    raise PlantNetBudgetUnavailable
            return self._parse_decision(json.loads(raw))
        except PlantNetBudgetUnavailable:
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
            raise PlantNetBudgetUnavailable from None

    @staticmethod
    def _parse_decision(payload: object) -> PlantNetBudgetDecision:
        expected_keys = {
            "allowed",
            "replayed",
            "policy_version",
            "usage_date",
            "attempt_limit",
            "attempts_used",
            "remaining_attempts",
            "attempt_cost_micros",
            "cost_limit_micros",
            "cost_exposure_micros",
            "remaining_cost_micros",
            "reason",
        }
        if not isinstance(payload, dict) or set(payload) != expected_keys:
            raise ValueError("invalid PlantNet budget response")
        allowed = payload["allowed"]
        replayed = payload["replayed"]
        policy_version = payload["policy_version"]
        usage_date = payload["usage_date"]
        attempt_limit = payload["attempt_limit"]
        attempts_used = payload["attempts_used"]
        remaining_attempts = payload["remaining_attempts"]
        attempt_cost = payload["attempt_cost_micros"]
        cost_limit = payload["cost_limit_micros"]
        cost_exposure = payload["cost_exposure_micros"]
        remaining_cost = payload["remaining_cost_micros"]
        reason = payload["reason"]
        integers = (
            attempt_limit,
            attempts_used,
            remaining_attempts,
            attempt_cost,
            cost_limit,
            cost_exposure,
            remaining_cost,
        )
        if (
            type(allowed) is not bool
            or type(replayed) is not bool
            or not isinstance(policy_version, str)
            or _POLICY_VERSION.fullmatch(policy_version) is None
            or not isinstance(usage_date, str)
            or any(type(value) is not int for value in integers)
            or attempt_limit <= 0
            or not 0 <= attempts_used <= attempt_limit
            or remaining_attempts != attempt_limit - attempts_used
            or attempt_cost <= 0
            or cost_limit <= 0
            or attempt_cost > cost_limit
            or not 0 <= cost_exposure <= cost_limit
            or remaining_cost != cost_limit - cost_exposure
            or reason not in {None, "attempt_limit", "cost_limit"}
            or (allowed and reason is not None)
            or (not allowed and reason is None)
            or (allowed and (attempts_used == 0 or cost_exposure < attempt_cost))
            or (reason == "attempt_limit" and attempts_used != attempt_limit)
            or (reason == "cost_limit" and remaining_cost >= attempt_cost)
        ):
            raise ValueError("invalid PlantNet budget response")
        try:
            date.fromisoformat(usage_date)
        except ValueError:
            raise ValueError("invalid PlantNet budget response") from None
        return PlantNetBudgetDecision(
            allowed=allowed,
            replayed=replayed,
            policy_version=policy_version,
            usage_date=usage_date,
            attempt_limit=attempt_limit,
            attempts_used=attempts_used,
            remaining_attempts=remaining_attempts,
            attempt_cost_micros=attempt_cost,
            cost_limit_micros=cost_limit,
            cost_exposure_micros=cost_exposure,
            remaining_cost_micros=remaining_cost,
            reason=reason,
        )


plantnet_budget_client = PlantNetBudgetClient.from_env()


__all__ = [
    "PlantNetBudgetClient",
    "PlantNetBudgetDecision",
    "PlantNetBudgetUnavailable",
    "plantnet_budget_client",
]
