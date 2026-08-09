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
    assert photos.is_allowed_url("https://ru.wikipedia.org/a.png")
    assert not photos.is_allowed_url("https://evil.com/a.jpg")
    assert not photos.is_allowed_url("http://upload.wikimedia.org/a.jpg")
    assert not photos.is_allowed_url("ftp://upload.wikimedia.org/a.jpg")
    assert not photos.is_allowed_url("https://commons.wikimedia.org/a.jpg")
    assert not photos.is_allowed_url("https://de.wikipedia.org/a.jpg")
    assert not photos.is_allowed_url("https://upload.wikimedia.org:444/a.jpg")
    assert not photos.is_allowed_url("https://user@upload.wikimedia.org/a.jpg")
    assert not photos.is_allowed_url("https://upload.wikimedia.org/a.jpg#duplicate-key")
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


def test_prune_enforces_byte_budget_and_removes_derived_variant(
    photos, monkeypatch,
):
    monkeypatch.setattr(photos, "_CACHE_MAX_FILES", 10)
    monkeypatch.setattr(photos, "_CACHE_MAX_BYTES", 10)
    photos.PHOTO_CACHE_DIR.mkdir(parents=True)
    oldest = photos.PHOTO_CACHE_DIR / "a.jpg"
    newest = photos.PHOTO_CACHE_DIR / "b.jpg"
    oldest.write_bytes(b"a" * 8)
    newest.write_bytes(b"b" * 8)
    variant_dir = photos.PHOTO_CACHE_DIR / ".webp"
    variant_dir.mkdir()
    variant = variant_dir / "a.8.webp"
    variant.write_bytes(b"v")
    import os

    os.utime(oldest, (1, 1))
    os.utime(newest, (2, 2))

    photos._prune()

    assert not oldest.exists()
    assert not variant.exists()
    assert newest.exists()


_COMMONS = "https://upload.wikimedia.org/wikipedia/commons"


def test_normalize_thumb_url_snaps_off_ladder_widths(photos):
    """Wikimedia refuses a direct request for a width it does not render
    (phab:T414805), so an off-ladder width means no photo at all. Round up to
    the next renderable step — never down, which would lose resolution."""
    assert photos.normalize_thumb_url(f"{_COMMONS}/thumb/a/ab/F.jpg/1024px-F.jpg") == (
        f"{_COMMONS}/thumb/a/ab/F.jpg/1280px-F.jpg"
    )
    assert photos.normalize_thumb_url(f"{_COMMONS}/thumb/a/ab/F.jpg/320px-F.jpg") == (
        f"{_COMMONS}/thumb/a/ab/F.jpg/330px-F.jpg"
    )
    # Already renderable, no width at all, and a query suffix: left intact.
    for url in (
        f"{_COMMONS}/thumb/a/ab/F.jpg/330px-F.jpg",
        f"{_COMMONS}/a/ab/F.jpg",
    ):
        assert photos.normalize_thumb_url(url) == url
    assert photos.normalize_thumb_url(
        f"{_COMMONS}/thumb/a/ab/F.jpg/1024px-F.jpg?utm_source=x",
    ) == f"{_COMMONS}/thumb/a/ab/F.jpg/1280px-F.jpg?utm_source=x"


def test_get_or_fetch_normalizes_before_touching_the_cache(photos, monkeypatch):
    """An app installed before the fix keeps sending its 7-day-cached
    `?u=…/1024px-…` links, which we cannot re-issue. They must resolve to the
    same fetch — and the same cache entry — as the snapped URL."""
    asked: list[str] = []

    def _download(url):
        asked.append(url)
        return _PNG

    monkeypatch.setattr(photos, "_download", _download)
    stale = photos.get_or_fetch(f"{_COMMONS}/thumb/a/ab/F.jpg/1024px-F.jpg")
    assert stale is not None
    assert asked == [f"{_COMMONS}/thumb/a/ab/F.jpg/1280px-F.jpg"]
    # Same photo, addressed the new way → cache hit, no second download.
    assert photos.get_or_fetch(f"{_COMMONS}/thumb/a/ab/F.jpg/1280px-F.jpg") == stale
    assert len(asked) == 1


def test_get_or_fetch_falls_back_to_the_original_when_the_render_fails(
    photos, monkeypatch,
):
    """Which widths Wikimedia renders is theirs to change — it just did. When
    the thumbnail won't come, pull the file behind it rather than dropping the
    card to its placeholder."""
    asked: list[str] = []

    def _download(url):
        asked.append(url)
        return None if "px-" in url else _PNG

    monkeypatch.setattr(photos, "_download", _download)
    path = photos.get_or_fetch(f"{_COMMONS}/thumb/a/ab/F.jpg/1280px-F.jpg")
    assert path is not None and path.is_file()
    assert asked == [
        f"{_COMMONS}/thumb/a/ab/F.jpg/1280px-F.jpg",
        f"{_COMMONS}/a/ab/F.jpg",
    ]


