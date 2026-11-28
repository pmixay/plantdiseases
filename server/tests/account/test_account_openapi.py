from __future__ import annotations

import json
from pathlib import Path

ACCOUNT_SERVER_ROOT = Path(__file__).resolve().parents[3] / "account-server"
OPENAPI_PATH = ACCOUNT_SERVER_ROOT / "openapi.json"

EXPECTED_PATHS = {
    "/.well-known/jwks.json",
    "/v1/auth/yandex/exchange",
    "/v1/auth/vk/exchange",
    "/v1/auth/email/register",
    "/v1/auth/email/login",
    "/v1/auth/email/reauth",
    "/v1/auth/email/verify",
    "/v1/auth/email/password-reset/request",
    "/v1/auth/email/password-reset/confirm",
    "/v1/auth/email/add",
    "/v1/auth/email/add/confirm",
    "/v1/auth/signup/complete",
    "/v1/auth/signup/confirm",
    "/v1/auth/signup/cancel",
    "/v1/auth/refresh",
    "/v1/auth/logout",
    "/v1/auth/sessions",
    "/v1/auth/sessions/{session_id}",
    "/v1/auth/reauth",
    "/v1/auth/yandex/link",
    "/v1/auth/vk/link",
    "/v1/billing/rustore/notifications",
    "/v1/billing/rustore/verify",
    "/v1/entitlements",
    "/v1/me",
    "/v1/me/nickname",
    "/v1/me/community-reward",
    "/v1/push/device",
}


def test_versioned_openapi_matches_the_application_contract():
    from app.main import app

    committed = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    assert committed == app.openapi()
    assert set(committed["paths"]) == EXPECTED_PATHS
    assert "/v1/internal/auth/verify" not in committed["paths"]
    assert "/v1/internal/entitlements/consume" not in committed["paths"]
    assert "/v1/internal/entitlements/release" not in committed["paths"]
    assert (
        "/v1/internal/billing/rustore/notifications/replay-dead-letters"
        not in committed["paths"]
    )
    assert "/v1/internal/notifications/opinion" not in committed["paths"]
    assert not [path for path in committed["paths"] if "/internal/" in path]


def test_protected_routes_declare_bearer_auth_and_requests_forbid_extra_fields():
    schema = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    assert schema["components"]["securitySchemes"]["HTTPBearer"] == {
        "type": "http",
        "scheme": "bearer",
    }

    protected_operations = (
        ("/v1/me", "get"),
        ("/v1/me/community-reward", "get"),
        ("/v1/me/nickname", "patch"),
        ("/v1/auth/sessions", "get"),
        ("/v1/push/device", "put"),
    )
    for path, method in protected_operations:
        assert schema["paths"][path][method]["security"] == [{"HTTPBearer": []}]

    logout = schema["paths"]["/v1/auth/logout"]["post"]
    assert "security" not in logout
    assert logout["requestBody"]["required"] is True
    assert logout["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/LogoutRequest"
    }
    refresh_request = schema["components"]["schemas"]["RefreshRequest"]
    assert refresh_request["required"] == ["refresh_token", "rotation_id"]
    assert refresh_request["properties"]["rotation_id"] == {
        "format": "uuid",
        "title": "Rotation Id",
        "type": "string",
    }

    for model in schema["components"]["schemas"].values():
        if model.get("title", "").endswith("Request"):
            assert model.get("additionalProperties") is False


def test_openapi_has_no_private_identity_or_hardware_fields():
    text = OPENAPI_PATH.read_text(encoding="utf-8").lower()
    forbidden = (
        "provider_subject",
        "account_id",
        "phone_model",
        "hardware_model",
        "manufacturer",
        "android_id",
        "advertising_id",
        "imei",
        "provider_access_token_example",
    )
    for value in forbidden:
        assert value not in text


def test_openapi_documents_the_non_reflective_error_shape():
    schema = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    error_schema = schema["components"]["schemas"]["ErrorResponse"]
    assert error_schema["additionalProperties"] is False
    for operation in (
        schema["paths"]["/v1/auth/yandex/exchange"]["post"],
        schema["paths"]["/v1/auth/refresh"]["post"],
        schema["paths"]["/v1/me"]["delete"],
    ):
        assert operation["responses"]["422"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }
