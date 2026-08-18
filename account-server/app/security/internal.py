"""Private service-to-service authentication material."""

from __future__ import annotations

import secrets
from pathlib import Path

from app.config import Settings, SettingsError


def load_internal_service_token(settings: Settings) -> str | None:
    path: Path | None = settings.internal_service_token_file
    if path is None:
        if settings.environment == "test" and settings.allow_ephemeral_keys:
            return secrets.token_urlsafe(32)
        return None
    try:
        token = path.read_text(encoding="ascii").strip()
    except OSError as exc:
        raise SettingsError("Cannot read internal service token") from exc
    if not 32 <= len(token) <= 512:
        raise SettingsError("Internal service token has an invalid length")
    return token


__all__ = ["load_internal_service_token"]
