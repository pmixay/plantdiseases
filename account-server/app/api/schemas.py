"""Public response schemas; private account identifiers are intentionally absent."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class StrictResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorDetail(StrictResponse):
    code: str


class ErrorResponse(StrictResponse):
    error: ErrorDetail


class TokenProfile(StrictResponse):
    nickname: str
    community_actor_id: uuid.UUID


class SessionView(StrictResponse):
    session_id: uuid.UUID
    installation_id: uuid.UUID
    device_label: str
    created_at: datetime
    last_used_at: datetime
    idle_expires_at: datetime
    absolute_expires_at: datetime
    current: bool


class AuthenticatedResponse(StrictResponse):
    status: Literal["authenticated"]
    access_token: str
    refresh_token: str
    token_type: Literal["Bearer"]
    expires_in: int
    profile: TokenProfile
    session: SessionView


class PendingSignupResponse(StrictResponse):
    status: Literal["profile_required"]
    provider: Literal["yandex", "vk"]
    pending_signup_token: str
    expires_in: int


class SessionListResponse(StrictResponse):
    sessions: list[SessionView]


class ReauthResponse(StrictResponse):
    reauth_token: str
    scope: Literal["deletion", "link_yandex", "link_vk"]
    expires_in: int


class LinkResponse(StrictResponse):
    status: Literal["linked"]
    provider: Literal["yandex", "vk"]


class PrimaryProviderProfile(StrictResponse):
    provider: Literal["yandex", "vk"]
    display_name: str
    avatar_url: str | None
    refreshed_at: datetime | None


class LinkedProvider(StrictResponse):
    provider: Literal["yandex", "vk"]
    refreshed_at: datetime


class MeResponse(StrictResponse):
    nickname: str
    community_actor_id: uuid.UUID
    primary_provider: PrimaryProviderProfile
    linked_providers: list[LinkedProvider]
    nickname_changed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    state: Literal["active", "deletion_pending"]


class NicknameResponse(StrictResponse):
    nickname: str
    access_token: str
    token_type: Literal["Bearer"]
    expires_in: int


class DeletionResponse(StrictResponse):
    status: Literal["deletion_pending"]


class PushRegistrationResponse(StrictResponse):
    status: Literal["registered"]
    platform: Literal["android"]


class Jwk(StrictResponse):
    kty: Literal["OKP"]
    crv: Literal["Ed25519"]
    use: Literal["sig"]
    alg: Literal["EdDSA"]
    kid: str
    x: str


class JwksResponse(StrictResponse):
    keys: list[Jwk]
