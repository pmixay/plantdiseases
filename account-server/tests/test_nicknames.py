from __future__ import annotations

import pytest

from app.api.errors import AccountAPIError
from app.nicknames import normalize_nickname, validate_nickname


def test_nfkc_casefold_is_the_uniqueness_key():
    assert normalize_nickname("Ｌｅａｆ_ÖNE") == "leaf_öne"
    validated = validate_nickname("Ｌｅａｆ_ÖNE")
    assert validated.display == "Leaf_ÖNE"
    assert validated.normalized == "leaf_öne"


@pytest.mark.parametrize(
    "nickname",
    [
        "ab",
        "a" * 25,
        "123_456",
        "leaf name",
        "leaf-name",
        "leaf🌱",
        "φυτο",
        "Leaf_л",
        "e\u0301e",
        "admin",
        "ПОДДЕРЖКА",
    ],
)
def test_invalid_reserved_or_mixed_script_nicknames_are_rejected(nickname: str):
    with pytest.raises(AccountAPIError) as exc_info:
        validate_nickname(nickname)
    assert exc_info.value.code == "nickname_invalid"


def test_configured_blocklist_uses_the_same_normalization():
    with pytest.raises(AccountAPIError, match="nickname_invalid"):
        validate_nickname("Ｂｌｏｃｋｅｄ", blocked=("blocked",))


@pytest.mark.parametrize("nickname", ["Leaf_123", "Растение_7", "Überblatt", "Ёлка"])
def test_valid_latin_or_cyrillic_nicknames_are_accepted(nickname: str):
    assert validate_nickname(nickname).display == nickname
