"""The /api/popular bundle: scope key, TTLs and the builder.

The short-lived cache itself, its stale-while-revalidate refresh and the boot-time
warm-up stay in ``main.py``, next to the executor they run on.
"""

from typing import Any

# Imports work flat (``uvicorn main:app`` from ``server/``) or as ``server.*``.
try:
    from . import community
except ImportError:
    import community
try:
    from . import community_geo
except ImportError:
    import community_geo
try:
    from . import config
except ImportError:
    import config
try:
    from . import plant_photos
except ImportError:
    import plant_photos
try:
    from . import popular_plants
except ImportError:
    import popular_plants
try:
    from . import species_info
except ImportError:
    import species_info


# The list is a live community-scan aggregate, so a bundle is cached only
# briefly (and served stale-while-revalidate) — new scans surface within the TTL
# instead of being pinned for hours. The (static) per-species photos are NOT
# re-resolved on a rebuild: they're served by the by-name /api/plant-photo
# proxy, which has its own long-lived disk cache. So a short bundle TTL is cheap.
POPULAR_TTL_S = config.get_int("POPULAR_BUNDLE_TTL_SECONDS", 1800)


# Both home carousels (popular plants + popular diseases) cap at 10 cards.
POPULAR_MAX_CARDS = 10

# A placeholder (scan-less country) is cached only briefly so it flips to real
# data within minutes of the first scan in that scope; real data keeps the
# full TTL since aggregates move slowly.
POPULAR_PLACEHOLDER_TTL_S = config.get_int("POPULAR_PLACEHOLDER_TTL_SECONDS", 300)


def popular_cache_key(
    country: str | None, lat, lon, locale: str,
) -> str:
    """Cache key for one popular bundle scope.

    Shared by the reader (:func:`popular_endpoint`) and the boot-time
    pre-warm, which used to build the key by hand in a different shape and so
    filed its warmed bundles under slots nothing could ever read."""
    return f"{country or ''}|{lat or ''},{lon or ''}|{locale}"


def popular_ttl(bundle: dict) -> int:
    return (POPULAR_PLACEHOLDER_TTL_S
            if bundle.get("source") == "placeholder" else POPULAR_TTL_S)


def worldwide_snapshot() -> dict:
    """Worldwide popularity: the anonymous tally of every consented, accepted
    diagnosis (community members included, location or not); the community
    corpus only while the tally is still empty (e.g. right after deploy)."""
    anonymous = community.anonymous_popular_snapshot(limit=POPULAR_MAX_CARDS)
    if anonymous.get("top_species") or anonymous.get("top_diseases"):
        return anonymous
    return community.popular_snapshot(limit=POPULAR_MAX_CARDS)


