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
  mixes in SVGs, which the app can't decode.

Best-effort throughout: any miss returns ``None`` so the card falls back to its
placeholder, exactly as it did before this proxy existed.
"""
from __future__ import annotations

import contextlib
import hashlib
import logging
import threading
import urllib.parse
from pathlib import Path

try:
    from .config import get_str
except ImportError:  # flat layout (Docker / uvicorn from server/)
    from config import get_str

logger = logging.getLogger("plantdiseases.plant_photos")

_TIMEOUT_S = 6.0
_MAX_BYTES = 16 * 1024 * 1024  # 16 MB — Wikimedia Commons originals routinely run 6-11 MB
_CACHE_MAX_FILES = 600  # LRU cap; oldest-by-mtime evicted on write
_USER_AGENT = (
    "PlantDiseases/1.1 (https://github.com/pmixay/plantdiseases; houseplant care app)"
)

# Only these hosts are fetched. The endpoint is public, so without this an
# attacker could turn it into a generic request proxy (SSRF).
_ALLOWED_HOST_SUFFIXES = (".wikimedia.org", ".wikipedia.org")

_DEFAULT_DIR = Path(__file__).resolve().parent / "plant_photo_cache"
PHOTO_CACHE_DIR = Path(get_str("PLANT_PHOTO_CACHE_DIR", str(_DEFAULT_DIR)))

_write_lock = threading.Lock()

# extension → media type for the raster formats Glide renders.
_EXT_MEDIA = {
    "jpg": "image/jpeg", "png": "image/png", "webp": "image/webp", "gif": "image/gif",
}


def is_allowed_url(url: str) -> bool:
    """True for an http(s) URL on a Wikimedia/Wikipedia host."""
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    return any(host == suffix[1:] or host.endswith(suffix) for suffix in _ALLOWED_HOST_SUFFIXES)


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
    URL at build time."""
    return (
        "api/plant-photo?name=" + urllib.parse.quote(name, safe="")
        + "&locale=" + urllib.parse.quote(locale or "en", safe="")
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
    """Keep the cache under the file cap, dropping oldest by mtime. Cheap;
    runs after a successful write."""
    try:
        files = sorted(PHOTO_CACHE_DIR.glob("*.*"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return
    if len(files) <= _CACHE_MAX_FILES:
        return
    for path in files[: len(files) - _CACHE_MAX_FILES]:
        with contextlib.suppress(OSError):
            path.unlink()


def get_or_fetch(url: str) -> Path | None:
    """Path to the on-disk cached image for ``url``, fetching + caching on first
    use. ``None`` when the URL isn't allowed, the fetch fails/oversizes, or the
    bytes aren't a raster image Glide can render."""
    if not is_allowed_url(url):
        return None
    hit = _existing(url)
    if hit is not None:
        with contextlib.suppress(OSError):
            hit.touch()  # bump mtime for the LRU prune
        return hit
    data = _download(url)
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
    except OSError as exc:
        logger.info("Plant photo cache write failed (%s)", exc)
        return None
    return out


def _download(url: str) -> bytes | None:
    try:
        import requests  # lazy — only this path needs it
    except Exception:  # pragma: no cover
        return None
    try:
        with requests.get(
            url,
            headers={"User-Agent": _USER_AGENT},
            timeout=_TIMEOUT_S,
            stream=True,
        ) as resp:
            resp.raise_for_status()
            buf = bytearray()
            for chunk in resp.iter_content(64 * 1024):
                buf.extend(chunk)
                if len(buf) > _MAX_BYTES:
                    logger.info("Plant photo over %d B; not caching %s", _MAX_BYTES, url)
                    return None
            return bytes(buf)
    except Exception as exc:  # network blip / timeout / HTTP error — all non-fatal
        logger.info("Plant photo fetch failed (%s); card falls back to placeholder", exc)
        return None
