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


class SignupCompletionResponse(StrictResponse):
    """Provisional credentials which are unusable until explicit confirmation."""

    status: Literal["confirmation_required"]
    access_token: str
    refresh_token: str
    token_type: Literal["Bearer"]
    expires_in: int
    profile: TokenProfile
    session: SessionView


class PendingSignupResponse(StrictResponse):
    status: Literal["profile_required"]
    provider: Literal["yandex", "vk", "email"]
    pending_signup_token: str
    expires_in: int


class EmailVerificationResponse(StrictResponse):
    """A code was mailed; send it to ``/v1/auth/email/verify``."""

    status: Literal["verification_required"]
    provider: Literal["email"]
    pending_signup_token: str
    expires_in: int


class EmailCodeSentResponse(StrictResponse):
    """A code was mailed; send it to ``/v1/auth/email/add/confirm``."""

    status: Literal["verification_required"]
    provider: Literal["email"]
    expires_in: int


class PasswordResetRequestedResponse(StrictResponse):
    """The same answer whether or not the address signs in."""

    status: Literal["code_sent_if_registered"]
    expires_in: int


class SessionListResponse(StrictResponse):
    sessions: list[SessionView]


class ReauthResponse(StrictResponse):
    reauth_token: str
    scope: Literal["deletion", "link_yandex", "link_vk", "link_email"]
    expires_in: int


class LinkResponse(StrictResponse):
    status: Literal["linked"]
    provider: Literal["yandex", "vk", "email"]


class PrimaryProviderProfile(StrictResponse):
    provider: Literal["yandex", "vk", "email"]
    display_name: str
    avatar_url: str | None
    refreshed_at: datetime | None


class LinkedProvider(StrictResponse):
    provider: Literal["yandex", "vk", "email"]
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


class EntitlementQuota(StrictResponse):
    feature: Literal[
        "gamma_analysis",
        "frontier_analysis",
        "plant_identification",
        "ai_doctor",
        "care_plan",
    ]
    daily_limit: int
    remaining: int
    reset_at: datetime


class EntitlementsResponse(StrictResponse):
    plan: Literal["free", "pro"]
    status: Literal["none", "pending", "trial", "active", "grace", "expired"]
    valid_until: datetime | None
    auto_renewing: bool
    billing_available: bool
    billing_external_id: uuid.UUID
    products: list[str]
    quota_policy_version: str
    quotas: list[EntitlementQuota]


class InternalEntitlementDecision(StrictResponse):
    allowed: bool
    replayed: bool
    released: bool
    plan: Literal["free", "pro"]
    policy_version: str
    feature: Literal[
        "gamma_analysis",
        "frontier_analysis",
        "plant_identification",
        "ai_doctor",
        "care_plan",
    ]
    limit: int
    remaining: int
    reset_at: datetime
    reason: Literal["pro_required", "quota_exhausted"] | None


class InternalPlantNetBudgetDecision(StrictResponse):
    allowed: bool
    replayed: bool
    policy_version: str
    usage_date: str
    attempt_limit: int
    attempts_used: int
    remaining_attempts: int
    attempt_cost_micros: int
    cost_limit_micros: int
    cost_exposure_micros: int
    remaining_cost_micros: int
    reason: Literal["attempt_limit", "cost_limit"] | None


class InternalNotificationReplayResponse(StrictResponse):
    replayed: int
    pending_total: int
    dead_letter_total: int
