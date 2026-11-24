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

import time
import urllib.parse

import pytest

import species_info


@pytest.fixture(autouse=True)
def _clear_cache(monkeypatch):
    species_info._cache.clear()
    monkeypatch.setattr(species_info, "_RETRY_DELAY_S", 0)
    yield
    species_info._cache.clear()


def test_cache_evicts_oldest_not_flush_all(monkeypatch):
    """At the cap the cache drops the single oldest entry (LRU), it does NOT
    flush everything (the old flush-all thrashed under many species in flight)."""
    monkeypatch.setattr(species_info, "_CACHE_MAX", 3)
    monkeypatch.setattr(
        species_info, "_fetch",
        lambda name, lang: {"title": name, "lang": lang, "image_url": "https://x/y.jpg"},
    )
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
    import species_info

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

    monkeypatch.setattr(species_info, "fetch_species_info", fake)
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


_THUMB = "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Ficus.jpg/320px-Ficus.jpg"


def test_every_width_we_ask_for_is_one_wikimedia_will_render():
    """The whole point of the ladder: since phab:T414805 a direct request for
    an off-ladder width is refused (HTTP 400), so it costs the card its photo
    entirely rather than merely serving a different size. Both constants and
    everything _standard_width can emit must stay on it."""
    ladder = species_info._STANDARD_THUMB_WIDTHS
    assert species_info._DISPLAY_WIDTH in ladder
    assert species_info._STRIP_WIDTH in ladder
    for requested in (1, 240, 320, 800, 1024, 1500, 99999):
        assert species_info._standard_width(requested) in ladder
        for ceiling in (119, 400, 1000, 5000):
            got = species_info._standard_width(requested, ceiling)
            assert got is None or (got in ladder and got <= ceiling)


def test_display_image_asks_for_a_card_sized_render():
    """Neither extreme the summary offers is right: the ~330px thumbnail is
    below what main.py re-encodes at, and the multi-megabyte original often
    doesn't arrive inside plant_photos' 6s fetch timeout at all. 1280 is the
    first renderable step clearing _PLANT_PHOTO_MAX_SIDE."""
    out = species_info._display_image(
        {"source": _THUMB, "width": 320},
        {"source": "https://upload.wikimedia.org/wikipedia/commons/a/ab/Ficus.jpg",
         "width": 4000},
    )
    assert out.endswith("/1280px-Ficus.jpg")


def test_display_image_never_asks_for_more_than_the_file_has():
    """Wikimedia refuses to upscale a raster original — asking anyway would
    404 the photo and drop the card back to its placeholder. A 600px file
    steps DOWN to 500, the largest render at or under what it holds; 600
    itself is not renderable."""
    out = species_info._display_image(
        {"source": _THUMB, "width": 320},
        {"source": "https://upload.wikimedia.org/wikipedia/commons/a/ab/Ficus.jpg",
         "width": 600},
    )
    assert out.endswith("/500px-Ficus.jpg")


def test_display_image_keeps_a_file_smaller_than_every_render():
    """Below the smallest ladder step there is no render to ask for, so serve
    the file whole rather than emitting a width that would be refused."""
    original = "https://upload.wikimedia.org/wikipedia/commons/a/ab/Tiny.jpg"
    out = species_info._display_image(
        {"source": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/"
                   "Tiny.jpg/12px-Tiny.jpg", "width": 12},
        {"source": original, "width": 15},
    )
    assert out == original


def test_display_image_renders_svg_originals_at_full_card_width():
    """An SVG has no pixel ceiling (and its nominal width is often tiny). This
    is the case that gains the most: plant_photos drops a raw .svg as
    unrenderable, so before this the card was stuck on its placeholder."""
    out = species_info._display_image(
        {"source": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/"
                   "Ficus.svg/240px-Ficus.svg.png", "width": 240},
        {"source": "https://upload.wikimedia.org/wikipedia/commons/a/ab/Ficus.svg",
         "width": 300},
    )
    assert out.endswith("/1280px-Ficus.svg.png")


