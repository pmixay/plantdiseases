"""Empty answers for the read routes app 1.9 still calls.

The second-opinion system, scan likes, author profiles and scan report/block
are retired, but app 1.9 (on RuStore since 2026-11-22) still calls some of
those routes. A missing route reaches 1.9 as an error, which is harmless on
most screens but not in its background poll: ``OpinionCheckWorker`` answers
any failure of ``GET /community/my-scan-opinions`` with ``Result.retry()``,
so every signed-in 1.9 install would keep re-polling a 404 on backoff.

Only reads and the no-op cancel answer here, each with the empty shape 1.9's
own models decode, because "nothing there" is the truth for all of them. The
retired writes (opinion, request-opinion, like, report, block) and author
profiles stay 404: a 200 would tell the user their action happened. 1.9 treats
every non-2xx the same way, so nothing is gained by another status.

These routes carry no data and touch no store, so they need no account
check. They are left out of the OpenAPI schema: no current client may call
them. Delete this module once 1.9 no longer has meaningful use.
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

_NO_STORE = {"Cache-Control": "no-store"}


def register(router: APIRouter, m) -> None:
    @router.get("/community/my-scan-opinions", include_in_schema=False)
    async def legacy_my_scan_opinions():
        """1.9's OpinionCheckWorker poll: no opinions, so it never notifies."""
        return JSONResponse(
            content={"scans": [], "total_opinions": 0, "total_views": 0},
            headers=_NO_STORE,
        )

    @router.get("/community/opinions/{scan_id}", include_in_schema=False)
    async def legacy_scan_opinions(scan_id: str):
        """1.9's opinion sheet and replies screen: no replies, no open request."""
        sid = scan_id.strip()
        if not sid or len(sid) > 128:
            raise HTTPException(status_code=422, detail="Invalid scan id")
        return JSONResponse(
            content={
                "scan_id": sid,
                "opinions": [],
                "consensus": [],
                "my_opinion": None,
                "views": 0,
                "wants_opinion": False,
                "opinion_requested_at": None,
                "like_count": 0,
                "liked_by_me": False,
            },
            headers=_NO_STORE,
        )

    @router.post("/community/request-opinion/cancel", include_in_schema=False)
    async def legacy_cancel_opinion_request():
        """Nothing is open to withdraw; 1.9 clears its waiting state."""
        return JSONResponse(content={"ok": True, "cleared": False}, headers=_NO_STORE)

    @router.post("/community/scan-counts", include_in_schema=False)
    async def legacy_scan_counts():
        """Gallery badges of a 1.9 build from ``main``: no counts to show."""
        return JSONResponse(content={"counts": {}}, headers=_NO_STORE)