def test_get_or_fetch_gives_up_when_there_is_no_original_to_fall_back_to(
    photos, monkeypatch,
):
    """A non-thumbnail URL has nothing behind it — one failed fetch, no retry."""
    asked: list[str] = []

    def _download(url):
        asked.append(url)
        return None

    monkeypatch.setattr(photos, "_download", _download)
    assert photos.get_or_fetch(f"{_COMMONS}/a/ab/F.jpg") is None
    assert asked == [f"{_COMMONS}/a/ab/F.jpg"]


def _stub_requests_get(monkeypatch, *, final_url, body=_PNG):
    """Stub one manual redirect and record every attempted request."""
    import requests

    class _Resp:
        def __init__(self, status_code, *, location=None):
            self.status_code = status_code
            self.headers = {} if location is None else {"Location": location}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def raise_for_status(self):
            pass

        def iter_content(self, _chunk):
            yield body

    calls = []

    def _get(url, **kwargs):
        calls.append((url, kwargs))
        if len(calls) == 1 and url != final_url:
            return _Resp(302, location=final_url)
        return _Resp(200)

    monkeypatch.setattr(requests, "get", _get)
    return calls


def test_download_rejects_redirect_off_the_allowlist(photos, monkeypatch):
    """An off-allowlist redirect is rejected before issuing its request."""
    source = "https://upload.wikimedia.org/x.png"
    calls = _stub_requests_get(monkeypatch, final_url="https://evil.com/x.png")
    assert photos._download(source) is None
    # The redirect target must never be requested, not merely discarded after fetch.
    assert [url for url, _kwargs in calls] == [source]
    assert calls[0][1]["allow_redirects"] is False


def test_download_allows_redirect_within_wikimedia(photos, monkeypatch):
    """Wikimedia's own thumb/FilePath redirects stay inside the allowlist and
    must keep working."""
    source = "https://en.wikipedia.org/x.png"
    destination = "https://upload.wikimedia.org/thumb/x.png/800px-x.png"
    calls = _stub_requests_get(
        monkeypatch, final_url=destination,
    )
    assert photos._download(source) == _PNG
    assert [url for url, _kwargs in calls] == [source, destination]
    assert all(kwargs["allow_redirects"] is False for _url, kwargs in calls)


def test_download_bounds_redirect_chain(photos, monkeypatch):
    import requests

    calls = []

    class _Redirect:
        status_code = 302

        def __init__(self, destination):
            self.headers = {"Location": destination}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _get(url, **kwargs):
        calls.append((url, kwargs))
        return _Redirect(f"/{len(calls)}.png")

    monkeypatch.setattr(requests, "get", _get)
    assert photos._download("https://upload.wikimedia.org/start.png") is None
    # Initial request plus exactly the configured number of followed redirects.
    assert len(calls) == photos._MAX_REDIRECTS + 1
    assert all(url.startswith("https://upload.wikimedia.org/") for url, _ in calls)


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
    prune_calls = []
    monkeypatch.setattr(
        server_main,
        "plant_photo_prune_cache",
        lambda: prune_calls.append(True),
    )
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
    assert prune_calls == [True]


def test_webp_variant_rejects_oversized_source_before_decode(tmp_path, monkeypatch):
    import main as server_main

    src = tmp_path / "oversized.png"
    src.write_bytes(_PNG)
    loaded = False

    class _OversizedImage:
        size = (server_main._WEBP_MAX_SOURCE_PIXELS + 1, 1)

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def load(self):
            nonlocal loaded
            loaded = True
            raise AssertionError("oversized image must be rejected before decode")

    monkeypatch.setattr(server_main.Image, "open", lambda _path: _OversizedImage())

    assert server_main._webp_variant(src, 1000, 80) is None
    assert loaded is False


def test_endpoint_never_falls_back_to_raw_oversized_source(
    client,
    tmp_path,
    monkeypatch,
):
    import main as server_main

    src = tmp_path / "oversized.png"
    src.write_bytes(_PNG)
    loaded = False

    class _OversizedImage:
        size = (server_main._WEBP_MAX_SOURCE_PIXELS + 1, 1)

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def load(self):
            nonlocal loaded
            loaded = True
            raise AssertionError("oversized image must be rejected before decode")

    monkeypatch.setattr(server_main, "plant_photo_get_or_fetch", lambda _url: src)
    monkeypatch.setattr(server_main.Image, "open", lambda _path: _OversizedImage())

    response = client.get(
        "/api/plant-photo",
        params={"u": "https://upload.wikimedia.org/oversized.png"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Photo unavailable"
    assert loaded is False


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
