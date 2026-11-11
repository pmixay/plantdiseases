from __future__ import annotations

import base64
import json
import os
import uuid

import pytest
from app.billing.notifications import (
    RuStoreNotificationDecoder,
    SubscriptionNotification,
)
from app.billing.notifications import (
    TestNotification as DecodedTestNotification,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _envelope(key: bytes, payload: dict) -> dict:
    nonce = os.urandom(12)
    encrypted = nonce + AESGCM(key).encrypt(
        nonce,
        json.dumps(payload, separators=(",", ":")).encode(),
        None,
    )
    return {
        "id": "notification-1",
        "timestamp": "2026-08-26T12:00:00.000+0000",
        "payload": base64.b64encode(encrypted).decode(),
    }


def test_decodes_subscription_event_without_trusting_its_status():
    key = os.urandom(32)
    purchase_id = uuid.uuid4()
    decoder = RuStoreNotificationDecoder(
        application_id=12345,
        key=key,
        allowed_products=("plantdiseases_pro_monthly",),
    )
    envelope = _envelope(
        key,
        {
            "app_id": 12345,
            "notification_type": "SUBSCRIPTION_EVENT",
            "data": json.dumps(
                {
                    "product_code": "plantdiseases_pro_monthly",
                    "purchase_id": str(purchase_id),
                    "event_time": "2026-08-26T12:00:00Z",
                    "subscription_event_type": "ACTIVATED",
                    "status_new": "ACTIVE",
                    "period_new": "MAIN",
                    "autorenewing": True,
                }
            ),
        },
    )

    result = decoder.decode(envelope)

    assert isinstance(result, SubscriptionNotification)
    assert result.purchase_id == purchase_id
    assert result.sandbox is False
    assert len(result.payload_fingerprint) == 64


def test_outer_id_is_not_part_of_authenticated_payload_fingerprint():
    key = os.urandom(32)
    decoder = RuStoreNotificationDecoder(
        application_id=12345,
        key=key,
        allowed_products=("plantdiseases_pro_monthly",),
    )
    payload = {
        "app_id": 12345,
        "notification_type": "SUBSCRIPTION_EVENT",
        "data": json.dumps(
            {
                "product_code": "plantdiseases_pro_monthly",
                "purchase_id": str(uuid.uuid4()),
                "event_time": "2026-08-26T12:00:00Z",
                "subscription_event_type": "PAYMENT_FAILED",
                "status_new": "ACTIVE",
                "period_new": "GRACE",
                "autorenewing": True,
            }
        ),
    }
    envelope = _envelope(key, payload)
    replay = {**envelope, "id": "attacker-selected-new-id"}

    assert decoder.decode(envelope).payload_fingerprint == decoder.decode(
        replay
    ).payload_fingerprint


def test_decodes_sandbox_connection_test():
    key = os.urandom(32)
    decoder = RuStoreNotificationDecoder(
        application_id=12345,
        key=key,
        allowed_products=("plantdiseases_pro_monthly",),
    )

    result = decoder.decode(
        _envelope(
            key,
            {
                "app_id": 12345,
                "notification_type": "TEST_EVENT_SANDBOX",
                "data": json.dumps({"test": "TEST"}),
            },
        )
    )

    assert isinstance(result, DecodedTestNotification)
    assert result.sandbox is True


def test_wrong_application_or_key_is_rejected():
    key = os.urandom(32)
    decoder = RuStoreNotificationDecoder(
        application_id=12345,
        key=key,
        allowed_products=("plantdiseases_pro_monthly",),
    )
    envelope = _envelope(
        os.urandom(32),
        {
            "app_id": 99999,
            "notification_type": "TEST_EVENT",
            "data": json.dumps({"test": "TEST"}),
        },
    )

    with pytest.raises(ValueError):
        decoder.decode(envelope)
