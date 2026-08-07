"""Bounded internal delivery of account-deletion tombstones to the app API."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable

from app.config import Settings


def build_community_deletion_dispatcher(
    settings: Settings,
    *,
    service_token: str | None,
) -> Callable[[uuid.UUID], bool]:
    url = settings.community_deletion_url
    if not url or not service_token:
        return lambda _actor_id: False

    def dispatch(actor_id: uuid.UUID) -> bool:
        body = json.dumps(
            {"actor_id": str(actor_id)}, separators=(",", ":")
        ).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Internal-Service-Token": service_token,
                "User-Agent": "plantdiseases-account-api",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=2.0) as response:
                response.read(4097)
                return response.status == 202
        except (OSError, urllib.error.URLError):
            return False

    return dispatch


__all__ = ["build_community_deletion_dispatcher"]