def test_display_image_leaves_unrecognised_urls_alone():
    """Not every summary hands us a /NNNpx- thumbnail URL. When there is
    nothing to rewrite we keep what Wikipedia gave us rather than guessing."""
    original = "https://ru.example/lead.jpg"
    assert species_info._display_image({"source": original}, None) == original
    assert species_info._display_image(None, {"source": original}) == original
    assert species_info._display_image(None, None) is None


def test_media_images_upsize_only_when_the_file_can_supply_it(monkeypatch):
    """Strip photos get the same treatment, but the media-list gives no
    originals — so we only ask for a bigger render when the item's own width
    says the file has the pixels."""
    def fake_get(url, params=None, headers=None, timeout=None):
        return _FakeResp(payload={"items": [
            {
                "type": "image", "title": "Ficus elastica leaf", "width": 3000,
                "srcset": [{"src": "//upload.example/thumb/a/ab/A.jpg/320px-A.jpg"}],
            },
            {
                "type": "image", "title": "Ficus elastica flower", "width": 400,
                "srcset": [{"src": "//upload.example/thumb/a/ab/B.jpg/320px-B.jpg"}],
            },
        ]})

    import requests

    monkeypatch.setattr(requests, "get", fake_get)

    images = species_info._media_images("Ficus_elastica", "en", limit=4)
    assert images == [
        # Upsized to the ladder step, not the raw _STRIP_WIDTH…
        "https://upload.example/thumb/a/ab/A.jpg/960px-A.jpg",
        # …and left alone when the file can't supply it. This one keeps
        # whatever width the media-list chose, which is why plant_photos
        # snaps again on the way out.
        "https://upload.example/thumb/a/ab/B.jpg/320px-B.jpg",
    ]


# ---------------------------------------------------------------------------
# Fallback chain: Wikidata -> Commons -> iNaturalist -> genus; transient cache
# ---------------------------------------------------------------------------

ROSA = "Rosa abietina"
_WD_SEARCH = {"search": [{"id": "Q1", "match": {"type": "label", "text": ROSA}}]}
_WD_ENTITIES = {"entities": {"Q1": {
    "id": "Q1",
    "claims": {
        "P225": [{"mainsnak": {"datavalue": {"value": ROSA}}}],
        "P18": [{"mainsnak": {"datavalue": {"value": "Rosa abietina 02.JPG"}}}],
        "P373": [{"mainsnak": {"datavalue": {"value": "Rosa abietina"}}}],
    },
    "sitelinks": {
        "enwiki": {"title": "Rosa abietina (synonym page)"},
        "ruwiki": {"title": "Роза пихтолистная"},
    },
}}}
_COMMONS_FILE = {"query": {"pages": [{
    "title": "File:Rosa abietina 02.JPG",
    "imageinfo": [{
        "thumburl": "https://thumb.wikimedia.org/wikipedia/commons/thumb/3/35/R.JPG/1280px-R.JPG",
        "extmetadata": {
            "Artist": {"value": '<a href="//x">Jane Doe</a>'},
            "LicenseShortName": {"value": "CC BY-SA 4.0"},
        },
    }],
}]}}
_INAT = {"results": [{
    "id": 487634, "name": ROSA,
    "wikipedia_url": "https://en.wikipedia.org/wiki/Rosa abietina",
    "wikipedia_summary": "<i><b>Rosa abietina</b></i> is a species of rose.",
    "default_photo": {
        "license_code": None, "attribution": "(c) X, all rights reserved",
        "large_url": "https://static.inaturalist.org/photos/1/large.jpeg",
    },
    "taxon_photos": [{"photo": {
        "license_code": "cc-by", "attribution": "(c) Y, some rights reserved (CC BY)",
        "large_url": "https://static.inaturalist.org/photos/2/large.jpeg",
    }}],
}]}


