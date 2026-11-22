"""The private service credential the account service presents to the community API."""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path


def _load_internal_token(token_path: str) -> str | None:
    if not token_path:
        return None
    path = Path(token_path)
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o027:
            return None
        token = path.read_text(encoding="ascii").strip()
    except OSError:
        return None
    return token if 32 <= len(token) <= 512 else None


_internal_service_token = _load_internal_token(
    os.environ.get("INTERNAL_SERVICE_TOKEN_FILE", "").strip()
)


def internal_service_authorized(supplied: str) -> bool:
    """Constant-time check of the private service credential.

    ``compare_digest`` raises TypeError on non-ASCII *str* operands, and
    Starlette decodes header bytes before this sees them, so an unauthenticated
    request carrying one high byte in ``X-Internal-Service-Token`` used to
    escape as a 500 with a traceback rather than the stable 401 the internal
    routes promise. ``_load_internal_token`` reads the credential as ASCII, so
    a non-ASCII candidate can only ever be wrong: reject it first.
    """
    token = _internal_service_token
    return bool(
        token
        and supplied
        and len(supplied) <= 512
        and supplied.isascii()
        and token.isascii()
        and secrets.compare_digest(token, supplied)
    )


__all__ = [
    "internal_service_authorized",
]
