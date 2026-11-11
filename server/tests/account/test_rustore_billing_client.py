from __future__ import annotations

import base64
import copy
import io
import json
import uuid
from datetime import UTC, datetime

import pytest
from app.billing.rustore import RuStoreSubscriptionClient, RuStoreUnavailable
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class _Response(io.BytesIO):
    def __init__(self, document: dict, *, status: int = 200):
        super().__init__(json.dumps(document).encode())
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class _QueueOpener:
    def __init__(self, *documents: dict):
        self.documents = list(documents)
        self.requests = []

    def __call__(self, request, *, timeout):  # noqa: ANN001
        assert timeout == 2.0
        self.requests.append(request)
        return _Response(self.documents.pop(0))


def _auth_response():
    return {"code": "OK", "message": None, "body": {"jwe": "j" * 64, "ttl": 900}}


def _subscription_response(*, external_account_id=None):
    if external_account_id is None:
        external_account_id = str(uuid.uuid4())
    return {
        "code": "OK",
        "message": "Successful result",
        "body": {
            "startTimeMillis": "1787700000000",
            "expiryTimeMillis": "1790378400000",
            "autoRenewing": True,
            "paymentState": 1,
            "acknowledgementState": "ACKNOWLEDGED",
            "externalAccountId": external_account_id,
            "orderId": "invoice..0",
            "priceCurrencyCode": "RUB",
            "priceAmountMicros": "199000000",
            "countryCode": "RU",
            "kind": "androidpublisher#subscriptionPurchase",
        },
    }


def test_validates_subscription_with_fresh_signed_public_token():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    external_account_id = str(uuid.uuid4())
    opener = _QueueOpener(
        _auth_response(),
        _subscription_response(external_account_id=external_account_id),
    )
    timestamp = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    client = RuStoreSubscriptionClient(
        key_id="12345",
        private_key=key,
        package_name="com.plantdiseases.app",
        allowed_products=("plantdiseases_pro_monthly",),
        timeout_seconds=2,
        opener=opener,
        clock=lambda: timestamp,
    )
    purchase_id = uuid.uuid4()

    result = client.validate_subscription(
        product_id="plantdiseases_pro_monthly",
        purchase_id=purchase_id,
        sandbox=False,
    )

    assert result.purchase_id == purchase_id
    assert result.external_account_id == external_account_id
    assert result.auto_renewing is True
    auth_request, validation_request = opener.requests
    auth_body = json.loads(auth_request.data)
    assert auth_body["keyId"] == "12345"
    assert auth_body["timestamp"] == "2026-08-26T12:00:00.000+00:00"
    key.public_key().verify(
        base64.b64decode(auth_body["signature"]),
        f"12345{auth_body['timestamp']}".encode(),
        padding.PKCS1v15(),
        hashes.SHA512(),
    )
    assert validation_request.get_header("Public-token") == "j" * 64
    assert "/public/v5/subscription/" in validation_request.full_url
    assert str(purchase_id) in validation_request.full_url


def test_public_token_is_cached_but_purchase_state_is_always_refetched():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    opener = _QueueOpener(
        _auth_response(),
        _subscription_response(),
        _subscription_response(),
    )
    client = RuStoreSubscriptionClient(
        key_id="12345",
        private_key=key,
        package_name="com.plantdiseases.app",
        allowed_products=("plantdiseases_pro_monthly",),
        timeout_seconds=2,
        opener=opener,
    )
    purchase_id = uuid.uuid4()

    for _ in range(2):
        client.validate_subscription(
            product_id="plantdiseases_pro_monthly",
            purchase_id=purchase_id,
            sandbox=False,
        )

    assert [request.get_method() for request in opener.requests] == ["POST", "GET", "GET"]


def test_malformed_provider_response_fails_closed():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    opener = _QueueOpener(_auth_response(), {"code": "OK", "body": {"autoRenewing": True}})
    client = RuStoreSubscriptionClient(
        key_id="12345",
        private_key=key,
        package_name="com.plantdiseases.app",
        allowed_products=("plantdiseases_pro_monthly",),
        timeout_seconds=2,
        opener=opener,
    )

    with pytest.raises(RuStoreUnavailable):
        client.validate_subscription(
            product_id="plantdiseases_pro_monthly",
            purchase_id=uuid.uuid4(),
            sandbox=False,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("startTimeMillis", 1787700000000),
        ("expiryTimeMillis", "-1"),
        ("autoRenewing", 1),
        ("paymentState", True),
        ("priceAmountMicros", 199000000),
        ("priceAmountMicros", "0"),
        ("priceCurrencyCode", "rub"),
        ("countryCode", "Russia"),
        ("externalAccountId", "not-the-issued-billing-uuid"),
        ("orderId", "unsafe\x00order"),
    ],
)
def test_money_time_and_binding_fields_are_parsed_without_coercion(field, value):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    document = copy.deepcopy(_subscription_response())
    document["body"][field] = value
    opener = _QueueOpener(_auth_response(), document)
    client = RuStoreSubscriptionClient(
        key_id="12345",
        private_key=key,
        package_name="com.plantdiseases.app",
        allowed_products=("plantdiseases_pro_monthly",),
        timeout_seconds=2,
        opener=opener,
    )

    with pytest.raises(RuStoreUnavailable):
        client.validate_subscription(
            product_id="plantdiseases_pro_monthly",
            purchase_id=uuid.uuid4(),
            sandbox=False,
        )
