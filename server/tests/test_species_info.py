"""Tests for the Wikipedia species enrichment, focused on the Russian
locale path.

The Russian Wikipedia frequently stores a plant article under a localized
common name or an older botanical synonym rather than the scientific name
PlantNet hands us (e.g. the screenshot case ``Heptapleurum arboricola`` →
ru article ``Шеффлера древовидная``). ``fetch_species_info`` must follow the
English wiki's interwiki langlinks to find that article instead of silently
serving an English summary. External HTTP is fully mocked — no network.
"""
from __future__ import annotations

import urllib.parse

import pytest

import species_info


@pytest.fixture(autouse=True)
def _clear_cache():
    species_info._cache.clear()
    yield
    species_info._cache.clear()


class _FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


SCI_NAME = "Heptapleurum arboricola"
RU_TITLE = "Шеффлера древовидная"

_RU_SUMMARY = {
    "type": "standard",
    "title": RU_TITLE,
    "extract": "Шеффлера древовидная — вечнозелёное растение семейства аралиевых.",
    "thumbnail": {"source": "https://ru.example/thumb.jpg"},
    "content_urls": {"desktop": {"page": "https://ru.wikipedia.org/wiki/Шеффлера"}},
}


def _router(monkeypatch, *, ru_direct_404=True):
    """Install a fake ``requests.get`` that emulates the ru-via-langlinks
    path: the direct ru lookup 404s, the en langlinks query resolves the ru
    title, and the ru summary for that title succeeds."""

    def fake_get(url, params=None, headers=None, timeout=None):
        # Interwiki langlinks query on the English wiki.
        if "/w/api.php" in url:
            assert params and params.get("lllang") == "ru"
            return _FakeResp(payload={
                "query": {"pages": [{"langlinks": [{"lang": "ru", "title": RU_TITLE}]}]}
            })
        # Page summaries.
        if "/page/summary/" in url:
            decoded = urllib.parse.unquote(url)
            if "ru.wikipedia.org" in url and "Heptapleurum" in decoded:
                # Direct ru lookup by the scientific name — not present.
                return _FakeResp(status_code=404 if ru_direct_404 else 200)
            if "ru.wikipedia.org" in url:
                return _FakeResp(payload=_RU_SUMMARY)
            # English fallback should never be reached in this scenario.
            raise AssertionError(f"unexpected English fallback fetch: {url}")
        # Media-list — return empty so no example photos are added.
        if "/page/media-list/" in url:
            return _FakeResp(payload={"items": []})
        raise AssertionError(f"unexpected URL: {url}")

    import requests

    monkeypatch.setattr(requests, "get", fake_get)


def test_ru_resolves_localized_title_via_langlinks(monkeypatch):
    _router(monkeypatch)

    info = species_info.fetch_species_info(SCI_NAME, "ru")

    assert info is not None
    assert info["lang"] == "ru"
    assert info["title"] == RU_TITLE
    assert "Шеффлера" in info["extract"]


def test_ru_direct_hit_skips_langlinks(monkeypatch):
    """When the ru wiki has the article under the scientific name directly,
    we use it and never need the langlinks detour."""

    def fake_get(url, params=None, headers=None, timeout=None):
        if "/page/summary/" in url and "ru.wikipedia.org" in url:
            return _FakeResp(payload=_RU_SUMMARY)
        if "/page/media-list/" in url:
            return _FakeResp(payload={"items": []})
        if "/w/api.php" in url:
            raise AssertionError("langlinks should not be queried on a direct hit")
        raise AssertionError(f"unexpected URL: {url}")

    import requests

    monkeypatch.setattr(requests, "get", fake_get)

    info = species_info.fetch_species_info(SCI_NAME, "ru")
    assert info is not None
    assert info["lang"] == "ru"
