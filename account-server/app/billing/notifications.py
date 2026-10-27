"""Decrypt and minimally validate RuStore Pay SDK server notifications."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

if TYPE_CHECKING:
    from app.config import Settings

_MAX_ENCRYPTED_BYTES = 64 * 1024
_NOTIFICATION_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")


@dataclass(frozen=True, slots=True)
class TestNotification:
    notification_id: str
    payload_fingerprint: str
    sandbox: bool
    key_epoch: str = "legacy-v1"


@dataclass(frozen=True, slots=True)
class SubscriptionNotification:
    notification_id: str
    payload_fingerprint: str
    sandbox: bool
    product_id: str
    purchase_id: uuid.UUID
    event_at: datetime
    event_type: str
    status_new: str
    period_new: str
    key_epoch: str = "legacy-v1"


_SUBSCRIPTION_EVENT_TYPES = frozenset(
    {"ACTIVATED", "RENEWED", "CANCELLED", "RESUMED", "PAYMENT_FAILED", "CLOSED"}
)
_SUBSCRIPTION_STATUSES = frozenset({"ACTIVE", "PAUSED", "CLOSED", "TERMINATED"})
_SUBSCRIPTION_PERIODS = frozenset({"TRIAL", "PROMO", "MAIN", "GRACE", "HOLD"})


def load_notification_key(path: Path) -> bytes:
    try:
        encoded = path.read_text(encoding="ascii").strip()
        key = base64.b64decode(encoded, validate=True)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("invalid RuStore notification key file") from exc
    if len(key) != 32:
        raise ValueError("RuStore notification key must be 256 bits")
    return key


class RuStoreNotificationDecoder:
    def __init__(
        self,
        *,
        application_id: int,
        key: bytes,
        allowed_products: tuple[str, ...],
        key_epoch: str = "legacy-v1",
    ):
        if (
            application_id <= 0
            or len(key) != 32
            or not allowed_products
            or not 1 <= len(key_epoch) <= 64
        ):
            raise ValueError("invalid RuStore notification configuration")
        self.application_id = application_id
        self._cipher = AESGCM(key)
        self.allowed_products = frozenset(allowed_products)
        self.key_epoch = key_epoch

    def decode(
        self,
        envelope: object,
    ) -> TestNotification | SubscriptionNotification:
        if not isinstance(envelope, dict) or set(envelope) != {"id", "timestamp", "payload"}:
            raise ValueError("invalid notification envelope")
        notification_id = envelope["id"]
        timestamp = envelope["timestamp"]
        payload = envelope["payload"]
        if (
            not isinstance(notification_id, str)
            or _NOTIFICATION_ID.fullmatch(notification_id) is None
            or not isinstance(timestamp, str)
            or not 1 <= len(timestamp) <= 64
            or not isinstance(payload, str)
            or not 1 <= len(payload) <= _MAX_ENCRYPTED_BYTES * 2
        ):
            raise ValueError("invalid notification envelope")
        try:
            encrypted = base64.b64decode(payload, validate=True)
            if not 12 + 16 <= len(encrypted) <= _MAX_ENCRYPTED_BYTES:
                raise ValueError
            clear = self._cipher.decrypt(encrypted[:12], encrypted[12:], None)
            document = json.loads(clear)
        except (InvalidTag, UnicodeError, ValueError, json.JSONDecodeError):
            raise ValueError("invalid notification payload") from None
        if not isinstance(document, dict):
            raise ValueError("invalid notification payload")
        kind = document.get("notification_type")
        app_id = document.get("app_id")
        data = document.get("data")
        if type(app_id) is not int or app_id != self.application_id or not isinstance(data, str):
            raise ValueError("invalid notification payload")
        sandbox = kind in {"TEST_EVENT_SANDBOX", "SUBSCRIPTION_EVENT_SANDBOX"}
        # RuStore encrypts only ``payload``; the outer notification ID and
        # timestamp are not authenticated by AES-GCM.  Deduplicate on the
        # authenticated cleartext so a captured callback cannot be replayed
        # with attacker-selected outer IDs to force unbounded Public API calls.
        payload_fingerprint = hashlib.sha256(clear).hexdigest()
        if kind in {"TEST_EVENT", "TEST_EVENT_SANDBOX"}:
            try:
                test_data = json.loads(data)
            except (json.JSONDecodeError, TypeError):
                raise ValueError("invalid test notification") from None
            if test_data != {"test": "TEST"}:
                raise ValueError("invalid test notification")
            return TestNotification(
                notification_id=notification_id,
                payload_fingerprint=payload_fingerprint,
                sandbox=sandbox,
                key_epoch=self.key_epoch,
            )
        if kind not in {"SUBSCRIPTION_EVENT", "SUBSCRIPTION_EVENT_SANDBOX"}:
            raise ValueError("unsupported notification type")
        try:
            subscription = json.loads(data)
            product_id = subscription["product_code"]
            purchase_id = uuid.UUID(subscription["purchase_id"])
            event_raw = subscription["event_time"]
            event_type = subscription["subscription_event_type"]
            status_new = subscription["status_new"]
            period_new = subscription["period_new"]
            auto_renewing = subscription["autorenewing"]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError, AttributeError):
            raise ValueError("invalid subscription notification") from None
        try:
            if not isinstance(event_raw, str) or not 1 <= len(event_raw) <= 64:
                raise ValueError
            event_at = datetime.fromisoformat(event_raw.replace("Z", "+00:00"))
            if event_at.tzinfo is None:
                raise ValueError
            event_at = event_at.astimezone(UTC)
        except ValueError:
            raise ValueError("invalid subscription notification") from None
        if (
            product_id not in self.allowed_products
            or event_type not in _SUBSCRIPTION_EVENT_TYPES
            or status_new not in _SUBSCRIPTION_STATUSES
            or period_new not in _SUBSCRIPTION_PERIODS
            or type(auto_renewing) is not bool
        ):
            raise ValueError("invalid subscription notification")
        return SubscriptionNotification(
            notification_id=notification_id,
            payload_fingerprint=payload_fingerprint,
            sandbox=sandbox,
            product_id=product_id,
            purchase_id=purchase_id,
            event_at=event_at,
            event_type=event_type,
            status_new=status_new,
            period_new=period_new,
            key_epoch=self.key_epoch,
        )


def build_notification_decoder(settings: Settings) -> RuStoreNotificationDecoder | None:
    if not settings.rustore_lifecycle_enabled:
        return None
    if (
        settings.rustore_application_id is None
        or settings.rustore_notification_key_file is None
    ):
        raise ValueError("incomplete RuStore notification configuration")
    return RuStoreNotificationDecoder(
        application_id=int(settings.rustore_application_id),
        key=load_notification_key(settings.rustore_notification_key_file),
        allowed_products=settings.rustore_products,
        key_epoch=settings.rustore_notification_key_epoch,
    )
