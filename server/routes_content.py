"""Static and proxied content: plant photos, guide photos, the Aqua model, popular plants.

Moved out of ``main.py``. ``register(router, m)`` adds the routes to the
``/api`` router and receives the ``main`` module; every handler reaches shared
state, helpers and collaborators through ``m.`` at call time — never by
importing them here — so a test that patches a name on ``main`` (or reloads
it) is seen by the routes exactly as before.
"""

import asyncio
import json
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse


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
            lambda: m._webp_variant(
                path,
                m._PLANT_PHOTO_MAX_SIDE,
                m._PLANT_PHOTO_QUALITY,
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
                lambda: m._webp_source_dimensions_allowed(path),
            )
            if dimensions_allowed is False:
                raise HTTPException(status_code=404, detail="Photo unavailable")
        serve, media = (
            (webp, "image/webp") if webp is not None
            else (path, m.plant_photos.media_type_for(path))
        )
        return m._cached_image_response(
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
            None, m._guide_webp_variant, path,
        )
        serve, media = (webp, "image/webp") if webp is not None else (path, "image/png")
        return m._cached_image_response(
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
        if not m._valid_aqua_classes(meta):
            return {"available": False}
        st = model_path.stat()
        if st.st_size <= 0 or st.st_size > 64 * 1024 * 1024:
            return {"available": False}
        model_sha = m._aqua_model_sha256(model_path, st.st_mtime, st.st_size)
        classes_sha = m._aqua_classes_sha256(meta)
        return {
            "available": True,
            "version": meta["model_version"],
            # ``sha256`` is retained for old clients and always means model bytes.
            "sha256": model_sha,
            "classes_sha256": classes_sha,
            "bundle_sha256": m._aqua_bundle_sha256(model_sha, classes_sha),
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
        geo = m.community_geo.client_location(country, region, lat, lon)
        country_norm = geo["country"]
        lat_n, lon_n = geo["lat"], geo["lon"]
        city_n = geo["city"]
        locale = "ru" if str(locale or "en").lower().startswith("ru") else "en"

        key = m._popular_cache_key(country_norm, lat_n, lon_n, locale)
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
                lambda: m._build_popular_bundle(country_norm, city_n, lat_n, lon_n, locale),
            )
            with m._popular_lock:
                m._popular_cache[key] = (now + m._popular_ttl(bundle), bundle)
                m._popular_cache.move_to_end(key)
                while len(m._popular_cache) > m._POPULAR_CACHE_MAX:
                    m._popular_cache.popitem(last=False)
        elif not fresh:
            # Stale: serve it now, refresh in the background (deduped per key).
            m._refresh_popular_bg(key, country_norm, city_n, lat_n, lon_n, locale)

        return JSONResponse(
            content=bundle,
            headers={"cache-control": f"public, max-age={m._popular_ttl(bundle)}"},
        )
