"""Per-source candidate probes + per-class query tables.

Each probe yields candidate descriptors ``{image_url|local_path, source,
source_url, subject_plant}``. Network probes are fail-soft (yield nothing on
error). Queries are ported from the notebook tables + EVAL_DATASET_PLAN.md §5.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

from .http import http_get_json

# Per-class search queries (EVAL_DATASET_PLAN §5 + notebook free-text tables).
CLASS_QUERIES: dict[str, list[str]] = {
    "nutrient_deficiency": [
        "interveinal chlorosis houseplant leaf", "iron chlorosis pothos leaf",
        "nitrogen deficiency plant leaf", "magnesium deficiency leaf",
        "phosphorus deficiency purple leaf", "potassium deficiency leaf margin",
    ],
    "leaf_scorch_underwatering": [
        "underwatered houseplant crispy leaves", "leaf scorch brown tips houseplant",
        "drought stress leaf margin necrosis", "calathea crispy brown edges",
        "fern frond scorch dry", "desiccated plant leaf",
    ],
    "gray_mold_botrytis": [
        "botrytis gray mold houseplant leaf", "botrytis blight begonia",
        "gray mould cyclamen", "botrytis cinerea ornamental leaf",
        "grey mould geranium", "botrytis african violet",
    ],
    "powdery_mildew": [
        "powdery mildew houseplant leaf", "powdery mildew begonia leaf",
        "powdery mildew violet leaf",
    ],
}

_OPENVERSE_API = "https://api.openverse.org/v1/images/"


def openverse(query: str, subject: str, page_size: int = 40) -> Iterator[dict]:
    """Openverse aggregates openly-licensed images (Flickr et al.)."""
    try:
        data = http_get_json(
            _OPENVERSE_API,
            params={"q": query, "page_size": page_size, "mature": "false"},
            timeout=30,
        )
    except Exception:  # noqa: BLE001 - fail-soft
        return
    for item in (data.get("results") or []):
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
        return
    if not (os.environ.get("KAGGLE_API_TOKEN")
            or (Path.home() / ".kaggle" / "access_token").exists()
            or (os.environ.get("KAGGLE_USERNAME") and os.environ.get("KAGGLE_KEY"))
            or (Path.home() / ".kaggle" / "kaggle.json").exists()):
        return  # no creds → skip silently
    try:
        root = Path(kagglehub.dataset_download(slug))
    except Exception:  # noqa: BLE001
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
