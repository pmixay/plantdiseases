"""Select the complete provider registry from validated settings."""

from __future__ import annotations

from app.config import Settings, SettingsError
from app.providers.base import ProviderAdapter, ProviderName
from app.providers.mock import MockProviderAdapter
from app.providers.remote import VkProviderAdapter, YandexProviderAdapter


def build_provider_registry(settings: Settings) -> dict[ProviderName, ProviderAdapter]:
    if settings.auth_provider_mode == "mock":
        return {
            provider: MockProviderAdapter(provider, environment=settings.environment)
            for provider in ("yandex", "vk")
        }
    if settings.auth_provider_mode != "remote":
        raise SettingsError("Unsupported provider mode")
    if settings.yandex_client_id is None or settings.vk_client_id is None:
        raise SettingsError("Remote provider application IDs are required")
    return {
        "yandex": YandexProviderAdapter(
            client_id=settings.yandex_client_id,
            timeout_seconds=settings.provider_http_timeout_seconds,
        ),
        "vk": VkProviderAdapter(
            client_id=settings.vk_client_id,
            timeout_seconds=settings.provider_http_timeout_seconds,
        ),
    }
