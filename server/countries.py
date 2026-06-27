"""ISO-3166 alpha-2 country canonicalization + EN/RU display names.

Shared by the HTTP layer (``main.py``) and the community store
(``community.py``) so a country bucket is the SAME whether the client sent
an ISO code (``"RU"``), an English name (``"Russia"``) or a localized name
(``"Россия"``). Without this the corpus split one country across several
representations and the popular / nearby aggregation disagreed per locale
(an English-locale user saw a near-empty feed while Russian worked).

Leaf module — imports nothing from the app, so both ``main`` and
``community`` can depend on it without an import cycle.
"""
from __future__ import annotations

# ISO alpha-2 -> (English name, Russian name). Covers every country
# ``popular_plants.BY_REGION`` curates plus the ones clients most often
# resolve to, so the Home carousel title is a localized name, not a bare code.
COUNTRY_NAMES: dict[str, tuple[str, str]] = {
    "AR": ("Argentina", "Аргентина"),
    "AU": ("Australia", "Австралия"),
    "AT": ("Austria", "Австрия"),
    "BE": ("Belgium", "Бельгия"),
    "BR": ("Brazil", "Бразилия"),
    "BG": ("Bulgaria", "Болгария"),
    "CA": ("Canada", "Канада"),
    "CN": ("China", "Китай"),
    "HR": ("Croatia", "Хорватия"),
    "CZ": ("Czechia", "Чехия"),
    "DK": ("Denmark", "Дания"),
    "FI": ("Finland", "Финляндия"),
    "FR": ("France", "Франция"),
    "DE": ("Germany", "Германия"),
    "GR": ("Greece", "Греция"),
    "HU": ("Hungary", "Венгрия"),
    "IN": ("India", "Индия"),
    "ID": ("Indonesia", "Индонезия"),
    "IE": ("Ireland", "Ирландия"),
    "IL": ("Israel", "Израиль"),
    "IT": ("Italy", "Италия"),
    "JP": ("Japan", "Япония"),
    "KZ": ("Kazakhstan", "Казахстан"),
    "MX": ("Mexico", "Мексика"),
    "NL": ("Netherlands", "Нидерланды"),
    "NZ": ("New Zealand", "Новая Зеландия"),
    "NO": ("Norway", "Норвегия"),
    "PL": ("Poland", "Польша"),
    "PT": ("Portugal", "Португалия"),
    "RO": ("Romania", "Румыния"),
    "RU": ("Russia", "Россия"),
    "RS": ("Serbia", "Сербия"),
    "SG": ("Singapore", "Сингапур"),
    "SK": ("Slovakia", "Словакия"),
    "ZA": ("South Africa", "ЮАР"),
    "KR": ("South Korea", "Южная Корея"),
    "ES": ("Spain", "Испания"),
    "SE": ("Sweden", "Швеция"),
    "CH": ("Switzerland", "Швейцария"),
    "TR": ("Turkey", "Турция"),
    "UA": ("Ukraine", "Украина"),
    "AE": ("United Arab Emirates", "ОАЭ"),
    "GB": ("United Kingdom", "Великобритания"),
    "US": ("United States", "США"),
    "VN": ("Vietnam", "Вьетнам"),
}

# Extra spellings a reverse-geocoder / older client might send, beyond the
# canonical EN/RU names above. Keys are lowercased.
_COUNTRY_ALIASES: dict[str, str] = {
    "usa": "US",
    "u.s.a.": "US",
    "u.s.": "US",
    "united states of america": "US",
    "america": "US",
    "uk": "GB",
    "u.k.": "GB",
    "great britain": "GB",
    "britain": "GB",
    "england": "GB",
    "russian federation": "RU",
    "republic of korea": "KR",
    "korea": "KR",
    "uae": "AE",
    "türkiye": "TR",
    "turkiye": "TR",
    "czech republic": "CZ",
    "viet nam": "VN",
    "holland": "NL",
}

# name (lowercased, EN or RU) -> ISO code. Built once at import.
_NAME_TO_CODE: dict[str, str] = {}
for _code, (_en, _ru) in COUNTRY_NAMES.items():
    _NAME_TO_CODE[_en.lower()] = _code
    _NAME_TO_CODE[_ru.lower()] = _code
_NAME_TO_CODE.update(_COUNTRY_ALIASES)


def to_iso_alpha2(raw: str | None) -> str:
    """Canonicalize a country string to its ISO-3166 alpha-2 code.

    Accepts a code (``"ru"`` / ``"RU"``), an English name (``"Russia"``) or a
    localized name (``"Россия"``) and returns the uppercase 2-letter code. An
    unknown value passes through trimmed (so a never-mapped country still
    buckets consistently by its own string); empty in -> empty out.
    """
    s = (raw or "").strip()
    if not s:
        return ""
    # Name/alias map first so a 2-letter alias ("uk" -> GB) wins over the bare
    # uppercase shortcut; a real ISO code ("ru") isn't a name key, so it falls
    # through to the 2-letter branch and uppercases to "RU".
    mapped = _NAME_TO_CODE.get(s.lower())
    if mapped:
        return mapped
    if len(s) == 2 and s.isalpha():
        return s.upper()
    return s
