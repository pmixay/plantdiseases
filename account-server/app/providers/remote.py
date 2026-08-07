"""Minimal server-side verification for Yandex ID and VK ID access tokens.

Provider tokens are opaque credentials. They are sent exactly once to the
provider's fixed HTTPS user-information endpoint, never placed in a URL or
log, and discarded after an allowlisted identity has been built.
"""

from __future__ import annotations

import asyncio
import json
import re
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    Request,
    build_opener,
)

from app.providers.base import (
    ProviderAdapter,
    ProviderErrorCode,
    ProviderName,
    ProviderVerificationError,
    VerifiedProviderIdentity,
)

YANDEX_USER_INFO_URL = "https://login.yandex.ru/info?format=json"
VK_USER_INFO_URL = "https://id.vk.ru/oauth2/user_info"
_MAX_PROVIDER_RESPONSE_BYTES = 64 * 1024
_MAX_TOKEN_LENGTH = 8192
_MAX_SUBJECT_LENGTH = 512
_MAX_DISPLAY_NAME_LENGTH = 200
_MAX_AVATAR_URL_LENGTH = 2048
_YANDEX_AVATAR_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_APPLICATION_ERRORS = frozenset(
    {"invalid_client", "invalid_client_id", "client_mismatch", "application_mismatch"}
)
_INVALID_TOKEN_ERRORS = frozenset(
    {
        "access_denied",
        "invalid_grant",
        "invalid_request",
        "invalid_token",
        "unauthorized_client",
    }
)


@dataclass(frozen=True, slots=True)
class ProviderHttpResponse:
    status_code: int
    payload: object


class ProviderTransportError(RuntimeError):
    """Fixed transport failure which deliberately contains no request detail."""


class ProviderHttpTransport(Protocol):
    async def request_json(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        form: Mapping[str, str] | None,
        timeout_seconds: int,
    ) -> ProviderHttpResponse: ...


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        del req, fp, code, msg, headers, newurl
        return None


class UrllibProviderTransport:
    """Small network transport with system TLS, no redirects, and bounded JSON."""

    async def request_json(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        form: Mapping[str, str] | None,
        timeout_seconds: int,
    ) -> ProviderHttpResponse:
        return await asyncio.to_thread(
            self._request_json,
            method=method,
            url=url,
            headers=headers,
            form=form,
            timeout_seconds=timeout_seconds,
        )

    @staticmethod
    def _request_json(
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        form: Mapping[str, str] | None,
        timeout_seconds: int,
    ) -> ProviderHttpResponse:
        encoded = urlencode(form).encode("ascii") if form is not None else None
        request_headers = {
            "Accept": "application/json",
            "User-Agent": "PlantDiseases-Account-API",
            **headers,
        }
        if encoded is not None:
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = Request(
            url,
            data=encoded,
            headers=request_headers,
            method=method,
        )
        opener = build_opener(
            _NoRedirects(),
            HTTPSHandler(context=ssl.create_default_context()),
        )
        try:
            with opener.open(request, timeout=timeout_seconds) as response:
                body = response.read(_MAX_PROVIDER_RESPONSE_BYTES + 1)
                status = int(response.status)
        except HTTPError as failure:
            status = int(failure.code)
            body = failure.read(_MAX_PROVIDER_RESPONSE_BYTES + 1)
        except (OSError, TimeoutError, URLError) as failure:
            raise ProviderTransportError("provider request failed") from failure
        if len(body) > _MAX_PROVIDER_RESPONSE_BYTES:
            raise ProviderTransportError("provider response was too large")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as failure:
            raise ProviderTransportError("provider response was invalid") from failure
        return ProviderHttpResponse(status_code=status, payload=payload)


def _token(value: str) -> str:
    if (
        not value
        or len(value) > _MAX_TOKEN_LENGTH
        or any(not 0x21 <= ord(character) <= 0x7E for character in value)
    ):
        raise ProviderVerificationError(ProviderErrorCode.INVALID_TOKEN)
    return value


def _required_subject(value: object) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= _MAX_SUBJECT_LENGTH
        or any(not 0x21 <= ord(character) <= 0x7E for character in value)
    ):
        raise ProviderVerificationError(ProviderErrorCode.PROFILE_REQUIRED)
    return value


