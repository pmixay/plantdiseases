"""Tests for the plant-photo proxy + disk cache (server-side image caching).

Network is never hit — the byte download is monkeypatched. Covers the SSRF
host guard, the proxy-path rewrite, the on-disk cache (fetch once, serve
cached), the non-raster rejection, and the /api/plant-photo endpoint.
"""
from __future__ import annotations

import importlib
import io

import pytest

# Minimal valid PNG header + filler — enough for the magic-byte sniff.
_PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


@pytest.fixture
def photos(tmp_path, monkeypatch):
    monkeypatch.setenv("PLANT_PHOTO_CACHE_DIR", str(tmp_path / "photos"))
    import plant_photos as pp
    importlib.reload(pp)
    return pp


def test_is_allowed_url_guards_host(photos):
    assert photos.is_allowed_url("https://upload.wikimedia.org/a.jpg")
    assert photos.is_allowed_url("https://en.wikipedia.org/a.png")
    assert photos.is_allowed_url("https://wikipedia.org/a.png")  # bare apex
    assert not photos.is_allowed_url("https://evil.com/a.jpg")
    assert not photos.is_allowed_url("ftp://upload.wikimedia.org/a.jpg")
    assert not photos.is_allowed_url("not a url")
    # No suffix-spoofing: a host that merely contains the text on another domain.
    assert not photos.is_allowed_url("https://wikimedia.org.evil.com/a.jpg")


def test_proxy_path(photos):
    p = photos.proxy_path("https://upload.wikimedia.org/a b.jpg")
    assert p is not None and p.startswith("api/plant-photo?u=")
    assert " " not in p  # URL-encoded
    assert photos.proxy_path(None) is None
    assert photos.proxy_path("") is None
    assert photos.proxy_path("https://evil.com/a.jpg") is None  # disallowed → drop


def test_name_proxy_path(photos):
    p = photos.name_proxy_path("Ficus elastica", "ru")
    assert p.startswith("api/plant-photo?name=")
    assert "locale=ru" in p
    assert " " not in p  # space in the name is URL-encoded


def test_get_or_fetch_caches_and_reuses(photos, monkeypatch):
    monkeypatch.setattr(photos, "_download", lambda url: _PNG)
    url = "https://upload.wikimedia.org/x.png"
    path = photos.get_or_fetch(url)
    assert path is not None and path.is_file() and path.suffix == ".png"

    # Second call must serve from disk — no second download.
    def _boom(_url):
        raise AssertionError("should not re-download a cached photo")

    monkeypatch.setattr(photos, "_download", _boom)
    assert photos.get_or_fetch(url) == path


def test_get_or_fetch_rejects_non_raster(photos, monkeypatch):
    monkeypatch.setattr(photos, "_download", lambda url: b"<svg xmlns='...'></svg>")
    assert photos.get_or_fetch("https://upload.wikimedia.org/x.svg") is None


def test_get_or_fetch_rejects_disallowed_host(photos):
    assert photos.get_or_fetch("https://evil.com/x.png") is None


def test_endpoint_serves_cached_photo(client, tmp_path, monkeypatch):
    import main as server_main
    f = tmp_path / "p.png"
    f.write_bytes(_PNG)
    monkeypatch.setattr(server_main, "plant_photo_get_or_fetch", lambda u: f)
    r = client.get(
        "/api/plant-photo", params={"u": "https://upload.wikimedia.org/x.png"},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("image/")


def test_endpoint_downscales_to_webp(client, tmp_path, monkeypatch):
    """A real full-res source is served as a downscaled WebP (the main-screen
    bad-internet win), not the multi-MB original. Backward compatible: any
    Glide client decodes WebP by magic bytes."""
    from PIL import Image

    import main as server_main
    src = tmp_path / "big.png"
    Image.new("RGB", (2400, 1600), (10, 120, 30)).save(src, format="PNG")
    monkeypatch.setattr(server_main, "plant_photo_get_or_fetch", lambda u: src)
    r = client.get(
        "/api/plant-photo", params={"u": "https://upload.wikimedia.org/big.png"},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/webp"
    # RIFF....WEBP magic — a real WebP, and downscaled to the 1000px bound.
    assert r.content[:4] == b"RIFF" and r.content[8:12] == b"WEBP"
    w, h = Image.open(io.BytesIO(r.content)).size
    assert max(w, h) <= server_main._PLANT_PHOTO_MAX_SIDE
    assert len(r.content) < src.stat().st_size  # smaller than the PNG original


def test_webp_variant_not_regenerated_on_mtime_touch(client, tmp_path):
    """The variant is keyed by source SIZE, not mtime, so plant_photos' LRU
    access-touch (which bumps the original's mtime on every cache hit) must NOT
    trigger a re-transcode. That was the regression: rebuilt every request,
    churning the ETag and doing a full PIL decode/resize/encode each time."""
    import os
    import time

    from PIL import Image

    import main as server_main
    src = tmp_path / "p.png"
    Image.new("RGB", (1200, 800), (10, 120, 30)).save(src, format="PNG")

    v1 = server_main._webp_variant(src, 1000, 80)
    assert v1 is not None and v1.is_file()
    first_mtime = v1.stat().st_mtime

    # Simulate an LRU access-touch on the original (mtime jumps forward).
    future = time.time() + 100
    os.utime(src, (future, future))

    v2 = server_main._webp_variant(src, 1000, 80)
    assert v2 == v1  # same variant reused, not a new file
    assert v2.stat().st_mtime == first_mtime  # NOT regenerated → stable ETag


def test_endpoint_rejects_disallowed_host(client):
    r = client.get("/api/plant-photo", params={"u": "https://evil.com/x.png"})
    assert r.status_code == 400


def test_endpoint_missing_source_is_400(client):
    r = client.get("/api/plant-photo")
    assert r.status_code == 400


def test_endpoint_resolves_by_name(client, tmp_path, monkeypatch):
    """The by-name form fully resolves the photo server-side (Wikipedia lookup
    → byte cache), so the popular carousel needs only the species name."""
    import main as server_main
    f = tmp_path / "p.png"
    f.write_bytes(_PNG)
    monkeypatch.setattr(
        server_main, "fetch_species_info",
        lambda n, lang="en": {"image_url": "https://upload.wikimedia.org/x.png"},
    )
    monkeypatch.setattr(server_main, "plant_photo_get_or_fetch", lambda u: f)
    r = client.get(
        "/api/plant-photo", params={"name": "Ficus elastica", "locale": "en"},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("image/")


def test_endpoint_by_name_404_when_unresolvable(client, monkeypatch):
    import main as server_main
    monkeypatch.setattr(server_main, "fetch_species_info", lambda n, lang="en": None)
    r = client.get("/api/plant-photo", params={"name": "Nope nope"})
    assert r.status_code == 404


def test_endpoint_404_when_uncacheable(client, monkeypatch):
    import main as server_main
    monkeypatch.setattr(server_main, "plant_photo_get_or_fetch", lambda u: None)
    r = client.get(
        "/api/plant-photo", params={"u": "https://upload.wikimedia.org/missing.png"},
    )
    assert r.status_code == 404
