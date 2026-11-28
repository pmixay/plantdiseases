"""Versioned account HTTP routes with strict, secret-safe request models."""

from __future__ import annotations

import secrets
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.api.errors import AccountAPIError
from app.api.schemas import (
    AuthenticatedResponse,
    DeletionResponse,
    EmailCodeSentResponse,
    EmailVerificationResponse,
    EntitlementsResponse,
    ErrorResponse,
    InternalEntitlementDecision,
    InternalNotificationReplayResponse,
    InternalPlantNetBudgetDecision,
    JwksResponse,
    LinkResponse,
    MeResponse,
    NicknameResponse,
    PasswordResetRequestedResponse,
    PendingSignupResponse,
    PushRegistrationResponse,
    ReauthResponse,
    SessionListResponse,
    SignupCompletionResponse,
)
from app.providers.base import ProviderName
from app.services.accounts import AccountService, CurrentAccount
from app.services.community_roles import CommunityRoleError, CommunityRoleService
from app.services.entitlements import EntitlementService
from app.services.vendor_budgets import VendorBudgetService, VendorBudgetUnavailable

router = APIRouter(
    responses={
        400: {"model": ErrorResponse, "description": "Stable request/domain error"},
        401: {"model": ErrorResponse, "description": "Authentication required or expired"},
        404: {"model": ErrorResponse, "description": "Caller-owned resource unavailable"},
        409: {"model": ErrorResponse, "description": "State or uniqueness conflict"},
        422: {"model": ErrorResponse, "description": "Non-reflective request validation error"},
        429: {"model": ErrorResponse, "description": "In-memory beta rate limit"},
        500: {"model": ErrorResponse, "description": "Redacted response-contract failure"},
        503: {"model": ErrorResponse, "description": "Provider or service unavailable"},
    }
)
bearer = HTTPBearer(auto_error=False)


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExchangeRequest(StrictRequest):
    provider_access_token: SecretStr = Field(min_length=1, max_length=8192)
    installation_id: uuid.UUID


class EmailCredentialRequest(StrictRequest):
    email: SecretStr = Field(min_length=3, max_length=254)
    password: SecretStr = Field(min_length=1, max_length=128)
    installation_id: uuid.UUID


class EmailVerifyRequest(StrictRequest):
    pending_signup_token: SecretStr = Field(min_length=1, max_length=256)
    code: SecretStr = Field(min_length=6, max_length=6)
    installation_id: uuid.UUID


class PasswordResetRequest(StrictRequest):
    email: SecretStr = Field(min_length=3, max_length=254)


class PasswordResetConfirmRequest(StrictRequest):
    email: SecretStr = Field(min_length=3, max_length=254)
    code: SecretStr = Field(min_length=6, max_length=6)
    new_password: SecretStr = Field(min_length=1, max_length=128)


class EmailAddRequest(StrictRequest):
    email: SecretStr = Field(min_length=3, max_length=254)
    password: SecretStr = Field(min_length=1, max_length=128)
    reauth_token: SecretStr = Field(min_length=1, max_length=256)


class EmailAddConfirmRequest(StrictRequest):
    code: SecretStr = Field(min_length=6, max_length=6)


class CompleteSignupRequest(StrictRequest):
    pending_signup_token: SecretStr = Field(min_length=1, max_length=256)
    completion_id: uuid.UUID
    nickname: str = Field(min_length=1, max_length=64)
    installation_id: uuid.UUID


class CancelSignupRequest(StrictRequest):
    pending_signup_token: SecretStr = Field(min_length=1, max_length=256)
    completion_id: uuid.UUID
    installation_id: uuid.UUID


class ConfirmSignupRequest(StrictRequest):
    pending_signup_token: SecretStr = Field(min_length=1, max_length=256)
    completion_id: uuid.UUID
    installation_id: uuid.UUID


class RefreshRequest(StrictRequest):
    refresh_token: SecretStr = Field(min_length=1, max_length=256)
    rotation_id: uuid.UUID


class LogoutRequest(StrictRequest):
    refresh_token: SecretStr = Field(min_length=1, max_length=256)


