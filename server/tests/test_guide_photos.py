"""Guide example-photo hosting.

The disease/pest example photos moved out of the Android APK into server
static hosting (server/static/guide/<key>.png) to shrink the app; the client
downloads + disk-caches them. These cover the manifest + serve routes and the
path-traversal guard.
"""

from __future__ import annotations


def test_guide_manifest_lists_keys(client):
    r = client.get("/api/guide/manifest")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == len(body["keys"])
    # The bundled set ships these (among others); assert a representative few
    # rather than the exact list so adding a photo doesn't break the test.
    assert {"powdery_mildew", "leaf_spot", "viral_disease"} <= set(body["keys"])
    assert body["count"] >= 10


def test_guide_photo_served_for_known_key(client):
    keys = client.get("/api/guide/manifest").json()["keys"]
    r = client.get(f"/api/guide/photo/{keys[0]}")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    assert r.headers.get("cache-control", "").startswith("public")
    assert len(r.content) > 0


def test_guide_photo_missing_key_404(client):
    r = client.get("/api/guide/photo/definitely_not_a_real_key")
    assert r.status_code == 404


def test_guide_photo_rejects_path_traversal(client):
    # Encoded slash / backslash / .. must never escape the guide dir. FastAPI
    # path params don't match a raw '/', so probe the explicit guards.
    for bad in ("..", "..%2f..%2fmain", r"foo\bar"):
        r = client.get(f"/api/guide/photo/{bad}")
        assert r.status_code in (400, 404), (bad, r.status_code)
