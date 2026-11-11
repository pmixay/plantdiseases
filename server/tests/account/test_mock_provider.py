from __future__ import annotations

import asyncio
from dataclasses import asdict

import pytest
from app.config import SettingsError
from app.providers.base import ProviderErrorCode, ProviderVerificationError
from app.providers.mock import (
    ERROR_MOCK_TOKENS,
    VALID_MOCK_TOKENS,
    MockProviderAdapter,
)


@pytest.mark.parametrize("provider", ["yandex", "vk"])
def test_all_valid_fixtures_are_synthetic_and_minimal(provider: str):
    adapter = MockProviderAdapter(provider, environment="test")
    for token in VALID_MOCK_TOKENS[provider]:
        identity = asyncio.run(adapter.verify(token))
        values = asdict(identity)
        assert set(values) == {
            "provider",
            "subject",
            "display_name",
            "avatar_url",
            "refreshed_at",
        }
        assert identity.provider == provider
        assert identity.subject.startswith(f"{provider}-test-subject-")
        assert identity.display_name.startswith("Synthetic ")
        assert identity.avatar_url is not None
        assert identity.avatar_url.startswith("https://profiles.invalid/")


@pytest.mark.parametrize("provider", ["yandex", "vk"])
def test_error_fixtures_return_stable_codes_without_echoing_tokens(provider: str):
    adapter = MockProviderAdapter(provider, environment="test")
    for token, expected_code in ERROR_MOCK_TOKENS[provider].items():
        with pytest.raises(ProviderVerificationError) as caught:
            asyncio.run(adapter.verify(token))
        assert caught.value.code == expected_code
        assert token not in str(caught.value)


def test_unknown_and_cross_provider_tokens_fail_closed():
    adapter = MockProviderAdapter("yandex", environment="test")
    for token in ("", "real-looking-token", "mock-vk-new"):
        with pytest.raises(ProviderVerificationError) as caught:
            asyncio.run(adapter.verify(token))
        assert caught.value.code == ProviderErrorCode.INVALID_TOKEN


@pytest.mark.parametrize("provider", ["yandex", "vk"])
def test_generated_fixture_tokens_create_deterministic_synthetic_subjects(provider: str):
    fixture_id = "0123456789abcdef0123456789abcdef"
    token = f"mock-{provider}-generated-{fixture_id}"
    adapter = MockProviderAdapter(provider, environment="test")

    identity = asyncio.run(adapter.verify(token))
    assert identity.subject == f"{provider}-test-generated-{fixture_id}"
    assert identity.display_name.startswith("Synthetic ")
    assert identity.avatar_url is not None and fixture_id in identity.avatar_url


@pytest.mark.parametrize("provider", ["yandex", "vk"])
def test_mock_adapter_refuses_production(provider: str):
    with pytest.raises(SettingsError, match="forbidden in production"):
        MockProviderAdapter(provider, environment="production")
