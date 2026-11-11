"""Server-side proxy + disk cache for external plant photos.

The popular carousel and species cards show Wikipedia lead images. Handing the
raw ``upload.wikimedia.org`` URLs straight to the app makes the photos slow or
blank on networks where Wikimedia is throttled/blocked (notably Russia).
Instead the server fetches each photo once, caches the bytes on disk, and
serves them from its own domain — reliable everywhere and cheap on repeat
(304 revalidation via main._cached_image_response).

Guards:
- only Wikimedia/Wikipedia hosts are ever fetched (SSRF — the source URL rides
  in from server-resolved Wikipedia metadata, but the proxy endpoint is public);
- only raster images Glide can render (JPEG/PNG/WebP/GIF) are cached — Wikipedia
  mixes in SVGs, which the app can't decode;
- thumbnail widths are snapped to the ladder Wikimedia will actually render
  (see ``normalize_thumb_url``), with the full-size original as a fallback.

Best-effort throughout: any miss returns ``None`` so the card falls back to its
placeholder, exactly as it did before this proxy existed.
"""
from __future__ import annotations

import contextlib
import hashlib
import logging
import re
import threading
import urllib.parse
from pathlib import Path

try:
    from .config import get_str
except ImportError:  # flat layout (uvicorn run from server/)
    from config import get_str

logger = logging.getLogger("plantdiseases.plant_photos")

_TIMEOUT_S = 6.0
_MAX_BYTES = 16 * 1024 * 1024  # 16 MB — Wikimedia Commons originals routinely run 6-11 MB
# LRU cap; oldest-by-mtime evicted on write. species_info now hands us
# width-normalised renders (a few hundred KB) rather than multi-MB originals,
# so the same disk budget holds far more species — raised to match.
_CACHE_MAX_FILES = 1500
# The public proxy lets callers choose which allowlisted assets enter the
# cache, so count and source+variant bytes are independently bounded.
_CACHE_MAX_BYTES = 512 * 1024 * 1024
_USER_AGENT = (
    "PlantDiseases/1.1 (plant care app)"
)

# Only these exact hosts are fetched. The endpoint is public, so a suffix-wide
# allowlist would unnecessarily trust every present and future Wikimedia
# subdomain. These are the only hosts emitted by species_info's en/ru lookups.
_ALLOWED_HOSTS = frozenset({
    "upload.wikimedia.org",
    "en.wikipedia.org",
    "ru.wikipedia.org",
})
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECTS = 5

_DEFAULT_DIR = Path(__file__).resolve().parent / "plant_photo_cache"
PHOTO_CACHE_DIR = Path(get_str("PLANT_PHOTO_CACHE_DIR", str(_DEFAULT_DIR)))

_write_lock = threading.Lock()

# extension → media type for the raster formats Glide renders.
_EXT_MEDIA = {
    "jpg": "image/jpeg", "png": "image/png", "webp": "image/webp", "gif": "image/gif",
}


def is_allowed_url(url: str) -> bool:
    """True for an HTTPS URL on an exact, expected Wikimedia host."""
    try:
        parsed = urllib.parse.urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    return (
        host in _ALLOWED_HOSTS
        and port in (None, 443)
        and parsed.username is None
        and parsed.password is None
        # Fragments are never sent to the origin. Accepting them would let one
        # source fan out into unlimited cache keys and repeated downloads.
        and not parsed.fragment
    )


def proxy_path(url: str | None) -> str | None:
    """App-facing relative path that proxies ``url`` through this server, or
    ``None`` when the URL is empty / not an allowed host (the caller keeps the
    ``None`` → client shows its placeholder). The client prepends the API base
    URL, matching the ``api/community/thumb/…`` convention."""
    if not url or not is_allowed_url(url):
        return None
    return "api/plant-photo?u=" + urllib.parse.quote(url, safe="")


def name_proxy_path(name: str, locale: str) -> str:
    """App-facing path that lets the proxy *fully resolve* a species photo from
    its scientific name (Wikipedia lookup + byte cache happen server-side). Used
    for the popular carousel so a species that only just became popular (a fresh
    scan) still resolves a photo on demand, without the bundle having to pin a
    URL at build time. Every production caller pins ``locale="en"`` by design —
    the lead image is the same photo whatever the UI language — so the parameter
    exists for the by-name lookup's benefit, not the caller's."""
    return (
        "api/plant-photo?name=" + urllib.parse.quote(name, safe="")
        + "&locale=" + urllib.parse.quote(locale or "en", safe="")
    )


