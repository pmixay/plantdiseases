"""Optional species enrichment from Wikipedia.

Given a scientific name + language, fetch the species' Wikipedia summary
(localized extract + lead image) via the public REST API. Free, no key.

Every failure path returns ``None`` so the species card simply omits the
enrichment — it is purely additive. Wikipedia's REST API refuses requests
without a descriptive ``User-Agent`` (HTTP 403), so we always send one.
Results are cached for a week (species facts are stable) and the fetch has a
tight timeout, so a slow/blocked network never holds up a scan result.
"""
from __future__ import annotations

import logging
import threading
import time
import urllib.parse

logger = logging.getLogger("plantdiseases.species_info")

_TIMEOUT_S = 4.0
_CACHE_TTL_S = 7 * 24 * 3600
_CACHE_MAX = 512
_USER_AGENT = (
    "PlantDiseases/1.1 (https://github.com/pmixay/plantdiseases; houseplant care app)"
)
_ALLOWED_LANGS = {"en", "ru"}

# (lower-name, lang) -> (expiry_epoch, payload_or_None)
_cache: dict[tuple[str, str], tuple[float, dict | None]] = {}
_lock = threading.Lock()


def fetch_species_info(scientific_name: str | None, lang: str = "en") -> dict | None:
    """Return ``{title, extract, image_url, page_url, lang, source}`` for a
    scientific name, or ``None`` on any failure / missing name. Never raises.

    Falls back to the English Wikipedia once if the localized wiki has no
    page for the species.
    """
    if not scientific_name or not scientific_name.strip():
        return None
    name = scientific_name.strip()
    lang = (lang or "en").strip().lower()[:2]
    if lang not in _ALLOWED_LANGS:
        lang = "en"

    key = (name.lower(), lang)
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]

    payload = _fetch(name, lang)
    if payload is None and lang != "en":
        payload = _fetch(name, "en")

    with _lock:
        if len(_cache) >= _CACHE_MAX:
            _cache.clear()
        _cache[key] = (now + _CACHE_TTL_S, payload)
    return payload


def _fetch(name: str, lang: str) -> dict | None:
    try:
        import requests  # lazy — only this path needs it
    except Exception:  # pragma: no cover
        return None

    title = urllib.parse.quote(name.replace(" ", "_"), safe="")
    url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}"
    try:
        r = requests.get(
            url,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            timeout=_TIMEOUT_S,
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
    except Exception as exc:  # network blip, timeout, bad JSON — all non-fatal
        logger.info("Wikipedia fetch failed (%s); species card continues without it", exc)
        return None

    if data.get("type") == "disambiguation":
        return None
    extract = (data.get("extract") or "").strip()
    image = (
        (data.get("originalimage") or {}).get("source")
        or (data.get("thumbnail") or {}).get("source")
    )
    page = ((data.get("content_urls") or {}).get("desktop") or {}).get("page")
    if not extract and not image:
        return None
    return {
        "title": data.get("title") or name,
        "extract": extract,
        "image_url": image,
        "page_url": page,
        "lang": lang,
        "source": "wikipedia",
    }
