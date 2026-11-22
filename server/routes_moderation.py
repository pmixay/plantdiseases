"""Community moderation routes: the admin/moderator/expert tools and appeals.

Every staff route checks the caller's roles with the account service first
(``m.community_roles``) and fails closed with 503 when it cannot. Moderators
may be scoped to one space; admins act everywhere. Members see the decisions
that affected them under ``/community/moderation/`` and can appeal each once.
"""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

# A collaborator module (never a name patched on ``main``) is imported here;
# flat (``uvicorn main:app`` from ``server/``) or as ``server.*``.
try:
    from . import community_moderation
except ImportError:
    import community_moderation

_NO_STORE = {"Cache-Control": "no-store"}
_MODERATORS = {"moderator"}
_EXPERTS = {"expert"}


class ModPostAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["remove", "restore", "lock", "unlock", "pin", "unpin", "move"]
    reason_code: str | None = Field(None, max_length=32)
    note: str | None = Field(None, max_length=1_000)
    space: str | None = Field(None, max_length=32)


class ModAnswerAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["remove", "restore"]
    reason_code: str | None = Field(None, max_length=32)
    note: str | None = Field(None, max_length=1_000)


class ModVerify(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verified: bool = True


class ModDismiss(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_type: Literal["post", "answer"]
    target_id: str = Field(..., min_length=1, max_length=32)


class ModSanction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["warn", "mute", "unmute", "ban", "unban"]
    reason_code: str | None = Field(None, max_length=32)
    hours: int | None = Field(None, ge=1, le=24 * 365)
    note: str | None = Field(None, max_length=1_000)


class ModAppealDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["uphold", "overturn"]


class ModRoleChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    author_token: str = Field(..., min_length=32, max_length=32)
    role: Literal["expert", "moderator"]
    scope: str = Field("*", min_length=1, max_length=32)
    grant: bool = True


class ModRewardFreeze(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frozen: bool


class ModImageBan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_id: str = Field(..., min_length=32, max_length=32)


class ModAvatarRemove(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ban_image: bool = True


class MemberAppeal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(..., min_length=32, max_length=32)
    text: str = Field("", max_length=1_500)


def register(router: APIRouter, m) -> None:
    account = m.account_dependency

    async def run(fn, /, *args, **kwargs):
        try:
            return await asyncio.get_running_loop().run_in_executor(
                None, lambda: fn(*args, **kwargs)
            )
        except m.community_forum.ForumError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.code) from None

    async def roles_of(actor_id: str):
        client = m.community_roles
        if client is None:
            raise HTTPException(status_code=503, detail="roles_unavailable")
        try:
            return await asyncio.get_running_loop().run_in_executor(
                m._get_net_executor(), client.roles_for, actor_id
            )
        except m.account_roles.RolesUnavailable:
            raise HTTPException(status_code=503, detail="roles_unavailable") from None

    async def staff(request: Request, allowed: set[str], *, space: str | None = None):
        principal = await m._require_account(request)
        roles = await roles_of(principal.actor_id)
        if not m.account_roles.can_act(roles, allowed, space):
            raise HTTPException(status_code=403, detail="forbidden")
        return principal, roles

    async def admin(request: Request):
        principal = await m._require_account(request)
        roles = await roles_of(principal.actor_id)
        if not any(role.role == "admin" for role in roles):
            raise HTTPException(status_code=403, detail="admin_required")
        return principal

    async def global_moderator(request: Request):
        principal, roles = await staff(request, _MODERATORS)
        if m.account_roles.scopes_for(roles, _MODERATORS) is not None:
            raise HTTPException(status_code=403, detail="global_moderator_required")
        return principal, roles

    # ── staff: who am I, queue, review ───────────────────────────────────

    @router.get("/community/admin/me", dependencies=[Depends(m.community_bearer)])
    async def moderation_me(principal=Depends(account)):
        """The caller's community roles; the app shows staff tools from this."""
        if m.community_roles is None:
            return JSONResponse(
                content={"available": False, "roles": []}, headers=_NO_STORE
            )
        roles = await roles_of(principal.actor_id)
        return JSONResponse(
            content={
                "available": True,
                "roles": [{"role": r.role, "scope": r.scope} for r in roles],
            },
            headers=_NO_STORE,
        )

    @router.get("/community/admin/queue", dependencies=[Depends(m.community_bearer)])
    async def moderation_queue(
        request: Request,
        kind: Literal["reports", "hidden", "appeals", "rewards", "label_review"] = "reports",
        limit: int = Query(50, ge=1, le=100),
    ):
        """Moderation work by kind. ``label_review`` is for experts; ``rewards``
        lists members whose achievement discount is frozen for review."""
        if kind == "label_review":
            await staff(request, _EXPERTS)
            result = await run(m.community_labels.review_queue, limit=limit)
            return JSONResponse(content=result, headers=_NO_STORE)
        if kind == "rewards":
            await global_moderator(request)
            result = await run(m.community_achievements.frozen_queue, limit=limit)
            return JSONResponse(content=result, headers=_NO_STORE)
        _principal, roles = await staff(request, _MODERATORS)
        scopes = m.account_roles.scopes_for(roles, _MODERATORS)
        result = await run(community_moderation.queue, kind=kind, scopes=scopes, limit=limit)
        return JSONResponse(content=result, headers=_NO_STORE)

    @router.get(
        "/community/admin/posts/{post_id}", dependencies=[Depends(m.community_bearer)]
    )
    async def moderation_post_view(post_id: str, request: Request):
        """A post with hidden and removed text, for moderators and experts."""
        space = await run(community_moderation.space_of, "post", post_id)
        await staff(request, _MODERATORS | _EXPERTS, space=space)
        result = await run(community_moderation.moderator_post_view, post_id)
        return JSONResponse(content=result, headers=_NO_STORE)

    # ── staff: content actions ───────────────────────────────────────────

    @router.post(
        "/community/admin/posts/{post_id}/action", dependencies=[Depends(m.community_bearer)]
    )
    async def moderation_post_action(post_id: str, payload: ModPostAction, request: Request):
        space = await run(community_moderation.space_of, "post", post_id)
        principal, roles = await staff(request, _MODERATORS, space=space)
        if payload.action == "move" and not m.account_roles.can_act(
            roles, _MODERATORS, payload.space
        ):
            raise HTTPException(status_code=403, detail="forbidden")
        result = await run(
            community_moderation.moderate_post,
            moderator_actor_id=principal.actor_id,
            post_id=post_id,
            action=payload.action,
            reason_code=payload.reason_code,
            note=payload.note,
            space=payload.space,
        )
        return JSONResponse(content=result, headers=_NO_STORE)

    @router.post(
        "/community/admin/answers/{answer_id}/action",
        dependencies=[Depends(m.community_bearer)],
    )
    async def moderation_answer_action(
        answer_id: str, payload: ModAnswerAction, request: Request
    ):
        space = await run(community_moderation.space_of, "answer", answer_id)
        principal, _roles = await staff(request, _MODERATORS, space=space)
        result = await run(
            community_moderation.moderate_answer,
            moderator_actor_id=principal.actor_id,
            answer_id=answer_id,
            action=payload.action,
            reason_code=payload.reason_code,
            note=payload.note,
        )
        return JSONResponse(content=result, headers=_NO_STORE)

    @router.post(
        "/community/admin/answers/{answer_id}/verify",
        dependencies=[Depends(m.community_bearer)],
    )
    async def moderation_answer_verify(answer_id: str, payload: ModVerify, request: Request):
        """An expert marks a diagnosis as verified."""
        space = await run(community_moderation.space_of, "answer", answer_id)
        principal, _roles = await staff(request, _EXPERTS, space=space)
        result = await run(
            community_moderation.verify_answer,
            expert_actor_id=principal.actor_id,
            answer_id=answer_id,
            verified=payload.verified,
        )
        return JSONResponse(content=result, headers=_NO_STORE)

    @router.post(
        "/community/admin/reports/dismiss", dependencies=[Depends(m.community_bearer)]
    )
    async def moderation_dismiss_reports(payload: ModDismiss, request: Request):
        space = await run(community_moderation.space_of, payload.target_type, payload.target_id)
        principal, _roles = await staff(request, _MODERATORS, space=space)
        result = await run(
            community_moderation.dismiss_reports,
            moderator_actor_id=principal.actor_id,
            target_type=payload.target_type,
            target_id=payload.target_id,
        )
        return JSONResponse(content=result, headers=_NO_STORE)

    # ── staff: members ───────────────────────────────────────────────────

    @router.get(
        "/community/admin/actors/{author_token}", dependencies=[Depends(m.community_bearer)]
    )
    async def moderation_actor(author_token: str, request: Request):
        await staff(request, _MODERATORS)
        actor = await run(community_moderation.actor_for_token, author_token)
        result = await run(community_moderation.actor_summary, actor)
        return JSONResponse(content=result, headers=_NO_STORE)

    @router.post(
        "/community/admin/actors/{author_token}/sanction",
        dependencies=[Depends(m.community_bearer)],
    )
    async def moderation_sanction(author_token: str, payload: ModSanction, request: Request):
        """Warn or mute (global moderators), ban or unban (admins only)."""
        if payload.kind in {"ban", "unban"}:
            principal = await admin(request)
        else:
            principal, _roles = await global_moderator(request)
        actor = await run(community_moderation.actor_for_token, author_token)
        result = await run(
            community_moderation.sanction,
            moderator_actor_id=principal.actor_id,
            actor_id=actor,
            kind=payload.kind,
            reason_code=payload.reason_code,
            hours=payload.hours,
            note=payload.note,
        )
        return JSONResponse(content=result, headers=_NO_STORE)

    @router.post(
        "/community/admin/actors/{author_token}/avatar/remove",
        dependencies=[Depends(m.community_bearer)],
    )
    async def moderation_remove_avatar(
        author_token: str, payload: ModAvatarRemove, request: Request
    ):
        """Take down a member's profile photo (global moderators); by default the
        same picture is refused as a future upload."""
        principal, _roles = await global_moderator(request)
        result = await run(
            community_moderation.remove_avatar,
            moderator_actor_id=principal.actor_id,
            author_token=author_token,
            ban_image=payload.ban_image,
        )
        return JSONResponse(content=result, headers=_NO_STORE)

    @router.post(
        "/community/admin/appeals/{appeal_id}/resolve",
        dependencies=[Depends(m.community_bearer)],
    )
    async def moderation_resolve_appeal(
        appeal_id: str, payload: ModAppealDecision, request: Request
    ):
        principal, _roles = await global_moderator(request)
        result = await run(
            community_moderation.resolve_appeal,
            reviewer_actor_id=principal.actor_id,
            appeal_id=appeal_id,
            decision=payload.decision,
        )
        return JSONResponse(content=result, headers=_NO_STORE)

    # ── admin only ───────────────────────────────────────────────────────

    @router.get("/community/admin/audit", dependencies=[Depends(m.community_bearer)])
    async def moderation_audit(
        request: Request,
        before: int | None = Query(None, ge=1),
        limit: int = Query(100, ge=1, le=200),
    ):
        await admin(request)
        result = await run(community_moderation.audit, limit=limit, before=before)
        return JSONResponse(content=result, headers=_NO_STORE)

    @router.get("/community/admin/roles", dependencies=[Depends(m.community_bearer)])
    async def moderation_role_holders(request: Request):
        await admin(request)
        try:
            holders = await asyncio.get_running_loop().run_in_executor(
                m._get_net_executor(), m.community_roles.holders
            )
        except m.account_roles.RolesUnavailable:
            raise HTTPException(status_code=503, detail="roles_unavailable") from None
        items = [
            {
                "author_token": community_moderation.author_token_for(item["actor_id"]),
                "nickname": item["nickname"],
                "role": item["role"],
                "scope": item["scope"],
                "granted_at": item["granted_at"],
            }
            for item in holders
        ]
        return JSONResponse(content={"items": items}, headers=_NO_STORE)

    @router.post("/community/admin/roles", dependencies=[Depends(m.community_bearer)])
    async def moderation_change_role(payload: ModRoleChange, request: Request):
        """Admins grant or revoke expert and moderator roles. Admin is CLI-only."""
        principal = await admin(request)
        actor = await run(community_moderation.actor_for_token, payload.author_token)
        try:
            result = await asyncio.get_running_loop().run_in_executor(
                m._get_net_executor(),
                lambda: m.community_roles.change(
                    grant=payload.grant,
                    actor_id=actor,
                    role=payload.role,
                    scope=payload.scope,
                    by_actor_id=principal.actor_id,
                ),
            )
        except m.account_roles.RoleChangeRefused as exc:
            raise HTTPException(status_code=exc.status, detail=exc.code) from None
        except m.account_roles.RolesUnavailable:
            raise HTTPException(status_code=503, detail="roles_unavailable") from None
        await run(
            community_moderation.record_role_change,
            admin_actor_id=principal.actor_id,
            actor_id=actor,
            role=payload.role,
            scope=payload.scope,
            granted=payload.grant,
        )
        result.pop("actor_id", None)
        return JSONResponse(content={"ok": True, **result}, headers=_NO_STORE)

    @router.post(
        "/community/admin/actors/{author_token}/rewards",
        dependencies=[Depends(m.community_bearer)],
    )
    async def moderation_reward_freeze(author_token: str, payload: ModRewardFreeze, request: Request):
        """Admins freeze or clear a member's achievement discount."""
        principal = await admin(request)
        actor = await run(community_moderation.actor_for_token, author_token)
        result = await run(
            m.community_achievements.set_frozen,
            admin_actor_id=principal.actor_id,
            actor_id=actor,
            frozen=payload.frozen,
        )
        return JSONResponse(content=result, headers=_NO_STORE)

    @router.post(
        "/community/admin/posts/{post_id}/exclude-training",
        dependencies=[Depends(m.community_bearer)],
    )
    async def moderation_exclude_training(post_id: str, request: Request):
        principal = await admin(request)
        result = await run(
            community_moderation.exclude_from_training,
            admin_actor_id=principal.actor_id,
            post_id=post_id,
        )
        return JSONResponse(content=result, headers=_NO_STORE)

    @router.post("/community/admin/images/ban", dependencies=[Depends(m.community_bearer)])
    async def moderation_ban_image(payload: ModImageBan, request: Request):
        principal = await admin(request)
        result = await run(
            community_moderation.ban_image,
            admin_actor_id=principal.actor_id,
            media_id=payload.media_id,
        )
        return JSONResponse(content=result, headers=_NO_STORE)

    # ── members: notices and appeals ─────────────────────────────────────

    @router.get(
        "/community/moderation/notices", dependencies=[Depends(m.community_bearer)]
    )
    async def moderation_notices(principal=Depends(account)):
        """Moderation decisions about my content or account, and whether I can appeal."""
        result = await run(community_moderation.notices_for, principal.actor_id)
        return JSONResponse(content=result, headers=_NO_STORE)

    @router.post(
        "/community/moderation/appeals", dependencies=[Depends(m.community_bearer)]
    )
    async def moderation_appeal(payload: MemberAppeal, request: Request):
        principal = await m._require_account(request)
        result = await run(
            community_moderation.appeal,
            actor_id=principal.actor_id,
            action_id=payload.action_id,
            text=payload.text,
        )
        return JSONResponse(content=result, status_code=201, headers=_NO_STORE)
