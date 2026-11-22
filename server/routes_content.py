"""Static and proxied content: plant photos, guide photos, the Aqua model, popular plants.

Moved out of ``main.py``. ``register(router, m)`` adds the routes to the
``/api`` router and receives the ``main`` module; every handler reaches shared
state, helpers and collaborators through ``m.`` at call time — never by
importing them here — so a test that patches a name on ``main`` (or reloads
it) is seen by the routes exactly as before.
"""

import asyncio
import functools
import hashlib
import json
import logging
import math
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image

# Imports work flat (``uvicorn main:app`` from ``server/``) or as ``server.*``.
try:
    from . import community_geo, config
except ImportError:
    import community_geo
    import config
try:
    from . import community_http
except ImportError:
    import community_http


logger = logging.getLogger("plantdiseases")


# Large source images (guide PNGs, full-res Wikimedia plant photos) are
# downscaled + re-encoded to WebP once, lazily, into a sibling cache dir and
# served instead: far fewer bytes for a visually identical phone-sized image.
# The variant is keyed by the source file's SIZE (not mtime: a cache hit
# touches the original's mtime for the LRU, which would otherwise force a
# re-transcode on every request); a replaced source of a different size
# regenerates it. WebP is decoded transparently by Glide on any app vintage
# (it sniffs magic bytes, not the URL/extension), so this is backward
# compatible with already-shipped clients.
_WEBP_DIR_NAME = ".webp"
_webp_lock = threading.Lock()
_WEBP_MAX_SOURCE_SIDE = 16_384
_WEBP_MAX_SOURCE_PIXELS = 20_000_000


def _webp_source_dimensions_allowed(src: Path) -> bool | None:
    """Return False only for a source that violates the decode-size policy.

    ``None`` preserves the historical raw-file fallback for unrelated Pillow
    failures. Oversized sources, including Pillow's decompression-bomb error,
    must never be served as that fallback.
    """
    try:
        with Image.open(src) as opened:
            width, height = opened.size
    except Image.DecompressionBombError:
        return False
    except Exception:  # noqa: BLE001 - caller may safely use the old fallback
        return None
    return (
        width > 0
        and height > 0
        and max(width, height) <= _WEBP_MAX_SOURCE_SIDE
        and width * height <= _WEBP_MAX_SOURCE_PIXELS
    )


def _webp_variant(
    src: Path,
    max_side: int,
    quality: int,
    after_write=None,
) -> Path | None:
    """Return a cached downscaled-WebP variant of ``src`` (``max_side`` px on
    the longest edge, ``quality`` 0–100), generating it if stale.

    Any failure (PIL hiccup, unwritable dir) returns ``None``. Callers may fall
    back to the original after enforcing their own source-safety policy.
    """
    try:
        out_dir = src.parent / _WEBP_DIR_NAME
        # Key the variant on the source SIZE, not its mtime. plant_photos
        # touches a cached original's mtime on every hit (its LRU signal), so an
        # mtime-based freshness check would re-transcode on EVERY request and
        # churn the served file's ETag. Size is stable across access-touch; a
        # genuinely replaced source (different bytes ⇒ different size) still gets
        # a fresh variant under a new name.
        out = out_dir / f"{src.stem}.{src.stat().st_size}.webp"
        if out.is_file():
            return out
        # One transcode at a time — two concurrent first requests for the
        # same key must not interleave writes into the same target file.
        with _webp_lock:
            if out.is_file():
                return out
            out_dir.mkdir(parents=True, exist_ok=True)
            with Image.open(src) as opened:
                w, h = opened.size
                if (
                    w <= 0
                    or h <= 0
                    or max(w, h) > _WEBP_MAX_SOURCE_SIDE
                    or w * h > _WEBP_MAX_SOURCE_PIXELS
                ):
                    raise ValueError("image dimensions exceed WebP source limit")
                opened.load()
                img = opened.convert("RGB")
            scale = max_side / float(max(w, h))
            if scale < 1.0:
                img = img.resize(
                    (max(1, int(w * scale)), max(1, int(h * scale))),
                    Image.LANCZOS,
                )
            tmp = out.with_suffix(".webp.tmp")
            img.save(tmp, format="WEBP", quality=quality, method=4)
            tmp.replace(out)
            if after_write is not None:
                after_write()
        return out
    except Exception:  # noqa: BLE001 — fall back to the original
        logger.warning("WebP transcode failed")
        return None


