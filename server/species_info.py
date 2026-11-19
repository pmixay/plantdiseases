"""Optional species enrichment: Wikipedia first, then a chain of fallbacks.

Given a scientific name + language, resolve a localized extract + photo for the
species card. Lookup order (first source that yields a species-level article
wins; each step is skipped when an earlier one already answered):

1. Wikipedia REST summary in the user's language (direct title, then the title
   discovered through the English wiki's interwiki langlinks);
2. English Wikipedia (machine-translated for ``ru``);
3. Wikidata, searched by taxon name (P225): its sitelinks find articles filed
   under a synonym, and its image (P18) / Commons category supply a photo when
   the article has no lead image;
4. iNaturalist taxa API (Wikipedia-derived summary + a CC-licensed photo);
5. the genus article, labelled ``level: "genus"`` so the client can say so.

Every failure path returns ``None`` so the species card simply omits the
enrichment — it is purely additive. Wikimedia refuses requests without a
descriptive ``User-Agent`` (HTTP 403/429), so we always send one.

Caching: a definite answer is cached for a week (species facts are stable). A
lookup during which any upstream call failed *transiently* (timeout, 429, 5xx)
and which therefore came back empty, genus-only or photo-less is cached for
only a couple of minutes, so a rate-limit blip is never remembered as "this
species has no article". A definite miss is cached for hours, not a week.
"""
from __future__ import annotations

import html as _html
import logging
import re
import threading
import time
import urllib.parse
from collections import OrderedDict

try:
    from .config import get_str
except ImportError:  # flat layout (uvicorn run from server/)
    from config import get_str

logger = logging.getLogger("plantdiseases.species_info")

_TIMEOUT_S = 4.0
_RETRY_DELAY_S = 0.7
_CACHE_TTL_S = 7 * 24 * 3600
# A definite "nothing found anywhere" (every source answered, none had it).
_MISS_TTL_S = 6 * 3600
# A lookup that hit a transient upstream failure and came back empty/degraded.
_TRANSIENT_TTL_S = 120
_CACHE_MAX = 512


def _build_user_agent() -> str:
    """Wikimedia's policy asks for a descriptive agent *with a way to reach the
    operator*; anonymous generic agents are throttled first. The contact is
    deployment config (``SPECIES_INFO_CONTACT``: a URL or e-mail), never
    hard-coded in the repository."""
    contact = (get_str("SPECIES_INFO_CONTACT", "") or "").strip()
    base = "PlantDiseases/1.2 (plant care app; species-info lookup"
    return f"{base}; {contact})" if contact else f"{base})"


_USER_AGENT = _build_user_agent()
_JSON_HEADERS = {"User-Agent": _USER_AGENT, "Accept": "application/json"}

_WIKIDATA_API = "https://www.wikidata.org/w/api.php"
_COMMONS_API = "https://commons.wikimedia.org/w/api.php"
_INAT_TAXA = "https://api.inaturalist.org/v1/taxa"
# iNaturalist photo licences we may redistribute (the server re-encodes and
# crops them, so no ND; no NC either, to stay clear of future monetisation).
_INAT_OK_LICENSES = {"cc0": "CC0", "cc-by": "CC BY", "cc-by-sa": "CC BY-SA"}
_RASTER_EXTS = (".jpg", ".jpeg", ".png", ".webp")
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
# Per-thread: did any upstream call of the lookup in progress fail transiently?
_state = threading.local()


def _mark_transient() -> None:
    _state.transient = True


def _http_json(url: str, params: dict | None = None) -> dict | list | None:
    """GET ``url`` and decode JSON. ``None`` for a 404/other 4xx (a definite
    "not there") and for failures; failures that say nothing about whether the
    page exists (network error, timeout, 429, 5xx, bad JSON) also flag the
    lookup transient so the caller does not cache the miss for long."""
    try:
        import requests  # lazy — only this path needs it
    except Exception:  # pragma: no cover
        return None
    try:
        for attempt in range(2):
            r = requests.get(url, params=params, headers=_JSON_HEADERS, timeout=_TIMEOUT_S)
            status = getattr(r, "status_code", 200)
            if status == 429 or status >= 500:
                if attempt == 0:
                    time.sleep(_RETRY_DELAY_S)  # one short retry for a rate-limit blip
                    continue
                _mark_transient()
                return None
            if not r.ok:
                return None
            return r.json()
    except Exception:  # network blip, timeout, bad JSON — all non-fatal
        _mark_transient()
        logger.info("Species info upstream call failed; continuing")
    return None


