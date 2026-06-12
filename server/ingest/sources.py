"""Kaggle-supplement candidate probe (the ONLY ingestion source).

The maintainer collects, filters and model-judges the dataset OFFLINE and
publishes it to Kaggle; the server just pulls the published folders. Each
probe yields candidate descriptors ``{local_path, source, source_url,
subject_plant}``. Probes are fail-soft (yield nothing on error).
"""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)


def _kaggle_credentials() -> tuple[str, str] | str | None:
    """Bearer token (str), basic-auth pair (tuple), or None. Download-only:
    the server NEVER uploads to Kaggle."""
    token = os.environ.get("KAGGLE_API_TOKEN")
    if not token:
        tok_file = Path.home() / ".kaggle" / "access_token"
        if tok_file.exists():
            token = tok_file.read_text().strip()
    if token:
        return token
    user, key = os.environ.get("KAGGLE_USERNAME"), os.environ.get("KAGGLE_KEY")
    if not (user and key):
        cred_file = Path.home() / ".kaggle" / "kaggle.json"
        if cred_file.exists():
            try:
                data = json.loads(cred_file.read_text())
                user, key = data.get("username"), data.get("key")
            except Exception:  # noqa: BLE001
                user = key = None
    if user and key:
        return (user, key)
    return None


def dataset_version(slug: str) -> int | None:
    """Current version number of the Kaggle dataset (cheap metadata call so
    the ingest loop can skip cycles when nothing was published). ``None`` on
    any failure — the caller skips the cycle (fail-soft)."""
    creds = _kaggle_credentials()
    if creds is None:
        logger.warning("dataset_version: no Kaggle credentials — skipping")
        return None
    try:
        import requests

        if isinstance(creds, str):
            headers = {"Authorization": f"Bearer {creds}"}
        else:
            basic = base64.b64encode(f"{creds[0]}:{creds[1]}".encode()).decode()
            headers = {"Authorization": f"Basic {basic}"}
        r = requests.get(
            f"https://www.kaggle.com/api/v1/datasets/view/{slug}",
            headers=headers, timeout=30,
        )
        r.raise_for_status()
        v = r.json().get("currentVersionNumber")
        return int(v) if v is not None else None
    except Exception as e:  # noqa: BLE001
        logger.warning("dataset_version(%s) failed: %s", slug, e)
        return None


def kaggle_supplement(slug: str, target_classes: set[str]) -> Iterator[dict]:
    """Download + parse the maintainer's Kaggle eval-supplement: read the
    ``<class>/`` folders (folder name == class) for the target classes only."""
    try:
        import kagglehub
    except Exception:  # noqa: BLE001
        logger.warning("kaggle_supplement: kagglehub not installed — source skipped")
        return
    if _kaggle_credentials() is None:
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
    """All descriptors for one class. Kept as the seam the pipeline (and its
    tests) stub — now a thin wrapper over the Kaggle supplement."""
    if kaggle_slug:
        yield from kaggle_supplement(kaggle_slug, {cls})