# Guide photos ship as large PNGs (80–565 KB of photographic content — the
# worst case for PNG); 800 px is plenty for the in-app example strips.
_GUIDE_WEBP_MAX_SIDE = 800
_GUIDE_WEBP_QUALITY = 80


def _guide_webp_variant(src: Path) -> Path | None:
    return _webp_variant(src, _GUIDE_WEBP_MAX_SIDE, _GUIDE_WEBP_QUALITY)


# Plant-photo proxy: this one URL feeds both the small home carousel and the
# species-detail hero, so 1000 px keeps the hero crisp while still cutting a
# multi-MB Wikimedia original to tens of KB. Config-driven so a deploy can tune
# the bandwidth/quality trade-off without a code change.
_PLANT_PHOTO_MAX_SIDE = config.get_int("PLANT_PHOTO_MAX_SIDE", 1000)
_PLANT_PHOTO_QUALITY = config.get_int("PLANT_PHOTO_QUALITY", 80)


# Cache the model digest by (path, mtime, size) so we hash the (multi-MB)
# tflite once per provisioned file instead of on every manifest poll.
# lru_cache is thread-safe and bounded (the previous hand-rolled dict had no
# lock — concurrent manifest polls could interleave on it).
@functools.lru_cache(maxsize=4)
def _aqua_model_sha256(path: Path, mtime: float, size: int) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _valid_aqua_classes(meta: object) -> bool:
    """Return whether an Aqua sidecar is installable by the Android runtime.

    Keep this contract intentionally narrow and mirrored in
    ``AquaModelManager.validClasses``.  Reporting a model as available with a
    merely parseable sidecar causes a guaranteed client-side install failure,
    so missing preprocessing or output semantics fail closed here.
    """
    if not isinstance(meta, dict):
        return False
    if any(
        key in meta
        for key in (
            "_model_sha256",
            "_classes_sha256",
            "_bundle_sha256",
            "_model_version",
        )
    ):
        return False
    names = meta.get("class_names")
    if (
        not isinstance(names, list)
        or not names
        or any(not isinstance(name, str) or not name or name.strip() != name for name in names)
        or len(set(names)) != len(names)
    ):
        return False
    if meta.get("model_family") != "aqua":
        return False
    version = meta.get("model_version")
    if not isinstance(version, str) or not version.strip():
        return False

    training = meta.get("training")
    if not isinstance(training, dict):
        return False
    img_size = training.get("img_size")
    resize_ratio = training.get("eval_resize_ratio")
    if isinstance(img_size, bool) or not isinstance(img_size, int) or not 1 <= img_size <= 4096:
        return False
    if (
        isinstance(resize_ratio, bool)
        or not isinstance(resize_ratio, (int, float))
        or not math.isfinite(float(resize_ratio))
        or not 1.0 <= float(resize_ratio) <= 4.0
    ):
        return False

    inference = meta.get("inference")
    if not isinstance(inference, dict):
        return False
    if (
        inference.get("layout") != "NHWC"
        or inference.get("tta") != "none"
        or inference.get("output_semantics") != "logits"
    ):
        return False

    def _valid_triple(key: str, *, positive: bool) -> bool:
        values = inference.get(key)
        if not isinstance(values, list) or len(values) != 3:
            return False
        for value in values:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return False
            number = float(value)
            if not math.isfinite(number) or (positive and number <= 0.0):
                return False
        return True

    if not (_valid_triple("normalize_mean", positive=False) and _valid_triple(
        "normalize_std", positive=True,
    )):
        return False
    return _valid_aqua_ood(meta.get("ood", None), names) if "ood" in meta else True


# Scopes the on-device MaxLogit floor may be scored over (notebook §5,
# ``AquaOodGate`` in the app); the server's own gate uses the same two.
_AQUA_OOD_SCOPES = frozenset({"plant_only", "all_classes"})


def _valid_aqua_ood(ood, names: list) -> bool:
    """The optional ``classes.json -> ood`` block of an Aqua sidecar.

    Absent, or present with a null floor, means the gate is off and the app
    serves exactly as before. A present floor must be a finite number with a
    known scope; ``plant_only`` needs a ``not_a_plant`` column to exclude.
    Mirrors ``AquaOodGate.fromSidecar`` in the app.
    """
    if ood is None:
        return True
    if not isinstance(ood, dict):
        return False
    floor = ood.get("max_logit_floor")
    if floor is None:
        return True
    if isinstance(floor, bool) or not isinstance(floor, (int, float)) or not math.isfinite(float(floor)):
        return False
    scope = ood.get("max_logit_scope")
    if scope not in _AQUA_OOD_SCOPES:
        return False
    return scope != "plant_only" or "not_a_plant" in names


