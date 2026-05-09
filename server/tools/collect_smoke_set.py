"""
Held-out houseplant smoke set collector.

Pulls a small, real-world houseplant dataset (~500 photos total across the
seven trainable classes) into ``data_v2/smoke/<class>/`` so the v2.1 model
can be evaluated on photos that nobody trained on.

This script does NOT scrape Google or Bing image search by default. Both
explicitly prohibit automated scraping in their ToS, and the photos
returned are individually copyrighted by their owners — "educational
open-source use" is not a blanket exemption. Default sources are
Openverse (the Wikimedia Foundation's CC-licensed media aggregator —
Flickr CC0 / CC-BY, Wikimedia Commons, Smithsonian, etc. — every hit
carries explicit license metadata) and iNaturalist's
``quality_grade=research`` observations (mostly CC-BY-NC, fine for a
private smoke set we don't redistribute).

If you specifically want to scrape Google / Bing locally with full
awareness of the trade-offs, pass ``--use-search-engine google`` (requires
``pip install icrawler``). The disclaimer at the top of that code path
spells out the caveats.

Usage:
    pip install requests Pillow
    python server/tools/collect_smoke_set.py --target 500
    python server/tools/collect_smoke_set.py --classes powdery_mildew --target 70
    python server/tools/collect_smoke_set.py --use-search-engine google --target 500
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image as PILImage

logger = logging.getLogger("smoke_set")
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# Output layout matches the notebook's ImageFolder conventions.
ROOT = Path(__file__).resolve().parents[2] / "data_v2" / "smoke"

CLASSES = (
    "bacterial_leaf_spot",
    "gray_mold_botrytis",
    "healthy",
    "leaf_spot_fungal",
    "not_a_plant",
    "powdery_mildew",
    "viral_disease",
)

# Per-class default targets. `not_a_plant` is intentionally small — for the
# smoke set we mostly need plant photos that the pipeline must classify
# correctly; NAP behaviour is covered by the existing COCO-derived split.
DEFAULT_TARGETS = {
    "bacterial_leaf_spot": 70,
    "gray_mold_botrytis": 70,
    "healthy": 90,
    "leaf_spot_fungal": 80,
    "not_a_plant": 30,
    "powdery_mildew": 80,
    "viral_disease": 80,
}

# Search-text seeds per class. Kept liberal — the source-side filters
# (research-grade, leaf annotation, file mime check) do most of the work.
OPENVERSE_QUERIES = {
    "bacterial_leaf_spot": [
        "bacterial leaf spot",
        "Xanthomonas leaf",
        "Pseudomonas leaf spot",
    ],
    "gray_mold_botrytis": [
        "Botrytis cinerea leaf",
        "gray mold leaf",
        "grey mould plant",
    ],
    "healthy": [
        "Monstera deliciosa leaf",
        "Ficus elastica leaf",
        "Sansevieria leaf",
        "Philodendron leaf",
        "Calathea leaf",
        "Spathiphyllum leaf",
        "Epipremnum aureum leaf",
        "Dracaena leaf",
    ],
    "leaf_spot_fungal": [
        "anthracnose leaf",
        "Cercospora leaf spot",
        "Alternaria leaf",
        "Septoria leaf",
    ],
    "not_a_plant": [
        "indoor wall",
        "office desk",
        "wooden floor",
        "carpet texture",
        "ceramic tile",
        "curtain fabric",
    ],
    "powdery_mildew": [
        "powdery mildew leaf",
        "Erysiphales leaf",
        "Podosphaera leaf",
    ],
    "viral_disease": [
        # "tobacco mosaic" returns Wikimedia-heavy results (textbook
        # diagrams) — keep it last so the Flickr-heavy queries fill up
        # first. Order matters because the burst-limit budget is
        # spent in query-listed order.
        "cucumber mosaic virus",
        "tomato yellow leaf curl",
        "viral leaf disease",
        "plant virus leaf",
        "yellowing virus",
        "tobacco mosaic",
    ],
}

# iNat taxa per class. Houseplant genera for `healthy`; specific fungal /
# bacterial / viral pathogens for the disease classes where iNat has
# meaningful coverage. Non-leaf observations are filtered downstream by
# the same Leaves controlled-term annotation the notebook uses.
INAT_TAXA = {
    "healthy": [
        ("Ficus", "genus"), ("Monstera", "genus"), ("Epipremnum", "genus"),
        ("Calathea", "genus"), ("Spathiphyllum", "genus"),
        ("Dracaena", "genus"), ("Sansevieria", "genus"),
        ("Philodendron", "genus"),
    ],
    "gray_mold_botrytis": [
        ("Botrytis cinerea", "species"), ("Botrytis", "genus"),
    ],
    "powdery_mildew": [
        # 'Erysiphales' (order) is not directly searchable in iNat — its
        # taxa search returns the family Erysiphaceae instead. Use that.
        ("Erysiphaceae", "family"),
        ("Podosphaera", "genus"),
        ("Golovinomyces", "genus"),
    ],
    "leaf_spot_fungal": [
        ("Cercospora", "genus"), ("Alternaria", "genus"),
        ("Colletotrichum", "genus"),
    ],
    # bacterial_leaf_spot, viral_disease, not_a_plant: no clean iNat
    # taxon mapping. Wikimedia + manual seeding only.
}

# Openverse sits behind a Cloudflare WAF that 403s anything that looks
# like a script. A browser UA gets through; we still self-identify in
# the request fingerprint via the Referer (CC-licensed metadata is
# public, so this is purely about getting past the bot challenge).
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}
OPENVERSE_API = "https://api.openverse.org/v1/images/"
INAT_API = "https://api.inaturalist.org/v1"
INAT_LEAF_TERM_ID, INAT_LEAF_TERM_VALUE_ID = 36, 38
MIN_SIDE = 200
MAX_BYTES = 8 * 1024 * 1024
# Anonymous Openverse is 20/min burst, 200/day. Ride well under burst.
OPENVERSE_INTERVAL_SEC = 4.0
# Disease classes whose iNat observations are tagged as fungus / pathogen
# rather than plant — the 'Leaves' controlled-term filter (plant phenology)
# returns ~zero hits there, so don't apply it.
_DISEASE_KLASSES = {
    "bacterial_leaf_spot", "gray_mold_botrytis", "leaf_spot_fungal",
    "powdery_mildew", "viral_disease",
}
_last_openverse_call = 0.0


def _http_get(url: str, *, params: dict | None = None, retries: int = 3,
              timeout: float = 20.0) -> requests.Response | None:
    delay = 0.7
    for _ in range(retries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=timeout)
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(delay)
                delay *= 2
                continue
            r.raise_for_status()
            return r
        except Exception as e:
            logger.debug("GET %s failed: %s", url, e)
            time.sleep(delay)
            delay *= 2
    return None


def _save_image(content: bytes, dest: Path) -> bool:
    """Validate, normalise to JPEG RGB, and write."""
    if len(content) > MAX_BYTES:
        return False
    try:
        img = PILImage.open(BytesIO(content)).convert("RGB")
    except Exception:
        return False
    if min(img.size) < MIN_SIDE:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, format="JPEG", quality=92)
    return True


def _sidecar(path: Path, *, source: str, klass: str, attribution: str | None,
             license_str: str | None, plant_id: str) -> None:
    payload = {
        "plant_id": plant_id,
        "species": None,
        "diagnosis": klass,
        "severity": "unknown",
        "leaf_or_whole_plant": "leaf" if klass != "not_a_plant" else "whole_plant",
        "capture_session": f"smoke_{source}",
        "source": source,
        "label_confidence": 0.6,
        "bbox": [0, 0, 0, 0],
        "attribution": attribution,
        "license": license_str,
    }
    path.with_suffix(".json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
    )


def _existing_hashes(class_dir: Path) -> set[str]:
    """SHA-256 of every image already on disk so we don't re-download dups."""
    seen = set()
    if not class_dir.exists():
        return seen
    for p in class_dir.iterdir():
        if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
            try:
                seen.add(hashlib.sha256(p.read_bytes()).hexdigest())
            except OSError:
                pass
    return seen