class _Net:
    """Fake ``requests.get`` routed by substring; records every URL asked for."""

    def __init__(self, monkeypatch, routes):
        self.routes = routes  # substring -> payload | status int | callable
        self.calls: list[str] = []
        import requests

        monkeypatch.setattr(requests, "get", self.get)

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(url)
        assert headers and "PlantDiseases" in headers["User-Agent"]
        for needle, handler in self.routes.items():
            if needle in url:
                if callable(handler):
                    handler = handler(url, params)
                if isinstance(handler, int):
                    return _FakeResp(status_code=handler)
                return _FakeResp(payload=handler)
        return _FakeResp(status_code=404)


def _wikidata_handler(url, params):
    return _WD_SEARCH if params["action"] == "wbsearchentities" else _WD_ENTITIES


def _now() -> float:
    return time.time()


def test_wikidata_sitelink_finds_article_filed_under_another_title(monkeypatch):
    """No article under the scientific name on ru/en, but Wikidata lists the ru
    article under its own title — the card gets it, not 'no entry'."""
    ru_summary = {**_RU_SUMMARY, "title": "Роза пихтолистная"}

    def ru_summary_for(url, params):
        return ru_summary if "%D0%A0" in url else 404

    net = _Net(monkeypatch, {
        "ru.wikipedia.org/api/rest_v1/page/summary": ru_summary_for,
        "wikidata.org": _wikidata_handler,
        "/page/media-list/": {"items": []},
        "/w/api.php": {"query": {"pages": [{}]}},  # no langlinks
    })
    info = species_info.fetch_species_info(ROSA, "ru")
    assert info and info["title"] == "Роза пихтолистная"
    assert info["lang"] == "ru" and info["level"] == "species"
    assert any("wikidata.org" in u for u in net.calls)


def test_missing_lead_image_filled_from_wikidata_p18_via_commons(monkeypatch):
    en_no_image = {"type": "standard", "title": ROSA, "extract": "A stub about a rose."}
    _Net(monkeypatch, {
        "en.wikipedia.org/api/rest_v1/page/summary": en_no_image,
        "/page/media-list/": {"items": []},
        "wikidata.org": _wikidata_handler,
        "commons.wikimedia.org": _COMMONS_FILE,
    })
    info = species_info.fetch_species_info(ROSA, "en")
    assert info["extract"] == "A stub about a rose."
    assert info["image_url"].endswith("1280px-R.JPG")
    assert info["images"][0] == info["image_url"]
    assert info["image_credit"] == "Jane Doe"
    assert info["image_license"] == "CC BY-SA 4.0"


def test_commons_category_used_when_wikidata_has_no_p18(monkeypatch):
    entities = {"entities": {"Q1": {
        **_WD_ENTITIES["entities"]["Q1"],
        "claims": {
            "P225": [{"mainsnak": {"datavalue": {"value": ROSA}}}],
            "P373": [{"mainsnak": {"datavalue": {"value": "Rosa abietina"}}}],
        },
    }}}
    category = {"query": {"pages": [
        {"title": "File:Rosa abietina herbarium sheet.jpg", "index": 1,
         "imageinfo": [{"thumburl": "https://thumb.wikimedia.org/a/1280px-H.jpg"}]},
        {"title": "File:Rosa abietina fruit.jpg", "index": 2,
         "imageinfo": [{"thumburl": "https://thumb.wikimedia.org/b/1280px-F.jpg"}]},
        {"title": "File:Rosa abietina map.svg", "index": 3,
         "imageinfo": [{"thumburl": "https://thumb.wikimedia.org/c/1280px-M.svg.png"}]},
    ]}}

    def wikidata(url, params):
        return _WD_SEARCH if params["action"] == "wbsearchentities" else entities

    _Net(monkeypatch, {
        "en.wikipedia.org/api/rest_v1/page/summary": {"extract": "Stub."},
        "/page/media-list/": {"items": []},
        "wikidata.org": wikidata,
        "commons.wikimedia.org": category,
    })
    info = species_info.fetch_species_info(ROSA, "en")
    assert info["image_url"] == "https://thumb.wikimedia.org/b/1280px-F.jpg"  # not the herbarium sheet


