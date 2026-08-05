"""Guide example-photo hosting.

The disease/pest example photos moved out of the Android APK into server
static hosting (server/static/guide/<key>.png) to shrink the app; the client
builds photo URLs from keys it already knows (GuideDataProvider) and
disk-caches the downloads. These cover the serve route and the
path-traversal guard.
"""

from __future__ import annotations

import main as server_main


def _bundled_keys() -> list[str]:
    """Keys that ship a photo, straight from the static dir (the old
    /api/guide/manifest endpoint was removed — no client ever called it;
    the app derives keys from its bundled guide content instead)."""
    d = server_main.GUIDE_STATIC_DIR
    return sorted(p.stem for p in d.glob("*.png")) if d.is_dir() else []


def test_bundled_photo_set_is_present():
    keys = _bundled_keys()
    # The bundled set ships these (among others); assert a representative few
    # rather than the exact list so adding a photo doesn't break the test.
    assert {"powdery_mildew", "leaf_spot", "viral_disease"} <= set(keys)
    assert len(keys) >= 10


def test_guide_photo_served_for_known_key(client):
    keys = _bundled_keys()
    r = client.get(f"/api/guide/photo/{keys[0]}")
    assert r.status_code == 200, r.text
    # Photos are served as the downscaled WebP variant (PNG only when
    # transcoding failed). Verify the declared type matches the bytes.
    ctype = r.headers["content-type"]
    assert ctype in ("image/webp", "image/png")
    if ctype == "image/webp":
        assert r.content[:4] == b"RIFF" and r.content[8:12] == b"WEBP"
        original = (server_main.GUIDE_STATIC_DIR / f"{keys[0]}.png").stat().st_size
        assert len(r.content) < original
    else:
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert r.headers.get("cache-control", "").startswith("public")
    assert len(r.content) > 0


def test_guide_photo_supports_304_revalidation(client):
    keys = _bundled_keys()
    r1 = client.get(f"/api/guide/photo/{keys[0]}")
    etag = r1.headers.get("etag")
    assert etag, "guide photo response must carry an ETag"
    r2 = client.get(
        f"/api/guide/photo/{keys[0]}", headers={"If-None-Match": etag},
    )
    assert r2.status_code == 304
    assert r2.content == b""


def test_guide_photo_missing_key_404(client):
    r = client.get("/api/guide/photo/definitely_not_a_real_key")
    assert r.status_code == 404


def test_guide_photo_rejects_path_traversal(client):
    # Encoded slash / backslash / .. must never escape the guide dir. FastAPI
    # path params don't match a raw '/', so probe the explicit guards.
    for bad in ("..", "..%2f..%2fmain", r"foo\bar"):
        r = client.get(f"/api/guide/photo/{bad}")
        assert r.status_code in (400, 404), (bad, r.status_code)
