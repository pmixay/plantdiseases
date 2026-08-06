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
import re
import threading
import time
import urllib.parse
from collections import OrderedDict

logger = logging.getLogger("plantdiseases.species_info")

_TIMEOUT_S = 4.0
_CACHE_TTL_S = 7 * 24 * 3600
_CACHE_MAX = 512
_USER_AGENT = (
    "PlantDiseases/1.1 (https://github.com/pmixay/plantdiseases; plant care app)"
)
_ALLOWED_LANGS = {"en", "ru"}
_BAD_MEDIA_TERMS = {
    "distribution", "range map", "map", "locator", "diagram", "logo", "icon",
    "flag", "portrait", "person", "herbarium", "specimen", "sheet",
    "drawing", "illustration", "coat of arms", "symbol",
    # Wikipedia plant articles lean heavily on 19th-century botanical plates
    # and taxonomy graphics. They are accurate but they don't look like the
    # plant on the user's windowsill, which is what a species card is for.
    # (Substring matches, so no "plate" — it hides inside "template" — and no
    # "graph", which would veto every caption saying "photograph".)
    "engraving", "lithograph", "sketch", "painting", "watercolor",
    "watercolour", "stamp", "banknote", "cladogram",
}
_GOOD_PLANT_TERMS = {
    "plant", "leaf", "leaves", "flower", "foliage", "stem", "fruit",
    "habit", "inflorescence", "houseplant",
}

# (lower-name, lang) -> (expiry_epoch, payload_or_None). OrderedDict so we
# evict the single oldest entry at the cap instead of flushing the whole
# cache (which thrashed under many distinct species in flight).
_cache: "OrderedDict[tuple[str, str], tuple[float, dict | None]]" = OrderedDict()
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
            _cache.move_to_end(key)
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
    if payload is None:
        # The wiki often lacks a SPECIES page for an obscure plant but DOES
        # have the GENUS article — which still gives a useful blurb + a
        # representative photo. This applies to EVERY language, including
        # English (e.g. en has "Spathiphyllum" but not "Spathiphyllum
        # blandum"): without it an English-locale popular card for an
        # uncommon community scan showed only the bare binomial and no image,
        # while Russian — which already had this fallback — worked fine.
        genus = name.split()[0] if name.split() else ""
        if genus and genus.lower() != name.lower():
            payload = _fetch(genus, lang)
            if payload is None and lang != "en":
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
        _cache[key] = (now + _CACHE_TTL_S, payload)
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
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
    image = _display_image(data.get("thumbnail"), data.get("originalimage"))
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


# Wikipedia's summary offers two extremes and neither is what we want to hand
# the photo proxy: `thumbnail` is ~330 px (soft once main.py re-encodes it) and
# `originalimage` is the untouched upload, routinely 6-11 MB. We used to take
# the original. The app never saw those megabytes — main.py transcodes what it
# serves down to _PLANT_PHOTO_MAX_SIDE (1000 px) WebP — but the SERVER still
# had to pull the whole file inside plant_photos' 6 s fetch timeout, and a
# 10 MB original on a slow hop simply doesn't arrive: the fetch fails and the
# card falls back to its placeholder. That is the picture-quality bug — missing
# photos, not blurry ones.
#
# Wikimedia thumb URLs carry their width in the last path segment
# (".../320px-Foo.jpg"), so we re-point the thumbnail at a card-sized render
# instead. Three wins: the fetch is a few hundred KB and lands inside the
# timeout, the byte cache holds far more species for the same disk, and — when
# the original is an SVG — we get a PNG render, where plant_photos dropped the
# raw .svg as unrenderable and left the card on its placeholder for good.
#
# The width we ask for is NOT free-form. Wikimedia renders thumbnails only at
# a fixed ladder of widths and, since phab:T414805, *rejects a direct request
# for any other width outright* — HTTP 400 "Use thumbnail sizes listed on
# https://www.mediawiki.org/wiki/Common_thumbnail_sizes". Hotlinking
# upload.wikimedia.org is exactly what plant_photos does, so an off-ladder
# width doesn't degrade to a smaller render: it kills the photo. Asking for
# 1024 px (one step off the ladder) is what emptied every species card of its
# lead image. Both widths below must stay ON this ladder.
_STANDARD_THUMB_WIDTHS = (20, 40, 60, 120, 250, 330, 500, 960, 1280, 1920, 3840)

