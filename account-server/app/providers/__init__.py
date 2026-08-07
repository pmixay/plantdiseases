"""Yandex/VK provider adapter contracts."""

from app.providers.base import (
    ProviderAdapter,
    ProviderErrorCode,
    ProviderName,
    ProviderVerificationError,
    VerifiedProviderIdentity,
)

__all__ = [
    "ProviderAdapter",
    "ProviderErrorCode",
    "ProviderName",
    "ProviderVerificationError",
    "VerifiedProviderIdentity",
]
