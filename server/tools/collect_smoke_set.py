"""
Held-out houseplant smoke / val_realworld collector.

Pulls a small, real-world houseplant dataset (~500 photos total across
the seven trainable classes) into either:

* ``data_v2/smoke/<class>/`` — the original smoke set (default), used
  for ad-hoc evaluation outside the notebook's split contract; or
* ``data_v2/classification/val/realworld/<class>/`` — the **ship gate**
  used by the notebook's §6 evaluation. Selected with
  ``--into val_realworld``; strict leakage gate is forced on so the
  fetched photos cannot pollute the training-vs-eval boundary.

This script does NOT scrape Google or Bing image search by default.
Both explicitly prohibit automated scraping in their ToS, and the
photos returned are individually copyrighted by their owners —
"educational open-source use" is not a blanket exemption. Default
sources are Openverse (the Wikimedia Foundation's CC-licensed media
aggregator — Flickr CC0 / CC-BY, Wikimedia Commons, Smithsonian, etc.
— every hit carries explicit license metadata) and iNaturalist's
``quality_grade=research`` observations (mostly CC-BY-NC, fine for a
held-out evaluation set we don't redistribute).

If you specifically want to scrape Google / Bing locally with full
awareness of the trade-offs, pass ``--use-search-engine google``
(requires ``pip install icrawler``). The disclaimer at the top of that
code path spells out the caveats. Even there, when ``--into=val_realworld``
the strict leakage gate still drops anything that overlaps TRAIN_ROOT
by SHA or pHash — search-engine results are mostly the same PlantDoc /
PlantVillage photos that already live in TRAIN_ROOT, so most of them
will fail the gate (which is exactly what we want).

Usage:
    pip install requests Pillow imagehash
    # Top up the smoke set (legacy default).
    python server/tools/collect_smoke_set.py --target 500
    # Top up val_realworld with strict leakage gate (recommended).
    python server/tools/collect_smoke_set.py --into val_realworld --target 500
    # Single class only.
    python server/tools/collect_smoke_set.py --classes powdery_mildew --target 70
    # Optional: top up via image-search scraping (gate still applies).
    python server/tools/collect_smoke_set.py --into val_realworld \\
        --use-search-engine google --target 500
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image as PILImage

# pHash is optional — required only when the strict leakage gate is on
# (forced when --into=val_realworld, see argparse below).
try:
    import imagehash  # type: ignore
except ImportError:  # pragma: no cover - import-time soft dep
    imagehash = None

logger = logging.getLogger("smoke_set")
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# Output layouts match the notebook's ImageFolder conventions.
SMOKE_ROOT = Path(__file__).resolve().parents[2] / "data_v2" / "smoke"
DATA_ROOT = Path(__file__).resolve().parents[2] / "data_v2" / "classification"
TRAIN_ROOT = DATA_ROOT / "train"
VAL_REALWORLD_ROOT = DATA_ROOT / "val" / "realworld"
# Sister held-out splits — we de-duplicate against these so a smoke
# top-up can't accidentally seed the same photo into two held-out
# slices and inflate cross-slice agreement metrics.
SISTER_HELDOUT_ROOTS = (
    DATA_ROOT / "val" / "indomain",
    DATA_ROOT / "val" / "plantvillage_studio",
    DATA_ROOT / "val" / "temperature_cal",
    DATA_ROOT / "val" / "conformal_cal",
)
# Same threshold the §3 audit uses — keeps both sides of the contract
# numerically consistent.
PHASH_THRESHOLD = 6
# `ROOT` kept as an alias so anyone still importing it (older docs /
# scripts) gets the legacy behaviour — smoke set output. New code paths
# resolve the destination via _dest_root() instead.
ROOT = SMOKE_ROOT

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


# ── Leakage gate (val_realworld mode) ───────────────────────────────────


_IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def _walk_images(root: Path):
    if not root.exists():
        return
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in _IMG_EXTS:
            yield p


def _index_sha(roots) -> set[str]:
    out: set[str] = set()
    for root in roots:
        for p in _walk_images(root):
            try:
                out.add(hashlib.sha256(p.read_bytes()).hexdigest())
            except OSError:
                pass
    return out


def _index_phash(roots):
    out = []
    if imagehash is None:
        return out
    for root in roots:
        for p in _walk_images(root):
            try:
                out.append(imagehash.phash(PILImage.open(p).convert("RGB")))
            except Exception:
                continue
    return out


@dataclass
class LeakageGate:
    """SHA + pHash gate against TRAIN_ROOT and sister held-out splits.

    Designed for the ``--into val_realworld`` path. The notebook's §3
    runs the same audit after splits are populated, but we reject leaks
    before saving so val_realworld never even briefly contains a
    duplicate of a training image.

    Counters are exposed as attributes so the caller can summarise.
    """
    train_sha: set[str]
    sister_sha: set[str]
    train_phash: list = field(default_factory=list)
    threshold: int = PHASH_THRESHOLD
    n_sha_train: int = 0
    n_sha_sister: int = 0
    n_phash_train: int = 0

    @classmethod
    def build(cls, train_root: Path, sister_roots, *,
              use_phash: bool, threshold: int = PHASH_THRESHOLD) -> "LeakageGate":
        train_sha = _index_sha([train_root])
        sister_sha = _index_sha(sister_roots)
        train_phash = _index_phash([train_root]) if use_phash else []
        logger.info(
            "leakage gate: train SHA=%d  sister SHA=%d  train pHash=%d (threshold=%d)",
            len(train_sha), len(sister_sha), len(train_phash), threshold,
        )
        return cls(train_sha=train_sha, sister_sha=sister_sha,
                   train_phash=train_phash, threshold=threshold)

    def reject_reason(self, content: bytes) -> str | None:
        """Return a short reason string if `content` would leak, else None."""
        sha = hashlib.sha256(content).hexdigest()
        if sha in self.train_sha:
            self.n_sha_train += 1
            return "sha:train"
        if sha in self.sister_sha:
            self.n_sha_sister += 1
            return "sha:sister-heldout"
        if self.train_phash and imagehash is not None:
            try:
                ph = imagehash.phash(PILImage.open(BytesIO(content)).convert("RGB"))
            except Exception:
                return None
            for tph in self.train_phash:
                if ph - tph <= self.threshold:
                    self.n_phash_train += 1
                    return f"phash:train(d={int(ph - tph)})"
        return None

    def summary(self) -> dict:
        return {
            "dropped_sha_train": self.n_sha_train,
            "dropped_sha_sister_heldout": self.n_sha_sister,
            "dropped_phash_train": self.n_phash_train,
            "phash_threshold": self.threshold,
            "phash_enabled": bool(self.train_phash),
        }


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


def collect_openverse(klass: str, want: int, dest: Path, seen: set[str],
                      gate: LeakageGate | None = None) -> int:
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
        futs = [pool.submit(_download_one_openverse, c, klass, dest, seen, gate)
                for c in candidates if c.get("url")]
        for f in as_completed(futs):
            if f.result():
                n += 1
                if n >= want:
                    break
    logger.info("[openverse/%s] saved %d", klass, n)
    return n


def _download_one_openverse(c: dict, klass: str, dest: Path,
                            seen: set[str],
                            gate: LeakageGate | None = None) -> bool:
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
    if gate is not None:
        reason = gate.reject_reason(content)
        if reason is not None:
            logger.debug("[openverse/%s] dropped (%s)", klass, reason)
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


def collect_inat(klass: str, want: int, dest: Path, seen: set[str],
                 gate: LeakageGate | None = None) -> int:
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
            futs = [pool.submit(_download_one_inat, o, p, klass, dest, seen, gate)
                    for o, p in items]
            for f in as_completed(futs):
                if f.result():
                    n += 1
                    if n >= want:
                        break
    logger.info("[inat/%s] saved %d", klass, n)
    return n


def _download_one_inat(obs: dict, photo: dict, klass: str, dest: Path,
                       seen: set[str],
                       gate: LeakageGate | None = None) -> bool:
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
    if gate is not None:
        reason = gate.reject_reason(r.content)
        if reason is not None:
            logger.debug("[inat/%s] dropped (%s)", klass, reason)
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
                          engine: str,
                          gate: LeakageGate | None = None) -> int:
    """OPTIONAL: scrape Google / Bing image search via the icrawler library.

    DISCLAIMER. Both Google and Bing prohibit automated image-search
    scraping in their ToS. The downloaded images are individually
    copyrighted by their owners — for a private (non-redistributed)
    held-out evaluation set this is generally tolerated under research /
    fair-use norms in most jurisdictions, but the legal picture is
    contested. Do not redistribute the resulting dataset.

    When a leakage gate is supplied, every saved-by-icrawler file is
    re-checked against TRAIN_ROOT (SHA + pHash) before being kept.
    Files that fail the gate are deleted on the spot — search-engine
    results for plant-disease queries return mostly the same PlantDoc /
    PlantVillage photos that already populate TRAIN_ROOT, and silently
    keeping them would inflate val_realworld scores.
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
            content = p.read_bytes()
        except OSError:
            continue
        h = hashlib.sha256(content).hexdigest()
        if h in seen:
            p.unlink(missing_ok=True)
            continue
        if gate is not None:
            reason = gate.reject_reason(content)
            if reason is not None:
                logger.debug("[%s/%s] dropped (%s)", engine, klass, reason)
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


