"""Bounded internal delivery of account-deletion tombstones to the app API."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable

from app.config import Settings

_MAX_RESPONSE_BYTES = 4096


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _open_no_redirect(request: urllib.request.Request, *, timeout: float):
    return urllib.request.build_opener(_NoRedirectHandler()).open(
        request, timeout=timeout
    )


def _post_internal_json(url: str, document: dict[str, str], service_token: str) -> bool:
    request = urllib.request.Request(
        url,
        data=json.dumps(document, separators=(",", ":")).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Internal-Service-Token": service_token,
            "User-Agent": "plantdiseases-account-api",
        },
        method="POST",
    )
    try:
        with _open_no_redirect(request, timeout=2.0) as response:
            body = response.read(_MAX_RESPONSE_BYTES + 1)
            return response.status == 202 and len(body) <= _MAX_RESPONSE_BYTES
    except (OSError, urllib.error.URLError):
        return False


def build_community_deletion_dispatcher(
    settings: Settings,
    *,
    service_token: str | None,
) -> Callable[[uuid.UUID], bool]:
    url = settings.community_deletion_url
    if not url or not service_token:
        return lambda _actor_id: False

    def dispatch(actor_id: uuid.UUID) -> bool:
        return _post_internal_json(url, {"actor_id": str(actor_id)}, service_token)

    return dispatch


def build_community_nickname_dispatcher(
    settings: Settings,
    *,
    service_token: str | None,
) -> Callable[[uuid.UUID, str], bool]:
    deletion_url = settings.community_deletion_url
    if not deletion_url or not service_token:
        return lambda _actor_id, _nickname: False
    base, separator, endpoint = deletion_url.rpartition("/")
    if not separator or endpoint != "account-deletions":
        return lambda _actor_id, _nickname: False
    url = f"{base}/account-nicknames"

    def dispatch(actor_id: uuid.UUID, nickname: str) -> bool:
        return _post_internal_json(
            url,
            {"actor_id": str(actor_id), "nickname": nickname},
            service_token,
        )

    return dispatch


__all__ = [
    "build_community_deletion_dispatcher",
    "build_community_nickname_dispatcher",
]
