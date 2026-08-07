"""Deterministic synthetic provider adapters for local/team beta tests only."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from app.config import Settings, SettingsError
from app.providers.base import (
    ProviderAdapter,
    ProviderErrorCode,
    ProviderName,
    ProviderVerificationError,
    VerifiedProviderIdentity,
)


@dataclass(frozen=True, slots=True)
class _ProfileFixture:
    subject: str
    display_name: str
    avatar_url: str


def _profile(provider: ProviderName, scenario: str, number: int) -> _ProfileFixture:
    provider_title = "Yandex" if provider == "yandex" else "VK"
    return _ProfileFixture(
        subject=f"{provider}-test-subject-{number:03d}",
        display_name=f"Synthetic {provider_title} {scenario}",
        avatar_url=f"https://profiles.invalid/{provider}/{number:03d}.png",
    )


_VALID_FIXTURES: dict[ProviderName, dict[str, _ProfileFixture]] = {
    "yandex": {
        "mock-yandex-new": _profile("yandex", "New", 1),
        "mock-yandex-existing": _profile("yandex", "Existing", 2),
        "mock-yandex-both-linked": _profile("yandex", "Linked", 3),
        "mock-yandex-owned-by-other": _profile("yandex", "Owned", 4),
    },
    "vk": {
        "mock-vk-new": _profile("vk", "New", 1),
        "mock-vk-existing": _profile("vk", "Existing", 2),
        "mock-vk-both-linked": _profile("vk", "Linked", 3),
        "mock-vk-owned-by-other": _profile("vk", "Owned", 4),
    },
}

_ERROR_SUFFIXES: dict[str, ProviderErrorCode] = {
    "expired": ProviderErrorCode.INVALID_TOKEN,
    "forged": ProviderErrorCode.INVALID_TOKEN,
    "wrong-application": ProviderErrorCode.APPLICATION_MISMATCH,
    "unavailable": ProviderErrorCode.UNAVAILABLE,
    "incomplete-profile": ProviderErrorCode.PROFILE_REQUIRED,
}
_GENERATED_TOKEN = re.compile(r"mock-(yandex|vk)-generated-([0-9a-f]{32})\Z")


class MockProviderAdapter(ProviderAdapter):
    """No-network provider contract backed only by explicit synthetic tokens."""

    def __init__(self, provider: ProviderName, *, environment: str):
        if environment == "production":
            raise SettingsError("Mock provider adapters are forbidden in production")
        self.provider = provider

    async def verify(self, access_token: str) -> VerifiedProviderIdentity:
        fixture = _VALID_FIXTURES[self.provider].get(access_token)
        generated = _GENERATED_TOKEN.fullmatch(access_token)
        if generated is not None and generated.group(1) == self.provider:
            fixture_id = generated.group(2)
            provider_title = "Yandex" if self.provider == "yandex" else "VK"
            return VerifiedProviderIdentity(
                provider=self.provider,
                subject=f"{self.provider}-test-generated-{fixture_id}",
                display_name=f"Synthetic {provider_title} Generated",
                avatar_url=(
                    f"https://profiles.invalid/{self.provider}/generated/{fixture_id}.png"
                ),
                refreshed_at=datetime.now(UTC),
            )
        if fixture is not None:
            return VerifiedProviderIdentity(
                provider=self.provider,
                subject=fixture.subject,
                display_name=fixture.display_name,
                avatar_url=fixture.avatar_url,
                refreshed_at=datetime.now(UTC),
            )

        prefix = f"mock-{self.provider}-"
        suffix = access_token.removeprefix(prefix) if access_token.startswith(prefix) else ""
        error = _ERROR_SUFFIXES.get(suffix, ProviderErrorCode.INVALID_TOKEN)
        raise ProviderVerificationError(error)


def build_provider_registry(settings: Settings) -> dict[ProviderName, ProviderAdapter]:
    if settings.auth_provider_mode != "mock":
        raise SettingsError("Remote provider adapters are not implemented yet")
    return {
        provider: MockProviderAdapter(provider, environment=settings.environment)
        for provider in ("yandex", "vk")
    }


VALID_MOCK_TOKENS = {
    provider: tuple(fixtures)
    for provider, fixtures in _VALID_FIXTURES.items()
}
ERROR_MOCK_TOKENS = {
    provider: {
        f"mock-{provider}-{suffix}": error
        for suffix, error in _ERROR_SUFFIXES.items()
    }
    for provider in ("yandex", "vk")
}