def test_inaturalist_used_when_wikipedia_and_wikidata_have_nothing(monkeypatch):
    _Net(monkeypatch, {
        "wikipedia.org": 404,
        "wikidata.org": {"search": []},
        "api.inaturalist.org": _INAT,
    })
    info = species_info.fetch_species_info(ROSA, "en")
    assert info["source"] == "inaturalist" and info["level"] == "species"
    assert info["extract"] == "Rosa abietina is a species of rose."  # HTML stripped
    # Skips the all-rights-reserved default photo, takes the CC BY one.
    assert info["image_url"].endswith("/photos/2/large.jpeg")
    assert info["image_license"] == "CC BY"
    assert "Y" in info["image_credit"]
    assert info["page_url"] == "https://en.wikipedia.org/wiki/Rosa_abietina"


def test_inaturalist_photo_ignored_without_open_licence(monkeypatch):
    inat = {"results": [{**_INAT["results"][0], "taxon_photos": []}]}
    _Net(monkeypatch, {
        "wikipedia.org": 404, "wikidata.org": {"search": []}, "api.inaturalist.org": inat,
    })
    info = species_info.fetch_species_info(ROSA, "en")
    assert info["image_url"] is None  # text only; never an all-rights-reserved photo


def test_inaturalist_requires_exact_name_match(monkeypatch):
    other = {"results": [{**_INAT["results"][0], "name": "Rosa abietina var. other"}]}
    _Net(monkeypatch, {
        "wikipedia.org": 404, "wikidata.org": {"search": []}, "api.inaturalist.org": other,
    })
    assert species_info.fetch_species_info(ROSA, "en") is None


_GENUS_EN = {
    "type": "standard", "title": "Rosa",
    "extract": "Rosa is a genus of about 150 species of flowering plants in the family Rosaceae.",
    "thumbnail": {"source": "https://en.example/rosa.jpg"},
}


def test_genus_is_last_resort_and_labelled(monkeypatch):
    def summary(url, params):
        return _GENUS_EN if url.endswith("/Rosa") else 404

    _Net(monkeypatch, {
        "en.wikipedia.org/api/rest_v1/page/summary": summary,
        "/page/media-list/": {"items": []},
        "wikidata.org": {"search": []},
        "api.inaturalist.org": {"results": []},
    })
    info = species_info.fetch_species_info(ROSA, "en")
    assert info["title"] == "Rosa" and info["level"] == "genus"


def test_genus_fallback_rejects_unrelated_article(monkeypatch):
    """'Xyzzy' is a video-game article, not a plant genus: no genus card."""
    game = {
        "type": "standard", "title": "Colossal Cave Adventure",
        "extract": "Colossal Cave Adventure is a text-based game.",
        "thumbnail": {"source": "https://en.example/g.png"},
    }

    def summary(url, params):
        return game if url.endswith("/Xyzzy") else 404

    _Net(monkeypatch, {
        "en.wikipedia.org/api/rest_v1/page/summary": summary,
        "/page/media-list/": {"items": []},
        "wikidata.org": {"search": []},
        "api.inaturalist.org": {"results": []},
    })
    assert species_info.fetch_species_info("Xyzzy plantus", "en") is None


