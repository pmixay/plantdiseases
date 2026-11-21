"""Community: shared scans, second opinions, safety, map and feeds.

Moved out of ``main.py``. ``register(router, m)`` adds the routes to the
``/api`` router and receives the ``main`` module; every handler reaches shared
state, helpers and collaborators through ``m.`` at call time — never by
importing them here — so a test that patches a name on ``main`` (or reloads
it) is seen by the routes exactly as before.
"""

import asyncio

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse


def register(router: APIRouter, m) -> None:
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
            payload = m.CommunityScanRequest(
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
            lambda: m.community_analysis_token_issued(payload.scan_id),
        )
        if not issued:
            m.logger.warning("Community publication capability rejected")
            raise HTTPException(
                status_code=422,
                detail="Unknown scan_id - re-scan before sharing to the community.",
            )

        geo = m._client_location(payload.country, payload.region, lat, lon)

        # Enforce the per-IP daily quota BEFORE reading the (up to 10 MB)
        # image body — an over-quota client should be rejected without us
        # ingesting its payload first.
        client_ip = m._client_ip(request)
        if not m._consume_daily_write("community_scan", client_ip, m.COMMUNITY_DAILY_QUOTA):
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
                lambda: m.community_record_analysis_scan(
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
        except m.AnalysisTokenUnknownError as exc:
            raise HTTPException(
                status_code=422,
                detail="Unknown scan_id - re-scan before sharing to the community.",
            ) from exc
        except m.AnalysisImageMismatchError as exc:
            raise HTTPException(
                status_code=422,
                detail="Community image does not match the analyzed image.",
            ) from exc
        except m.AnalysisTokenConflictError as exc:
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


    @router.post(
        "/community/safety/report", dependencies=[Depends(m.community_bearer)]
    )
    async def community_report_content(
        payload: m.CommunityReportRequest, request: Request,
    ):
        """Queue a report bound to the signed actor and stored content author.

        The request contains only an opaque public content token plus a fixed
        reason.  It never accepts an actor id or free-form moderation text.
        """
        principal = await m._require_account(request)
        kind = payload.target_type.strip().lower()
        if kind not in {"scan", "opinion"}:
            raise HTTPException(status_code=422, detail="invalid_target_type")
        try:
            created = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: m.community_record_content_report(
                    reporter_actor_id=principal.actor_id,
                    target_type=kind,
                    target_id=payload.target_id.strip(),
                    reason=payload.reason,
                ),
            )
        except ValueError as exc:
            m._raise_community_safety_error(exc)
            raise AssertionError("unreachable") from exc
        return JSONResponse(
            content={"ok": True, "created": bool(created)},
            headers={"Cache-Control": "no-store"},
        )


    @router.post(
        "/community/safety/block", dependencies=[Depends(m.community_bearer)]
    )
    async def community_block_author(
        payload: m.CommunityBlockRequest, request: Request,
    ):
        """Block the verified author resolved from an opaque content token."""
        principal = await m._require_account(request)
        kind = payload.target_type.strip().lower()
        if kind not in {"scan", "opinion"}:
            raise HTTPException(status_code=422, detail="invalid_target_type")
        try:
            result = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: m.community_block_content_author(
                    blocker_actor_id=principal.actor_id,
                    target_type=kind,
                    target_id=payload.target_id.strip(),
                ),
            )
        except ValueError as exc:
            m._raise_community_safety_error(exc)
            raise AssertionError("unreachable") from exc
        return JSONResponse(
            content={"ok": True, **result},
            headers={"Cache-Control": "no-store"},
        )


    @router.get(
        "/community/safety/blocks", dependencies=[Depends(m.community_bearer)]
    )
    async def community_list_blocks(request: Request):
        """List only the caller's blocks using unlinkable opaque block ids."""
        principal = await m._require_account(request)
        items = await asyncio.get_running_loop().run_in_executor(
            None, lambda: m.community_actor_blocks(principal.actor_id)
        )
        return JSONResponse(
            content={"items": items}, headers={"Cache-Control": "no-store"}
        )


    @router.delete(
        "/community/safety/blocks/{block_id}",
        dependencies=[Depends(m.community_bearer)],
    )
    async def community_remove_block(block_id: str, request: Request):
        """Delete only a block owned by the signed caller."""
        principal = await m._require_account(request)
        if len(block_id) != 32 or not block_id.startswith("cb"):
            raise HTTPException(status_code=422, detail="invalid_block_id")
        removed = await asyncio.get_running_loop().run_in_executor(
            None, lambda: m.community_unblock_actor(principal.actor_id, block_id)
        )
        if not removed:
            raise HTTPException(status_code=404, detail="block_not_found")
        return JSONResponse(
            content={"ok": True}, headers={"Cache-Control": "no-store"}
        )


    @router.post("/community/opinion", dependencies=[Depends(m.community_bearer)])
    async def community_opinion_submit(payload: m.CommunityOpinionRequest, request: Request):
        """Record one community 'second opinion' on a shared scan.

        The suggested disease must be a known classifier class so the consensus
        tally stays clean; any free text lives only in the optional note.
        """
        principal = await m._require_account(request)
        disease = payload.suggested_disease.strip()
        if disease not in m.DISEASES_DATABASE:
            raise HTTPException(status_code=422, detail="Unknown suggested disease")

        client_ip = m._client_ip(request)
        loop = asyncio.get_running_loop()
        if not m._consume_daily_write(
            "community_opinion", client_ip, m.COMMUNITY_OPINION_DAILY_QUOTA
        ):
            raise HTTPException(status_code=429, detail="Daily opinion quota reached.")

        # Viewers arrive with the feed's public token; the scan's owner (and
        # legacy cached feeds) with the real scan_id. Resolve to the real id
        # and require the shared scan to exist — opinions against ids that
        # were never shared are write-only noise in the consensus tally.
        sid = await loop.run_in_executor(
            None, lambda: m.community_resolve_scan_token(payload.scan_id.strip()),
        )
        known = await loop.run_in_executor(None, lambda: m.community_scan_exists(sid))
        if not known:
            raise HTTPException(status_code=422, detail="Unknown scan")
        owner_actor = await loop.run_in_executor(
            None, lambda: m.community_account_owner_of(sid)
        )
        if owner_actor is None:
            raise HTTPException(status_code=409, detail="legacy_scan_read_only")
        # A scan older than 30 days goes read-only so stale photos cannot keep
        # collecting opinions. Its per-scan public read handle remains stable.
        fresh = await loop.run_in_executor(None, lambda: m.community_opinion_open(sid))
        if not fresh:
            raise HTTPException(status_code=410, detail="Scan too old for new opinions")
        try:
            _opinion_id, created = await loop.run_in_executor(
                None,
                lambda: m.community_record_opinion(
                    scan_id=sid,
                    account_actor_id=principal.actor_id,
                    suggested_disease=disease,
                    note=payload.note,
                    nickname=principal.nickname,
                ),
            )
        except m.OpinionTargetUnavailableError as exc:
            raise HTTPException(
                status_code=410,
                detail="Scan no longer accepts opinions",
            ) from exc
        except ValueError as exc:
            if str(exc) == "blocked_interaction":
                raise HTTPException(status_code=409, detail="blocked_interaction") from None
            m.logger.error("Community opinion write failed")
            raise HTTPException(status_code=500, detail="Failed to record opinion") from exc
        except Exception as exc:  # noqa: BLE001
            m.logger.error("Community opinion write failed")
            raise HTTPException(status_code=500, detail="Failed to record opinion") from exc

        # Push the scan's owner a notification (best-effort; no-op when push isn't
        # configured). Skip self-opinions — you don't get pinged for your own note.
        # Only owners who explicitly REQUESTED a second opinion get pushed: sharing
        # a scan to the corpus is not a request to be notified.
        author = principal.actor_id

        def _push_owner() -> None:
            try:
                if not created:
                    return
                if not m.community_wants_opinion(sid):
                    return
                owner = owner_actor
                if owner and owner != author:
                    # Delivery is delegated to the account service, which resolves
                    # encrypted device registrations privately. Wired below via
                    # the internal notification client.
                    m._send_account_opinion_notification(
                        owner, sid, disease, payload.note, principal.nickname
                    )
            except Exception:  # noqa: BLE001
                m.logger.debug("Opinion push failed")

        # FCM is external HTTP — keep it off the default (DB-op) pool too.
        await loop.run_in_executor(m._get_net_executor(), _push_owner)

        result = await loop.run_in_executor(
            None,
            lambda: m.community_opinions_for(
                sid, viewer_account_actor_id=principal.actor_id
            ),
        )
        return JSONResponse(
            content={"ok": True, "count": len(result["opinions"]), **result}
        )


    @router.post(
        "/community/request-opinion", dependencies=[Depends(m.community_bearer)]
    )
    async def community_request_opinion(
        payload: m.CommunityRequestOpinionRequest, request: Request,
    ):
        """Flag the scanner's own shared scan as wanting a second opinion.

        Unlike ``/community/opinion`` (where a *viewer* suggests a diagnosis
        for someone else's scan), this is the *scanner* raising their hand:
        "I'm unsure — community, please weigh in." The flag is owner-scoped
        (the update only matches the signed account actor) and
        idempotent.

        Degrades gracefully: the community upload is fire-and-forget and the
        user may have opted out of sharing, so the owned row might not exist
        yet. We still return 200 with ``flagged: false`` — the client shows
        its local "waiting for the community" state regardless.
        """
        principal = await m._require_account(request)
        sid = payload.scan_id.strip()
        client_ip = m._client_ip(request)
        loop = asyncio.get_running_loop()
        if not m._consume_daily_write(
            "community_request_opinion", client_ip, m.COMMUNITY_OPINION_DAILY_QUOTA
        ):
            raise HTTPException(status_code=429, detail="Daily opinion quota reached.")
        try:
            flagged = await loop.run_in_executor(
                None,
                lambda: m.community_set_wants_opinion(
                    sid, account_actor_id=principal.actor_id
                ),
            )
        except Exception as exc:  # noqa: BLE001
            m.logger.error("Community request-opinion failed")
            raise HTTPException(
                status_code=500, detail="Failed to request opinion",
            ) from exc
        return JSONResponse(content={"ok": True, "flagged": bool(flagged)})


    @router.post(
        "/community/request-opinion/cancel", dependencies=[Depends(m.community_bearer)]
    )
    async def community_request_opinion_cancel(
        payload: m.CommunityRequestOpinionRequest, request: Request,
    ):
        """Withdraw an open second-opinion request on the caller's own scan.

        The inverse of ``/community/request-opinion``: drops the durable
        request row + the feed badge, so the community stops seeing the ask
        and the owner stops being notified about new opinions. Opinions
        already left stay visible. Owner-scoped and idempotent — cancelling
        a request that doesn't exist (or isn't yours) returns
        ``cleared: false`` with 200.

        (Requests also lapse on their own after
        ``OPINION_REQUEST_TTL_HOURS`` — see ``community.py``.)
        """
        principal = await m._require_account(request)
        sid = payload.scan_id.strip()
        loop = asyncio.get_running_loop()
        try:
            cleared = await loop.run_in_executor(
                None,
                lambda: m.community_clear_wants_opinion(
                    sid, account_actor_id=principal.actor_id
                ),
            )
        except Exception as exc:  # noqa: BLE001
            m.logger.error("Community cancel-request-opinion failed")
            raise HTTPException(
                status_code=500, detail="Failed to cancel opinion request",
            ) from exc
        return JSONResponse(content={"ok": True, "cleared": bool(cleared)})


    @router.get("/community/opinions/{scan_id}")
    async def community_opinions_list(scan_id: str, request: Request):
        """List community opinions + a consensus tally for one shared scan.

        Accepts the real scan_id (owner's own app) or the feed's public
        token (viewers); echoes back whichever form the caller sent.
        Authentication is optional for this public read. When present it adds
        caller-specific fields such as ``liked_by_me`` and ``is_mine`` and applies
        the caller's block list.
        """
        sid = (scan_id or "").strip()
        if not sid or len(sid) > 128:
            raise HTTPException(status_code=422, detail="Invalid scan id")
        loop = asyncio.get_running_loop()
        principal = await m._optional_account(request)
        resolved = await loop.run_in_executor(
            None, lambda: m.community_resolve_scan_token(sid),
        )
        result = await loop.run_in_executor(
            None,
            lambda: m.community_opinions_for(
                resolved,
                viewer_account_actor_id=principal.actor_id if principal else None,
            ),
        )
        return JSONResponse(content={"scan_id": sid, **result})


    @router.get("/community/profile/{author_token}")
    async def community_author_profile(author_token: str, request: Request):
        """Public community activity for one opaque account-author handle."""
        if len(author_token) != 32 or not author_token.startswith("ca"):
            raise HTTPException(status_code=404, detail="profile_not_found")
        principal = await m._optional_account(request)
        profile = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: m.community_public_account_profile(
                author_token,
                viewer_account_actor_id=principal.actor_id if principal else None,
            ),
        )
        if profile is None:
            raise HTTPException(status_code=404, detail="profile_not_found")
        for item in profile.get("items", []):
            m._localize_item_country(item)
        return JSONResponse(content=profile, headers={"Cache-Control": "no-store"})


    @router.get(
        "/community/my-scan-opinions", dependencies=[Depends(m.community_bearer)]
    )
    async def community_my_scan_opinions(request: Request):
        """Opinion counts on the caller's OWN shared scans. The client polls this
        on a schedule to notify the user when someone leaves a second opinion on a
        scan they shared (the per-scan view already exists via /opinions/{scan_id})."""
        principal = await m._require_account(request)
        result = await asyncio.get_running_loop().run_in_executor(
            None, lambda: m.community_opinion_counts_for_actor(principal.actor_id),
        )
        return JSONResponse(content=result)


    @router.post("/community/view", dependencies=[Depends(m.community_bearer)])
    async def community_record_scan_view(payload: m.CommunityViewRequest, request: Request):
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
        # An explicit single-scan open keeps the read-only contract the opinion
        # route states: a legacy (pre-account) row is readable but never mutable,
        # and the caller is told so rather than being handed a silent no-op. A
        # feed-scroll batch stays lenient and just skips any such id.
        if payload.scan_id and payload.scan_id.strip() and not payload.scan_ids:
            sid = await loop.run_in_executor(
                None, lambda: m.community_resolve_scan_token(payload.scan_id.strip()),
            )
            owner_actor = await loop.run_in_executor(
                None, lambda: m.community_account_owner_of(sid)
            )
            if owner_actor is None:
                raise HTTPException(status_code=409, detail="legacy_scan_read_only")
        counted = await loop.run_in_executor(
            None,
            lambda: m.community_record_views(ids, account_actor_id=principal.actor_id),
        )
        return JSONResponse(content={"ok": True, "counted": counted > 0, "counted_n": counted})


    @router.post("/community/like", dependencies=[Depends(m.community_bearer)])
    async def community_like_scan(payload: m.CommunityLikeRequest, request: Request):
        """Like or unlike a shared scan. A self-like is refused (0 either way);
        liking your own scan makes no more sense than viewing it does."""
        principal = await m._require_account(request)
        client_ip = m._client_ip(request)
        loop = asyncio.get_running_loop()
        if payload.liked and not m._consume_daily_write(
            "community_like", client_ip, m.COMMUNITY_LIKE_DAILY_QUOTA
        ):
            raise HTTPException(status_code=429, detail="Daily like quota reached.")
        sid = await loop.run_in_executor(
            None, lambda: m.community_resolve_scan_token(payload.scan_id.strip()),
        )
        liked = await loop.run_in_executor(
            None,
            lambda: m.community_set_like(
                sid,
                liked=payload.liked,
                account_actor_id=principal.actor_id,
            ),
        )
        count = await loop.run_in_executor(
            None, lambda: m.community_like_counts([sid]).get(sid, 0),
        )
        return JSONResponse(content={"ok": True, "liked": liked, "count": count})


    @router.post("/community/scan-counts", dependencies=[Depends(m.community_bearer)])
    async def community_scan_counts_endpoint(
        payload: m.CommunityScanCountsRequest, request: Request,
    ):
        """Batched ``{opinions, views}`` per scan_id so the Gallery can badge the
        caller's own rows in one round-trip. Unknown/zero scans are omitted."""
        principal = await m._require_account(request)
        ids = [s.strip() for s in payload.scan_ids if s and s.strip()][:500]
        counts = (
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: m.community_scan_counts_for_actor(principal.actor_id, ids),
            )
            if ids else {}
        )
        return JSONResponse(content={"counts": counts})


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
            recommendation_cursor = m.community_decode_recommendation_cursor(cursor)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid community cursor.") from exc
        if ranking_norm == "recent" and recommendation_cursor is not None \
                and recommendation_cursor[0] != 0:
            raise HTTPException(status_code=422, detail="Invalid recent community cursor.")
        geo = m._client_location(country, region, lat, lon)
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
                lambda: m.community_recommended_scans(
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
                lambda: m.community_nearby_scans(
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
            next_cursor = m.community_encode_recommendation_cursor(
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
            lambda: m.community_region_summary(
                country_norm or "",
                None,
                exclude_account_actor_id=principal.actor_id if principal else None,
                viewer_account_actor_id=principal.actor_id if principal else None,
            ),
        )
        # Annotate each cell with how many second opinions it has so the carousel
        # can show a badge, then swap the publication capability for its stable,
        # per-scan public token. The author pseudonym rotates independently;
        # feedback uses a separate receipt.
        scan_ids = [str(r.get("scan_id")) for r in rows if r.get("scan_id")]
        counts: dict = {}
        views: dict = {}
        if ranking_norm == "recent":
            counts = await loop.run_in_executor(
                None,
                lambda: m.community_opinion_counts(
                    scan_ids, viewer_account_actor_id=principal.actor_id if principal else None,
                ),
            )
            views = await loop.run_in_executor(
                None, lambda: m.community_view_counts(scan_ids),
            )
        for r in rows:
            sid = str(r.get("scan_id"))
            r.setdefault("opinion_count", counts.get(sid, 0))
            r.setdefault("view_count", views.get(sid, 0))
            r["scan_id"] = r.pop("public_id", None) or r["scan_id"]
            r.pop("_cursor_score", None)
            r.pop("_cursor_row_id", None)
            # Localize the per-row country so cards show "Poland"/"Польша", not "PL".
            m._localize_item_country(r)
        meta = m._country_display(country_norm)
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
        real scan_id), so the app can pin a marker and open the existing opinion
        sheet. Public (no opt-in to view); ``not_a_plant`` excluded; ``truncated``
        signals an over-full bbox."""
        principal = await m._optional_account(request)
        box = m._valid_bbox(min_lat, min_lon, max_lat, max_lon)
        if box is None:
            raise HTTPException(status_code=422, detail="Invalid bbox")
        lo_lat, lo_lon, hi_lat, hi_lon = box
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(
            None,
            lambda: m.community_map_scans(
                lo_lat, hi_lat, lo_lon, hi_lon,
                disease=(disease or None),
                days=days,
                limit=max(1, min(int(limit), 500)),
                exclude_account_actor_id=principal.actor_id if principal else None,
                viewer_account_actor_id=principal.actor_id if principal else None,
            ),
        )
        rows = data["items"]
        # Same opinion-count annotation + public-token swap + country localization as
        # the nearby feed; here we KEEP lat/lon for the markers.
        map_scan_ids = [str(r.get("scan_id")) for r in rows if r.get("scan_id")]
        counts = await loop.run_in_executor(
            None,
            lambda: m.community_opinion_counts(
                map_scan_ids, viewer_account_actor_id=principal.actor_id if principal else None,
            ),
        )
        for r in rows:
            r["opinion_count"] = counts.get(str(r.get("scan_id")), 0)
            r["scan_id"] = r.pop("public_id", None) or r["scan_id"]
            m._localize_item_country(r)
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
        box = m._valid_bbox(min_lat, min_lon, max_lat, max_lon)
        if box is None:
            raise HTTPException(status_code=422, detail="Invalid bbox")
        lo_lat, lo_lon, hi_lat, hi_lon = box
        data = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: m.community_density_grid(
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
    ):
        """Community scans of one species (by scientific name), across all
        regions — the result screen's 'other <species> in the community' strip."""
        principal = await m._optional_account(request)
        sci = (species or "").strip()
        if not sci:
            raise HTTPException(status_code=422, detail="Invalid species")
        loop = asyncio.get_running_loop()
        rows = await loop.run_in_executor(
            None,
            lambda: m.community_scans_by_species(
                sci,
                limit=max(1, min(int(limit), 50)),
                exclude_account_actor_id=principal.actor_id if principal else None,
                viewer_account_actor_id=principal.actor_id if principal else None,
            ),
        )
        species_scan_ids = [str(r.get("scan_id")) for r in rows if r.get("scan_id")]
        counts = await loop.run_in_executor(
            None,
            lambda: m.community_opinion_counts(
                species_scan_ids, viewer_account_actor_id=principal.actor_id if principal else None,
            ),
        )
        for r in rows:
            r["opinion_count"] = counts.get(str(r.get("scan_id")), 0)
            # Public token only — never the community publication capability.
            r["scan_id"] = r.pop("public_id", None) or r["scan_id"]
            # Localize the per-row country so cards show "Poland"/"Польша", not "PL".
            m._localize_item_country(r)
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
            None, lambda: m.community_thumbnail_is_visible(filename)
        )
        if not visible:
            raise HTTPException(status_code=404, detail="Thumbnail not found")
        path = m.COMMUNITY_THUMB_DIR / filename
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Thumbnail not found")
        # Streams off the event loop; supports 304 revalidation. Thumbnails
        # are always re-encoded to JPEG by community._save_thumbnail.
        return m._cached_image_response(
            request, path,
            media_type="image/jpeg",
            cache_control="public, max-age=300, must-revalidate",
        )
