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
        # The localized wiki frequently has the article under a DIFFERENT
        # title than the scientific name we were given — most often because
        # the name is a recent synonym (e.g. "Heptapleurum arboricola" while
        # the ru article lives at "Шеффлера древовидная"). The English wiki
        # resolves redirects/synonyms and carries interwiki langlinks, so use
        # it to discover the correct localized title before giving up.
        localized = _localized_title(name, lang)
        if localized:
            payload = _fetch(localized, lang)
    if payload is None and lang != "en":
        # The localized wiki often lacks a SPECIES page for an obscure plant
        # (e.g. ru has no "Didymochlaena truncatula") but DOES have the GENUS
        # article — which still gives a useful localized blurb. Try the genus
        # in the user's language before giving up to English.
        genus = name.split()[0] if name.split() else ""
        if genus and genus.lower() != name.lower():
            payload = _fetch(genus, lang)
            if payload is None:
                localized_genus = _localized_title(genus, lang)
                if localized_genus:
                    payload = _fetch(localized_genus, lang)
    if payload is None and lang != "en":
        payload = _fetch(name, "en")

    # Last resort: we only found an English article but the user wants another
    # language — machine-translate the summary so the card isn't English-only.
    if payload is not None and lang != "en" and payload.get("lang") == "en":
        translated = _translate(payload.get("extract") or "", lang)
        if translated:
            payload = {
                **payload,
                "extract": translated,
                "lang": lang,
                "translated": True,
            }

    with _lock:
        if len(_cache) >= _CACHE_MAX:
            _cache.clear()
        _cache[key] = (now + _CACHE_TTL_S, payload)
    return payload


def _localized_title(name: str, target_lang: str, source_lang: str = "en") -> str | None:
    """Resolve the ``target_lang`` Wikipedia title for ``name`` via the
    ``source_lang`` wiki's interwiki langlinks.

    A direct title lookup on the localized wiki misses articles that exist
    under a synonym or a localized common name (very common for plants whose
    botanical name was recently reclassified). The English wiki follows
    redirects (``redirects=1``) and exposes the localized title through its
    langlinks, which is far more robust. Best-effort — returns ``None`` on any
    failure so callers fall through to the next strategy.
    """
    try:
        import requests  # lazy — only this path needs it
    except Exception:  # pragma: no cover
        return None

    url = f"https://{source_lang}.wikipedia.org/w/api.php"
    params = {
        "action": "query",
        "titles": name,
        "redirects": "1",
        "prop": "langlinks",
        "lllang": target_lang,
        "lllimit": "1",
        "format": "json",
        "formatversion": "2",
    }
    try:
        r = requests.get(
            url,
            params=params,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            timeout=_TIMEOUT_S,
        )
        if not r.ok:
            return None
        pages = (r.json().get("query") or {}).get("pages") or []
    except Exception as exc:  # network blip, timeout, bad JSON — all non-fatal
        logger.info("Wikipedia langlink lookup failed (%s); continuing", exc)
        return None

    for page in pages:
        for link in page.get("langlinks") or []:
            title = (link.get("title") or "").strip()
            if title:
                return title
    return None


def _translate(text: str, target_lang: str, source_lang: str = "en") -> str | None:
    """Machine-translate ``text`` into ``target_lang`` via Google Translate's
    free (keyless) ``gtx`` endpoint.

    Best-effort and last-resort only — used when we could find a Wikipedia
    summary in the source language but not the user's language, so the card
    isn't English-only. Returns ``None`` on any failure (the caller then keeps
    the original-language text). The endpoint is unofficial; we keep the
    request tiny and time-boxed and never let a failure surface to the user.
    """
    text = (text or "").strip()
    if not text:
        return None
    # Keep the request well within the endpoint's practical query limit; a
    # Wikipedia lead extract is a paragraph, so this only trims pathological
    # cases rather than real content.
    if len(text) > 4000:
        text = text[:4000]
    try:
        import requests  # lazy — only this path needs it
    except Exception:  # pragma: no cover
        return None

    url = "https://translate.googleapis.com/translate_a/single"
    params = {
        "client": "gtx",
        "sl": source_lang,
        "tl": target_lang,
        "dt": "t",
        "q": text,
    }
    try:
        r = requests.get(
            url,
            params=params,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            timeout=_TIMEOUT_S,
        )
        if not r.ok:
            return None
        data = r.json()
    except Exception as exc:  # network blip, timeout, bad JSON — all non-fatal
        logger.info("Translation failed (%s); keeping original-language text", exc)
        return None

    # gtx shape: [[[translated, original, ...], [translated, original, ...]], ...]
    try:
        segments = data[0]
        out = "".join(seg[0] for seg in segments if seg and seg[0])
    except (IndexError, TypeError):
        return None
    out = out.strip()
    return out or None


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
    images = _media_images(title, lang)
    if not images and image:
        images = [image]
    return {
        "title": data.get("title") or name,
        "extract": extract,
        "image_url": image,
        "images": images,
        "page_url": page,
        "lang": lang,
        "source": "wikipedia",
    }


def _media_images(quoted_title: str, lang: str, limit: int = 4) -> list[str]:
    """A few example photos from the page's media-list (JPEG/PNG only),
    for the 'example photos' strips. Best-effort — returns [] on any failure."""
    try:
        import requests  # lazy
    except Exception:  # pragma: no cover
        return []
    url = f"https://{lang}.wikipedia.org/api/rest_v1/page/media-list/{quoted_title}"
    try:
        r = requests.get(
            url,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            timeout=_TIMEOUT_S,
        )
        if not r.ok:
            return []
        items = r.json().get("items") or []
    except Exception:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item.get("type") != "image":
            continue
        srcset = item.get("srcset") or []
        if not srcset:
            continue
        src = (srcset[-1].get("src") or "").strip()
        if src.startswith("//"):
            src = "https:" + src
        base = src.lower().split("?")[0]
        if not base.endswith((".jpg", ".jpeg", ".png")):
            continue
        if src in seen:
            continue
        seen.add(src)
        out.append(src)
        if len(out) >= limit:
            break
    return out