def test_transient_failure_is_not_cached_like_a_real_miss(monkeypatch):
    """A 429 from Wikipedia must not be remembered for a week as 'no article'."""
    _Net(monkeypatch, {"wikipedia.org": 429, "wikidata.org": 503, "api.inaturalist.org": 500})
    assert species_info.fetch_species_info(ROSA, "en") is None
    expiry = species_info._cache[(ROSA.lower(), "en")][0]
    assert expiry - _now() <= species_info._TRANSIENT_TTL_S + 1

    # The next request retries and succeeds once the upstream recovers.
    species_info._cache[(ROSA.lower(), "en")] = (0, None)  # simulate TTL expiry
    _Net(monkeypatch, {
        "en.wikipedia.org/api/rest_v1/page/summary": _EN_SUMMARY,
        "/page/media-list/": {"items": []},
    })
    info = species_info.fetch_species_info(ROSA, "en")
    assert info and info["extract"].startswith("Heptapleurum")


def test_definite_miss_cached_for_hours_not_a_week(monkeypatch):
    _Net(monkeypatch, {
        "wikipedia.org": 404, "wikidata.org": {"search": []},
        "api.inaturalist.org": {"results": []},
    })
    assert species_info.fetch_species_info(ROSA, "en") is None
    ttl = species_info._cache[(ROSA.lower(), "en")][0] - _now()
    assert species_info._TRANSIENT_TTL_S < ttl <= species_info._MISS_TTL_S + 1


def test_degraded_answer_during_outage_is_retried_soon(monkeypatch):
    """Species page 429s but the genus page answers: serve the genus, yet retry
    the species lookup within minutes, not a week."""
    def summary(url, params):
        return _GENUS_EN if url.endswith("/Rosa") else 429

    _Net(monkeypatch, {
        "en.wikipedia.org/api/rest_v1/page/summary": summary,
        "/page/media-list/": {"items": []},
        "wikidata.org": 429, "api.inaturalist.org": 429,
    })
    info = species_info.fetch_species_info(ROSA, "en")
    assert info["level"] == "genus"
    assert species_info._cache[(ROSA.lower(), "en")][0] - _now() <= species_info._TRANSIENT_TTL_S + 1


def test_wikidata_ignores_non_taxon_items(monkeypatch):
    search = {"search": [
        {"id": "Q9", "match": {"type": "label", "text": ROSA}},  # no P225: not a taxon
        {"id": "Q8", "match": {"type": "alias", "text": ROSA}},  # accepted-name taxon (synonym hit)
    ]}
    entities = {"entities": {
        "Q9": {"id": "Q9", "claims": {}, "sitelinks": {"enwiki": {"title": "Surname"}}},
        "Q8": {
            "id": "Q8",
            "claims": {"P225": [{"mainsnak": {"datavalue": {"value": "Rosa pinus"}}}]},
            "sitelinks": {"enwiki": {"title": "Rosa pinus"}},
        },
    }}

    def wikidata(url, params):
        return search if params["action"] == "wbsearchentities" else entities

    _Net(monkeypatch, {"wikidata.org": wikidata})
    found = species_info._wikidata_lookup(ROSA)
    assert found and found["qid"] == "Q8"
    assert found["sitelinks"] == {"en": "Rosa pinus"}


def test_user_agent_is_descriptive_and_takes_deployment_contact(monkeypatch):
    monkeypatch.setenv("SPECIES_INFO_CONTACT", "ops@example.org")
    assert "ops@example.org" in species_info._build_user_agent()
    monkeypatch.delenv("SPECIES_INFO_CONTACT")
    assert species_info._build_user_agent().startswith("PlantDiseases/")


def test_species_info_endpoint_exposes_level_and_credit_and_short_cache(client, monkeypatch):
    import species_info

    def fake(name, lang="en"):
        return {
            "title": "Rosa", "extract": "genus text", "level": "genus",
            "image_url": "https://upload.wikimedia.org/a.jpg", "images": [],
            "lang": "en", "image_credit": "Jane", "image_license": "CC BY",
        }

    monkeypatch.setattr(species_info, "fetch_species_info", fake)
    r = client.get("/api/species-info?name=Rosa%20abietina")
    body = r.json()
    assert body["level"] == "genus" and body["image_credit"] == "Jane"
    assert body["image_license"] == "CC BY"
    assert r.headers["cache-control"] == "public, max-age=900"
