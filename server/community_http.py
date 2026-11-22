"""HTTP helpers shared by the community, forum and content routes."""

from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
from starlette.responses import Response as StarletteResponse


def raise_community_safety_error(error: ValueError) -> None:
    code = str(error)
    if code == "report_limit":
        raise HTTPException(status_code=429, detail=code) from None
    if code in {"self_target", "target_not_blockable", "block_limit"}:
        raise HTTPException(status_code=409, detail=code) from None
    if code == "deleted account actor":
        raise HTTPException(status_code=403, detail="account_deleted") from None
    if code in {
        "invalid_actor",
        "invalid_reason",
        "target_not_found",
    }:
        raise HTTPException(status_code=422, detail=code) from None
    raise HTTPException(status_code=500, detail="community_safety_failed") from None


def cached_image_response(
    request: Request,
    path: Path,
    *,
    media_type: str,
    cache_control: str,
) -> StarletteResponse:
    """FileResponse with working HTTP revalidation.

    Starlette's ``FileResponse`` attaches an ETag but never *checks*
    ``If-None-Match``, so a revalidating client re-downloads the full
    bytes on every poll. This wrapper answers a matching conditional
    request with an empty 304 — for the guide photos and community
    thumbnails that is the difference between ~100 KB and ~100 bytes
    per revalidation on a mobile uplink.
    """
    st = path.stat()
    etag = f'W/"{int(st.st_mtime)}-{st.st_size}"'
    headers = {"Cache-Control": cache_control, "ETag": etag}
    inm = request.headers.get("if-none-match", "")
    if etag in {t.strip() for t in inm.split(",") if t.strip()}:
        return StarletteResponse(status_code=304, headers=headers)
    return FileResponse(path, media_type=media_type, headers=headers)


