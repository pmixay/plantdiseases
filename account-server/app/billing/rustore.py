"""Minimal, strict RuStore Public API V5 client for Pay SDK subscriptions."""

from __future__ import annotations

import base64
import json
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable
from urllib.parse import quote

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

if TYPE_CHECKING:
    from app.config import Settings

_AUTH_URL = "https://public-api.rustore.ru/public/auth/"
_PRODUCTION_SUBSCRIPTION_BASE = "https://public-api.rustore.ru/public/v5/subscription"
_SANDBOX_SUBSCRIPTION_BASE = (
    "https://public-api.rustore.ru/public/sandbox/v5/subscription"
)
_PRODUCT_ID = re.compile(r"[A-Za-z0-9._-]{1,128}\Z")
_ORDER_ID = re.compile(r"[\x20-\x7E]{1,256}\Z")
_PACKAGE_NAME = re.compile(
    r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+\Z"
)
_MAX_RESPONSE_BYTES = 32 * 1024


class RuStoreUnavailable(Exception):
    """RuStore could not provide a trustworthy canonical result."""


class RuStorePurchaseNotFound(Exception):
    """The requested subscription purchase does not exist."""


@dataclass(frozen=True, slots=True)
class VerifiedSubscription:
    product_id: str
    purchase_id: uuid.UUID
    sandbox: bool
    start_at: datetime
    expires_at: datetime
    auto_renewing: bool
    payment_state: int | None
    acknowledgement_state: str
    external_account_id: str | None
    order_id: str
    price_currency_code: str
    price_amount_micros: int
    country_code: str

    @property
    def access_active(self) -> bool:
        now = datetime.now(UTC)
        return self.start_at <= now < self.expires_at and self.payment_state in {1, 2}


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _open_no_redirect(request: urllib.request.Request, *, timeout: float):
    return urllib.request.build_opener(_NoRedirectHandler()).open(
        request,
        timeout=timeout,
    )


def load_private_key(path: Path) -> rsa.RSAPrivateKey:
    """Load the Base64 PKCS#8 key exported by RuStore Console."""
    try:
        encoded = path.read_text(encoding="ascii").strip()
        raw = base64.b64decode(encoded, validate=True)
        key = serialization.load_der_private_key(raw, password=None)
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise ValueError("invalid RuStore private key file") from exc
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
        raise ValueError("RuStore private key must be RSA with at least 2048 bits")
    return key


