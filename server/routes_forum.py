"""Community forum routes: posts, answers, likes, photos, search.

``register(router, m)`` adds the routes to the ``/api`` router and receives
the ``main`` module; handlers reach shared state through ``m.`` at call time
so tests that patch names on ``main`` are seen here. Reading the forum needs
no account; every write is bound to the verified account actor and its
nickname, never to client-supplied identity.
"""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field


class ForumPostCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["question", "scan", "recovery", "tip"]
    title: str = Field("", max_length=400)
    body: str = Field("", max_length=12_000)
    space: str | None = Field(None, max_length=32)
    species: str | None = Field(None, max_length=200)
    condition: str | None = Field(None, max_length=48)
    urgent: bool = False
    media_ids: list[str] = Field(default_factory=list, max_length=4)
    scan_token: str | None = Field(None, max_length=128)
    parent_post_id: str | None = Field(None, max_length=32)
    training_consent: bool = False
    locale: str | None = Field(None, max_length=16)


class ForumPostEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(None, max_length=400)
    body: str | None = Field(None, max_length=12_000)
    space: str | None = Field(None, max_length=32)
    species: str | None = Field(None, max_length=200)
    condition: str | None = Field(None, max_length=48)
    urgent: bool | None = None
    training_consent: bool | None = None


class ForumAnswerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field("", max_length=6_000)
    parent_id: str | None = Field(None, max_length=32)
    steps: list[str] = Field(default_factory=list, max_length=8)
    diagnosis: str | None = Field(None, max_length=48)
    media_ids: list[str] = Field(default_factory=list, max_length=3)
    training_consent: bool = False


class ForumAnswerEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str | None = Field(None, max_length=6_000)
    steps: list[str] | None = Field(None, max_length=8)
    diagnosis: str | None = Field(None, max_length=48)


class ForumLikeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    liked: bool = True


class ForumReactionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reaction: Literal["like", "not_helpful"] | None = None


class ForumAcceptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer_id: str | None = Field(None, max_length=32)


class ForumReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_type: Literal["post", "answer"]
    target_id: str = Field(..., min_length=1, max_length=32)
    reason: str = Field(..., min_length=1, max_length=32)


class ForumBlockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_type: Literal["post", "answer"]
    target_id: str = Field(..., min_length=1, max_length=32)


_NO_STORE = {"Cache-Control": "no-store"}