# Wikimedia renders thumbnails only at a fixed ladder of widths and, since
# phab:T414805, rejects a *direct* request for any other width with HTTP 400
# ("Use thumbnail sizes listed on
# https://www.mediawiki.org/wiki/Common_thumbnail_sizes"). Hotlinking is
# precisely what this module does, so an off-ladder width is not a smaller
# photo — it is no photo at all.
#
# species_info now only ever asks for ladder widths, but URLs also reach us
# second-hand and we cannot re-issue those: an installed app caches
# /api/species-info for 7 days, so its stored `?u=…/1024px-…` links keep
# arriving here long after the server is fixed, and Wikipedia's own media-list
# srcset hands back whatever width it likes. Snapping in the one place every
# photo URL passes through heals all of them at once.
_STANDARD_THUMB_WIDTHS = (20, 40, 60, 120, 250, 330, 500, 960, 1280, 1920, 3840)
_THUMB_WIDTH_RE = re.compile(r"/(\d+)px-")


def normalize_thumb_url(url: str) -> str:
    """Snap a ``/NNNpx-`` Wikimedia thumbnail width up to the nearest width
    Wikimedia will actually render. Returned unchanged when the URL carries no
    width or already uses a ladder step."""
    match = _THUMB_WIDTH_RE.search(url)
    if match is None:
        return url
    width = int(match.group(1))
    if width in _STANDARD_THUMB_WIDTHS:
        return url
    snapped = next(
        (w for w in _STANDARD_THUMB_WIDTHS if w >= width), _STANDARD_THUMB_WIDTHS[-1],
    )
    return f"{url[:match.start()]}/{snapped}px-{url[match.end():]}"


def _original_from_thumb(url: str) -> str | None:
    """The full-size file behind a Wikimedia thumbnail URL
    (``…/commons/thumb/a/ab/Name.jpg/960px-Name.jpg`` →
    ``…/commons/a/ab/Name.jpg``), or ``None`` when ``url`` is not a thumbnail.

    Last resort only — originals are routinely 6-11 MB, which is why we ask for
    a render in the first place. But which widths Wikimedia will render is
    theirs to change (it just did), and a photo that arrives slowly beats a
    card stuck on its placeholder.
    """
    parsed = urllib.parse.urlparse(url)
    parts = parsed.path.split("/")
    if "thumb" not in parts or _THUMB_WIDTH_RE.search(url) is None:
        return None
    cut = parts.index("thumb")
    # Drop the "thumb" marker and the trailing "NNNpx-Name.ext" render segment.
    rest = parts[cut + 1:-1]
    if not rest:
        return None
    return urllib.parse.urlunparse(
        parsed._replace(path="/".join(parts[:cut] + rest), query=""),
    )


def _digest(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]


def _existing(url: str) -> Path | None:
    digest = _digest(url)
    for ext in _EXT_MEDIA:
        path = PHOTO_CACHE_DIR / f"{digest}.{ext}"
        if path.is_file():
            return path
    return None


def _sniff(data: bytes) -> str | None:
    """Return the file extension for a known raster format, else ``None``."""
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return None


def media_type_for(path: Path) -> str:
    return _EXT_MEDIA.get(path.suffix.lstrip(".").lower(), "application/octet-stream")


