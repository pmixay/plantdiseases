"""Deficit-driven ingestion from the maintainer's Kaggle supplement (the only
source — the offline collection workflow owns scraping/filtering; see
``docs/DATASET_LABELING_PLAN.md``). Every candidate is gated (image quality →
dedup), model auto-routed, and persisted to the labeling store. Runs in a
worker thread; fail-soft per candidate.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .imagefilter import (
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

_MAX_PROBE_PER_CLASS = 1500  # bound work per run

_INGESTED_VERSION_KEY = "last_ingested_dataset_version"


def run_ingestion_versioned(
    targets: dict[str, int], kaggle_slug: str | None, img_dir: Path | None = None,
    classify=None, audit_rate: float = 0.0,
) -> dict[str, int] | None:
    """Version-gated wrapper: run a full ingestion only when the Kaggle
    dataset's published version changed since the last successful run (or on
    first run). Returns ``None`` when the metadata call failed (cycle
    skipped), ``{}`` when the version is unchanged, else ``run_ingestion``'s
    result."""
    from . import sources

    version = sources.dataset_version(kaggle_slug) if kaggle_slug else None
    if version is None:
        logger.warning("labeling ingest: dataset version unavailable — skipping cycle")
        return None
    last = labeling.get_meta(_INGESTED_VERSION_KEY)
    if last is not None and str(version) == last:
        logger.debug("labeling ingest: dataset still at version %s — nothing new", version)
        return {}
    added = run_ingestion(
        targets, kaggle_slug, img_dir, classify=classify, audit_rate=audit_rate,
    )
    labeling.set_meta(_INGESTED_VERSION_KEY, str(version))
    if added:
        logger.info("labeling ingest: dataset v%s added %s", version, added)
    return added


def _ext_for(body: bytes) -> str:
    if body[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if body[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return ".webp"
    return ".jpg"


def _audit_sampled(sha: str, rate: float) -> bool:
    """Deterministic (by sha) audit sampling of confident model calls: those
    candidates land in the human queue with audit=1 instead, so auto
    pre-filtering can never silently bury model false positives."""
    if rate <= 0:
        return False
    return int(sha[:8], 16) % 10_000 < int(rate * 10_000)


def run_ingestion(
    targets: dict[str, int], kaggle_slug: str | None = None, img_dir: Path | None = None,
    classify=None, audit_rate: float = 0.0,
) -> dict[str, int]:
    """Top up every deficit class. Returns ``{class: n_added}``.

    ``classify(image_pil, proposed_class) -> (status, pred, conf, set)`` is the
    optional model auto-labeler. When given, each candidate is routed to
    ``auto_yes`` / ``auto_no`` / ``pending`` instead of all going to the human
    queue. ``auto_no`` rows are KEPT (image + row) — confident disagreements
    are exactly where model false positives hide — but don't count toward the
    deficit. ``audit_rate`` routes a deterministic fraction of confident calls
    to the human queue (audit=1) for spot-checking.
    """
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
        logger.info("labeling ingest: all weighted classes satisfied — nothing to do")
        return {}
    logger.info("labeling ingest: deficits %s", deficits)

    seen_ph: set[str] = set(labeling.existing_phashes())
    added: dict[str, int] = {}

    for cls, deficit in deficits.items():
        got = 0
        probed = 0
        for desc in probe_class(cls, kaggle_slug, {cls}):
            if got >= deficit or probed >= _MAX_PROBE_PER_CLASS:
                break
            probed += 1
            # Descriptors are local Kaggle files only (no remote fetch).
            try:
                body = Path(desc["local_path"]).read_bytes()
            except Exception:  # noqa: BLE001
                continue
            im = valid_image_bytes(body)
            if im is None:
                continue
            sha = sha256_bytes(body)
            ph = phash_bytes(body)
            if is_near_dup(ph, seen_ph):
                continue
            # Model auto-label (trust-the-model routing). auto_yes counts
            # toward the target; pending → human; auto_no is stored but
            # doesn't reduce the deficit. A sampled fraction of confident
            # calls is re-routed to the human queue as an audit.
            status, mpred, mconf, mset = "pending", None, None, None
            audit = False
            if classify is not None:
                try:
                    status, mpred, mconf, mset = classify(im, cls)
                except Exception:  # noqa: BLE001 - fail-soft to human
                    status = "pending"
                if status in ("auto_yes", "auto_no") and _audit_sampled(sha, audit_rate):
                    status, audit = "pending", True
            fn = f"{sha}{_ext_for(body)}"
            try:
                (img_dir / fn).write_bytes(body)
            except OSError:
                continue
            row_id = labeling.insert_candidate(
                cls=cls, image_path=fn, source=desc["source"],
                source_url=desc.get("source_url"), subject_plant=desc.get("subject_plant"),
                sha=sha, phash=ph, status=status, model_pred=mpred,
                model_conf=mconf, model_set=mset, audit=audit,
            )
            if row_id is None:  # sha duplicate already in DB
                continue
            if ph:
                seen_ph.add(ph)
            if status != "auto_no":
                got += 1
        added[cls] = got
        logger.info("labeling ingest: %s += %d (probed %d)", cls, got, probed)
    logger.info("labeling ingest: done, added=%s", added)
    return added
