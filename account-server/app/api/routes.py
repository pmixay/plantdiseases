"""Versioned account HTTP routes with strict, secret-safe request models."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.api.errors import AccountAPIError
from app.api.schemas import (
    AuthenticatedResponse,
    DeletionResponse,
    ErrorResponse,
    JwksResponse,
    LinkResponse,
    MeResponse,
    NicknameResponse,
    PendingSignupResponse,
    PushRegistrationResponse,
    ReauthResponse,
    SessionListResponse,
)
from app.providers.base import ProviderName
from app.services.accounts import AccountService, CurrentAccount

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


class CompleteSignupRequest(StrictRequest):
    pending_signup_token: SecretStr = Field(min_length=1, max_length=256)
    nickname: str = Field(min_length=1, max_length=64)
    installation_id: uuid.UUID


class CancelSignupRequest(StrictRequest):
    pending_signup_token: SecretStr = Field(min_length=1, max_length=256)


class RefreshRequest(StrictRequest):
    refresh_token: SecretStr = Field(min_length=1, max_length=256)


class ReauthRequest(StrictRequest):
    provider: Literal["yandex", "vk"]
    provider_access_token: SecretStr = Field(min_length=1, max_length=8192)
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


def _service(request: Request) -> AccountService:
    service = getattr(request.app.state, "account_service", None)
    if service is None:
        raise AccountAPIError("service_unavailable", status_code=503)
    return service


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
    address = request.client.host if request.client is not None else "unknown"
    service.limit_ip(operation, address)


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
    "/v1/auth/signup/complete",
    status_code=status.HTTP_201_CREATED,
    response_model=AuthenticatedResponse,
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
        nickname=body.nickname,
        installation_id=body.installation_id,
    )


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
    service.cancel_signup(pending_token=body.pending_signup_token.get_secret_value())
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
    return service.refresh(refresh_token=body.refresh_token.get_secret_value())


@router.post(
    "/v1/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["authentication"],
)
def logout(
    current: Annotated[CurrentAccount, Depends(_current_account)],
    service: Annotated[AccountService, Depends(_service)],
) -> Response:
    service.logout(current)
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
    _limit_ip(request, service, "reauth")
    return await service.create_reauth_challenge(
        current,
        provider=body.provider,
        provider_access_token=body.provider_access_token.get_secret_value(),
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
    current: Annotated[CurrentAccount, Depends(_current_account)],
    service: Annotated[AccountService, Depends(_service)],
) -> dict:
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
