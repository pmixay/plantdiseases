"""Country and coarse-location normalisation for the community corpus.

Shared by the community routes (scan upload, nearby, map) and the popular-plants
bundle; the client's location is never geolocated from its IP on the server.
"""

# Imports work flat (``uvicorn main:app`` from ``server/``) or as ``server.*``.
try:
    from . import countries
except ImportError:
    import countries


# Region-string alphabet (country name / code + city / admin area).
# A region char is any Unicode letter (str.isalpha() is Unicode-aware, so
# Cyrillic / accented Latin / CJK localities like "Москва", "Köln", "東京" all
# pass) or digit, plus apostrophe + period + hyphen + space, which cover the
# bulk of place names ("Saint-John's", "Plymouth, U.K." trims to "Plymouth").
# Commas are deliberately excluded — country and locality are separate fields,
# so callers never embed a comma — and control chars / injection punctuation
# (";", "\n", …) still get filtered out.
def region_char_ok(ch: str) -> bool:
    return ch.isalpha() or ch.isdigit() or ch in " .'-"


def normalise_country(raw: str | None) -> str:
    """Sanitise the top-level country bucket.

    Accepts the new explicit country form ("GB", "Germany") and the
    legacy "City, Country" composite that older clients still send —
    in the latter case we keep the segment after the last comma. The
    result is trimmed, charset-checked and capped at 80 chars; an
    empty string means "rejected".
    """
    if not raw:
        return ""
    if "," in raw:
        raw = raw.rsplit(",", 1)[-1]
    cleaned = raw.strip()[:80]
    if not cleaned or any(not region_char_ok(ch) for ch in cleaned):
        return ""
    # Canonicalize to an ISO-3166 alpha-2 code so "RU" / "ru" / "Russia" /
    # "Россия" all collapse into one bucket — otherwise the same country
    # splits across locale-specific representations and the popular / nearby
    # aggregation (and the curated ``popular_for`` map, which only matches
    # 2-letter codes) disagree per locale.
    return countries.to_iso_alpha2(cleaned)


def normalise_locality(raw: str | None) -> str:
    """Sanitise the sub-country city / admin-area string.

    Returns the cleaned value or "" when the caller didn't send a
    locality (country-only writes are legal — the corpus simply
    skips the second hierarchy level).
    """
    if not raw:
        return ""
    cleaned = raw.strip()[:80]
    if not cleaned or any(not region_char_ok(ch) for ch in cleaned):
        return ""
    return cleaned


# There is no "worldwide" bucket any more — every scope is a real country.
# When an IP can't be located at all (private/VPN/no .mmdb) we fall back to
# this primary-market country so the feed + carousels still have a scope.
# Legacy sentinel still present in old DB rows; only used to scrub them on read.
GLOBAL_COUNTRY = "GLOBAL"


def country_display(country: str | None) -> dict:
    code = (country or "").strip().upper()
    if not code or code == GLOBAL_COUNTRY:
        # No location (community off / permission denied) → worldwide.
        return {
            "country": None,
            "country_name_en": "Worldwide",
            "country_name_ru": "по всему миру",
            "scope": "global",
        }
    en, ru = countries.COUNTRY_NAMES.get(code, (country or code, country or code))
    return {
        "country": code if len(code) == 2 else country,
        "country_name_en": en,
        "country_name_ru": ru,
        "scope": "country",
    }


def localize_item_country(row: dict) -> None:
    """Annotate one community-feed row with localized country *names* and
    scrub the internal ``GLOBAL`` bucket sentinel.

    Each row stores ``country`` as an ISO alpha-2 code (e.g. ``"PL"``) — the
    raw code is useless to a reader, so the feed cards used to print "PL".
    We attach ``country_name_en`` / ``country_name_ru`` (so the client can
    show "Poland" / "Польша" per locale) and blank the ``GLOBAL`` sentinel
    that detection-failed uploads carry, since it isn't a real place.
    """
    code = str(row.get("country") or "").strip().upper()
    if not code or code == GLOBAL_COUNTRY:
        row["country"] = ""
        row["country_name_en"] = None
        row["country_name_ru"] = None
        return
    en, ru = countries.COUNTRY_NAMES.get(code, (row.get("country"), row.get("country")))
    row["country_name_en"] = en
    row["country_name_ru"] = ru


def valid_coord(value, limit: float) -> float | None:
    """Parse a client-sent coordinate, range-check it, and round to ~1 km so a
    precise position never lands in the corpus (coarse-location privacy)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or abs(f) > limit:  # NaN or out of range
        return None
    return round(f, 2)


def client_location(
    country: str | None = None,
    region: str | None = None,
    lat=None,
    lon=None,
) -> dict:
    """Normalize the client-supplied coarse location. There is no server-side
    IP geolocation any more — the app sends what it resolved from
    ``ACCESS_COARSE_LOCATION`` (+ an on-device reverse-geocode). Any field may
    be empty (community off / permission denied) → the caller falls back to the
    worldwide scope.
    """
    # ``normalise_country`` already canonicalizes to an ISO alpha-2 code.
    c = (normalise_country(country) if country else "")
    city = normalise_locality(region) if region else ""
    la = valid_coord(lat, 90.0)
    lo = valid_coord(lon, 180.0)
    if la is None or lo is None:  # coords only count as a pair
        la = lo = None
    return {"country": c or None, "city": city or None, "lat": la, "lon": lo}


