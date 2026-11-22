"""Service-to-service callbacks from the account service.

Moved out of ``main.py``. ``register(router, m)`` adds the routes to the
``/api`` router and receives the ``main`` module; every handler reaches shared
state, helpers and collaborators through ``m.`` at call time — never by
importing them here — so a test that patches a name on ``main`` (or reloads
it) is seen by the routes exactly as before.
"""

import asyncio
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse

# A collaborator module (never a name patched on ``main``) is imported here;
# flat (``uvicorn main:app`` from ``server/``) or as ``server.*``.
try:
    from . import account_internal
except ImportError:
    import account_internal


def register(router: APIRouter, m) -> None:
    @router.post(
        "/internal/account-deletions",
        status_code=202,
        include_in_schema=False,
    )
    async def internal_account_deletion(
        payload: m.InternalActorDeletionRequest,
        request: Request,
        background_tasks: BackgroundTasks,
    ):
        """Immediately tombstone an actor; physical removal is retryable."""
        if not account_internal.internal_service_authorized(
            request.headers.get("x-internal-service-token", "")
        ):
            raise HTTPException(status_code=401, detail="auth_required")
        try:
            actor_id = str(uuid.UUID(payload.actor_id))
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid_request") from None
        accepted = await asyncio.get_running_loop().run_in_executor(
            None, lambda: m.community.request_actor_deletion(actor_id)
        )
        if not accepted:
            raise HTTPException(status_code=422, detail="invalid_request")
        background_tasks.add_task(m.community.process_cleanup_jobs)
        return JSONResponse(status_code=202, content={"status": "accepted"})


    @router.post(
        "/internal/account-nicknames",
        status_code=202,
        include_in_schema=False,
    )
    async def internal_account_nickname(
        payload: m.InternalActorNicknameRequest,
        request: Request,
    ):
        """Idempotently replace an actor's denormalized historical nickname."""
        if not account_internal.internal_service_authorized(
            request.headers.get("x-internal-service-token", "")
        ):
            raise HTTPException(status_code=401, detail="auth_required")
        try:
            actor_id = str(uuid.UUID(payload.actor_id))
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid_request") from None
        updated = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: m.community.update_actor_nickname(actor_id, payload.nickname),
        )
        return JSONResponse(
            status_code=202,
            content={"status": "accepted", "updated": updated},
        )