def fetch_species_info(scientific_name: str | None, lang: str = "en") -> dict | None:
    """Return ``{title, extract, image_url, images, page_url, lang, source,
    level, image_credit, image_license}`` for a scientific name, or ``None``
    on any failure / missing name. Never raises.

    ``level`` is ``"species"`` or ``"genus"`` (genus article used as a last
    resort). ``image_credit`` / ``image_license`` are set when the photo came
    from Commons or iNaturalist and carry its author and licence.
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

    _state.transient = False
    try:
        payload = _resolve(name, lang)
    except Exception:  # never let an unexpected parse error break the card
        logger.exception("Species info lookup crashed; treating as a miss")
        payload = None
        _mark_transient()
    transient = bool(getattr(_state, "transient", False))

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

    if payload is None:
        ttl = _TRANSIENT_TTL_S if transient else _MISS_TTL_S
    elif transient and (payload.get("level") == "genus" or not payload.get("image_url")):
        ttl = _TRANSIENT_TTL_S  # degraded answer: retry soon for the real one
    else:
        ttl = _CACHE_TTL_S
    with _lock:
        _cache[key] = (now + ttl, payload)
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return payload


def _resolve(name: str, lang: str) -> dict | None:
    """Walk the fallback chain (see the module docstring) for ``name``."""
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
        payload = _fetch(name, "en")

    wikidata: dict | None = None
    wikidata_tried = False
    if payload is None:
        # No Wikipedia article under this exact name. Wikidata knows the taxon
        # by name (P225) incl. synonyms and lists the article under whatever
        # title each wiki uses.
        wikidata, wikidata_tried = _wikidata_lookup(name), True
        for code in ((lang, "en") if lang != "en" else ("en",)):
            title = ((wikidata or {}).get("sitelinks") or {}).get(code)
            if title:
                payload = _fetch(title, code)
                if payload is not None:
                    break
    if payload is None:
        payload = _inaturalist_lookup(name)

    if payload is None:
        # The wiki often lacks a SPECIES page for an obscure plant but DOES
        # have the GENUS article — which still gives a useful blurb + a
        # representative photo. Clearly labelled so the client can say so.
        payload = _genus_fallback(name, lang)

    if payload is not None and not payload.get("image_url"):
        if not wikidata_tried:
            wikidata, wikidata_tried = _wikidata_lookup(name), True
        photo = _species_photo(name, wikidata)
        if photo is not None:
            payload = {
                **payload,
                "image_url": photo["url"],
                "images": [photo["url"], *(payload.get("images") or [])][:5],
                "image_credit": photo.get("credit"),
                "image_license": photo.get("license"),
            }
    return payload


def _genus_fallback(name: str, lang: str) -> dict | None:
    parts = name.split()
    genus = parts[0] if parts else ""
    if not genus or genus.lower() == name.lower():
        return None
    payload = _fetch(genus, lang)
    if payload is None and lang != "en":
        localized_genus = _localized_title(genus, lang)
        if localized_genus:
            payload = _fetch(localized_genus, lang)
    if payload is None and lang != "en":
        payload = _fetch(genus, "en")
    if payload is None or not _looks_like_plant_genus(payload.get("extract") or ""):
        # A bare genus name can collide with an unrelated article (a game, a
        # surname) — only accept text that describes a plant genus.
        return None
    return {**payload, "level": "genus"}


_GENUS_WORD_RE = re.compile(r"\b(genus|genera|род|рода|роды)\b", re.IGNORECASE)
_PLANT_HINT_RE = re.compile(
    r"plant|flower|shrub|tree|fern|herb|succulent|cact|orchid|palm|vine|"
    r"растени|цветк|кустарник|дерев|папоротник|трав|кактус|орхиде|пальм|"
    r"\b[A-Z][a-z]+aceae\b",
    re.IGNORECASE,
)


def _looks_like_plant_genus(extract: str) -> bool:
    head = extract[:400]
    return bool(_GENUS_WORD_RE.search(head) and _PLANT_HINT_RE.search(head))


def _species_photo(name: str, wikidata: dict | None) -> dict | None:
    """A photo of the species itself when its article has no lead image:
    Wikidata P18, then a photo from the taxon's Commons category, then a
    CC-licensed iNaturalist photo."""
    wd = wikidata or {}
    if wd.get("image_file"):
        photo = _commons_file_image(wd["image_file"])
        if photo:
            return photo
    if wd.get("commons_category"):
        photo = _commons_category_image(wd["commons_category"])
        if photo:
            return photo
    inat = _inaturalist_lookup(name)
    if inat and inat.get("image_url"):
        return {
            "url": inat["image_url"],
            "credit": inat.get("image_credit"),
            "license": inat.get("image_license"),
        }
    return None


def _localized_title(name: str, target_lang: str) -> str | None:
    """Resolve the ``target_lang`` Wikipedia title for ``name`` via the
    English wiki's interwiki langlinks.

    A direct title lookup on the localized wiki misses articles that exist
    under a synonym or a localized common name (very common for plants whose
    botanical name was recently reclassified). The English wiki follows
    redirects (``redirects=1``) and exposes the localized title through its
    langlinks, which is far more robust. Best-effort — returns ``None`` on any
    failure so callers fall through to the next strategy.
    """
    data = _http_json(
        "https://en.wikipedia.org/w/api.php",
        {
            "action": "query",
            "titles": name,
            "redirects": "1",
            "prop": "langlinks",
            "lllang": target_lang,
            "lllimit": "1",
            "format": "json",
            "formatversion": "2",
        },
    )
    if not isinstance(data, dict):
        return None
    for page in (data.get("query") or {}).get("pages") or []:
        for link in page.get("langlinks") or []:
            title = (link.get("title") or "").strip()
            if title:
                return title
    return None


def _wikidata_lookup(name: str) -> dict | None:
    """Find the taxon item for ``name`` on Wikidata (taxon name, P225).

    Returns ``{qid, sitelinks: {"en": title, "ru": title}, image_file,
    commons_category}`` or ``None``. A synonym that Wikidata knows as an alias
    resolves to the accepted taxon, which is how articles filed under another
    name are found.
    """
    found = _http_json(_WIKIDATA_API, {
        "action": "wbsearchentities",
        "search": name,
        "language": "en",
        "type": "item",
        "limit": "6",
        "format": "json",
    })
    if not isinstance(found, dict):
        return None
    hits = [h for h in (found.get("search") or []) if h.get("id")]
    if not hits:
        return None
    ids = [h["id"] for h in hits][:6]
    data = _http_json(_WIKIDATA_API, {
        "action": "wbgetentities",
        "ids": "|".join(ids),
        "props": "claims|sitelinks",
        "sitefilter": "enwiki|ruwiki",
        "format": "json",
    })
    entities = (data or {}).get("entities") if isinstance(data, dict) else None
    if not isinstance(entities, dict):
        return None

    def claim_values(entity: dict, prop: str) -> list[str]:
        out = []
        for claim in (entity.get("claims") or {}).get(prop) or []:
            value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value")
            if isinstance(value, str) and value.strip():
                out.append(value.strip())
        return out

    wanted = name.strip().lower()
    chosen: dict | None = None
    # Exact taxon-name match first, then a taxon found through an alias match.
    for exact in (True, False):
        for hit in hits:
            entity = entities.get(hit["id"])
            if not isinstance(entity, dict):
                continue
            taxon_names = [t.lower() for t in claim_values(entity, "P225")]
            if not taxon_names:
                continue  # not a taxon (a person, a place, a surname ...)
            if exact and wanted in taxon_names:
                chosen = entity
            elif (
                not exact
                and str((hit.get("match") or {}).get("text") or "").strip().lower() == wanted
            ):
                chosen = entity
            if chosen:
                break
        if chosen:
            break
    if chosen is None:
        return None

    sitelinks: dict[str, str] = {}
    for code in ("en", "ru"):
        title = ((chosen.get("sitelinks") or {}).get(f"{code}wiki") or {}).get("title")
        if isinstance(title, str) and title.strip():
            sitelinks[code] = title.strip()
    images = claim_values(chosen, "P18")
    categories = claim_values(chosen, "P373")
    return {
        "qid": chosen.get("id"),
        "sitelinks": sitelinks,
        "image_file": images[0] if images else None,
        "commons_category": categories[0] if categories else None,
    }


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()


def _commons_photo_from_info(title: str, info: dict) -> dict | None:
    """``{url, credit, license}`` for a Commons ``imageinfo`` record, or
    ``None`` for anything that is not a plain raster photo."""
    file_name = title.split(":", 1)[-1]
    url = (info.get("thumburl") or info.get("url") or "").strip()
    if not url or not file_name.lower().endswith(_RASTER_EXTS):
        return None
    meta = info.get("extmetadata") or {}

    def meta_text(key: str) -> str | None:
        value = _strip_html(str((meta.get(key) or {}).get("value") or ""))
        return value or None

    return {
        "url": url,
        "credit": meta_text("Artist"),
        "license": meta_text("LicenseShortName"),
    }


def _commons_file_image(file_name: str) -> dict | None:
    """Card-sized render of one Commons file (Wikidata's P18)."""
    data = _http_json(_COMMONS_API, {
        "action": "query",
        "titles": "File:" + file_name.replace("_", " "),
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
        "iiurlwidth": str(_DISPLAY_WIDTH),
        "format": "json",
        "formatversion": "2",
    })
    if not isinstance(data, dict):
        return None
    for page in (data.get("query") or {}).get("pages") or []:
        infos = page.get("imageinfo") or []
        if infos:
            photo = _commons_photo_from_info(page.get("title") or file_name, infos[0])
            if photo:
                return photo
    return None


def _commons_category_image(category: str) -> dict | None:
    """First plausible photo in the taxon's Commons category."""
    data = _http_json(_COMMONS_API, {
        "action": "query",
        "generator": "categorymembers",
        "gcmtitle": "Category:" + category,
        "gcmtype": "file",
        "gcmlimit": "12",
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
        "iiurlwidth": str(_DISPLAY_WIDTH),
        "format": "json",
        "formatversion": "2",
    })
    if not isinstance(data, dict):
        return None
    pages = sorted(
        (data.get("query") or {}).get("pages") or [],
        key=lambda p: p.get("index", 0),
    )
    for page in pages:
        title = page.get("title") or ""
        lowered = title.lower().replace("_", " ").replace("-", " ")
        if any(term in lowered for term in _BAD_MEDIA_TERMS):
            continue
        infos = page.get("imageinfo") or []
        if not infos:
            continue
        photo = _commons_photo_from_info(title, infos[0])
        if photo:
            return photo
    return None


def _inaturalist_lookup(name: str) -> dict | None:
    """iNaturalist's taxon record for an exact species name: its
    Wikipedia-derived summary and a redistributable (CC0 / CC BY / CC BY-SA)
    photo. ``None`` when iNaturalist has neither."""
    data = _http_json(_INAT_TAXA, {"q": name, "per_page": "5", "is_active": "true"})
    if not isinstance(data, dict):
        return None
    wanted = name.strip().lower()
    taxon = next(
        (
            t for t in (data.get("results") or [])
            if isinstance(t, dict) and str(t.get("name") or "").strip().lower() == wanted
        ),
        None,
    )
    if taxon is None:
        return None

    extract = _strip_html(str(taxon.get("wikipedia_summary") or ""))
    photos = [taxon.get("default_photo")] + [
        tp.get("photo") for tp in (taxon.get("taxon_photos") or []) if isinstance(tp, dict)
    ]
    image_url = credit = licence = None
    for photo in photos:
        if not isinstance(photo, dict):
            continue
        code = str(photo.get("license_code") or "").lower()
        url = str(photo.get("large_url") or photo.get("medium_url") or "").strip()
        if code in _INAT_OK_LICENSES and url.startswith("https://"):
            image_url, licence = url, _INAT_OK_LICENSES[code]
            credit = str(photo.get("attribution") or "").strip() or None
            break
    if not extract and not image_url:
        return None

    wiki_url = str(taxon.get("wikipedia_url") or "").strip().replace(" ", "_")
    page_url = (
        wiki_url if wiki_url.startswith("https://en.wikipedia.org/wiki/")
        else f"https://www.inaturalist.org/taxa/{taxon.get('id')}"
    )
    return {
        "title": str(taxon.get("name") or name),
        "extract": extract,
        "image_url": image_url,
        "images": [image_url] if image_url else [],
        "page_url": page_url,
        "lang": "en",
        "source": "inaturalist",
        "level": "species",
        "image_credit": credit,
        "image_license": licence,
    }


def _translate(text: str, target_lang: str) -> str | None:
    """Machine-translate ``text`` into ``target_lang`` via Google Translate's
    free (keyless) ``gtx`` endpoint.

    Best-effort and last-resort only — used when we could find an English
    Wikipedia summary but not one in the user's language, so the card isn't
    English-only. Returns ``None`` on any failure (the caller then keeps
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
        "sl": "en",
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
    except Exception:  # network blip, timeout, bad JSON — all non-fatal
        logger.info("Translation failed; keeping original-language text")
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
    title = urllib.parse.quote(name.replace(" ", "_"), safe="")
    data = _http_json(f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}")
    if not isinstance(data, dict):
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
        "level": "species",
        "image_credit": None,
        "image_license": None,
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
