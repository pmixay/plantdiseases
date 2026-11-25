"""The desktop moderation panel at ``/admin/``.

Three static files (``admin_panel/``) and one config answer. The panel is a
client of the existing APIs only: it signs in with Yandex ID in the browser,
exchanges that token at the account service (``/id/v1/auth/yandex/exchange``,
same origin in production) and then calls the ``/api/community/admin/...``
routes with the account access token like the app does. Every permission
check stays in those routes; this module serves files and grants nothing.

``ADMIN_PANEL_YANDEX_CLIENT_ID`` is the Yandex OAuth app's client id, the
same app the Android client uses (the account service only accepts tokens
issued to it). That app needs a Web platform with the redirect URL
``https://<host>/admin/``. Unset, the panel loads and says it is not set up.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

try:
    from . import config
except ImportError:
    import config

PANEL_DIR = Path(__file__).resolve().parent / "admin_panel"

YANDEX_CLIENT_ID = config.get_str("ADMIN_PANEL_YANDEX_CLIENT_ID", "").strip()
ACCOUNT_BASE_PATH = config.get_str("ADMIN_PANEL_ACCOUNT_BASE_PATH", "/id").strip().rstrip("/")

# Scripts, styles, images and requests only from this origin. No inline
# script, no framing, no referrer: the Yandex token arrives in the URL
# fragment, and the panel wipes it from the address bar at once.
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; form-action 'none'; "
    "base-uri 'none'; frame-ancestors 'none'"
)
HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

_FILES = {
    "admin.js": "text/javascript; charset=utf-8",
    "admin.css": "text/css; charset=utf-8",
}


def register(app: FastAPI) -> None:
    @app.get("/admin", include_in_schema=False)
    async def admin_panel_redirect():
        return RedirectResponse("/admin/", status_code=308, headers=HEADERS)

    @app.get("/admin/", include_in_schema=False)
    async def admin_panel_page():
        return FileResponse(
            PANEL_DIR / "index.html", media_type="text/html; charset=utf-8", headers=HEADERS
        )

    @app.get("/admin/config.json", include_in_schema=False)
    async def admin_panel_config():
        return JSONResponse(
            content={
                "available": bool(YANDEX_CLIENT_ID),
                "yandex_client_id": YANDEX_CLIENT_ID or None,
                "account_base_path": ACCOUNT_BASE_PATH,
            },
            headers=HEADERS,
        )

    @app.get("/admin/{name}", include_in_schema=False)
    async def admin_panel_asset(name: str):
        media_type = _FILES.get(name)
        if media_type is None:
            return JSONResponse(status_code=404, content={"detail": "Not Found"}, headers=HEADERS)
        return FileResponse(PANEL_DIR / name, media_type=media_type, headers=HEADERS)
