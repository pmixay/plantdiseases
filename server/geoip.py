"""Dependency-free server-side IP -> country detection.

Header-first (CDN/proxy country header), then one free HTTPS lookup as a
fallback, both cached in-memory. No MaxMind / geoip2 / .mmdb dependency — this
is purely additive personalization, so every failure path returns ``None`` and
``detect_country`` never raises.
"""
from __future__ import annotations

import ipaddress
import logging
import threading
import time
from collections import OrderedDict

logger = logging.getLogger("plantdiseases.geoip")

# CDN/proxy-injected country headers, in preference order. Free + synchronous.
_COUNTRY_HEADERS = (
    "cf-ipcountry",
    "x-vercel-ip-country",
    "x-country-code",
    "x-geo-country",
)

_TIMEOUT_S = 3.0
_CACHE_TTL_S = 24 * 3600
_CACHE_MAX = 4096

# ip -> (expiry_epoch, country_or_None). OrderedDict LRU, same pattern as
# species_info._cache.
_cache: "OrderedDict[str, tuple[float, str | None]]" = OrderedDict()
_lock = threading.Lock()


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


def _lookup_country(ip: str) -> str | None:
    """One free HTTPS lookup, cached. Returns a 2-letter code or ``None``."""
    now = time.time()
    with _lock:
        hit = _cache.get(ip)
        if hit and hit[0] > now:
            _cache.move_to_end(ip)
            return hit[1]

    code = None
    try:
        import requests  # lazy — same client species_info uses
        r = requests.get(f"https://ipapi.co/{ip}/country/", timeout=_TIMEOUT_S)
        if r.ok:
            code = _valid_code(r.text)
    except Exception as exc:  # network blip, timeout — non-fatal
        logger.info("IP->country lookup failed (%s); skipping personalization", exc)

    with _lock:
        _cache[ip] = (now + _CACHE_TTL_S, code)
        _cache.move_to_end(ip)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return code


def detect_country(request, client_ip: str | None = None) -> str | None:
    """Best-effort 2-letter country for a request. Header first, then a cached
    HTTPS lookup on a public IP, else ``None``. Never raises.

    ``client_ip`` lets the caller pass the already-resolved (proxy-aware) IP
    from ``main._client_ip``; when omitted we read ``request.client.host``.
    """
    try:
        for h in _COUNTRY_HEADERS:
            code = _valid_code(request.headers.get(h))
            if code:
                return code
        ip = client_ip or (request.client.host if request.client else None)
        if ip and _is_public_ip(ip):
            return _lookup_country(ip)
    except Exception as exc:  # defensive — personalization must never error
        logger.info("detect_country failed (%s)", exc)
    return None