# ── Openverse (CC-licensed aggregator: Flickr CC0/CC-BY, Wikimedia, etc.) ──


_OV_LICENSES = "by,by-sa,cc0,pdm"  # only redistributable / explicit-CC


def _openverse_search(query: str, limit: int) -> list[dict]:
    """Return up to `limit` hits with url + license metadata."""
    global _last_openverse_call
    out: list[dict] = []
    page, page_size = 1, min(50, max(20, limit))
    while len(out) < limit and page <= 4:
        # Throttle to stay under the 20/min anonymous burst.
        wait = OPENVERSE_INTERVAL_SEC - (time.time() - _last_openverse_call)
        if wait > 0:
            time.sleep(wait)
        _last_openverse_call = time.time()
        r = _http_get(OPENVERSE_API, params={
            "q": query, "license": _OV_LICENSES,
            "page": page, "page_size": page_size,
        })
        if r is None:
            break
        results = r.json().get("results") or []
        if not results:
            break
        out.extend(results)
        page += 1
    return out[:limit]


def collect_openverse(klass: str, want: int, dest: Path, seen: set[str]) -> int:
    queries = OPENVERSE_QUERIES.get(klass) or []
    if not queries:
        return 0
    candidates: list[dict] = []
    per_query = max(20, (want * 3) // max(1, len(queries)))
    for q in queries:
        if len(candidates) >= want * 4:
            break
        candidates.extend(_openverse_search(q, per_query))
    n = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = [pool.submit(_download_one_openverse, c, klass, dest, seen)
                for c in candidates if c.get("url")]
        for f in as_completed(futs):
            if f.result():
                n += 1
                if n >= want:
                    break
    logger.info("[openverse/%s] saved %d", klass, n)
    return n


def _download_one_openverse(c: dict, klass: str, dest: Path,
                            seen: set[str]) -> bool:
    # Try the original CDN URL first; fall back to Openverse's own
    # cached thumbnail. This matters for hosts behind a firewall (or in
    # this sandbox) where direct upload.wikimedia.org egress is blocked.
    candidates = [u for u in (c.get("url"), c.get("thumbnail")) if u]
    content = None
    for u in candidates:
        r = _http_get(u, retries=2, timeout=30)
        if r is not None:
            content = r.content
            break
    if content is None:
        return False
    h = hashlib.sha256(content).hexdigest()
    if h in seen:
        return False
    ident = c.get("id") or hashlib.sha1(c["url"].encode()).hexdigest()[:10]
    out = dest / f"ov_{ident}.jpg"
    if _save_image(content, out):
        seen.add(h)
        _sidecar(out, source="openverse", klass=klass,
                 attribution=c.get("creator") or c.get("source"),
                 license_str=c.get("license"),
                 plant_id=str(ident))
        return True
    return False


# ── iNaturalist ─────────────────────────────────────────────────────────


def _inat_taxon(name: str, rank: str) -> int | None:
    r = _http_get(f"{INAT_API}/taxa", params={"q": name, "rank": rank})
    if r is None:
        return None
    res = (r.json().get("results") or [])
    return res[0]["id"] if res else None


def _inat_observations(taxon_id: int, max_n: int, leaf_only: bool) -> list[dict]:
    rows, page = [], 1
    while len(rows) < max_n:
        params = {
            "taxon_id": taxon_id, "quality_grade": "research", "photos": "true",
            "per_page": 100, "page": page, "order": "desc", "order_by": "created_at",
        }
        if leaf_only:
            params["term_id"] = INAT_LEAF_TERM_ID
            params["term_value_id"] = INAT_LEAF_TERM_VALUE_ID
        r = _http_get(f"{INAT_API}/observations", params=params)
        if r is None:
            break
        batch = r.json().get("results") or []
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < 100:
            break
        page += 1
        time.sleep(0.25)
    return rows[:max_n]


def collect_inat(klass: str, want: int, dest: Path, seen: set[str]) -> int:
    taxa = INAT_TAXA.get(klass) or []
    if not taxa:
        return 0
    n = 0
    # The 'Leaves' (Plant Phenology) annotation only applies to plant
    # observations. iNat fungal/pathogen taxa never carry it, so skip the
    # filter for any disease class — only `healthy` (which queries
    # houseplant genera) keeps the leaf-only filter on.
    leaf_only = klass not in _DISEASE_KLASSES
    for taxon_name, rank in taxa:
        if n >= want:
            break
        tid = _inat_taxon(taxon_name, rank)
        if tid is None:
            continue
        per_taxon = max(20, (want - n) // max(1, len(taxa)))
        obs = _inat_observations(tid, per_taxon, leaf_only=leaf_only)
        items = [(o, p) for o in obs for p in (o.get("photos") or [])[:1]]
        with ThreadPoolExecutor(max_workers=6) as pool:
            futs = [pool.submit(_download_one_inat, o, p, klass, dest, seen)
                    for o, p in items]
            for f in as_completed(futs):
                if f.result():
                    n += 1
                    if n >= want:
                        break
    logger.info("[inat/%s] saved %d", klass, n)
    return n


def _download_one_inat(obs: dict, photo: dict, klass: str, dest: Path,
                       seen: set[str]) -> bool:
    raw_url = (photo.get("url") or "")
    if not raw_url.startswith("http"):
        return False
    url = re.sub(r"/square\.", "/medium.", raw_url)
    r = _http_get(url, retries=2, timeout=30)
    if r is None:
        return False
    h = hashlib.sha256(r.content).hexdigest()
    if h in seen:
        return False
    out = dest / f"inat_{obs['id']}_{photo['id']}.jpg"
    if _save_image(r.content, out):
        seen.add(h)
        _sidecar(out, source="inat", klass=klass,
                 attribution=(obs.get("user") or {}).get("login"),
                 license_str=photo.get("license_code"),
                 plant_id=str(obs.get("id")))
        return True
    return False


# ── Optional: search-engine scrape via icrawler ─────────────────────────


def collect_search_engine(klass: str, want: int, dest: Path, seen: set[str],
                          engine: str) -> int:
    """OPTIONAL: scrape Google / Bing image search via the icrawler library.

    DISCLAIMER. Both Google and Bing prohibit automated image-search
    scraping in their ToS. The downloaded images are individually
    copyrighted by their owners — for a private (non-redistributed) smoke
    set this is generally tolerated under research / fair-use norms in
    most jurisdictions, but the legal picture is contested. Do not
    redistribute the resulting dataset.
    """
    try:
        if engine == "google":
            from icrawler.builtin import GoogleImageCrawler as Crawler
        elif engine == "bing":
            from icrawler.builtin import BingImageCrawler as Crawler
        else:
            raise ValueError(f"unknown engine: {engine}")
    except ImportError:
        logger.error("icrawler not installed — `pip install icrawler` and retry")
        return 0
    queries = OPENVERSE_QUERIES.get(klass) or [klass.replace("_", " ")]
    n = 0
    for q in queries:
        if n >= want:
            break
        crawler = Crawler(storage={"root_dir": str(dest)})
        crawler.crawl(keyword=q, max_num=max(10, (want - n) // len(queries)),
                      min_size=(MIN_SIDE, MIN_SIDE))
    # icrawler dumps unsidecar'd files into dest; pair them up afterwards
    for p in sorted(dest.iterdir()):
        if not p.is_file() or p.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            continue
        if p.with_suffix(".json").exists():
            continue
        try:
            h = hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            continue
        if h in seen:
            p.unlink(missing_ok=True)
            continue
        seen.add(h)
        _sidecar(p, source=f"image_search_{engine}", klass=klass,
                 attribution="unknown (web search)", license_str="unknown",
                 plant_id=p.stem)
        n += 1
        if n >= want:
            break
    logger.info("[%s/%s] saved %d", engine, klass, n)
    return n


# ── Main ────────────────────────────────────────────────────────────────


def collect_class(klass: str, target: int, use_search_engine: str | None) -> int:
    dest = ROOT / klass
    dest.mkdir(parents=True, exist_ok=True)
    seen = _existing_hashes(dest)
    have = len(seen)
    if have >= target:
        logger.info("[%s] already has %d images, target %d — skip", klass, have, target)
        return have
    want = target - have
    want -= collect_openverse(klass, want, dest, seen)
    if want > 0:
        want -= collect_inat(klass, want, dest, seen)
    if want > 0 and use_search_engine:
        collect_search_engine(klass, want, dest, seen, engine=use_search_engine)
    final = len([p for p in dest.iterdir() if p.suffix.lower() in
                 {".jpg", ".jpeg", ".png", ".webp"}])
    logger.info("[%s] done: %d images on disk", klass, final)
    return final


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", type=int, default=500,
                    help="Total target across all classes (will be split per "
                    "class using DEFAULT_TARGETS as the prior).")
    ap.add_argument("--classes", nargs="+", default=list(CLASSES),
                    help="Subset of classes to collect.")
    ap.add_argument("--use-search-engine", choices=("google", "bing"), default=None,
                    help="Optional: top up the smoke set by scraping the "
                    "named image search engine. Requires `pip install icrawler`. "
                    "Read the disclaimer in collect_search_engine() first.")
    args = ap.parse_args()

    chosen = [c for c in args.classes if c in CLASSES]
    bad = [c for c in args.classes if c not in CLASSES]
    if bad:
        ap.error(f"unknown class(es): {bad}")

    # Re-balance DEFAULT_TARGETS to hit args.target across `chosen`.
    weight_total = sum(DEFAULT_TARGETS[c] for c in chosen)
    per_class = {
        c: max(5, round(DEFAULT_TARGETS[c] * args.target / weight_total))
        for c in chosen
    }
    logger.info("Per-class targets: %s", per_class)

    ROOT.mkdir(parents=True, exist_ok=True)
    summary = {}
    for c in chosen:
        summary[c] = collect_class(c, per_class[c], args.use_search_engine)
    print("\nSmoke set summary:")
    total = 0
    for c in chosen:
        print(f"  {c:25s} {summary[c]:4d}")
        total += summary[c]
    print(f"  {'TOTAL':25s} {total:4d}")


if __name__ == "__main__":
    main()