# Keep _DISPLAY_WIDTH at or above _PLANT_PHOTO_MAX_SIDE (1000 px): below it the
# transcode would be upscaling, which is where "blurry" would actually come
# from. 1280 is the first ladder step that clears it.
_DISPLAY_WIDTH = 1280
# Secondary "example photos" strip — smaller on screen than the lead image, so
# it gets a smaller render rather than paying for 1280 px four times over.
_STRIP_WIDTH = 960
_THUMB_WIDTH_RE = re.compile(r"/(\d+)px-")


def _standard_width(width: int, ceiling: int | None = None) -> int | None:
    """The ladder step to actually request for ``width``.

    Rounds UP to the smallest standard step ≥ ``width`` — what MediaWiki's own
    PHP entry points do with an off-ladder request, and never a downgrade in
    resolution. When ``ceiling`` is given (a raster original's true width) the
    result steps back DOWN to the largest step ≤ ceiling, so we never ask for
    more pixels than the file holds. ``None`` when the file is smaller than
    every step — the caller then keeps whatever URL Wikipedia gave it.
    """
    up = next(
        (w for w in _STANDARD_THUMB_WIDTHS if w >= width), _STANDARD_THUMB_WIDTHS[-1],
    )
    if ceiling is None or up <= ceiling:
        return up
    return next((w for w in reversed(_STANDARD_THUMB_WIDTHS) if w <= ceiling), None)


def _resize_thumb(url: str, width: int) -> str | None:
    """Re-point a Wikimedia thumbnail URL at ``width``, snapped to a renderable
    ladder step. ``None`` if the URL isn't a thumbnail (nothing to rewrite —
    the caller keeps what it has)."""
    standard = _standard_width(width)
    if standard is None:
        return None
    resized, count = _THUMB_WIDTH_RE.subn(f"/{standard}px-", url, count=1)
    return resized if count == 1 else None


def _display_image(thumbnail: dict | None, original: dict | None) -> str | None:
    """Best display URL for a species card: the lead image rendered at
    ``_DISPLAY_WIDTH``, falling back to whatever the summary actually gave us."""
    thumb_src = ((thumbnail or {}).get("source") or "").strip()
    orig_src = ((original or {}).get("source") or "").strip()
    if not thumb_src:
        return orig_src or None
    orig_width = (original or {}).get("width")
    # Never ask for more pixels than the file holds — Wikimedia refuses to
    # upscale a raster original. An SVG has no such ceiling: it renders at any
    # width, and its nominal `width` would otherwise clamp us to a tiny render.
    ceiling = (
        orig_width
        if (
            isinstance(orig_width, int) and orig_width > 0
            and not orig_src.lower().endswith(".svg")
        )
        else None
    )
    width = _standard_width(_DISPLAY_WIDTH, ceiling)
    if width is None:  # smaller than the smallest render — serve it whole
        return orig_src or thumb_src
    thumb_width = (thumbnail or {}).get("width")
    if isinstance(thumb_width, int) and thumb_width >= width:
        return thumb_src  # already at least as large as we'd ask for
    return _resize_thumb(thumb_src, width) or orig_src or thumb_src


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
    title_text = urllib.parse.unquote(quoted_title).replace("_", " ")
    genus = title_text.split()[0].lower() if title_text.split() else ""
    sci_terms = {t.lower() for t in re.split(r"\s+", title_text) if len(t) > 2}
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
        # Same width normalisation as the lead image, in the other direction:
        # the largest srcset entry is only ~2× the media-list default (~640 px),
        # so here the render is UPSIZED to stay above what main.py re-encodes
        # rather than downsized to fit the fetch timeout. The item carries the
        # source file's own width, so we ask for more pixels only when the file
        # can supply them and the URL isn't already at least that large.
        current = _THUMB_WIDTH_RE.search(src)
        item_width = item.get("width")
        if (
            current and int(current.group(1)) < _STRIP_WIDTH
            and isinstance(item_width, int) and item_width >= _STRIP_WIDTH
        ):
            src = _resize_thumb(src, _STRIP_WIDTH) or src
        base = src.lower().split("?")[0]
        if not base.endswith((".jpg", ".jpeg", ".png")) or base.endswith(".svg"):
            continue
        caption = " ".join(
            str(item.get(k) or "") for k in ("title", "caption", "description")
        ).lower()
        haystack = " ".join([base, caption]).replace("_", " ").replace("-", " ")
        if any(term in haystack for term in _BAD_MEDIA_TERMS):
            continue
        if not (
            any(term in haystack for term in sci_terms)
            or (genus and genus in haystack)
            or any(term in haystack for term in _GOOD_PLANT_TERMS)
        ):
            continue
        if src in seen:
            continue
        seen.add(src)
        out.append(src)
        if len(out) >= limit:
            break
    return out
