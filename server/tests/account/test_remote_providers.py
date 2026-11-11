"""Remote provider contracts use fake HTTP only; no provider network is called."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest
from app.config import Settings, SettingsError
from app.providers.base import ProviderErrorCode, ProviderVerificationError
from app.providers.registry import build_provider_registry
from app.providers.remote import (
    ProviderHttpResponse,
    ProviderTransportError,
    UrllibProviderTransport,
    VkProviderAdapter,
    YandexProviderAdapter,
)


@dataclass
class FakeTransport:
    response: ProviderHttpResponse | None = None
    failure: Exception | None = None
    calls: list[dict] = field(default_factory=list)

    async def request_json(self, **kwargs) -> ProviderHttpResponse:
        self.calls.append(kwargs)
        if self.failure is not None:
            raise self.failure
        assert self.response is not None
        return self.response


class FakeNetworkResponse:
    def __init__(self, body: bytes, *, status: int = 200):
        self.body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _maximum: int) -> bytes:
        return self.body


class FakeOpener:
    def __init__(self, response: FakeNetworkResponse | Exception):
        self.response = response
        self.calls: list[tuple[object, int]] = []

    def open(self, request, timeout: int):
        self.calls.append((request, timeout))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_remote_registry_requires_no_request_at_startup():
    settings = Settings.from_env(
        {
            "ENVIRONMENT": "test",
            "AUTH_PROVIDER_MODE": "remote",
            "YANDEX_CLIENT_ID": "yandex-public-client",
            "VKID_CLIENT_ID": "12345",
            "ACCOUNT_ISSUER": "https://id.example.invalid",
            "ACCOUNT_AUDIENCE": "plantdiseases-app",
            "ACCOUNT_DATABASE_URL": (
                "postgresql+psycopg://account:secret@localhost/accounts"
            ),
        }
    )
    registry = build_provider_registry(settings)
    assert isinstance(registry["yandex"], YandexProviderAdapter)
    assert isinstance(registry["vk"], VkProviderAdapter)


@pytest.mark.parametrize(
    ("provider_environment", "expected_provider", "expected_type"),
    [
        ({"YANDEX_CLIENT_ID": "yandex-public-client"}, "yandex", YandexProviderAdapter),
        ({"VKID_CLIENT_ID": "12345"}, "vk", VkProviderAdapter),
    ],
)
def test_remote_registry_supports_one_provider_at_a_time(
    provider_environment, expected_provider, expected_type
):
    settings = Settings.from_env(
        {
            "ENVIRONMENT": "test",
            "AUTH_PROVIDER_MODE": "remote",
            "ACCOUNT_ISSUER": "https://id.example.invalid",
            "ACCOUNT_AUDIENCE": "plantdiseases-app",
            "ACCOUNT_DATABASE_URL": (
                "postgresql+psycopg://account:secret@localhost/accounts"
            ),
        }
        | provider_environment
    )

    registry = build_provider_registry(settings)

    assert set(registry) == {expected_provider}
    assert isinstance(registry[expected_provider], expected_type)


def test_remote_registry_rejects_unvalidated_direct_settings():
    settings = Settings(
        environment="test",
        auth_provider_mode="remote",
        issuer="https://id.example.invalid",
        audience="plantdiseases-app",
        database_url="postgresql+psycopg://account:secret@localhost/accounts",
    )
    with pytest.raises(SettingsError, match="At least one"):
        build_provider_registry(settings)


def test_urllib_transport_has_fixed_tls_no_redirect_contract(monkeypatch):
    opener = FakeOpener(FakeNetworkResponse(b'{"user":{"user_id":"1"}}'))
    handlers: list[object] = []

    def fake_build_opener(*configured_handlers):
        handlers.extend(configured_handlers)
        return opener

    monkeypatch.setattr("app.providers.remote.build_opener", fake_build_opener)
    response = UrllibProviderTransport._request_json(
        method="POST",
        url="https://id.vk.ru/oauth2/user_info?client_id=12345",
        headers={},
        form={"access_token": "opaque-token"},
        timeout_seconds=4,
    )

    assert response.status_code == 200
    assert response.payload == {"user": {"user_id": "1"}}
    assert [handler.__class__.__name__ for handler in handlers] == [
        "_NoRedirects",
        "HTTPSHandler",
    ]
    request, timeout = opener.calls[0]
    assert request.full_url == "https://id.vk.ru/oauth2/user_info?client_id=12345"
    assert request.data == b"access_token=opaque-token"
    assert timeout == 4


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"x" * (64 * 1024 + 1), "too large"),
        (b"not-json", "invalid"),
    ],
    ids=("oversized-body", "invalid-json"),
)
def test_urllib_transport_rejects_unsafe_provider_responses(
    monkeypatch,
    body: bytes,
    message: str,
):
    monkeypatch.setattr(
        "app.providers.remote.build_opener",
        lambda *_handlers: FakeOpener(FakeNetworkResponse(body)),
    )
    with pytest.raises(ProviderTransportError, match=message):
        UrllibProviderTransport._request_json(
            method="GET",
            url="https://login.yandex.ru/info?format=json",
            headers={"Authorization": "OAuth opaque-token"},
            form=None,
            timeout_seconds=5,
        )


def test_urllib_transport_wraps_network_details_in_fixed_error(monkeypatch):
    monkeypatch.setattr(
        "app.providers.remote.build_opener",
        lambda *_handlers: FakeOpener(OSError("private network detail")),
    )
    with pytest.raises(ProviderTransportError) as raised:
        UrllibProviderTransport._request_json(
            method="GET",
            url="https://login.yandex.ru/info?format=json",
            headers={"Authorization": "OAuth opaque-token"},
            form=None,
            timeout_seconds=5,
        )
    assert str(raised.value) == "provider request failed"


def test_yandex_verifies_application_and_keeps_only_allowlisted_profile():
    transport = FakeTransport(
        response=ProviderHttpResponse(
            200,
            {
                "id": "global-id-is-not-retained",
                "psuid": "app-specific-subject",
                "client_id": "yandex-public-client",
                "display_name": "Synthetic Yandex Person",
                "default_avatar_id": "avatar_123",
                "is_avatar_empty": False,
                "default_email": "must-not-be-retained@example.invalid",
                "birthday": "2000-01-01",
                "default_phone": {"number": "+70000000000"},
            },
        )
    )
    adapter = YandexProviderAdapter(
        client_id="yandex-public-client",
        timeout_seconds=4,
        transport=transport,
    )

    identity = asyncio.run(adapter.verify("opaque-yandex-token"))

    assert identity.provider == "yandex"
    assert identity.subject == "app-specific-subject"
    assert identity.display_name == "Synthetic Yandex Person"
    assert identity.avatar_url == (
        "https://avatars.yandex.net/get-yapic/avatar_123/islands-200"
    )
    assert not hasattr(identity, "email")
    call = transport.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == "https://login.yandex.ru/info?format=json"
    assert call["headers"] == {"Authorization": "OAuth opaque-yandex-token"}
    assert call["form"] is None
    assert call["timeout_seconds"] == 4
    assert "opaque-yandex-token" not in call["url"]


def test_yandex_standard_profile_needs_no_optional_personal_data_scope():
    adapter = YandexProviderAdapter(
        client_id="yandex-public-client",
        timeout_seconds=4,
        transport=FakeTransport(
            response=ProviderHttpResponse(
                200,
                {
                    "login": "minimal-yandex-login",
                    "id": "global-id-is-not-retained",
                    "psuid": "app-specific-subject",
                    "client_id": "yandex-public-client",
                },
            )
        ),
    )

    identity = asyncio.run(adapter.verify("opaque-yandex-token"))

    assert identity.display_name == "minimal-yandex-login"
    assert identity.avatar_url is None


def test_yandex_rejects_token_issued_to_another_application():
    adapter = YandexProviderAdapter(
        client_id="expected-client",
        timeout_seconds=5,
        transport=FakeTransport(
            response=ProviderHttpResponse(
                200,
                {
                    "psuid": "wrong-app-subject",
                    "client_id": "different-client",
                    "display_name": "Wrong App",
                },
            )
        ),
    )
    with pytest.raises(ProviderVerificationError) as raised:
        asyncio.run(adapter.verify("opaque-token"))
    assert raised.value.code == ProviderErrorCode.APPLICATION_MISMATCH
    assert "opaque-token" not in str(raised.value)


def test_vk_uses_client_bound_user_info_and_discards_phone_and_email():
    transport = FakeTransport(
        response=ProviderHttpResponse(
            200,
            {
                "user": {
                    "user_id": "123456",
                    "first_name": "Synthetic",
                    "last_name": "VK Person",
                    "avatar": "https://sun6-21.userapi.com/s/v1/if1/avatar.jpg",
                    "email": "must-not-be-retained@example.invalid",
                    "phone": "+70000000000",
                }
            },
        )
    )
    adapter = VkProviderAdapter(
        client_id="12345",
        timeout_seconds=3,
        transport=transport,
    )

    identity = asyncio.run(adapter.verify("opaque-vk-token"))

    assert identity.provider == "vk"
    assert identity.subject == "123456"
    assert identity.display_name == "Synthetic VK Person"
    assert identity.avatar_url == "https://sun6-21.userapi.com/s/v1/if1/avatar.jpg"
    assert not hasattr(identity, "phone")
    call = transport.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://id.vk.ru/oauth2/user_info?client_id=12345"
    assert call["headers"] == {}
    assert call["form"] == {"access_token": "opaque-vk-token"}
    assert "opaque-vk-token" not in call["url"]


@pytest.mark.parametrize(
    "avatar",
    [
        "https://attacker.invalid/vk/avatar.png",
        "https://userapi.com.attacker.invalid/avatar.png",
        "https://vk.ru.evil.invalid/avatar.png",
        "http://sun6-21.userapi.com/avatar.jpg",
        "https://user:pass@sun6-21.userapi.com/avatar.jpg",
        "https://sun6-21.userapi.com/avatar.jpg#fragment",
    ],
)
def test_vk_portrait_outside_vk_photo_infrastructure_is_dropped(avatar):
    """A provider-supplied host must never reach the phone's image loader.

    Dropping it is not an error: the account keeps its identity and the app
    renders its placeholder, exactly as for a VK user with no portrait.
    """
    transport = FakeTransport(
        response=ProviderHttpResponse(
            200,
            {
                "user": {
                    "user_id": "123456",
                    "first_name": "Synthetic",
                    "last_name": "VK Person",
                    "avatar": avatar,
                }
            },
        )
    )
    adapter = VkProviderAdapter(client_id="12345", timeout_seconds=3, transport=transport)

    identity = asyncio.run(adapter.verify("opaque-vk-token"))

    assert identity.subject == "123456"
    assert identity.avatar_url is None


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (ProviderHttpResponse(400, {"error": "invalid_client"}),
         ProviderErrorCode.APPLICATION_MISMATCH),
        (ProviderHttpResponse(401, {"error": "invalid_token"}),
         ProviderErrorCode.INVALID_TOKEN),
        (ProviderHttpResponse(429, {"error": "rate_limited"}),
         ProviderErrorCode.UNAVAILABLE),
        (ProviderHttpResponse(503, {}), ProviderErrorCode.UNAVAILABLE),
    ],
)
def test_remote_provider_errors_map_to_stable_non_reflective_codes(
    response: ProviderHttpResponse,
    expected: ProviderErrorCode,
):
    adapter = VkProviderAdapter(
        client_id="12345",
        timeout_seconds=5,
        transport=FakeTransport(response=response),
    )
    with pytest.raises(ProviderVerificationError) as raised:
        asyncio.run(adapter.verify("secret-token-value"))
    assert raised.value.code == expected
    assert "secret-token-value" not in str(raised.value)


def test_remote_transport_failure_and_incomplete_profile_fail_closed():
    unavailable = YandexProviderAdapter(
        client_id="expected-client",
        timeout_seconds=5,
        transport=FakeTransport(failure=ProviderTransportError("fixed")),
    )
    with pytest.raises(ProviderVerificationError) as raised:
        asyncio.run(unavailable.verify("opaque-token"))
    assert raised.value.code == ProviderErrorCode.UNAVAILABLE

    incomplete = VkProviderAdapter(
        client_id="12345",
        timeout_seconds=5,
        transport=FakeTransport(
            response=ProviderHttpResponse(200, {"user": {"user_id": "123"}})
        ),
    )
    with pytest.raises(ProviderVerificationError) as raised:
        asyncio.run(incomplete.verify("opaque-token"))
    assert raised.value.code == ProviderErrorCode.PROFILE_REQUIRED


@pytest.mark.parametrize(
    "token",
    ["", "has space", "line\nbreak", "non-ascii-токен", "x" * 8193],
)
def test_malformed_provider_token_is_rejected_before_http(token: str):
    transport = FakeTransport(response=ProviderHttpResponse(200, {}))
    adapter = YandexProviderAdapter(
        client_id="expected-client",
        timeout_seconds=5,
        transport=transport,
    )
    with pytest.raises(ProviderVerificationError) as raised:
        asyncio.run(adapter.verify(token))
    assert raised.value.code == ProviderErrorCode.INVALID_TOKEN
    assert transport.calls == []


def test_provider_subject_is_not_normalized_or_accepted_with_whitespace():
    adapter = YandexProviderAdapter(
        client_id="expected-client",
        timeout_seconds=5,
        transport=FakeTransport(
            response=ProviderHttpResponse(
                200,
                {
                    "psuid": "subject with spaces",
                    "client_id": "expected-client",
                    "display_name": "Synthetic Person",
                },
            )
        ),
    )
    with pytest.raises(ProviderVerificationError) as raised:
        asyncio.run(adapter.verify("opaque-token"))
    assert raised.value.code == ProviderErrorCode.PROFILE_REQUIRED