class ReauthRequest(StrictRequest):
    provider: Literal["yandex", "vk"]
    provider_access_token: SecretStr = Field(min_length=1, max_length=8192)
    scope: Literal["deletion", "link_yandex", "link_vk", "link_email"]


class EmailReauthRequest(StrictRequest):
    password: SecretStr = Field(min_length=1, max_length=128)
    scope: Literal["deletion", "link_yandex", "link_vk"]


class LinkRequest(StrictRequest):
    provider_access_token: SecretStr = Field(min_length=1, max_length=8192)
    reauth_token: SecretStr = Field(min_length=1, max_length=256)


class NicknameRequest(StrictRequest):
    nickname: str = Field(min_length=1, max_length=64)


class DeletionRequest(StrictRequest):
    reauth_token: SecretStr = Field(min_length=1, max_length=256)


class PushDeviceRequest(StrictRequest):
    fcm_token: SecretStr = Field(min_length=20, max_length=4096)
    platform: Literal["android"] = "android"


class RuStoreVerificationRequest(StrictRequest):
    product_id: str = Field(min_length=1, max_length=128)
    purchase_id: uuid.UUID
    sandbox: bool = False


class RuStoreNotificationEnvelope(StrictRequest):
    id: str = Field(min_length=1, max_length=128)
    timestamp: str = Field(min_length=1, max_length=64)
    payload: SecretStr = Field(min_length=1, max_length=131_072)


class InternalEntitlementConsumeRequest(StrictRequest):
    actor_id: uuid.UUID
    feature: Literal[
        "gamma_analysis",
        "frontier_analysis",
        "plant_identification",
        "ai_doctor",
        "care_plan",
    ]
    request_id: uuid.UUID


class InternalPlantNetBudgetRequest(StrictRequest):
    request_id: uuid.UUID


class InternalNotificationReplayRequest(StrictRequest):
    limit: int = Field(default=10, ge=1, le=100)


class InternalCommunityActorRequest(StrictRequest):
    actor_id: uuid.UUID


class InternalCommunityRoleChange(StrictRequest):
    actor_id: uuid.UUID
    role: Literal["expert", "moderator"]
    scope: str = Field(default="*", min_length=1, max_length=32)
    by_actor_id: uuid.UUID


class InternalCommunityRewardRequest(StrictRequest):
    actor_id: uuid.UUID
    achievement_count: int = Field(ge=0, le=1000)


class InternalCommunityListRequest(StrictRequest):
    pass


class CommunityRewardResponse(BaseModel):
    achievement_count: int
    discount_percent: int
    discount_cap_percent: int
    discount_per_achievement: int


def _service(request: Request) -> AccountService:
    service = getattr(request.app.state, "account_service", None)
    if service is None:
        raise AccountAPIError("service_unavailable", status_code=503)
    return service


def _entitlements(request: Request) -> EntitlementService:
    service = getattr(request.app.state, "entitlement_service", None)
    if service is None:
        raise AccountAPIError("service_unavailable", status_code=503)
    return service


def _vendor_budgets(request: Request) -> VendorBudgetService:
    service = getattr(request.app.state, "vendor_budget_service", None)
    if service is None:
        raise AccountAPIError("service_unavailable", status_code=503)
    return service


def _community_roles(request: Request) -> CommunityRoleService:
    service = getattr(request.app.state, "community_role_service", None)
    if service is None:
        raise AccountAPIError("service_unavailable", status_code=503)
    return service


def _role_call(operation):
    try:
        return operation()
    except CommunityRoleError as exc:
        raise AccountAPIError(exc.code, status_code=exc.status_code) from None


