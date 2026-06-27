"""Server-side IP -> location via MaxMind GeoLite2-City.

Drop a ``GeoLite2-City.mmdb`` into the ``server/`` folder (provisioned out of
band — it's ~60 MB and license-restricted, so never committed; we also accept
any ``*.mmdb`` sitting next to it). The resolver then returns country +
subdivision + city + coordinates. Without the database, or for a private /
unresolvable IP, it falls back to the CDN country header, and finally to
``None`` — callers default to the configured primary-market country.

The reader is memory-mapped and opened once; lookups are local + fast (no
network), so this is safe to call inline from async handlers. There is
deliberately no HTTPS geo-lookup fallback any more: the old ipapi.co call
blocked the event loop and only ever yielded a country, which the .mmdb (or
the country header) already covers.
"""
from __future__ import annotations

import ipaddress
import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("plantdiseases.geoip")

# CDN/proxy-injected country headers, in preference order. Country only — no
# city — so they only help the country fallback, not the "nearby" grouping.
_COUNTRY_HEADERS = (
    "cf-ipcountry",
    "x-vercel-ip-country",
    "cloudfront-viewer-country",
    "fastly-client-country-code",
    "x-country-code",
    "x-geo-country",
)

_DB_DIR = Path(__file__).resolve().parent
_CACHE_MAX = 8192


@dataclass(frozen=True)
class GeoLocation:
    """A resolved coarse location. Any field may be ``None``."""

    country: str | None = None
    subdivision: str | None = None
    city: str | None = None
    lat: float | None = None
    lon: float | None = None


# Lazily-opened mmdb reader (thread-safe for reads once open).
_reader = None
_reader_tried = False
_reader_lock = threading.Lock()

# ip -> GeoLocation|None LRU. Same pattern as species_info._cache.
_cache: "OrderedDict[str, GeoLocation | None]" = OrderedDict()
_lock = threading.Lock()


def _find_db() -> Path | None:
    """First ``GeoLite2-City.mmdb`` in the server folder, else any ``*.mmdb``."""
    named = _DB_DIR / "GeoLite2-City.mmdb"
    if named.exists():
        return named
    for p in sorted(_DB_DIR.glob("*.mmdb")):
        return p
    return None


def _get_reader():
    """Open the mmdb once. Returns ``None`` (cached) when absent/unreadable."""
    global _reader, _reader_tried
    if _reader_tried:
        return _reader
    with _reader_lock:
        if _reader_tried:
            return _reader
        _reader_tried = True
        path = _find_db()
        if path is None:
            logger.info(
                "No GeoLite2 .mmdb in %s; geo uses country header + default only",
                _DB_DIR,
            )
            return None
        try:
            import geoip2.database  # lazy: only needed when a db is present
            _reader = geoip2.database.Reader(str(path))
            logger.info("GeoLite2 city DB loaded from %s", path)
        except Exception as exc:  # missing dep / corrupt db — stay fail-soft
            logger.warning("Failed to open GeoLite2 db %s: %s", path, exc)
            _reader = None
        return _reader


def _valid_code(raw: str | None) -> str | None:
    code = (raw or "").strip().upper()
    return code if len(code) == 2 and code.isalpha() else None


def _is_public_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_multicast or addr.is_reserved or addr.is_unspecified)


def _lookup(ip: str) -> GeoLocation | None:
    """Cached mmdb city lookup for one public IP. ``None`` when unresolved."""
    if not _is_public_ip(ip):
        return None
    with _lock:
        if ip in _cache:
            _cache.move_to_end(ip)
            return _cache[ip]
    reader = _get_reader()
    loc: GeoLocation | None = None
    if reader is not None:
        try:
            r = reader.city(ip)
            sub = r.subdivisions.most_specific
            loc = GeoLocation(
                country=_valid_code(r.country.iso_code),
                subdivision=(sub.name or None),
                city=(r.city.name or None),
                lat=(float(r.location.latitude)
                     if r.location.latitude is not None else None),
                lon=(float(r.location.longitude)
                     if r.location.longitude is not None else None),
            )
        except Exception:  # AddressNotFoundError + friends — leave None
            loc = None
    with _lock:
        _cache[ip] = loc
        _cache.move_to_end(ip)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return loc


def detect_location(request, client_ip: str | None = None) -> GeoLocation | None:
    """Best-effort coarse location for a request. mmdb first (precise), then
    the CDN country header (country only). Never raises.

    ``client_ip`` lets the caller pass the already-resolved (proxy-aware) IP
    from ``main._client_ip``; when omitted we read ``request.client.host``.
    """
    try:
        ip = client_ip or (request.client.host if request and request.client else None)
        loc = _lookup(ip) if ip else None
        if loc and loc.country:
            return loc
        if request is not None:
            for h in _COUNTRY_HEADERS:
                code = _valid_code(request.headers.get(h))
                if code:
                    return GeoLocation(country=code)
        return loc
    except Exception as exc:  # defensive — personalization must never error
        logger.info("detect_location failed (%s)", exc)
        return None


def detect_country(request, client_ip: str | None = None) -> str | None:
    """Best-effort 2-letter country (thin wrapper over :func:`detect_location`)."""
    loc = detect_location(request, client_ip)
    return loc.country if loc else None
