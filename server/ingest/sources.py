"""Per-source candidate probes + per-class query tables.

Each probe yields candidate descriptors ``{image_url|local_path, source,
source_url, subject_plant}``. Network probes are fail-soft (yield nothing on
error). Queries are ported from the notebook tables + EVAL_DATASET_PLAN.md §5.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Iterator

from .http import http_get_json

logger = logging.getLogger(__name__)

# Per-class search queries. SHORT (2-3 words): Openverse ANDs the terms, so the
# old 4-6 word queries matched almost nothing. Kept high-recall + on-topic.
CLASS_QUERIES: dict[str, list[str]] = {
    "nutrient_deficiency": [
        "leaf chlorosis", "interveinal chlorosis", "iron chlorosis",
        "nutrient deficiency leaf", "yellowing leaf",
    ],
    "leaf_scorch_underwatering": [
        "leaf scorch", "leaf tip burn", "crispy brown leaf",
        "drought stressed plant", "scorched leaf",
    ],
    "gray_mold_botrytis": [
        "botrytis", "gray mold leaf", "grey mould plant",
        "botrytis cinerea", "botrytis blight",
    ],
    "powdery_mildew": [
        "powdery mildew", "powdery mildew leaf", "mildew foliage",
    ],
}

_OPENVERSE_API = "https://api.openverse.org/v1/images/"


def openverse(query: str, subject: str, page_size: int = 20) -> Iterator[dict]:
    """Openverse aggregates openly-licensed images (Flickr et al.). page_size is
    capped at 20: anonymous requests 429 on larger pages."""
    try:
        data = http_get_json(
            _OPENVERSE_API,
            params={"q": query, "page_size": min(page_size, 20), "mature": "false"},
            timeout=30,
        )
    except Exception as e:  # noqa: BLE001 - fail-soft, but log so it isn't silent
        logger.warning("openverse query %r failed: %s", query, e)
        return
    results = data.get("results") or []
    if not results:
        logger.info("openverse query %r returned 0 results", query)
    for item in results:
        url = item.get("url")
        if not url:
            continue
        yield {
            "image_url": url,
            "source": "openverse",
            "source_url": item.get("foreign_landing_url") or url,
            "subject_plant": subject,
        }


def web_image_search(query: str, subject: str) -> Iterator[dict]:
    """Best-effort web image search hook. Returns nothing unless an operator
    wires a search backend (kept a no-op so ingestion never hard-depends on a
    brittle scraper — Openverse + the Kaggle supplement carry the load)."""
    return iter(())


def kaggle_supplement(slug: str, target_classes: set[str]) -> Iterator[dict]:
    """Download + parse the maintainer's Kaggle eval-supplement: read the
    ``<class>/`` folders (folder name == class) for the target classes only."""
    try:
        import kagglehub
    except Exception:  # noqa: BLE001
        logger.warning("kaggle_supplement: kagglehub not installed — source skipped")
        return
    if not (os.environ.get("KAGGLE_API_TOKEN")
            or (Path.home() / ".kaggle" / "access_token").exists()
            or (os.environ.get("KAGGLE_USERNAME") and os.environ.get("KAGGLE_KEY"))
            or (Path.home() / ".kaggle" / "kaggle.json").exists()):
        logger.warning(
            "kaggle_supplement: no Kaggle credentials (KAGGLE_API_TOKEN / ~/.kaggle) "
            "— source skipped; the candidate queue will stay empty until set")
        return
    try:
        root = Path(kagglehub.dataset_download(slug))
    except Exception as e:  # noqa: BLE001
        logger.warning("kaggle_supplement: download of %s failed: %s", slug, e)
        return
    exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    for p in root.rglob("*"):
        if not (p.is_file() and p.suffix.lower() in exts):
            continue
        cls = p.parent.name
        if cls not in target_classes:
            continue
        yield {
            "local_path": str(p),
            "source": "kaggle_supplement",
            "source_url": f"kaggle:{slug}/{cls}/{p.name}",
            "subject_plant": None,
        }


def probe_class(cls: str, kaggle_slug: str | None, target_classes: set[str]) -> Iterator[dict]:
    """All descriptors for one class, across sources."""
    if kaggle_slug:
        yield from kaggle_supplement(kaggle_slug, {cls})
    for q in CLASS_QUERIES.get(cls, []):
        yield from openverse(q, subject=cls)
        yield from web_image_search(q, subject=cls)
