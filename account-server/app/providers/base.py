"""Provider-neutral identity verification types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol

ProviderName = Literal["yandex", "vk"]


@dataclass(frozen=True, slots=True)
class VerifiedProviderIdentity:
    """The deliberately minimal profile retained from an OAuth provider."""

    provider: ProviderName
    subject: str
    display_name: str
    avatar_url: str | None
    refreshed_at: datetime


class ProviderErrorCode(StrEnum):
    INVALID_TOKEN = "invalid_provider_token"
    UNAVAILABLE = "provider_unavailable"
    APPLICATION_MISMATCH = "provider_application_mismatch"
    PROFILE_REQUIRED = "profile_required"


class ProviderVerificationError(RuntimeError):
    """Stable provider error which never includes the presented token."""

    def __init__(self, code: ProviderErrorCode):
        self.code = code
        super().__init__(code.value)


class ProviderAdapter(Protocol):
    provider: ProviderName

    async def verify(self, access_token: str) -> VerifiedProviderIdentity:
        """Validate a provider token and return the allowlisted profile."""
