"""Tests for the plant-photo proxy + disk cache (server-side image caching).

Network is never hit — the byte download is monkeypatched. Covers the SSRF
host guard, the proxy-path rewrite, the on-disk cache (fetch once, serve
cached), the non-raster rejection, and the /api/plant-photo endpoint.
"""
from __future__ import annotations

import importlib

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


def test_endpoint_rejects_disallowed_host(client):
    r = client.get("/api/plant-photo", params={"u": "https://evil.com/x.png"})
    assert r.status_code == 400


def test_endpoint_404_when_uncacheable(client, monkeypatch):
    import main as server_main
    monkeypatch.setattr(server_main, "plant_photo_get_or_fetch", lambda u: None)
    r = client.get(
        "/api/plant-photo", params={"u": "https://upload.wikimedia.org/missing.png"},
    )
    assert r.status_code == 404