def _aqua_classes_sha256(meta: dict) -> str:
    """Hash the sidecar's JSON meaning, independent of whitespace/key order."""
    canonical = json.dumps(
        meta,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _aqua_bundle_sha256(model_sha256: str, classes_sha256: str) -> str:
    """Bind the model and semantic sidecar into one freshness identity."""
    digest = hashlib.sha256()
    digest.update(b"plantvision-aqua-bundle-v1\0")
    digest.update(bytes.fromhex(model_sha256))
    digest.update(bytes.fromhex(classes_sha256))
    return digest.hexdigest()


def register(router: APIRouter, m) -> None:
    @router.get("/plant-photo")
    async def plant_photo(request: Request, u: str | None = None,
                          name: str | None = None, locale: str = "en"):
        """Proxy + cache an external plant photo so the app loads it from this
        server's domain — reliable where Wikimedia is throttled/blocked.

        Two ways to address a photo:
        - ``u`` — a (URL-encoded) source URL (used for species-detail extra photos);
        - ``name`` — a scientific name the proxy *fully resolves* to its Wikipedia
          lead image server-side (used by the popular carousel, so a species that
          only just became popular still resolves a photo on demand).

        Only Wikimedia/Wikipedia hosts are ever fetched. 404 when the photo can't be
        cached (no image, fetch failure, or a non-raster format) — the client then
        shows its plant placeholder, the same graceful fallback as before."""
        loop = asyncio.get_running_loop()
        url = (u or "").strip()
        if url:
            if not m.plant_photos.is_allowed_url(url):
                raise HTTPException(status_code=400, detail="Invalid photo URL")
        elif name:
            nm = name.strip()[:128]
            lang = "ru" if str(locale or "en").lower().startswith("ru") else "en"
            info = await loop.run_in_executor(
                m._get_net_executor(), lambda: m.species_info.fetch_species_info(nm, lang),
            )
            url = ((info or {}).get("image_url") or "").strip()
            if not url:
                raise HTTPException(status_code=404, detail="Photo unavailable")
        else:
            raise HTTPException(status_code=400, detail="Missing photo source")
        path = await loop.run_in_executor(
            m._get_net_executor(), lambda: m.plant_photos.get_or_fetch(url),
        )
        if path is None:
            raise HTTPException(status_code=404, detail="Photo unavailable")
        # Wikimedia lead images come at full resolution (often multi-MB) — the home
        # carousel pulls ~10 of them, the worst payload on the main screen for a
        # slow connection. Downscale + WebP-transcode to a phone-sized variant once
        # and serve that; fall back to the original bytes if transcoding fails.
        # Backward compatible: Glide decodes WebP transparently on any app version.
        webp = await loop.run_in_executor(
            None,
            lambda: _webp_variant(
                path,
                _PLANT_PHOTO_MAX_SIDE,
                _PLANT_PHOTO_QUALITY,
                m.plant_photos.prune_cache,
            ),
        )
        if webp is None:
            # A transcode failure normally falls back to the original. Dimension
            # policy failures are different: returning that compressed source
            # would pass a decompression bomb or extreme allocation risk through
            # to the mobile decoder, defeating the pre-load guard above.
            dimensions_allowed = await loop.run_in_executor(
                None,
                lambda: _webp_source_dimensions_allowed(path),
            )
            if dimensions_allowed is False:
                raise HTTPException(status_code=404, detail="Photo unavailable")
        serve, media = (
            (webp, "image/webp") if webp is not None
            else (path, m.plant_photos.media_type_for(path))
        )
        return community_http.cached_image_response(
            request, serve,
            media_type=media,
            cache_control="public, max-age=604800",
        )


    @router.get("/guide/photo/{key}")
    async def guide_photo(key: str, request: Request):
        """Serve a guide example photo by key (e.g. ``powdery_mildew``).

        Path-traversal guarded like the community-thumb route; missing keys 404.
        Long-cached because the photos are immutable per release. Serves the
        downscaled WebP variant when available (Glide sniffs the magic bytes,
        so clients of any vintage decode it transparently); the original PNG
        is the fallback if transcoding ever fails.
        """
        if "/" in key or "\\" in key or ".." in key:
            raise HTTPException(status_code=400, detail="Invalid guide photo key")
        path = m.GUIDE_STATIC_DIR / f"{key}.png"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Guide photo not found")
        webp = await asyncio.get_running_loop().run_in_executor(
            None, _guide_webp_variant, path,
        )
        serve, media = (webp, "image/webp") if webp is not None else (path, "image/png")
        return community_http.cached_image_response(
            request, serve,
            media_type=media,
            cache_control="public, max-age=604800",
        )


    @router.get("/aqua/manifest")
    async def aqua_manifest():
        """Describe the hosted Aqua on-device model so the app can decide whether
        to (re)download it. ``available:false`` when the artefact isn't provisioned
        — the app then just uses the server model. ``sha256`` lets the client skip
        a re-download when it already has the current bytes; ``classes`` is the
        sidecar metadata (class order, input size, normalisation) the app needs to
        run the model.  ``bundle_sha256`` binds model bytes to the semantic
        sidecar, so changing class order or preprocessing also forces an update."""
        model_path = m.AQUA_STATIC_DIR / "aqua_classifier.tflite"
        meta_path = m.AQUA_STATIC_DIR / "classes.json"
        if not model_path.is_file() or not meta_path.is_file():
            return {"available": False}
        try:
            meta = json.loads(
                meta_path.read_text(encoding="utf-8"),
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"non-standard JSON constant {value}")
                ),
            )
        except (OSError, ValueError, TypeError):
            return {"available": False}
        if not _valid_aqua_classes(meta):
            return {"available": False}
        st = model_path.stat()
        if st.st_size <= 0 or st.st_size > 64 * 1024 * 1024:
            return {"available": False}
        model_sha = _aqua_model_sha256(model_path, st.st_mtime, st.st_size)
        classes_sha = _aqua_classes_sha256(meta)
        return {
            "available": True,
            "version": meta["model_version"],
            # ``sha256`` is retained for old clients and always means model bytes.
            "sha256": model_sha,
            "classes_sha256": classes_sha,
            "bundle_sha256": _aqua_bundle_sha256(model_sha, classes_sha),
            "bundle_schema_version": 1,
            "size_bytes": st.st_size,
            "classes": meta,
        }


    @router.get("/aqua/model")
    async def aqua_model():
        """Serve the Aqua TFLite model bytes. 404 when not provisioned."""
        model_path = m.AQUA_STATIC_DIR / "aqua_classifier.tflite"
        if not model_path.is_file():
            raise HTTPException(status_code=404, detail="Aqua model not provisioned")
        return FileResponse(
            model_path,
            media_type="application/octet-stream",
            filename="aqua_classifier.tflite",
            headers={"Cache-Control": "no-cache"},
        )


    @router.get("/popular")
    async def popular_endpoint(request: Request, country: str | None = None,
                               region: str | None = None, lat: float | None = None,
                               lon: float | None = None, locale: str = "en"):
        """Personalized popular-plants bundle with lead images pre-resolved.

        Location is supplied by the opted-in client (coarse ``lat``/``lon`` +
        reverse-geocoded ``country``/``region`` city); a request with none gets the
        worldwide bundle. Cached per (country, coords, locale); a stale entry is
        served immediately and refreshed in the background so a user never waits on
        the per-species Wikipedia calls."""
        geo = community_geo.client_location(country, region, lat, lon)
        country_norm = geo["country"]
        lat_n, lon_n = geo["lat"], geo["lon"]
        city_n = geo["city"]
        locale = "ru" if str(locale or "en").lower().startswith("ru") else "en"

        key = m.popular_bundle.popular_cache_key(country_norm, lat_n, lon_n, locale)
        now = time.time()
        with m._popular_lock:
            hit = m._popular_cache.get(key)
            if hit:
                m._popular_cache.move_to_end(key)
        bundle = hit[1] if hit else None
        fresh = bool(hit and hit[0] > now)

        if bundle is None:
            # Cold: build synchronously (first ever request for this scope).
            bundle = await asyncio.get_running_loop().run_in_executor(
                m._get_net_executor(),
                lambda: m.popular_bundle.build_popular_bundle(country_norm, city_n, lat_n, lon_n, locale),
            )
            with m._popular_lock:
                m._popular_cache[key] = (now + m.popular_bundle.popular_ttl(bundle), bundle)
                m._popular_cache.move_to_end(key)
                while len(m._popular_cache) > m._POPULAR_CACHE_MAX:
                    m._popular_cache.popitem(last=False)
        elif not fresh:
            # Stale: serve it now, refresh in the background (deduped per key).
            m._refresh_popular_bg(key, country_norm, city_n, lat_n, lon_n, locale)

        return JSONResponse(
            content=bundle,
            headers={"cache-control": f"public, max-age={m.popular_bundle.popular_ttl(bundle)}"},
        )
