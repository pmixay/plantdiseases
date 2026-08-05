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


def test_cache_evicts_oldest_not_flush_all(monkeypatch):
    """At the cap the cache drops the single oldest entry (LRU), it does NOT
    flush everything (the old flush-all thrashed under many species in flight)."""
    monkeypatch.setattr(species_info, "_CACHE_MAX", 3)
    monkeypatch.setattr(species_info, "_fetch", lambda name, lang: {"title": name, "lang": lang})
    for nm in ("Aaa", "Bbb", "Ccc"):
        species_info.fetch_species_info(nm, "en")
    assert len(species_info._cache) == 3
    # touch the oldest so it's no longer the LRU victim
    species_info.fetch_species_info("Aaa", "en")
    species_info.fetch_species_info("Ddd", "en")           # cap exceeded -> evict 1
    assert len(species_info._cache) == 3                   # not flushed to 0/1
    assert ("bbb", "en") not in species_info._cache        # oldest-untouched evicted
    assert ("aaa", "en") in species_info._cache            # recently touched survives
    assert ("ddd", "en") in species_info._cache


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


_EN_SUMMARY = {
    "type": "standard",
    "title": SCI_NAME,
    "extract": "Heptapleurum arboricola is a flowering plant in the family Araliaceae.",
    "thumbnail": {"source": "https://en.example/thumb.jpg"},
    "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Heptapleurum_arboricola"}},
}
_RU_TRANSLATION = "Гептаплеурум древовидный — цветковое растение семейства аралиевых."


def test_ru_translates_when_only_english_exists(monkeypatch):
    """No ru article (direct, langlinks, or genus) — fall back to the English
    summary and machine-translate it into Russian."""

    def fake_get(url, params=None, headers=None, timeout=None):
        if "translate.googleapis.com" in url:
            assert params["sl"] == "en" and params["tl"] == "ru"
            # gtx shape: [[[translated, original, ...]], ...]
            return _FakeResp(payload=[[[_RU_TRANSLATION, params["q"], None, None]]])
        if "/w/api.php" in url:
            # No ru interwiki link available.
            return _FakeResp(payload={"query": {"pages": [{}]}})
        if "/page/summary/" in url:
            if "ru.wikipedia.org" in url:
                return _FakeResp(status_code=404)
            if "en.wikipedia.org" in url:
                return _FakeResp(payload=_EN_SUMMARY)
        if "/page/media-list/" in url:
            return _FakeResp(payload={"items": []})
        raise AssertionError(f"unexpected URL: {url}")

    import requests

    monkeypatch.setattr(requests, "get", fake_get)

    info = species_info.fetch_species_info(SCI_NAME, "ru")
    assert info is not None
    assert info["translated"] is True
    assert info["lang"] == "ru"
    assert "Гептаплеурум" in info["extract"]


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


def test_species_info_endpoint_pins_images_to_english(client, monkeypatch):
    """The species card's photos are pinned to the English article for every
    locale — text (title, extract) stays localized, but the same species shows
    the same images regardless of UI language."""
    import main as server_main

    def fake(name, lang="en"):
        if lang == "en":
            return {
                "title": "Ficus elastica", "extract": "EN text",
                "image_url": "https://upload.wikimedia.org/en_lead.jpg",
                "images": ["https://upload.wikimedia.org/en_lead.jpg",
                           "https://upload.wikimedia.org/en_2.jpg"],
                "lang": "en",
            }
        return {
            "title": "Фикус каучуконосный", "extract": "RU text",
            "image_url": "https://upload.wikimedia.org/ru_lead.jpg",
            "images": ["https://upload.wikimedia.org/ru_lead.jpg"],
            "lang": "ru",
        }

    monkeypatch.setattr(server_main, "fetch_species_info", fake)
    body = client.get("/api/species-info?name=Ficus%20elastica&locale=ru").json()
    assert body["available"] is True
    # Localized text is preserved…
    assert body["title"] == "Фикус каучуконосный"
    assert body["extract"] == "RU text"
    # …but every image comes from the English article, not the RU one.
    assert "en_lead" in body["image_url"]
    assert all("ru_" not in img for img in body["images"])
    assert any("en_2" in img for img in body["images"])


def test_media_images_excludes_non_plant_media(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        assert "/page/media-list/" in url
        return _FakeResp(payload={"items": [
            {
                "type": "image",
                "title": "Distribution map",
                "srcset": [{"src": "//upload.example/Ficus_elastica_distribution_map.png"}],
            },
            {
                "type": "image",
                "title": "Herbarium specimen sheet",
                "srcset": [{"src": "//upload.example/Ficus_elastica_herbarium.jpg"}],
            },
            {
                "type": "image",
                "title": "Ficus elastica leaf",
                "caption": "Ficus elastica foliage",
                "srcset": [{"src": "//upload.example/Ficus_elastica_leaf.jpg"}],
            },
            {
                "type": "image",
                "title": "Ficus elastica logo",
                "srcset": [{"src": "//upload.example/Ficus_elastica_logo.svg"}],
            },
        ]})

    import requests

    monkeypatch.setattr(requests, "get", fake_get)

    images = species_info._media_images("Ficus_elastica", "en", limit=4)
    assert images == ["https://upload.example/Ficus_elastica_leaf.jpg"]