def _prune() -> None:
    """Keep source and derived caches bounded, dropping oldest sources first."""
    sources: list[tuple[Path, float, int]] = []
    for path in PHOTO_CACHE_DIR.glob("*.*"):
        try:
            if path.is_file():
                info = path.stat()
                sources.append((path, info.st_mtime, info.st_size))
        except OSError:
            continue
    sources.sort(key=lambda item: item[1])

    variant_dir = PHOTO_CACHE_DIR / ".webp"
    variants: dict[str, list[tuple[Path, int]]] = {}
    source_stems = {path.stem for path, _mtime, _size in sources}
    for path in variant_dir.glob("*.webp"):
        try:
            if not path.is_file():
                continue
            size = path.stat().st_size
        except OSError:
            continue
        source_stem = path.name.split(".", 1)[0]
        if source_stem not in source_stems:
            # Derived files have no value once their source was evicted. This
            # also cleans variants orphaned by older count-only deployments.
            with contextlib.suppress(OSError):
                path.unlink()
            continue
        variants.setdefault(source_stem, []).append((path, size))

    total_bytes = sum(size for _path, _mtime, size in sources) + sum(
        size for entries in variants.values() for _path, size in entries
    )
    while sources and (
        len(sources) > _CACHE_MAX_FILES or total_bytes > _CACHE_MAX_BYTES
    ):
        path, _mtime, size = sources.pop(0)
        try:
            path.unlink()
            total_bytes -= size
        except OSError:
            continue
        for variant, variant_size in variants.pop(path.stem, []):
            try:
                variant.unlink()
                total_bytes -= variant_size
            except OSError:
                continue


def prune_cache() -> None:
    """Serialize a full source+variant budget pass with cache writers."""
    with _write_lock:
        _prune()


def get_or_fetch(url: str) -> Path | None:
    """Path to the on-disk cached image for ``url``, fetching + caching on first
    use. ``None`` when the URL isn't allowed, the fetch fails/oversizes, or the
    bytes aren't a raster image Glide can render."""
    if not is_allowed_url(url):
        return None
    # Before anything touches the cache, so an off-ladder width and its snapped
    # form share one cache entry instead of the former permanently missing.
    url = normalize_thumb_url(url)
    hit = _existing(url)
    if hit is not None:
        with contextlib.suppress(OSError):
            hit.touch()  # bump mtime for the LRU prune
        return hit
    data = _download(url)
    if data is None:
        # The render was refused (or has gone away) but the file behind it
        # hasn't. Cached under the thumbnail's digest either way, so this
        # costs one extra fetch on a miss and nothing on a hit.
        original = _original_from_thumb(url)
        data = _download(original) if original else None
    if data is None:
        return None
    ext = _sniff(data)
    if ext is None:
        return None  # SVG / non-image — the app can't decode it
    out = PHOTO_CACHE_DIR / f"{_digest(url)}.{ext}"
    try:
        with _write_lock:
            PHOTO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = out.with_name(out.name + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(out)
            _prune()
    except OSError:
        logger.info("Plant photo cache write failed")
        return None
    return out


def _download(url: str) -> bytes | None:
    try:
        import requests  # lazy — only this path needs it
    except Exception:  # pragma: no cover
        return None
    try:
        current_url = url
        # Follow redirects ourselves. requests' default auto-follow would issue
        # the next request before we could validate its destination, allowing a
        # public Wikimedia URL to bounce this proxy into an internal service.
        for redirect_count in range(_MAX_REDIRECTS + 1):
            if not is_allowed_url(current_url):
                logger.info("Plant photo URL is outside the fetch allowlist")
                return None
            with requests.get(
                current_url,
                headers={"User-Agent": _USER_AGENT},
                timeout=_TIMEOUT_S,
                stream=True,
                allow_redirects=False,
            ) as resp:
                if resp.status_code in _REDIRECT_STATUSES:
                    location = resp.headers.get("Location")
                    if not isinstance(location, str) or not location.strip():
                        logger.info("Plant photo redirect had no destination")
                        return None
                    destination = urllib.parse.urljoin(current_url, location.strip())
                    # Validate before the next requests.get call, not after it.
                    if not is_allowed_url(destination):
                        logger.info("Plant photo redirected off allowlist; skipping")
                        return None
                    if redirect_count >= _MAX_REDIRECTS:
                        logger.info("Plant photo exceeded redirect limit")
                        return None
                    current_url = destination
                    continue

                resp.raise_for_status()
                buf = bytearray()
                for chunk in resp.iter_content(64 * 1024):
                    buf.extend(chunk)
                    if len(buf) > _MAX_BYTES:
                        logger.info("Plant photo exceeded cache size limit")
                        return None
                return bytes(buf)
    except Exception:  # network blip / timeout / HTTP error — all non-fatal
        logger.info("Plant photo fetch failed; card falls back to placeholder")
        return None