def register(router: APIRouter, m) -> None:
    async def run(fn, /, *args, **kwargs):
        """Run a store call off the event loop and map rule refusals to HTTP."""
        try:
            return await asyncio.get_running_loop().run_in_executor(
                None, lambda: fn(*args, **kwargs)
            )
        except m.community_forum.ForumError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.code) from None

    def check_label(value: str | None) -> None:
        if value and value.strip().lower() not in m._FEEDBACK_ALLOWED_CORRECTIONS:
            raise HTTPException(status_code=422, detail="unknown_label")

    def spend(operation: str, actor_id: str, limit: int) -> None:
        if not m._consume_daily_write(operation, f"actor:{actor_id}", limit):
            raise HTTPException(status_code=429, detail="daily_quota_reached")

    async def viewer_of(request: Request) -> str | None:
        principal = await m._optional_account(request)
        return principal.actor_id if principal else None

    # ── reading ──────────────────────────────────────────────────────────

    @router.get("/community/spaces")
    async def forum_spaces():
        """Curated spaces (plant families and problem topics) with post counts."""
        items = await run(m.community_forum.list_spaces)
        return JSONResponse(
            content={"items": items},
            headers={"Cache-Control": "public, max-age=300"},
        )

    @router.get("/community/feed")
    async def forum_feed(
        request: Request,
        tab: Literal["all", "needs_help", "solved", "mine"] = "all",
        sort: Literal["hot", "new", "top"] = "hot",
        space: str | None = Query(None, max_length=32),
        cursor: str | None = Query(None, max_length=64),
        limit: int = Query(20, ge=1, le=50),
    ):
        """One page of posts. ``needs_help`` lists unanswered questions, oldest first."""
        viewer = await viewer_of(request)
        result = await run(
            m.community_forum.feed,
            viewer_actor_id=viewer,
            tab=tab,
            sort=sort,
            space=space,
            limit=limit,
            cursor=cursor,
        )
        return JSONResponse(content=result, headers=_NO_STORE)

    @router.get("/community/search")
    async def forum_search(
        request: Request,
        q: str | None = Query(None, max_length=200),
        space: str | None = Query(None, max_length=32),
        solved: bool | None = None,
        with_photos: bool | None = None,
        condition: str | None = Query(None, max_length=48),
        species: str | None = Query(None, max_length=200),
        cursor: str | None = Query(None, max_length=64),
        limit: int = Query(20, ge=1, le=50),
    ):
        """Search titles, bodies and answers (RU and EN). Without ``q``, a scan's
        ``condition`` and ``species`` find posts about the same plant and problem."""
        check_label(condition)
        viewer = await viewer_of(request)
        result = await run(
            m.community_forum.search,
            query=q,
            viewer_actor_id=viewer,
            space=space,
            solved=solved,
            with_photos=with_photos,
            condition=condition,
            species=species,
            limit=limit,
            cursor=cursor,
        )
        return JSONResponse(content=result, headers=_NO_STORE)

    @router.get("/community/posts/{post_id}")
    async def forum_post_detail(post_id: str, request: Request):
        """A post with its threaded answers; the AI verdict is the first answer."""
        viewer = await viewer_of(request)
        result = await run(m.community_forum.get_post, post_id, viewer_actor_id=viewer)
        return JSONResponse(content=result, headers=_NO_STORE)

    @router.get("/community/media/{media_id}")
    async def forum_media_file(media_id: str, request: Request):
        """A forum photo; follows its post's visibility (uploads: owner only)."""
        viewer = await viewer_of(request)
        path = await run(m.community_forum.media_file, media_id, viewer_actor_id=viewer)
        if path is None and viewer is not None and m.community_roles is not None:
            # Staff reviewing hidden or removed content still see its photos.
            try:
                roles = await asyncio.get_running_loop().run_in_executor(
                    m._get_net_executor(), m.community_roles.roles_for, viewer
                )
            except m.account_roles.RolesUnavailable:
                roles = []
            if m.account_roles.can_act(roles, {"moderator", "expert"}):
                path = await run(
                    m.community_forum.media_file, media_id, viewer_actor_id=viewer, moderator=True
                )
        if path is None:
            raise HTTPException(status_code=404, detail="media_not_found")
        return m._cached_image_response(
            request,
            path,
            media_type="image/jpeg",
            cache_control="public, max-age=300, must-revalidate",
        )

    @router.get("/community/activity", dependencies=[Depends(m.community_bearer)])
    async def forum_activity(request: Request, since: int = Query(0, ge=0)):
        """New answers on my posts, replies to my answers and my accepted answers."""
        principal = await m._require_account(request)
        result = await run(m.community_forum.activity_for, principal.actor_id, since=since)
        return JSONResponse(content=result, headers=_NO_STORE)

    @router.get("/community/helpers/{author_token}")
    async def forum_helper_profile(author_token: str):
        """A member's level, achievements and helper stats (public)."""
        result = await run(m.community_achievements.progress_for_token, author_token)
        return JSONResponse(content=result, headers=_NO_STORE)

    @router.get("/community/avatars/{author_token}")
    async def forum_avatar_file(
        author_token: str,
        request: Request,
        v: int | None = Query(None, ge=0, description="avatar_version from the author object"),
    ):
        """A member's profile photo (public, 404 when none). A URL carrying the
        current ``avatar_version`` as ``v`` may be cached for a long time."""
        found = await run(m.community_forum.avatar_file, author_token)
        if found is None:
            raise HTTPException(status_code=404, detail="avatar_not_found")
        path, version = found
        cache_control = (
            "public, max-age=604800, immutable"
            if v == version
            else "public, max-age=300, must-revalidate"
        )
        return m._cached_image_response(
            request, path, media_type="image/jpeg", cache_control=cache_control
        )

    @router.get("/community/me/achievements", dependencies=[Depends(m.community_bearer)])
    async def forum_my_achievements(request: Request):
        """My level and achievements, plus the PRO discount they have earned."""
        principal = await m._require_account(request)
        result = await run(
            m.community_achievements.progress, principal.actor_id, include_private=True
        )
        return JSONResponse(content=result, headers=_NO_STORE)

    # ── writing ──────────────────────────────────────────────────────────

    @router.post("/community/media", dependencies=[Depends(m.community_bearer)])
    async def forum_media_upload(
        request: Request,
        image: UploadFile = File(...),
        scan_token: str | None = Form(None, max_length=128),
    ):
        """Upload one photo for a post or answer. With ``scan_token`` the bytes
        must be the photo the server analysed, so the post can show its verdict."""
        principal = await m._require_account(request)
        spend("forum_media", principal.actor_id, m.FORUM_MEDIA_DAILY_QUOTA)
        if image.content_type and image.content_type not in m._COMMUNITY_ALLOWED_TYPES:
            raise HTTPException(status_code=400, detail="unsupported_image_type")
        body = await m._read_upload(image)
        m._validate_image_bytes(body)
        result = await run(
            m.community_forum.store_media,
            actor_id=principal.actor_id,
            image_bytes=body,
            scan_token=scan_token,
        )
        return JSONResponse(content=result, status_code=201, headers=_NO_STORE)

    @router.put("/community/me/avatar", dependencies=[Depends(m.community_bearer)])
    async def forum_avatar_upload(request: Request, image: UploadFile = File(...)):
        """Publish or replace my profile photo, shown beside my posts and answers.
        It is re-encoded as a square JPEG without metadata."""
        principal = await m._require_account(request)
        spend("forum_avatar", principal.actor_id, m.FORUM_AVATAR_DAILY_QUOTA)
        if image.content_type and image.content_type not in m._COMMUNITY_ALLOWED_TYPES:
            raise HTTPException(status_code=400, detail="unsupported_image_type")
        body = await m._read_upload(image)
        m._validate_image_bytes(body)
        result = await run(
            m.community_forum.set_avatar, actor_id=principal.actor_id, image_bytes=body
        )
        return JSONResponse(content=result, headers=_NO_STORE)

    @router.delete("/community/me/avatar", dependencies=[Depends(m.community_bearer)])
    async def forum_avatar_delete(request: Request):
        """Withdraw my profile photo from Community."""
        principal = await m._require_account(request)
        removed = await run(m.community_forum.delete_avatar, actor_id=principal.actor_id)
        return JSONResponse(content={"ok": True, "removed": removed}, headers=_NO_STORE)

    @router.post("/community/posts", dependencies=[Depends(m.community_bearer)])
    async def forum_post_create(payload: ForumPostCreate, request: Request):
        principal = await m._require_account(request)
        check_label(payload.condition)
        spend("forum_post", principal.actor_id, m.FORUM_POST_DAILY_QUOTA)
        result = await run(
            m.community_forum.create_post,
            actor_id=principal.actor_id,
            nickname=principal.nickname,
            kind=payload.kind,
            title=payload.title,
            body=payload.body,
            space=payload.space,
            species=payload.species,
            condition=payload.condition,
            urgent=payload.urgent,
            media_ids=payload.media_ids,
            scan_token=payload.scan_token,
            parent_post_id=payload.parent_post_id,
            training_consent=payload.training_consent,
            locale=payload.locale,
        )
        return JSONResponse(content=result, status_code=201, headers=_NO_STORE)

    @router.patch("/community/posts/{post_id}", dependencies=[Depends(m.community_bearer)])
    async def forum_post_edit(post_id: str, payload: ForumPostEdit, request: Request):
        principal = await m._require_account(request)
        check_label(payload.condition)
        result = await run(
            m.community_forum.edit_post,
            actor_id=principal.actor_id,
            post_id=post_id,
            **payload.model_dump(exclude_unset=True),
        )
        return JSONResponse(content=result, headers=_NO_STORE)

    @router.delete("/community/posts/{post_id}", dependencies=[Depends(m.community_bearer)])
    async def forum_post_delete(post_id: str, request: Request):
        principal = await m._require_account(request)
        await run(m.community_forum.delete_post, actor_id=principal.actor_id, post_id=post_id)
        return JSONResponse(content={"ok": True}, headers=_NO_STORE)

    @router.post("/community/posts/{post_id}/like", dependencies=[Depends(m.community_bearer)])
    async def forum_post_like(post_id: str, payload: ForumLikeRequest, request: Request):
        principal = await m._require_account(request)
        if payload.liked:
            spend("forum_reaction", principal.actor_id, m.FORUM_REACTION_DAILY_QUOTA)
        result = await run(
            m.community_forum.set_post_like,
            actor_id=principal.actor_id,
            post_id=post_id,
            liked=payload.liked,
        )
        return JSONResponse(content=result, headers=_NO_STORE)

    @router.post(
        "/community/posts/{post_id}/answers", dependencies=[Depends(m.community_bearer)]
    )
    async def forum_answer_create(post_id: str, payload: ForumAnswerCreate, request: Request):
        principal = await m._require_account(request)
        check_label(payload.diagnosis)
        spend("forum_answer", principal.actor_id, m.FORUM_ANSWER_DAILY_QUOTA)
        result = await run(
            m.community_forum.create_answer,
            actor_id=principal.actor_id,
            nickname=principal.nickname,
            post_id=post_id,
            body=payload.body,
            parent_id=payload.parent_id,
            steps=payload.steps,
            diagnosis=payload.diagnosis,
            media_ids=payload.media_ids,
            training_consent=payload.training_consent,
        )
        return JSONResponse(content=result, status_code=201, headers=_NO_STORE)

    @router.post(
        "/community/posts/{post_id}/accept", dependencies=[Depends(m.community_bearer)]
    )
    async def forum_answer_accept(post_id: str, payload: ForumAcceptRequest, request: Request):
        principal = await m._require_account(request)
        result = await run(
            m.community_forum.accept_answer,
            actor_id=principal.actor_id,
            post_id=post_id,
            answer_id=payload.answer_id,
        )
        return JSONResponse(content=result, headers=_NO_STORE)

    @router.patch("/community/answers/{answer_id}", dependencies=[Depends(m.community_bearer)])
    async def forum_answer_edit(answer_id: str, payload: ForumAnswerEdit, request: Request):
        principal = await m._require_account(request)
        check_label(payload.diagnosis)
        result = await run(
            m.community_forum.edit_answer,
            actor_id=principal.actor_id,
            answer_id=answer_id,
            **payload.model_dump(exclude_unset=True),
        )
        return JSONResponse(content=result, headers=_NO_STORE)

    @router.delete("/community/answers/{answer_id}", dependencies=[Depends(m.community_bearer)])
    async def forum_answer_delete(answer_id: str, request: Request):
        principal = await m._require_account(request)
        await run(
            m.community_forum.delete_answer, actor_id=principal.actor_id, answer_id=answer_id
        )
        return JSONResponse(content={"ok": True}, headers=_NO_STORE)

    @router.post(
        "/community/answers/{answer_id}/reaction", dependencies=[Depends(m.community_bearer)]
    )
    async def forum_answer_reaction(
        answer_id: str, payload: ForumReactionRequest, request: Request
    ):
        """Like an answer, mark it not helpful (the only downvote), or clear."""
        principal = await m._require_account(request)
        if payload.reaction is not None:
            spend("forum_reaction", principal.actor_id, m.FORUM_REACTION_DAILY_QUOTA)
        result = await run(
            m.community_forum.set_answer_reaction,
            actor_id=principal.actor_id,
            answer_id=answer_id,
            reaction=payload.reaction,
        )
        return JSONResponse(content=result, headers=_NO_STORE)

    # ── safety ───────────────────────────────────────────────────────────

    @router.post("/community/forum/report", dependencies=[Depends(m.community_bearer)])
    async def forum_report(payload: ForumReportRequest, request: Request):
        """Report a post or answer with a fixed reason; no free text is stored."""
        principal = await m._require_account(request)
        created = await run(
            m.community_forum.report_content,
            reporter_actor_id=principal.actor_id,
            target_type=payload.target_type,
            target_id=payload.target_id,
            reason=payload.reason,
        )
        return JSONResponse(content={"ok": True, "created": created}, headers=_NO_STORE)

    @router.post("/community/forum/block", dependencies=[Depends(m.community_bearer)])
    async def forum_block(payload: ForumBlockRequest, request: Request):
        """Block the author of a post or answer (same block list as the scan feed)."""
        principal = await m._require_account(request)
        await run(m.community_forum.init_db)
        try:
            result = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: m.community_block_content_author(
                    blocker_actor_id=principal.actor_id,
                    target_type=payload.target_type,
                    target_id=payload.target_id.strip(),
                ),
            )
        except ValueError as exc:
            m._raise_community_safety_error(exc)
            raise AssertionError("unreachable") from exc
        return JSONResponse(content={"ok": True, **result}, headers=_NO_STORE)
