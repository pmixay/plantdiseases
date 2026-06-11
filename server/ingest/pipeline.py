"""Deficit-driven ingestion: scrape ONLY classes below target, gate every
candidate (disjointness → image quality → green → dedup), persist to the
labeling store. Runs in a worker thread; fail-soft per source.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .disjoint import is_disjoint
from .http import http_get_bytes
from .imagefilter import (
    green_ratio,
    is_near_dup,
    phash_bytes,
    sha256_bytes,
    valid_image_bytes,
)
from .sources import probe_class

try:
    from .. import labeling
except ImportError:  # pragma: no cover - script mode
    import labeling  # type: ignore

logger = logging.getLogger(__name__)

_DIVERSITY_CAP = 3        # max kept per (source_url page / subject)
_MAX_PROBE_PER_CLASS = 1500  # bound work per run


def _ext_for(body: bytes) -> str:
    if body[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if body[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return ".webp"
    return ".jpg"


def run_ingestion(
    targets: dict[str, int], kaggle_slug: str | None = None, img_dir: Path | None = None,
) -> dict[str, int]:
    """Top up every deficit class. Returns ``{class: n_added}``."""
    labeling.init_db()
    img_dir = Path(img_dir) if img_dir else labeling.LABELING_IMG_DIR
    img_dir.mkdir(parents=True, exist_ok=True)

    st = labeling.stats(targets)["per_class"]
    deficits = {
        c: t["weight"] - t["validated"] - t["pending"]
        for c, t in st.items()
        if t["weight"] > 0 and (t["weight"] - t["validated"] - t["pending"]) > 0
    }
    if not deficits:
        return {}

    seen_ph: set[str] = set(labeling.existing_phashes())
    target_classes = {c for c, t in targets.items() if int(t or 0) > 0}
    added: dict[str, int] = {}

    for cls, deficit in deficits.items():
        got = 0
        per_subject: dict[str, int] = {}
        probed = 0
        for desc in probe_class(cls, kaggle_slug, target_classes):
            if got >= deficit or probed >= _MAX_PROBE_PER_CLASS:
                break
            probed += 1
            if not is_disjoint(desc.get("source_url")):
                continue
            key = desc.get("source_url") or ""
            if per_subject.get(key, 0) >= _DIVERSITY_CAP:
                continue
            # fetch bytes (remote url or local kaggle file)
            try:
                if desc.get("local_path"):
                    body = Path(desc["local_path"]).read_bytes()
                else:
                    body = http_get_bytes(desc["image_url"])
            except Exception:  # noqa: BLE001
                body = None
            if not body:
                continue
            im = valid_image_bytes(body)
            if im is None or green_ratio(im) < 0.10:
                continue
            sha = sha256_bytes(body)
            ph = phash_bytes(body)
            if is_near_dup(ph, seen_ph):
                continue
            fn = f"{sha}{_ext_for(body)}"
            try:
                (img_dir / fn).write_bytes(body)
            except OSError:
                continue
            row_id = labeling.insert_candidate(
                cls=cls, image_path=fn, source=desc["source"],
                source_url=desc.get("source_url"), subject_plant=desc.get("subject_plant"),
                sha=sha, phash=ph,
            )
            if row_id is None:  # sha duplicate already in DB
                continue
            if ph:
                seen_ph.add(ph)
            per_subject[key] = per_subject.get(key, 0) + 1
            got += 1
        if got:
            added[cls] = got
            logger.info("labeling ingest: %s += %d", cls, got)
    return added