def _current_account(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    service: Annotated[AccountService, Depends(_service)],
) -> CurrentAccount:
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or len(credentials.credentials) > 8192
    ):
        raise AccountAPIError(
            "auth_required",
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
    return service.authenticate(credentials.credentials)


def _limit_ip(request: Request, service: AccountService, operation: str) -> None:
    service.limit_ip(operation, _client_address(request))


def _require_email_password_auth(request: Request) -> None:
    if not _email_password_auth_enabled(request):
        raise AccountAPIError("email_auth_unavailable", status_code=503)


def _email_password_auth_enabled(request: Request) -> bool:
    """The operator switch is on and there is a way to mail codes."""

    settings = getattr(request.app.state, "settings", None)
    service = getattr(request.app.state, "account_service", None)
    return bool(
        settings is not None
        and settings.email_password_auth_enabled
        and getattr(service, "mail_available", False)
    )


def _client_address(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


def _require_internal_service(request: Request) -> None:
    expected = getattr(request.app.state, "internal_service_token", None)
    supplied = request.headers.get("x-internal-service-token", "")
    # ``compare_digest`` refuses non-ASCII *str* operands with TypeError.
    # Starlette decodes header bytes as latin-1, so a single high byte in this
    # header used to leave the comparison unhandled: an unauthenticated caller
    # got a bare 500 with a server traceback instead of the stable 401 this
    # route promises. The credential is ASCII by construction (both loaders
    # read it with ``encoding="ascii"``), so anything else is simply wrong.
    if (
        not isinstance(expected, str)
        or not expected.isascii()
        or not supplied
        or len(supplied) > 512
        or not supplied.isascii()
        or not secrets.compare_digest(supplied, expected)
    ):
        raise AccountAPIError("auth_required", status_code=401)


async def _exchange(
    provider: ProviderName,
    body: ExchangeRequest,
    request: Request,
    service: AccountService,
) -> dict:
    _limit_ip(request, service, f"exchange-{provider}")
    return await service.exchange(
        provider=provider,
        provider_access_token=body.provider_access_token.get_secret_value(),
        installation_id=body.installation_id,
    )


@router.post(
    "/v1/auth/yandex/exchange",
    response_model=AuthenticatedResponse | PendingSignupResponse,
    tags=["authentication"],
)
async def exchange_yandex(
    body: ExchangeRequest,
    request: Request,
    service: Annotated[AccountService, Depends(_service)],
) -> dict:
    return await _exchange("yandex", body, request, service)


@router.post(
    "/v1/auth/vk/exchange",
    response_model=AuthenticatedResponse | PendingSignupResponse,
    tags=["authentication"],
)
async def exchange_vk(
    body: ExchangeRequest,
    request: Request,
    service: Annotated[AccountService, Depends(_service)],
) -> dict:
    return await _exchange("vk", body, request, service)


@router.post(
    "/v1/auth/email/register",
    response_model=EmailVerificationResponse,
    tags=["authentication"],
)
def register_email(
    body: EmailCredentialRequest,
    request: Request,
    service: Annotated[AccountService, Depends(_service)],
) -> dict:
    _require_email_password_auth(request)
    _limit_ip(request, service, "email-register")
    return service.register_with_email(
        email=body.email.get_secret_value(),
        password=body.password.get_secret_value(),
        installation_id=body.installation_id,
        client_address=_client_address(request),
    )


@router.post(
    "/v1/auth/email/verify",
    response_model=PendingSignupResponse,
    tags=["authentication"],
)
def verify_email(
    body: EmailVerifyRequest,
    request: Request,
    service: Annotated[AccountService, Depends(_service)],
) -> dict:
    _require_email_password_auth(request)
    _limit_ip(request, service, "email-verify")
    return service.verify_email_signup(
        pending_token=body.pending_signup_token.get_secret_value(),
        code=body.code.get_secret_value(),
        installation_id=body.installation_id,
        client_address=_client_address(request),
    )


@router.post(
    "/v1/auth/email/password-reset/request",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=PasswordResetRequestedResponse,
    tags=["authentication"],
)
def request_password_reset(
    body: PasswordResetRequest,
    request: Request,
    service: Annotated[AccountService, Depends(_service)],
) -> dict:
    _require_email_password_auth(request)
    _limit_ip(request, service, "email-reset-request")
    return service.request_password_reset(
        email=body.email.get_secret_value(),
        client_address=_client_address(request),
    )


@router.post(
    "/v1/auth/email/password-reset/confirm",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["authentication"],
)
def confirm_password_reset(
    body: PasswordResetConfirmRequest,
    request: Request,
    service: Annotated[AccountService, Depends(_service)],
) -> Response:
    _require_email_password_auth(request)
    _limit_ip(request, service, "email-reset-confirm")
    service.confirm_password_reset(
        email=body.email.get_secret_value(),
        code=body.code.get_secret_value(),
        new_password=body.new_password.get_secret_value(),
        client_address=_client_address(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/v1/auth/email/add",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=EmailCodeSentResponse,
    tags=["authentication"],
)
def add_email(
    body: EmailAddRequest,
    request: Request,
    current: Annotated[CurrentAccount, Depends(_current_account)],
    service: Annotated[AccountService, Depends(_service)],
) -> dict:
    _require_email_password_auth(request)
    _limit_ip(request, service, "email-add")
    return service.request_email_link(
        current,
        email=body.email.get_secret_value(),
        password=body.password.get_secret_value(),
        reauth_token=body.reauth_token.get_secret_value(),
        client_address=_client_address(request),
    )


@router.post(
    "/v1/auth/email/add/confirm",
    response_model=LinkResponse,
    tags=["authentication"],
)
def confirm_add_email(
    body: EmailAddConfirmRequest,
    request: Request,
    current: Annotated[CurrentAccount, Depends(_current_account)],
    service: Annotated[AccountService, Depends(_service)],
) -> dict:
    _require_email_password_auth(request)
    _limit_ip(request, service, "email-add-confirm")
    return service.confirm_email_link(
        current,
        code=body.code.get_secret_value(),
        client_address=_client_address(request),
    )


@router.post(
    "/v1/auth/email/login",
    response_model=AuthenticatedResponse | PendingSignupResponse,
    tags=["authentication"],
)
def login_email(
    body: EmailCredentialRequest,
    request: Request,
    service: Annotated[AccountService, Depends(_service)],
) -> dict:
    _require_email_password_auth(request)
    _limit_ip(request, service, "email-login")
    return service.login_with_email(
        email=body.email.get_secret_value(),
        password=body.password.get_secret_value(),
        installation_id=body.installation_id,
    )


@router.post(
    "/v1/auth/signup/complete",
    status_code=status.HTTP_201_CREATED,
    response_model=SignupCompletionResponse,
    tags=["authentication"],
)
def complete_signup(
    body: CompleteSignupRequest,
    request: Request,
    service: Annotated[AccountService, Depends(_service)],
) -> dict:
    _limit_ip(request, service, "signup-complete")
    return service.complete_signup(
        pending_token=body.pending_signup_token.get_secret_value(),
        completion_id=body.completion_id,
        nickname=body.nickname,
        installation_id=body.installation_id,
        email_password_auth_enabled=_email_password_auth_enabled(request),
    )


@router.post(
    "/v1/auth/signup/confirm",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["authentication"],
)
def confirm_signup(
    body: ConfirmSignupRequest,
    request: Request,
    service: Annotated[AccountService, Depends(_service)],
) -> Response:
    _limit_ip(request, service, "signup-confirm")
    service.confirm_signup(
        pending_token=body.pending_signup_token.get_secret_value(),
        completion_id=body.completion_id,
        installation_id=body.installation_id,
        email_password_auth_enabled=_email_password_auth_enabled(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/v1/auth/signup/cancel",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["authentication"],
)
def cancel_signup(
    body: CancelSignupRequest,
    request: Request,
    service: Annotated[AccountService, Depends(_service)],
) -> Response:
    _limit_ip(request, service, "signup-cancel")
    service.cancel_signup(
        pending_token=body.pending_signup_token.get_secret_value(),
        completion_id=body.completion_id,
        installation_id=body.installation_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/v1/auth/refresh",
    response_model=AuthenticatedResponse,
    tags=["authentication"],
)
def refresh(
    body: RefreshRequest,
    request: Request,
    service: Annotated[AccountService, Depends(_service)],
) -> dict:
    _limit_ip(request, service, "refresh")
    return service.refresh(
        refresh_token=body.refresh_token.get_secret_value(),
        rotation_id=body.rotation_id,
    )


@router.post(
    "/v1/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["authentication"],
)
def logout(
    body: LogoutRequest,
    request: Request,
    service: Annotated[AccountService, Depends(_service)],
) -> Response:
    _limit_ip(request, service, "logout")
    service.logout_with_refresh(refresh_token=body.refresh_token.get_secret_value())
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/v1/auth/sessions",
    response_model=SessionListResponse,
    tags=["authentication"],
)
def list_sessions(
    current: Annotated[CurrentAccount, Depends(_current_account)],
    service: Annotated[AccountService, Depends(_service)],
) -> dict:
    return service.list_sessions(current)


@router.delete(
    "/v1/auth/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["authentication"],
)
def revoke_session(
    session_id: uuid.UUID,
    current: Annotated[CurrentAccount, Depends(_current_account)],
    service: Annotated[AccountService, Depends(_service)],
) -> Response:
    service.revoke_owned_session(current, session_id=session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/v1/auth/reauth",
    response_model=ReauthResponse,
    tags=["authentication"],
)
async def reauth(
    body: ReauthRequest,
    request: Request,
    current: Annotated[CurrentAccount, Depends(_current_account)],
    service: Annotated[AccountService, Depends(_service)],
) -> dict:
    if body.scope == "link_email":
        _require_email_password_auth(request)
    _limit_ip(request, service, "reauth")
    return await service.create_reauth_challenge(
        current,
        provider=body.provider,
        provider_access_token=body.provider_access_token.get_secret_value(),
        scope=body.scope,
    )


@router.post(
    "/v1/auth/email/reauth",
    response_model=ReauthResponse,
    tags=["authentication"],
)
def reauth_email(
    body: EmailReauthRequest,
    request: Request,
    current: Annotated[CurrentAccount, Depends(_current_account)],
    service: Annotated[AccountService, Depends(_service)],
) -> dict:
    _require_email_password_auth(request)
    _limit_ip(request, service, "email-reauth")
    return service.create_email_reauth_challenge(
        current,
        password=body.password.get_secret_value(),
        scope=body.scope,
    )


async def _link(
    provider: ProviderName,
    body: LinkRequest,
    request: Request,
    current: CurrentAccount,
    service: AccountService,
) -> dict:
    _limit_ip(request, service, f"link-{provider}")
    return await service.link_identity(
        current,
        target_provider=provider,
        provider_access_token=body.provider_access_token.get_secret_value(),
        reauth_token=body.reauth_token.get_secret_value(),
    )


@router.post(
    "/v1/auth/yandex/link",
    response_model=LinkResponse,
    tags=["authentication"],
)
async def link_yandex(
    body: LinkRequest,
    request: Request,
    current: Annotated[CurrentAccount, Depends(_current_account)],
    service: Annotated[AccountService, Depends(_service)],
) -> dict:
    return await _link("yandex", body, request, current, service)


@router.post(
    "/v1/auth/vk/link",
    response_model=LinkResponse,
    tags=["authentication"],
)
async def link_vk(
    body: LinkRequest,
    request: Request,
    current: Annotated[CurrentAccount, Depends(_current_account)],
    service: Annotated[AccountService, Depends(_service)],
) -> dict:
    return await _link("vk", body, request, current, service)


@router.get("/v1/me", response_model=MeResponse, tags=["profile"])
def me(
    current: Annotated[CurrentAccount, Depends(_current_account)],
    service: Annotated[AccountService, Depends(_service)],
) -> dict:
    return service.get_me(current)


@router.get(
    "/v1/me/community-reward",
    response_model=CommunityRewardResponse,
    tags=["profile"],
)
def community_reward(
    current: Annotated[CurrentAccount, Depends(_current_account)],
    service: Annotated[CommunityRoleService, Depends(_community_roles)],
) -> dict:
    """The PRO discount earned with community achievements (2 % each, up to 40 %)."""
    return service.reward_for_account(current.account_id)


@router.get(
    "/v1/entitlements",
    response_model=EntitlementsResponse,
    tags=["billing"],
)
def entitlements(
    current: Annotated[CurrentAccount, Depends(_current_account)],
    service: Annotated[EntitlementService, Depends(_entitlements)],
) -> dict:
    return service.get_entitlements(current)


@router.post(
    "/v1/billing/rustore/verify",
    response_model=EntitlementsResponse,
    tags=["billing"],
)
def verify_rustore_purchase(
    body: RuStoreVerificationRequest,
    request: Request,
    current: Annotated[CurrentAccount, Depends(_current_account)],
    account_service: Annotated[AccountService, Depends(_service)],
    service: Annotated[EntitlementService, Depends(_entitlements)],
) -> dict:
    _limit_ip(request, account_service, "billing-verify")
    return service.verify_purchase(
        current,
        product_id=body.product_id,
        purchase_id=body.purchase_id,
        sandbox=body.sandbox,
    )


@router.post(
    "/v1/billing/rustore/notifications",
    status_code=status.HTTP_200_OK,
    tags=["billing"],
)
def rustore_notification(
    body: RuStoreNotificationEnvelope,
    request: Request,
    account_service: Annotated[AccountService, Depends(_service)],
    service: Annotated[EntitlementService, Depends(_entitlements)],
) -> Response:
    # Reject callback floods before AES-GCM work or a canonical Public API
    # fetch.  RuStore retries non-2xx responses, so a brief limit is recoverable.
    _limit_ip(request, account_service, "billing-notification")
    service.accept_notification(
        {
            "id": body.id,
            "timestamp": body.timestamp,
            "payload": body.payload.get_secret_value(),
        }
    )
    # RuStore retries unless it receives a literal 200 within three seconds.
    # The authenticated event is committed to the durable inbox above; the
    # canonical Public API fetch is deliberately outside this request path.
    return Response(status_code=status.HTTP_200_OK)


@router.patch(
    "/v1/me/nickname",
    response_model=NicknameResponse,
    tags=["profile"],
)
def change_nickname(
    body: NicknameRequest,
    request: Request,
    current: Annotated[CurrentAccount, Depends(_current_account)],
    service: Annotated[AccountService, Depends(_service)],
) -> dict:
    _limit_ip(request, service, "nickname")
    return service.change_nickname(current, nickname=body.nickname)


@router.delete(
    "/v1/me",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=DeletionResponse,
    tags=["profile"],
)
def delete_me(
    body: DeletionRequest,
    request: Request,
    current: Annotated[CurrentAccount, Depends(_current_account)],
    service: Annotated[AccountService, Depends(_service)],
) -> dict:
    _limit_ip(request, service, "deletion")
    return service.request_deletion(
        current, reauth_token=body.reauth_token.get_secret_value()
    )


@router.put(
    "/v1/push/device",
    response_model=PushRegistrationResponse,
    tags=["push"],
)
def register_push(
    body: PushDeviceRequest,
    request: Request,
    current: Annotated[CurrentAccount, Depends(_current_account)],
    service: Annotated[AccountService, Depends(_service)],
) -> dict:
    _limit_ip(request, service, "push-register")
    return service.register_push(
        current,
        fcm_token=body.fcm_token.get_secret_value(),
        platform=body.platform,
    )


@router.delete(
    "/v1/push/device",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["push"],
)
def disable_push(
    current: Annotated[CurrentAccount, Depends(_current_account)],
    service: Annotated[AccountService, Depends(_service)],
) -> Response:
    service.disable_current_push(current)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/.well-known/jwks.json",
    response_model=JwksResponse,
    tags=["authentication"],
)
def jwks(service: Annotated[AccountService, Depends(_service)]) -> dict:
    return service.token_service.jwks()


@router.post("/v1/internal/auth/verify", include_in_schema=False)
def verify_internal_access(request: Request) -> dict:
    """Confirm live session/account state after the app API verifies the JWT.

    This route is intentionally absent from the public OpenAPI document and
    requires a separate file-backed service credential before it performs any
    account lookup.
    """
    _require_internal_service(request)
    authorization = request.headers.get("authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token or len(token) > 8192:
        raise AccountAPIError("auth_required", status_code=401)
    current = _service(request).authenticate(token)
    return {
        "session_id": str(current.session_id),
        "actor_id": str(current.community_actor_id),
        "nickname": current.nickname,
        "authorization_version": current.authorization_version,
    }


@router.post(
    "/v1/internal/entitlements/consume",
    response_model=InternalEntitlementDecision,
    include_in_schema=False,
)
def consume_internal_entitlement(
    body: InternalEntitlementConsumeRequest,
    request: Request,
    service: Annotated[EntitlementService, Depends(_entitlements)],
) -> dict:
    _require_internal_service(request)
    return service.consume(
        actor_id=body.actor_id,
        feature=body.feature,
        request_id=body.request_id,
    )


@router.post(
    "/v1/internal/entitlements/release",
    response_model=InternalEntitlementDecision,
    include_in_schema=False,
)
def release_internal_entitlement(
    body: InternalEntitlementConsumeRequest,
    request: Request,
    service: Annotated[EntitlementService, Depends(_entitlements)],
) -> dict:
    _require_internal_service(request)
    return service.release(
        actor_id=body.actor_id,
        feature=body.feature,
        request_id=body.request_id,
    )


@router.post(
    "/v1/internal/billing/rustore/notifications/replay-dead-letters",
    response_model=InternalNotificationReplayResponse,
    include_in_schema=False,
)
def replay_internal_rustore_dead_letters(
    body: InternalNotificationReplayRequest,
    request: Request,
    service: Annotated[EntitlementService, Depends(_entitlements)],
) -> dict:
    _require_internal_service(request)
    replayed = service.replay_dead_letters(limit=body.limit)
    # Only aggregate queue gauges leave the service. Notification IDs,
    # purchase IDs, fingerprints, payload fields, and error details do not.
    return {"replayed": replayed, **service.notification_metrics()}


@router.post(
    "/v1/internal/vendor-budgets/plantnet/reserve",
    response_model=InternalPlantNetBudgetDecision,
    include_in_schema=False,
)
def reserve_internal_plantnet_budget(
    body: InternalPlantNetBudgetRequest,
    request: Request,
    service: Annotated[VendorBudgetService, Depends(_vendor_budgets)],
) -> dict:
    _require_internal_service(request)
    try:
        return service.reserve_plantnet_attempt(request_id=body.request_id)
    except VendorBudgetUnavailable:
        raise AccountAPIError("vendor_budget_unavailable", status_code=503) from None


@router.post("/v1/internal/community/roles/lookup", include_in_schema=False)
def lookup_internal_community_roles(
    body: InternalCommunityActorRequest,
    request: Request,
    service: Annotated[CommunityRoleService, Depends(_community_roles)],
) -> dict:
    _require_internal_service(request)
    return {"roles": service.roles_for_actor(body.actor_id)}


@router.post("/v1/internal/community/roles/list", include_in_schema=False)
def list_internal_community_roles(
    body: InternalCommunityListRequest,
    request: Request,
    service: Annotated[CommunityRoleService, Depends(_community_roles)],
) -> dict:
    _require_internal_service(request)
    return {"items": service.holders()}


@router.post("/v1/internal/community/roles/grant", include_in_schema=False)
def grant_internal_community_role(
    body: InternalCommunityRoleChange,
    request: Request,
    service: Annotated[CommunityRoleService, Depends(_community_roles)],
) -> dict:
    _require_internal_service(request)
    return _role_call(
        lambda: service.grant(
            actor_id=body.actor_id,
            role=body.role,
            scope=body.scope,
            granted_by_actor_id=body.by_actor_id,
        )
    )


@router.post("/v1/internal/community/roles/revoke", include_in_schema=False)
def revoke_internal_community_role(
    body: InternalCommunityRoleChange,
    request: Request,
    service: Annotated[CommunityRoleService, Depends(_community_roles)],
) -> dict:
    _require_internal_service(request)
    revoked = _role_call(
        lambda: service.revoke(
            actor_id=body.actor_id,
            role=body.role,
            scope=body.scope,
            revoked_by_actor_id=body.by_actor_id,
        )
    )
    return {"revoked": bool(revoked)}


@router.post("/v1/internal/community/rewards", include_in_schema=False)
def set_internal_community_reward(
    body: InternalCommunityRewardRequest,
    request: Request,
    service: Annotated[CommunityRoleService, Depends(_community_roles)],
) -> dict:
    _require_internal_service(request)
    return _role_call(
        lambda: service.set_reward(
            actor_id=body.actor_id, achievement_count=body.achievement_count
        )
    )