class RuStoreSubscriptionClient:
    def __init__(
        self,
        *,
        key_id: str,
        private_key: rsa.RSAPrivateKey,
        package_name: str,
        allowed_products: tuple[str, ...],
        timeout_seconds: float = 3.0,
        opener: Callable[..., object] = _open_no_redirect,
        monotonic: Callable[[], float] = time.monotonic,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        if (
            not key_id.isascii()
            or not key_id.isdecimal()
            or not 1 <= len(key_id) <= 32
            or int(key_id) <= 0
        ):
            raise ValueError("RuStore key ID must be decimal")
        if _PACKAGE_NAME.fullmatch(package_name) is None:
            raise ValueError("invalid RuStore package name")
        if (
            not allowed_products
            or len(set(allowed_products)) != len(allowed_products)
            or any(_PRODUCT_ID.fullmatch(item) is None for item in allowed_products)
        ):
            raise ValueError("invalid RuStore subscription product allowlist")
        self.key_id = key_id
        self.private_key = private_key
        self.package_name = package_name
        self.allowed_products = frozenset(allowed_products)
        self.timeout_seconds = max(0.5, min(float(timeout_seconds), 10.0))
        self._opener = opener
        self._monotonic = monotonic
        self._clock = clock
        self._token_lock = threading.Lock()
        self._public_token: str | None = None
        self._public_token_expires_at = 0.0

    def validate_subscription(
        self,
        *,
        product_id: str,
        purchase_id: uuid.UUID,
        sandbox: bool,
    ) -> VerifiedSubscription:
        if product_id not in self.allowed_products:
            raise ValueError("subscription product is not allowlisted")
        purchase_id = uuid.UUID(str(purchase_id))
        base = _SANDBOX_SUBSCRIPTION_BASE if sandbox else _PRODUCTION_SUBSCRIPTION_BASE
        url = "/".join(
            (
                base,
                quote(self.package_name, safe=""),
                quote(product_id, safe=""),
                str(purchase_id),
            )
        )
        for attempt in range(2):
            token = self._authorization_token()
            request = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/json",
                    "Public-Token": token,
                    "User-Agent": "plantdiseases-account-api",
                },
                method="GET",
            )
            try:
                payload = self._read_json(request)
                return self._parse_subscription(
                    payload,
                    product_id=product_id,
                    purchase_id=purchase_id,
                    sandbox=sandbox,
                )
            except urllib.error.HTTPError as exc:
                if exc.code in {401, 403} and attempt == 0:
                    self._invalidate_token(token)
                    continue
                if exc.code == 404:
                    raise RuStorePurchaseNotFound from None
                raise RuStoreUnavailable from None
            except RuStorePurchaseNotFound:
                raise
            except (OSError, UnicodeError, ValueError, TypeError, urllib.error.URLError):
                raise RuStoreUnavailable from None
        raise RuStoreUnavailable

    def _authorization_token(self) -> str:
        with self._token_lock:
            now = self._monotonic()
            if self._public_token and now < self._public_token_expires_at:
                return self._public_token
            timestamp = self._clock().astimezone(UTC).isoformat(timespec="milliseconds")
            message = f"{self.key_id}{timestamp}".encode("utf-8")
            signature = self.private_key.sign(
                message,
                padding.PKCS1v15(),
                hashes.SHA512(),
            )
            request = urllib.request.Request(
                _AUTH_URL,
                data=json.dumps(
                    {
                        "keyId": self.key_id,
                        "timestamp": timestamp,
                        "signature": base64.b64encode(signature).decode("ascii"),
                    },
                    separators=(",", ":"),
                ).encode("utf-8"),
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "plantdiseases-account-api",
                },
                method="POST",
            )
            try:
                payload = self._read_json(request)
                if not isinstance(payload, dict) or payload.get("code") != "OK":
                    raise ValueError
                body = payload.get("body")
                if not isinstance(body, dict) or set(body) != {"jwe", "ttl"}:
                    raise ValueError
                token = body["jwe"]
                ttl = body["ttl"]
                if (
                    not isinstance(token, str)
                    or not 32 <= len(token) <= 8192
                    or type(ttl) is not int
                    or not 60 <= ttl <= 3600
                ):
                    raise ValueError
            except (OSError, UnicodeError, ValueError, TypeError, urllib.error.URLError):
                raise RuStoreUnavailable from None
            self._public_token = token
            # RuStore currently returns 900 seconds. Refresh early so a token
            # cannot expire between this cache check and the validation call.
            self._public_token_expires_at = now + max(30, ttl - 30)
            return token

    def _invalidate_token(self, token: str) -> None:
        with self._token_lock:
            if self._public_token == token:
                self._public_token = None
                self._public_token_expires_at = 0.0

    def _read_json(self, request: urllib.request.Request) -> object:
        with self._opener(request, timeout=self.timeout_seconds) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
            if response.status != 200 or len(raw) > _MAX_RESPONSE_BYTES:
                raise ValueError
        return json.loads(raw)

    @staticmethod
    def _parse_subscription(
        payload: object,
        *,
        product_id: str,
        purchase_id: uuid.UUID,
        sandbox: bool,
    ) -> VerifiedSubscription:
        if not isinstance(payload, dict):
            raise ValueError
        if payload.get("code") != "OK":
            message = payload.get("message")
            if message == "Purchase not found":
                raise RuStorePurchaseNotFound
            raise ValueError
        body = payload.get("body")
        if not isinstance(body, dict):
            raise ValueError
        try:
            start_raw = body["startTimeMillis"]
            expiry_raw = body["expiryTimeMillis"]
            auto_renewing = body["autoRenewing"]
            payment_state = body.get("paymentState")
            acknowledgement_state = body["acknowledgementState"]
            external_account_id = body.get("externalAccountId")
            order_id = body["orderId"]
            price_currency_code = body["priceCurrencyCode"]
            price_raw = body["priceAmountMicros"]
            country_code = body["countryCode"]
            kind = body["kind"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError from exc
        if (
            not isinstance(start_raw, str)
            or not 1 <= len(start_raw) <= 19
            or not start_raw.isdecimal()
            or not isinstance(expiry_raw, str)
            or not 1 <= len(expiry_raw) <= 19
            or not expiry_raw.isdecimal()
            or type(auto_renewing) is not bool
            or (
                payment_state is not None
                and (type(payment_state) is not int or payment_state not in {0, 1, 2})
            )
            or acknowledgement_state
            not in {"PENDING", "ACKNOWLEDGED", "UNKNOWN"}
            or (
                external_account_id is not None
                and (
                    not isinstance(external_account_id, str)
                    or not 1 <= len(external_account_id) <= 36
                )
            )
            or not isinstance(order_id, str)
            or _ORDER_ID.fullmatch(order_id) is None
            or not isinstance(price_currency_code, str)
            or re.fullmatch(r"[A-Z]{3}", price_currency_code) is None
            or not isinstance(price_raw, str)
            or not 1 <= len(price_raw) <= 18
            or not price_raw.isdecimal()
            or not isinstance(country_code, str)
            or re.fullmatch(r"[A-Z]{2}", country_code) is None
            or kind != "androidpublisher#subscriptionPurchase"
        ):
            raise ValueError
        if external_account_id is not None:
            try:
                if str(uuid.UUID(external_account_id)) != external_account_id:
                    raise ValueError
            except (ValueError, AttributeError):
                raise ValueError from None
        try:
            start_ms = int(start_raw)
            expiry_ms = int(expiry_raw)
            price_amount_micros = int(price_raw)
            if not 0 < start_ms < expiry_ms or price_amount_micros <= 0:
                raise ValueError
            start_at = datetime.fromtimestamp(start_ms / 1000, tz=UTC)
            expires_at = datetime.fromtimestamp(expiry_ms / 1000, tz=UTC)
        except (OverflowError, OSError, ValueError) as exc:
            raise ValueError from exc
        return VerifiedSubscription(
            product_id=product_id,
            purchase_id=purchase_id,
            sandbox=sandbox,
            start_at=start_at,
            expires_at=expires_at,
            auto_renewing=auto_renewing,
            payment_state=payment_state,
            acknowledgement_state=acknowledgement_state,
            external_account_id=external_account_id,
            order_id=order_id,
            price_currency_code=price_currency_code,
            price_amount_micros=price_amount_micros,
            country_code=country_code,
        )


def build_rustore_client(settings: Settings) -> RuStoreSubscriptionClient | None:
    # The canonical client belongs to subscription lifecycle processing, not
    # checkout.  Operators may stop new sales while callbacks and scheduled
    # reconciliation continue to revoke/renew existing access.
    if not settings.rustore_lifecycle_enabled:
        return None
    if (
        settings.rustore_key_id is None
        or settings.rustore_private_key_file is None
    ):
        raise ValueError("incomplete RuStore billing configuration")
    return RuStoreSubscriptionClient(
        key_id=settings.rustore_key_id,
        private_key=load_private_key(settings.rustore_private_key_file),
        package_name=settings.rustore_package_name,
        allowed_products=settings.rustore_products,
    )
