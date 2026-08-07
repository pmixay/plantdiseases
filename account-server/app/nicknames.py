"""Unicode nickname normalization and MVP anti-impersonation rules."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from app.api.errors import AccountAPIError

_RESERVED = {
    "admin",
    "administrator",
    "moderator",
    "plantdiseases",
    "root",
    "support",
    "system",
    "админ",
    "модератор",
    "поддержка",
    "система",
}


@dataclass(frozen=True, slots=True)
class ValidatedNickname:
    display: str
    normalized: str


def normalize_nickname(value: str) -> str:
    """Apply the database uniqueness transform required by the contract."""
    return unicodedata.normalize("NFKC", value).casefold()


def validate_nickname(value: str, *, blocked: tuple[str, ...] = ()) -> ValidatedNickname:
    display = unicodedata.normalize("NFKC", value)
    if not 3 <= len(display) <= 24:
        raise AccountAPIError("nickname_invalid")

    scripts: set[str] = set()
    has_letter = False
    for character in display:
        category = unicodedata.category(character)
        if category.startswith("L"):
            has_letter = True
            name = unicodedata.name(character, "")
            if "LATIN" in name:
                scripts.add("latin")
            elif "CYRILLIC" in name:
                scripts.add("cyrillic")
            else:
                raise AccountAPIError("nickname_invalid")
        elif category == "Nd" or character == "_":
            continue
        else:
            raise AccountAPIError("nickname_invalid")

    if not has_letter or len(scripts) != 1:
        raise AccountAPIError("nickname_invalid")

    normalized = normalize_nickname(display)
    blocked_normalized = {normalize_nickname(item) for item in blocked}
    if normalized in _RESERVED or normalized in blocked_normalized:
        raise AccountAPIError("nickname_invalid")
    return ValidatedNickname(display=display, normalized=normalized)
