"""Optional weather context for care plans.

Open-Meteo (https://open-meteo.com) — free, no API key, no auth. We fetch a
tiny current-conditions + short-forecast payload by latitude/longitude and
reduce it to the handful of numbers the care-plan note needs.

Every failure path returns ``None`` so the care plan is byte-for-byte
identical to the no-coordinates case: the feature is purely additive and
degrades silently when coordinates are missing, the network is blocked, or
the service is slow. Results are cached per (rounded) coordinate for half an
hour so a demo doesn't hammer the API or pay the latency twice.
"""
from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger("plantdiseases.weather")

_OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
_TIMEOUT_S = 1.5
_CACHE_TTL_S = 30 * 60
_CACHE_MAX = 256

# coord-keyed TTL cache: {(rlat, rlon): (expiry_epoch, payload_or_None)}
_cache: dict[tuple[float, float], tuple[float, dict | None]] = {}
_cache_lock = threading.Lock()


def fetch_weather(latitude: float | None, longitude: float | None) -> dict | None:
    """Return a compact weather dict for (lat, lon), or ``None`` on any
    failure / missing coordinates. Never raises.

    Shape on success::

        {"temp_c": float, "humidity": float, "precip_now": float,
         "precip_today": float, "precip_next": float}
    """
    if latitude is None or longitude is None:
        return None
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None

    key = (round(lat, 2), round(lon, 2))
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]

    payload = _fetch_remote(key[0], key[1])

    with _cache_lock:
        # Crude bound: a demo never sees enough distinct coordinates to need
        # an LRU, so just reset if it somehow grows pathologically.
        if len(_cache) >= _CACHE_MAX:
            _cache.clear()
        _cache[key] = (now + _CACHE_TTL_S, payload)
    return payload


def _fetch_remote(lat: float, lon: float) -> dict | None:
    try:
        import requests  # lazy — only the weather path needs it
    except Exception:  # pragma: no cover - requests is a hard dep in practice
        return None

    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,precipitation",
        "daily": "precipitation_sum",
        "forecast_days": 2,
        "timezone": "auto",
    }
    try:
        r = requests.get(_OPEN_METEO_URL, params=params, timeout=_TIMEOUT_S)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:  # network blip, timeout, bad JSON — all non-fatal
        logger.info("weather fetch failed (%s); care plan continues without it", exc)
        return None

    try:
        current = data.get("current") or {}
        daily = data.get("daily") or {}
        precip_series = daily.get("precipitation_sum") or []
        precip_today = (
            float(precip_series[0])
            if len(precip_series) >= 1 and precip_series[0] is not None
            else 0.0
        )
        precip_next = (
            float(precip_series[1])
            if len(precip_series) >= 2 and precip_series[1] is not None
            else 0.0
        )
        return {
            "temp_c": float(current["temperature_2m"]),
            "humidity": float(current["relative_humidity_2m"]),
            "precip_now": float(current.get("precipitation") or 0.0),
            "precip_today": precip_today,
            "precip_next": precip_next,
        }
    except (KeyError, TypeError, ValueError, IndexError):
        return None