def build_popular_bundle(
    country: str | None,
    city: str | None,
    lat: float | None,
    lon: float | None,
    locale: str,
) -> dict:
    """Popular species + diseases for the home carousels, with the ladder
    nearby(≤30 km) → country → worldwide → curated placeholder.

    Real-scan scopes carry per-item ``count``/``percent`` (the client shows
    "P% of scans"); the placeholder carries zeros + ``source="placeholder"``
    so the client suppresses the percentage. Both lists are capped at 10.
    """
    lang = "ru" if locale == "ru" else "en"

    def _has(snap: dict) -> bool:
        return bool(snap.get("top_species") or snap.get("top_diseases"))

    snap: dict | None = None
    source = "worldwide"
    region_label: str | None = None
    if lat is not None and lon is not None:
        s = community.popular_snapshot(lat=lat, lon=lon, limit=POPULAR_MAX_CARDS)
        if _has(s):
            snap, source, region_label = s, "nearby", city
    if snap is None and country:
        s = community.popular_snapshot(country=country, limit=POPULAR_MAX_CARDS)
        if _has(s):
            snap, source = s, "country"
    if snap is None:
        s = worldwide_snapshot()
        if _has(s):
            snap, source = s, "worldwide"

    # A local (nearby) snapshot we can't attach a real place to — no
    # resolvable country AND no city — would be labeled "Worldwide" by
    # ``_country_display(None)`` below and rendered as "Popular in Worldwide"
    # over what is actually a single local scan. Show the true worldwide
    # aggregate instead so the label matches the data. (Worldwide always has
    # ≥ the local rows, so this never empties a populated feed.)
    if source == "nearby" and not country and not region_label:
        s = worldwide_snapshot()
        if _has(s):
            snap, source, region_label = s, "worldwide", None

    # No community data in any scope → curated placeholder. Titled for the
    # known country when we have one, else worldwide. The first real scan in
    # range flips this to live data on the next (short-lived) cache miss.
    if snap is None:
        # Curated names are bundled, so the placeholder build does NO network:
        # the photo is resolved lazily + cached by the by-name proxy.
        items: list[dict[str, Any]] = [
            {
                "scientific": p.scientific,
                "common_en": p.common_en,
                "common_ru": p.common_ru,
                # Always resolve the ENGLISH Wikipedia lead image regardless of
                # UI locale: the localized (e.g. RU) article often has a
                # different or missing lead photo, which made two distinct
                # species look identical in one locale but not another. Titles
                # stay localized; only the picture is pinned to EN.
                "image_url": plant_photos.name_proxy_path(p.scientific, "en"),
                "count": 0,
                "percent": 0,
            }
            for p in popular_plants.popular_for(country)[:POPULAR_MAX_CARDS]
        ]
        return {
            **community_geo.country_display(country),
            "locale": locale,
            "region": None,
            "items": items,
            "top_diseases": [],
            "total": 0,
            "source": "placeholder",
        }

    display = community_geo.country_display(country) if source != "worldwide" else community_geo.country_display(None)
    items = []
    used_titles: set[str] = set()
    for row in (snap.get("top_species") or [])[:POPULAR_MAX_CARDS]:
        scientific = row["species"]
        # Only the localized title needs resolving here (cached); the photo is
        # resolved + cached on demand by the by-name proxy, so a species that
        # only just trended (a fresh scan) still gets an image.
        info = species_info.fetch_species_info(scientific, lang)
        title = (info or {}).get("title") or scientific
        # A localized title can collapse two distinct species to one name (e.g.
        # RU Wikipedia titles both "Spathiphyllum" and "Spathiphyllum
        # floribundum" as "Спатифиллюм"), which shows two identical-looking
        # cards. On a collision fall back to the scientific name so each card
        # stays distinguishable.
        if title in used_titles:
            title = scientific
        used_titles.add(title)
        items.append({
            "scientific": scientific,
            "common_en": title,
            "common_ru": title,
            # English lead image for every locale — see the placeholder branch.
            "image_url": plant_photos.name_proxy_path(scientific, "en"),
            "count": row.get("count", 0),
            "percent": row.get("percent", 0),
        })
    # A thin community corpus (a new region, or one with a handful of scans)
    # would otherwise leave a one- or two-card carousel that never seems to
    # change. Top it up from the curated list for the same scope: the real
    # scans stay first with their percentages, the curated picks follow with
    # zero counts (the client shows those without a percentage).
    seen = {it["scientific"].lower() for it in items}
    for p in popular_plants.popular_for(country if source != "worldwide" else None):
        if len(items) >= POPULAR_MAX_CARDS:
            break
        if p.scientific.lower() in seen:
            continue
        seen.add(p.scientific.lower())
        items.append({
            "scientific": p.scientific,
            "common_en": p.common_en,
            "common_ru": p.common_ru,
            "image_url": plant_photos.name_proxy_path(p.scientific, "en"),
            "count": 0,
            "percent": 0,
        })
    return {
        **display,
        "locale": locale,
        "region": region_label if source == "nearby" else None,
        "items": items,
        "top_diseases": (snap.get("top_diseases") or [])[:POPULAR_MAX_CARDS],
        "total": snap.get("total", 0),
        "source": source,
    }