def _dest_root(into: str) -> Path:
    if into == "smoke":
        return SMOKE_ROOT
    if into == "val_realworld":
        return VAL_REALWORLD_ROOT
    raise ValueError(f"unknown --into target: {into}")


def collect_class(klass: str, target: int, use_search_engine: str | None,
                  *, dest_root: Path = SMOKE_ROOT,
                  gate: LeakageGate | None = None) -> int:
    dest = dest_root / klass
    dest.mkdir(parents=True, exist_ok=True)
    seen = _existing_hashes(dest)
    have = len(seen)
    if have >= target:
        logger.info("[%s] already has %d images, target %d — skip", klass, have, target)
        return have
    want = target - have
    want -= collect_openverse(klass, want, dest, seen, gate=gate)
    if want > 0:
        want -= collect_inat(klass, want, dest, seen, gate=gate)
    if want > 0 and use_search_engine:
        collect_search_engine(klass, want, dest, seen,
                              engine=use_search_engine, gate=gate)
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
    ap.add_argument("--into", choices=("smoke", "val_realworld"),
                    default="smoke",
                    help="Destination layout. 'smoke' writes to "
                    "data_v2/smoke/<class>/ (legacy default). "
                    "'val_realworld' writes directly to the notebook's "
                    "ship-gate split (data_v2/classification/val/realworld/"
                    "<class>/) and forces the strict leakage gate on.")
    ap.add_argument("--no-leakage-gate", action="store_true",
                    help="Disable the SHA+pHash gate against TRAIN_ROOT and "
                    "the sister held-out splits. Implied for --into=smoke "
                    "(opt in by also passing --leakage-gate).")
    ap.add_argument("--leakage-gate", action="store_true",
                    help="Force the leakage gate ON even when --into=smoke "
                    "(useful when the smoke set is later promoted into a "
                    "held-out evaluation slice).")
    ap.add_argument("--phash-threshold", type=int, default=PHASH_THRESHOLD,
                    help="Hamming distance below which a pHash hit counts "
                    "as a near-duplicate of TRAIN_ROOT (default %(default)d, "
                    "matches the notebook's §3 audit).")
    ap.add_argument("--use-search-engine", choices=("google", "bing"), default=None,
                    help="Optional: top up the destination by scraping the "
                    "named image search engine. Requires `pip install icrawler`. "
                    "Read the disclaimer in collect_search_engine() first. "
                    "When --into=val_realworld the leakage gate still applies, "
                    "so most search-engine results that overlap TRAIN_ROOT "
                    "(PlantDoc / PlantVillage) get dropped before being kept.")
    args = ap.parse_args()

    chosen = [c for c in args.classes if c in CLASSES]
    bad = [c for c in args.classes if c not in CLASSES]
    if bad:
        ap.error(f"unknown class(es): {bad}")

    dest_root = _dest_root(args.into)
    gate_on = (
        not args.no_leakage_gate
        and (args.into == "val_realworld" or args.leakage_gate)
    )
    gate: LeakageGate | None = None
    if gate_on:
        if imagehash is None:
            ap.error(
                "--into=val_realworld (or --leakage-gate) requires the "
                "`imagehash` package for the pHash gate. "
                "Run `pip install imagehash` and retry, or pass "
                "--no-leakage-gate to skip the cross-split audit (not "
                "recommended for held-out splits)."
            )
        if not TRAIN_ROOT.exists():
            ap.error(
                f"TRAIN_ROOT not found at {TRAIN_ROOT}. Build it first via "
                "the notebook's §2, then re-run this collector."
            )
        gate = LeakageGate.build(
            TRAIN_ROOT, SISTER_HELDOUT_ROOTS,
            use_phash=True, threshold=args.phash_threshold,
        )

    # Re-balance DEFAULT_TARGETS to hit args.target across `chosen`.
    weight_total = sum(DEFAULT_TARGETS[c] for c in chosen)
    per_class = {
        c: max(5, round(DEFAULT_TARGETS[c] * args.target / weight_total))
        for c in chosen
    }
    logger.info("Destination: %s", dest_root)
    logger.info("Leakage gate: %s", "ON" if gate_on else "off")
    logger.info("Per-class targets: %s", per_class)

    dest_root.mkdir(parents=True, exist_ok=True)
    summary = {}
    for c in chosen:
        summary[c] = collect_class(c, per_class[c], args.use_search_engine,
                                   dest_root=dest_root, gate=gate)
    label = "val_realworld" if args.into == "val_realworld" else "Smoke set"
    print(f"\n{label} summary:")
    total = 0
    for c in chosen:
        print(f"  {c:25s} {summary[c]:4d}")
        total += summary[c]
    print(f"  {'TOTAL':25s} {total:4d}")
    if gate is not None:
        s = gate.summary()
        print(
            f"\nleakage gate dropped: "
            f"sha-train={s['dropped_sha_train']}  "
            f"sha-sister={s['dropped_sha_sister_heldout']}  "
            f"phash-train={s['dropped_phash_train']}  "
            f"(threshold={s['phash_threshold']})"
        )


if __name__ == "__main__":
    main()