def _required_text(value: object, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise ProviderVerificationError(ProviderErrorCode.PROFILE_REQUIRED)
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > maximum:
        raise ProviderVerificationError(ProviderErrorCode.PROFILE_REQUIRED)
    return normalized


def _safe_avatar(value: object) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or len(value) > _MAX_AVATAR_URL_LENGTH:
        return None
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        return None
    return value


def _provider_error(response: ProviderHttpResponse) -> ProviderVerificationError | None:
    payload = response.payload if isinstance(response.payload, dict) else {}
    error = str(payload.get("error", "")).strip().lower()
    if error in _APPLICATION_ERRORS:
        return ProviderVerificationError(ProviderErrorCode.APPLICATION_MISMATCH)
    if error in _INVALID_TOKEN_ERRORS or response.status_code in {400, 401, 403}:
        return ProviderVerificationError(ProviderErrorCode.INVALID_TOKEN)
    if response.status_code == 429 or response.status_code >= 500:
        return ProviderVerificationError(ProviderErrorCode.UNAVAILABLE)
    if not 200 <= response.status_code < 300 or error:
        return ProviderVerificationError(ProviderErrorCode.UNAVAILABLE)
    return None


class YandexProviderAdapter(ProviderAdapter):
    provider: ProviderName = "yandex"

    def __init__(
        self,
        *,
        client_id: str,
        timeout_seconds: int,
        transport: ProviderHttpTransport | None = None,
    ):
        self.client_id = client_id
        self.timeout_seconds = timeout_seconds
        self.transport = transport or UrllibProviderTransport()

    async def verify(self, access_token: str) -> VerifiedProviderIdentity:
        try:
            response = await self.transport.request_json(
                method="GET",
                url=YANDEX_USER_INFO_URL,
                headers={"Authorization": f"OAuth {_token(access_token)}"},
                form=None,
                timeout_seconds=self.timeout_seconds,
            )
        except ProviderTransportError as failure:
            raise ProviderVerificationError(ProviderErrorCode.UNAVAILABLE) from failure
        error = _provider_error(response)
        if error is not None:
            raise error
        if not isinstance(response.payload, dict):
            raise ProviderVerificationError(ProviderErrorCode.UNAVAILABLE)
        if response.payload.get("client_id") != self.client_id:
            raise ProviderVerificationError(ProviderErrorCode.APPLICATION_MISMATCH)

        subject = _required_subject(response.payload.get("psuid"))
        display_name = _required_text(
            response.payload.get("display_name"),
            maximum=_MAX_DISPLAY_NAME_LENGTH,
        )
        avatar_url = None
        avatar_id = response.payload.get("default_avatar_id")
        if (
            response.payload.get("is_avatar_empty") is not True
            and isinstance(avatar_id, str)
            and _YANDEX_AVATAR_ID.fullmatch(avatar_id)
        ):
            avatar_url = (
                f"https://avatars.yandex.net/get-yapic/{avatar_id}/islands-200"
            )
        return VerifiedProviderIdentity(
            provider=self.provider,
            subject=subject,
            display_name=display_name,
            avatar_url=avatar_url,
            refreshed_at=datetime.now(UTC),
        )


class VkProviderAdapter(ProviderAdapter):
    provider: ProviderName = "vk"

    def __init__(
        self,
        *,
        client_id: str,
        timeout_seconds: int,
        transport: ProviderHttpTransport | None = None,
    ):
        self.client_id = client_id
        self.timeout_seconds = timeout_seconds
        self.transport = transport or UrllibProviderTransport()

    async def verify(self, access_token: str) -> VerifiedProviderIdentity:
        try:
            response = await self.transport.request_json(
                method="POST",
                url=f"{VK_USER_INFO_URL}?{urlencode({'client_id': self.client_id})}",
                headers={},
                form={"access_token": _token(access_token)},
                timeout_seconds=self.timeout_seconds,
            )
        except ProviderTransportError as failure:
            raise ProviderVerificationError(ProviderErrorCode.UNAVAILABLE) from failure
        error = _provider_error(response)
        if error is not None:
            raise error
        if not isinstance(response.payload, dict):
            raise ProviderVerificationError(ProviderErrorCode.UNAVAILABLE)
        user = response.payload.get("user")
        if not isinstance(user, dict):
            raise ProviderVerificationError(ProviderErrorCode.PROFILE_REQUIRED)

        subject = _required_subject(user.get("user_id"))
        first_name = _required_text(
            user.get("first_name"),
            maximum=_MAX_DISPLAY_NAME_LENGTH,
        )
        last_name_value = user.get("last_name")
        last_name = (
            _required_text(last_name_value, maximum=_MAX_DISPLAY_NAME_LENGTH)
            if last_name_value not in (None, "")
            else ""
        )
        display_name = f"{first_name} {last_name}".strip()
        if len(display_name) > _MAX_DISPLAY_NAME_LENGTH:
            raise ProviderVerificationError(ProviderErrorCode.PROFILE_REQUIRED)
        return VerifiedProviderIdentity(
            provider=self.provider,
            subject=subject,
            display_name=display_name,
            avatar_url=_safe_avatar(user.get("avatar")),
            refreshed_at=datetime.now(UTC),
        )
