"""Community: shared scans, safety, map and feeds.

Moved out of ``main.py``. ``register(router, m)`` adds the routes to the
``/api`` router and receives the ``main`` module; every handler reaches shared
state, helpers and collaborators through ``m.`` at call time — never by
importing them here — so a test that patches a name on ``main`` (or reloads
it) is seen by the routes exactly as before.
"""

import asyncio

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator

# Collaborator modules (never names patched on ``main``) are imported here;
# flat (``uvicorn main:app`` from ``server/``) or as ``server.*``.
try:
    from . import community_geo, config, feedback_labels
except ImportError:
    import community_geo
    import config
    import feedback_labels
try:
    from . import community_http
except ImportError:
    import community_http


class CommunityScanRequest(BaseModel):
    """Upload metadata for a single community-shared scan. The image
    bytes ride on a separate multipart field on the upload endpoint
    so a thin client never has to base64-encode anything.

    Hierarchy: ``country`` is the canonical top bucket (ISO code or
    country name); ``region`` is the optional sub-country locality
    (city, admin area). Older clients that still send the legacy
    composite as ``region`` get auto-split — the country tail lands
    in ``country`` and the leading segment in ``region``.
    """

    scan_id: str = Field(..., min_length=8, max_length=64)
    country: str | None = Field(default=None, min_length=2, max_length=80)
    region: str = Field(default="", max_length=80)
    disease: str = Field(..., min_length=1, max_length=64)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    species: str | None = Field(default=None, max_length=128)
    species_score: float | None = Field(default=None, ge=0.0, le=1.0)
    locale: str | None = Field(default=None, max_length=8)
    @field_validator("scan_id")
    @classmethod
    def _safe_token(cls, v: str) -> str:
        if any(ch not in feedback_labels.SCAN_ID_ALLOWED for ch in v):
            raise ValueError("invalid characters in id; expected [A-Za-z0-9_-]")
        return v

    @field_validator("country")
    @classmethod
    def _safe_country(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = community_geo.normalise_country(v)
        if not cleaned:
            raise ValueError(
                "country must be a country name or ISO code (no control chars)"
            )
        return cleaned

    @field_validator("region")
    @classmethod
    def _safe_region(cls, v: str) -> str:
        # Empty is legal — country-only writes are supported.
        cleaned = community_geo.normalise_locality(v)
        if v and not cleaned:
            raise ValueError("region contains invalid characters")
        return cleaned

    @field_validator("disease")
    @classmethod
    def _known_disease(cls, v: str) -> str:
        cleaned = v.strip().lower()
        # ``not_a_plant`` and the deferred physiological classes are
        # legal community-share values too — they're still useful
        # signals for the retraining loop.
        if cleaned not in feedback_labels.FEEDBACK_ALLOWED_CORRECTIONS:
            raise ValueError(
                f"disease must be one of {sorted(feedback_labels.FEEDBACK_ALLOWED_CORRECTIONS)}"
            )
        return cleaned

    @field_validator("locale")
    @classmethod
    def _normalise_locale(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip().lower()[:8]
        return cleaned or None


class CommunityViewRequest(BaseModel):
    # ``scan_id`` (singular) is the original single-open shape, kept required
    # in name only — older installs still send it alone. ``scan_ids`` is the
    # newer batch shape for reporting many feed impressions in one call. A
    # request must carry at least one of the two; see the validator below.
    scan_id: str | None = Field(None, max_length=128)
    scan_ids: list[str] | None = Field(None, max_length=50)

    @model_validator(mode="after")
    def _require_a_target(self) -> "CommunityViewRequest":
        if not (self.scan_id or "").strip() and not any(
            (s or "").strip() for s in (self.scan_ids or [])
        ):
            raise ValueError("scan_id or scan_ids is required")
        return self


def _valid_bbox(
    min_lat: float, min_lon: float, max_lat: float, max_lon: float,
) -> tuple[float, float, float, float] | None:
    """Sanity-check + clamp a map bbox. Returns ``(lo_lat, lo_lon, hi_lat,
    hi_lon)`` within ±90/±180, or ``None`` when degenerate / out of range."""
    try:
        lo_lat, hi_lat = sorted((float(min_lat), float(max_lat)))
        lo_lon, hi_lon = sorted((float(min_lon), float(max_lon)))
    except (TypeError, ValueError):
        return None
    lo_lat, hi_lat = max(-90.0, lo_lat), min(90.0, hi_lat)
    lo_lon, hi_lon = max(-180.0, lo_lon), min(180.0, hi_lon)
    if hi_lat <= lo_lat or hi_lon <= lo_lon:
        return None
    return (lo_lat, lo_lon, hi_lat, hi_lon)


COMMUNITY_DAILY_QUOTA = config.get_int("COMMUNITY_DAILY_QUOTA", 50)


def register(router: APIRouter, m) -> None:
    account = m.account_dependency

    @router.post("/community/scan", dependencies=[Depends(m.community_bearer)])
    async def community_scan_upload(
        request: Request,
        scan_id: str = Form(...),
        country: str | None = Form(None),
        region: str = Form(""),
        lat: float | None = Form(None),
        lon: float | None = Form(None),
        disease: str = Form(...),
        confidence: float | None = Form(None),
        species: str | None = Form(None),
        species_score: float | None = Form(None),
        locale: str | None = Form(None),
        image: UploadFile | None = File(None),
    ):
        """Append one community-shared scan to the corpus.

        Identity and nickname come exclusively from the verified access token.
        Location is supplied by the opted-in client from its coarse location
        (``country`` + ``region`` city + rounded ``lat``/``lon``) — there is no
        server-side IP geolocation.
        The image is required and must be byte-for-byte identical to the upload
        accepted by ``/api/analyze`` when ``scan_id`` was issued. The server derives
        the stored thumbnail from that already-sanitized upload.
        """

        principal = await m._require_account(request)
        try:
            payload = CommunityScanRequest(
                scan_id=scan_id,
                country=country,
                region=region,
                disease=disease,
                confidence=confidence,
                species=species,
                species_score=species_score,
                locale=locale,
            )
        except Exception as exc:  # noqa: BLE001 — pydantic raises a ValidationError subclass
            m.logger.info("Community scan validation failed")
            raise HTTPException(status_code=422, detail="Invalid scan upload") from exc

        # Bind the shared scan to a publication token we actually issued at
        # /api/analyze. This capability is independent from feedback receipts;
        # without it any client could
        # post a fabricated diagnosis (arbitrary species/disease/confidence) into
        # the public feed under a made-up or copied scan_id. On-device Aqua scans
        # have no server-issued id and are kept private client-side, so a
        # legitimate upload always carries an issued id. Checked BEFORE the quota
        # read and the (up to 10 MB) image body so a forged id is rejected without
        # us ingesting its payload.
        issued = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: m.community_scans.is_analysis_token_issued(payload.scan_id),
        )
        if not issued:
            m.logger.warning("Community publication capability rejected")
            raise HTTPException(
                status_code=422,
                detail="Unknown scan_id - re-scan before sharing to the community.",
            )

        geo = community_geo.client_location(payload.country, payload.region, lat, lon)

        # Enforce the per-IP daily quota BEFORE reading the (up to 10 MB)
        # image body — an over-quota client should be rejected without us
        # ingesting its payload first.
        client_ip = m._client_ip(request)
        if not m._consume_daily_write("community_scan", client_ip, COMMUNITY_DAILY_QUOTA):
            m.logger.warning("Community daily upload quota reached")
            raise HTTPException(
                status_code=429,
                detail="Daily community-upload quota reached.",
            )

        image_bytes: bytes | None = None
        if image is not None:
            if image.content_type and image.content_type not in m._COMMUNITY_ALLOWED_TYPES:
                raise HTTPException(
                    status_code=400,
                    detail="Community image must be JPEG / PNG / WebP / BMP",
                )
            try:
                image_bytes = await m._read_upload(image)
            except HTTPException:
                raise
            except Exception:  # noqa: BLE001
                m.logger.info("Community image read failed")
                image_bytes = None
            # Re-validate the actual bytes — Content-Type is attacker-controlled
            # (and optional), but the magic bytes don't lie. The exact bytes are
            # also matched against the digest stored with the analysis capability.
            if image_bytes:
                m._validate_image_bytes(image_bytes)
        try:
            row_id = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: m.community_scans.record_analysis_scan(
                    scan_id=payload.scan_id,
                    account_actor_id=principal.actor_id,
                    country=geo["country"] or "",
                    region=geo["city"] or "",
                    city=geo["city"],
                    lat=geo["lat"],
                    lon=geo["lon"],
                    client_disease=payload.disease,
                    client_confidence=payload.confidence,
                    client_species=payload.species,
                    nickname=principal.nickname,
                    client_species_score=payload.species_score,
                    image_bytes=image_bytes,
                    locale=payload.locale,
                ),
            )
        except m.community_scans.AnalysisTokenUnknownError as exc:
            raise HTTPException(
                status_code=422,
                detail="Unknown scan_id - re-scan before sharing to the community.",
            ) from exc
        except m.community_scans.AnalysisImageMismatchError as exc:
            raise HTTPException(
                status_code=422,
                detail="Community image does not match the analyzed image.",
            ) from exc
        except m.community_scans.AnalysisTokenConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail="This scan_id has already been published.",
            ) from exc
        except Exception as exc:  # noqa: BLE001
            m.logger.error("Community scan write failed")
            raise HTTPException(status_code=500, detail="Failed to record scan") from exc

        # A new scan can change "popular near you" — drop the cached bundles so it
        # surfaces on the next request rather than only after the TTL lapses.
        m._invalidate_popular_cache()
        return JSONResponse(content={"ok": True, "id": row_id})


    @router.get(
        "/community/safety/blocks", dependencies=[Depends(m.community_bearer)]
    )
    async def community_list_blocks(principal=Depends(account)):
        """List only the caller's blocks using unlinkable opaque block ids."""
        items = await asyncio.get_running_loop().run_in_executor(
            None, lambda: m.community_safety.actor_blocks(principal.actor_id)
        )
        return JSONResponse(
            content={"items": items}, headers={"Cache-Control": "no-store"}
        )


    @router.delete(
        "/community/safety/blocks/{block_id}",
        dependencies=[Depends(m.community_bearer)],
    )
    async def community_remove_block(block_id: str, principal=Depends(account)):
        """Delete only a block owned by the signed caller."""
        if len(block_id) != 32 or not block_id.startswith("cb"):
            raise HTTPException(status_code=422, detail="invalid_block_id")
        removed = await asyncio.get_running_loop().run_in_executor(
            None, lambda: m.community_safety.unblock_actor(principal.actor_id, block_id)
        )
        if not removed:
            raise HTTPException(status_code=404, detail="block_not_found")
        return JSONResponse(
            content={"ok": True}, headers={"Cache-Control": "no-store"}
        )


    @router.post("/community/view", dependencies=[Depends(m.community_bearer)])
    async def community_record_scan_view(payload: CommunityViewRequest, request: Request):
        """Record that the caller opened (or merely scrolled past) someone
        else's shared scan — one id via ``scan_id``, or up to 50 via
        ``scan_ids`` for feed-scroll impressions. Deduped per viewer
        server-side; a scan owner viewing their own scan and views of an
        unknown scan never count, so this is safe to fire liberally.

        The whole batch is resolved and written in one pass and is bound to the
        verified account actor, never a client-supplied identifier."""
        principal = await m._require_account(request)
        loop = asyncio.get_running_loop()
        ids = [s.strip() for s in (payload.scan_ids or []) if s and s.strip()]
        if payload.scan_id and payload.scan_id.strip():
            ids.append(payload.scan_id.strip())
        # The declared cap is per-field; ``scan_id`` + a full ``scan_ids`` can
        # exceed it, and a client may repeat an id across the two.
        ids = list(dict.fromkeys(ids))[:50]
        # An explicit single-scan open keeps a read-only contract: a legacy
        # (pre-account) row is readable but never mutable, and the caller is told so rather than being handed a silent no-op. A
        # feed-scroll batch stays lenient and just skips any such id.
        if payload.scan_id and payload.scan_id.strip() and not payload.scan_ids:
            sid = await loop.run_in_executor(
                None, lambda: m.community.resolve_scan_token(payload.scan_id.strip()),
            )
            owner_actor = await loop.run_in_executor(
                None, lambda: m.community_scans.account_owner_of(sid)
            )
            if owner_actor is None:
                raise HTTPException(status_code=409, detail="legacy_scan_read_only")
        counted = await loop.run_in_executor(
            None,
            lambda: m.community_views.record_views(ids, account_actor_id=principal.actor_id),
        )
        return JSONResponse(content={"ok": True, "counted": counted > 0, "counted_n": counted})


    @router.get("/community/nearby")
    async def community_scan_nearby(
        request: Request,
        country: str | None = None,
        region: str | None = None,
        lat: float | None = None,
        lon: float | None = None,
        disease: str | None = None,
        species: str | None = None,
        limit: int = 20,
        scope: str | None = None,
        ranking: str | None = None,
        cursor: str | None = None,
    ):
        """List the most recent community-shared scans near the requester.

        Location is supplied by the opted-in client (coarse ``lat``/``lon`` +
        reverse-geocoded ``country``/``region`` city). ``scope`` is the app's
        filter toggle: ``nearby`` (default) restricts the feed to a ~30 km radius
        of the coords; ``country`` widens to the whole country; ``worldwide`` drops
        the geo filter entirely (every country, newest first — needs no location).
        ``species`` + ``disease`` narrow the result-screen "similar scans"
        carousel, which widens nearby→country to stay filled.

        ``cursor`` pages both recommendation and recent order with a signed,
        opaque composite key; tied upload timestamps therefore cannot lose rows.
        The response cursor is null once a page comes back short. Paging is
        single-pass only:
        combining it with ``disease`` or ``species`` is rejected with 422,
        because those widen the query into several concatenated passes whose
        concatenation has no single global ``created_at`` order to page on.
        """
        ranking_norm = (ranking or "recent").strip().lower()
        if ranking_norm not in {"recent", "recommended"}:
            raise HTTPException(status_code=422, detail="Invalid community ranking.")
        if cursor and ((disease or "").strip() or (species or "").strip()):
            raise HTTPException(
                status_code=422,
                detail="cursor cannot be combined with disease or species.",
            )
        if ranking_norm == "recommended" and ((disease or "").strip() or (species or "").strip()):
            raise HTTPException(
                status_code=422,
                detail="recommended ranking cannot be combined with disease or species.",
            )
        principal = await m._optional_account(request)
        try:
            recommendation_cursor = m.community_feeds.decode_recommendation_cursor(cursor)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid community cursor.") from exc
        if ranking_norm == "recent" and recommendation_cursor is not None \
                and recommendation_cursor[0] != 0:
            raise HTTPException(status_code=422, detail="Invalid recent community cursor.")
        geo = community_geo.client_location(country, region, lat, lon)
        country_norm = geo["country"]
        scope_norm = (scope or "nearby").strip().lower()
        if scope_norm not in ("nearby", "country", "worldwide"):
            scope_norm = "nearby"
        nearby = scope_norm == "nearby"
        worldwide = scope_norm == "worldwide"
        has_coords = geo["lat"] is not None
        # With neither coords nor a country a *geo-scoped* query has no anchor, so
        # the feed comes back empty for a reason the client can't otherwise tell
        # apart from "no scans near you" (the usual cause: community opted in but
        # the coarse-location permission was never granted / no fix yet). Surface
        # it as an explicit flag so the client can prompt to enable location
        # instead of rendering a misleading empty state. Worldwide needs no anchor,
        # so it never sets the flag. Storage stays privacy-preserving — there is
        # deliberately no server-side IP geolocation.
        location_required = not worldwide and not (bool(country_norm) or has_coords)
        effective_limit = max(1, min(int(limit), 50))
        # Every community_* call below is blocking sqlite3. This is the feed the
        # gallery polls and the queries are the heaviest reads in the server, so
        # each one hops to a worker thread rather than stalling the event loop.
        loop = asyncio.get_running_loop()
        if ranking_norm == "recommended":
            rows = await loop.run_in_executor(
                None,
                lambda: m.community_feeds.recommended_scans(
                    country=country_norm,
                    lat=geo["lat"] if nearby else None,
                    lon=geo["lon"] if nearby else None,
                    limit=effective_limit,
                    exclude_account_actor_id=principal.actor_id if principal else None,
                    viewer_account_actor_id=principal.actor_id if principal else None,
                    scope=scope_norm,
                    cursor=recommendation_cursor,
                ),
            )
        else:
            rows = await loop.run_in_executor(
                None,
                lambda: m.community_feeds.nearby_scans(
                    country=country_norm,
                    lat=geo["lat"] if nearby else None,
                    lon=geo["lon"] if nearby else None,
                    disease=(disease or None),
                    species=(species or None),
                    limit=effective_limit,
                    exclude_account_actor_id=principal.actor_id if principal else None,
                    viewer_account_actor_id=principal.actor_id if principal else None,
                    scope=scope_norm,
                    cursor=(recommendation_cursor[1], recommendation_cursor[2])
                    if recommendation_cursor is not None else None,
                ),
            )
        # A short page means the scope is exhausted; a full page might have more
        # (the very last page just happens to also be full-sized, and the client
        # finds out on the follow-up request that comes back empty).
        next_cursor = None
        if rows and len(rows) >= effective_limit:
            last = rows[-1]
            next_cursor = m.community_feeds.encode_recommendation_cursor(
                int(last.get("_cursor_score", 0)),
                int(last["created_at"]),
                int(last["_cursor_row_id"]),
            )
        # Country-level overview for the chip row (the coords feed itself is
        # radius-scoped; the summary stays country-wide for a stable "what's
        # around" readout). Excludes the requester's own rows so the row doesn't
        # surface the scan they just uploaded.
        summary = await loop.run_in_executor(
            None,
            lambda: m.community_feeds.region_summary(
                country_norm or "",
                None,
                exclude_account_actor_id=principal.actor_id if principal else None,
                viewer_account_actor_id=principal.actor_id if principal else None,
            ),
        )
        # Swap the publication capability for its stable, per-scan public token;
        # feedback uses a separate receipt.
        scan_ids = [str(r.get("scan_id")) for r in rows if r.get("scan_id")]
        views: dict = {}
        if ranking_norm == "recent":
            views = await loop.run_in_executor(
                None, lambda: m.community_views.view_counts(scan_ids),
            )
        for r in rows:
            sid = str(r.get("scan_id"))
            r.setdefault("view_count", views.get(sid, 0))
            r["scan_id"] = r.pop("public_id", None) or r["scan_id"]
            r.pop("_cursor_score", None)
            r.pop("_cursor_row_id", None)
            # Localize the per-row country so cards show "Poland"/"Польша", not "PL".
            community_geo.localize_item_country(r)
        meta = community_geo.country_display(country_norm)
        # Tell the client which filter was actually served + the nearby label.
        if worldwide:
            meta["scope"] = "worldwide"
            meta["region"] = None
        else:
            meta["scope"] = "nearby" if (nearby and has_coords) else meta["scope"]
            meta["region"] = geo["city"] if (nearby and has_coords) else None
        meta["location_required"] = location_required
        summary.update(meta)
        # Older installed app versions type CommunityRegionSummary.country as a
        # non-nullable String and decode with strict kotlinx.serialization (no
        # coerceInputValues) — an explicit `null` here (worldwide scope, or a
        # coarse location in a territory with no ISO code) throws a decode error
        # client-side, surfacing as "couldn't reach the community". Newer app
        # builds tolerate the null directly; this coercion is just backward
        # compatibility for what's already installed. Only the nested copy is
        # coerced — the top-level `country` field stays nullable.
        summary["country"] = summary.get("country") or ""
        return JSONResponse(
            content={
                "items": rows,
                "summary": summary,
                "ranking": ranking_norm,
                "next_cursor": next_cursor,
                **meta,
            },
        )


    @router.get("/community/map")
    async def community_map(
        request: Request,
        min_lat: float, min_lon: float, max_lat: float, max_lon: float,
        disease: str | None = None,
        days: int = 30,
        limit: int = 300,
    ):
        """Individual community scans inside a bbox — the zoomed-in map markers.

        Each scan carries its ~1 km-rounded coords + the PUBLIC token (never the
        real scan_id), so the app can pin a marker and open it. Public (no opt-in to view); ``not_a_plant`` excluded; ``truncated``
        signals an over-full bbox."""
        principal = await m._optional_account(request)
        box = _valid_bbox(min_lat, min_lon, max_lat, max_lon)
        if box is None:
            raise HTTPException(status_code=422, detail="Invalid bbox")
        lo_lat, lo_lon, hi_lat, hi_lon = box
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(
            None,
            lambda: m.community_feeds.map_scans(
                lo_lat, hi_lat, lo_lon, hi_lon,
                disease=(disease or None),
                days=days,
                limit=max(1, min(int(limit), 500)),
                exclude_account_actor_id=principal.actor_id if principal else None,
                viewer_account_actor_id=principal.actor_id if principal else None,
            ),
        )
        rows = data["items"]
        # Same public-token swap + country localization as
        # the nearby feed; here we KEEP lat/lon for the markers.
        for r in rows:
            r["scan_id"] = r.pop("public_id", None) or r["scan_id"]
            community_geo.localize_item_country(r)
        return JSONResponse(content={"items": rows, "truncated": data["truncated"]})


    @router.get("/community/heatmap")
    async def community_heatmap(
        min_lat: float, min_lon: float, max_lat: float, max_lon: float,
        disease: str | None = None,
        days: int = 30,
    ):
        """K-anonymized scan density (grid cells) for the zoomed-out heat layer.

        Public; returns only cell centers + counts (each ≥ 3 scans), never per-scan
        coords; ``not_a_plant`` excluded."""
        box = _valid_bbox(min_lat, min_lon, max_lat, max_lon)
        if box is None:
            raise HTTPException(status_code=422, detail="Invalid bbox")
        lo_lat, lo_lon, hi_lat, hi_lon = box
        data = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: m.community_feeds.density_grid(
                lo_lat, hi_lat, lo_lon, hi_lon,
                disease=(disease or None),
                days=days,
            ),
        )
        return JSONResponse(content=data)


    @router.get("/community/by-species")
    async def community_by_species(
        request: Request,
        species: str | None = None,
        limit: int = 12,
        country: str | None = None,
        region: str | None = None,
        lat: float | None = None,
        lon: float | None = None,
    ):
        """Community scans of one species (by scientific name) — the species
        page's "scans of this species near you" strip. A community member's
        app sends its coarse location (``lat``/``lon`` + ``country``), which
        orders the strip nearest first with a distance per row; without it
        the strip spans all regions."""
        principal = await m._optional_account(request)
        sci = (species or "").strip()
        if not sci:
            raise HTTPException(status_code=422, detail="Invalid species")
        geo = community_geo.client_location(country, region, lat, lon)
        loop = asyncio.get_running_loop()
        rows = await loop.run_in_executor(
            None,
            lambda: m.community_feeds.scans_by_species(
                sci,
                limit=max(1, min(int(limit), 50)),
                exclude_account_actor_id=principal.actor_id if principal else None,
                viewer_account_actor_id=principal.actor_id if principal else None,
                country=geo["country"],
                lat=geo["lat"],
                lon=geo["lon"],
            ),
        )
        for r in rows:
            # Public token only — never the community publication capability.
            r["scan_id"] = r.pop("public_id", None) or r["scan_id"]
            # Localize the per-row country so cards show "Poland"/"Польша", not "PL".
            community_geo.localize_item_country(r)
        return JSONResponse(content={"items": rows})


    @router.get("/community/thumb/{filename}")
    async def community_scan_thumb(filename: str, request: Request):
        """Serve a community thumbnail by filename.

        The corpus only ever stores filenames that ``community._save_thumbnail``
        minted, but we still hard-guard against path traversal: any
        filename containing ``/`` or ``..`` is refused outright. Missing
        files return 404 instead of 500.
        """
        if "/" in filename or "\\" in filename or ".." in filename:
            raise HTTPException(status_code=400, detail="Invalid thumbnail name")
        visible = await asyncio.get_running_loop().run_in_executor(
            None, lambda: m.community_scans.thumbnail_is_visible(filename)
        )
        if not visible:
            raise HTTPException(status_code=404, detail="Thumbnail not found")
        path = m.community.COMMUNITY_THUMB_DIR / filename
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Thumbnail not found")
        # Streams off the event loop; supports 304 revalidation. Thumbnails
        # are always re-encoded to JPEG by community_scans._save_thumbnail.
        return community_http.cached_image_response(
            request, path,
            media_type="image/jpeg",
            cache_control="public, max-age=300, must-revalidate",
        )
