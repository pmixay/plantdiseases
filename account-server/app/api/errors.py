"""Stable, non-reflective account API errors."""

from __future__ import annotations

from collections.abc import Mapping


class AccountAPIError(RuntimeError):
    """An expected API failure whose message contains only a stable code."""

    def __init__(
        self,
        code: str,
        *,
        status_code: int = 400,
        headers: Mapping[str, str] | None = None,
    ):
        self.code = code
        self.status_code = status_code
        self.headers = dict(headers or {})
        super().__init__(code)
