"""
Community scan corpus for "similar scans near you".

Append-only SQLite at ``server/community.db``. Each row captures one
*shared* scan: the disease verdict, the auto-detected species, the
user's coarse-region string (city or admin region — never raw GPS),
the user's per-install pseudo-id, and an optional thumbnail path. The
store is intentionally write-mostly + retention-bounded — there is no
update path, only an explicit prune by age.

Threading model: mirrors ``feedback.py``. One short-lived SQLite
connection per call; a module-level RLock serialises writes so
concurrent uploads don't race on the cursor.

Privacy posture
---------------
* No raw lat/lon ever lands here. The Android client reverse-geocodes
  the device's *coarse* location to a `City, Country` string and that
  string is what we store.
* `client_ip_hash` mirrors the feedback store: peppered SHA-256, 64
  bits, used for abuse-rate windows only.
* Thumbnails are 512px JPEGs uploaded by the client. The user can opt
  out from the Android app; uploads default to ON after the consent
  acknowledgement on first scan.
* `delete_for_user(user_pseudo_id)` lets us honour deletion requests
  without joining against any other table.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

try:
    from .config import get_str  # type: ignore[import-not-found]
    from .feedback import hash_client_ip
except ImportError:
    from config import get_str
    from feedback import hash_client_ip

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = Path(__file__).resolve().parent / "community.db"
COMMUNITY_DB_PATH = Path(get_str("COMMUNITY_DB_PATH", str(_DEFAULT_DB_PATH)))

_DEFAULT_THUMB_DIR = Path(__file__).resolve().parent / "community_thumbs"
COMMUNITY_THUMB_DIR = Path(get_str("COMMUNITY_THUMB_DIR", str(_DEFAULT_THUMB_DIR)))

# Cap thumbnails on disk so a runaway client can't fill the volume.
COMMUNITY_MAX_THUMB_BYTES = 350 * 1024  # 350 KB — generous for a 512px JPEG

_SCHEMA = """
CREATE TABLE IF NOT EXISTS community_scans (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id         TEXT    NOT NULL,
    user_pseudo_id  TEXT    NOT NULL,
    region          TEXT    NOT NULL,
    disease         TEXT    NOT NULL,
    confidence      REAL,
    species         TEXT,
    species_score   REAL,
    thumb_path      TEXT,
    locale          TEXT,
    client_ip_hash  TEXT,
    created_at      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_community_region   ON community_scans(region, created_at);
CREATE INDEX IF NOT EXISTS ix_community_user     ON community_scans(user_pseudo_id);
CREATE INDEX IF NOT EXISTS ix_community_created  ON community_scans(created_at);
"""

_write_lock = threading.RLock()
_init_lock = threading.Lock()
_initialised = False


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(COMMUNITY_DB_PATH),
        check_same_thread=False,
        isolation_level=None,
    )
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db() -> None:
    """Create the schema + thumb dir if missing. Idempotent."""
    global _initialised
    with _init_lock:
        if _initialised:
            return
        COMMUNITY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        COMMUNITY_THUMB_DIR.mkdir(parents=True, exist_ok=True)
        with _connect() as conn:
            conn.executescript(_SCHEMA)
        _initialised = True
        logger.info("Community DB ready at %s", COMMUNITY_DB_PATH)


def _save_thumbnail(scan_id: str, image_bytes: bytes) -> str | None:
    """Persist a 512px JPEG thumbnail under COMMUNITY_THUMB_DIR.

    Returns the relative file path (suitable for storage in the
    ``thumb_path`` column) or ``None`` when the bytes are missing /
    oversized. Image decoding is the caller's job — we only validate
    the byte ceiling and write the bytes straight through.
    """
    if not image_bytes:
        return None
    if len(image_bytes) > COMMUNITY_MAX_THUMB_BYTES:
        logger.info(
            "Community thumb rejected (oversized: %d bytes)", len(image_bytes)
        )
        return None
    # Hash-prefix the filename so two scans with the same scan_id (a
    # mis-behaved client) can't collide. Truncate to 24 hex chars —
    # plenty of collision resistance for a per-pseudo-id namespace.
    digest = hashlib.sha256(image_bytes).hexdigest()[:24]
    filename = f"{scan_id}_{digest}.jpg"
    target = COMMUNITY_THUMB_DIR / filename
    try:
        target.write_bytes(image_bytes)
    except OSError as exc:
        logger.warning("Failed to write community thumb %s: %s", target, exc)
        return None
    return filename


def record_scan(
    *,
    scan_id: str,
    user_pseudo_id: str,
    region: str,
    disease: str,
    confidence: float | None = None,
    species: str | None = None,
    species_score: float | None = None,
    image_bytes: bytes | None = None,
    locale: str | None = None,
    client_ip: str | None = None,
) -> int:
    """Append a community-shared scan row. Returns the new row id.

    Thumbnails are saved best-effort: if the bytes are missing or
    oversized we just skip the file and leave ``thumb_path`` NULL —
    the row still surfaces in nearby queries, the client just renders
    a fallback icon.
    """
    init_db()
    now = int(time.time())
    thumb_path = _save_thumbnail(scan_id, image_bytes) if image_bytes else None
    ip_hash = hash_client_ip(client_ip)
    with _write_lock, _connect() as conn:
        cur = conn.execute(
            "INSERT INTO community_scans ("
            "  scan_id, user_pseudo_id, region, disease, confidence, "
            "  species, species_score, thumb_path, locale, "
            "  client_ip_hash, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                scan_id,
                user_pseudo_id,
                region,
                disease,
                confidence,
                species,
                species_score,
                thumb_path,
                locale,
                ip_hash,
                now,
            ),
        )
        return int(cur.lastrowid or 0)


def nearby_scans(
    *,
    region: str,
    disease: str | None = None,
    limit: int = 20,
    exclude_user_pseudo_id: str | None = None,
) -> list[dict]:
    """Return the most recent shared scans in ``region``.

    When ``disease`` is supplied, prefer rows with the same diagnosis
    (sorted by most recent), then top up with other diagnoses in the
    same region until ``limit`` is reached. ``exclude_user_pseudo_id``
    drops the requesting user's own rows so they don't see themselves
    in the carousel.

    Each row is a compact dict — ``thumb_path`` is the file under
    COMMUNITY_THUMB_DIR, exposed via /community/thumb/<filename> by
    the FastAPI layer.
    """
    init_db()
    limit = max(1, min(int(limit), 50))
    base_sql = (
        "SELECT scan_id, region, disease, confidence, species, "
        "species_score, thumb_path, locale, created_at "
        "FROM community_scans "
        "WHERE region = ? "
    )
    params: list = [region]
    if exclude_user_pseudo_id:
        base_sql += "AND user_pseudo_id != ? "
        params.append(exclude_user_pseudo_id)
    # Disease-matching rows first.
    matched: list[dict] = []
    fillers: list[dict] = []
    with _connect() as conn:
        if disease:
            rows = conn.execute(
                base_sql + "AND disease = ? ORDER BY created_at DESC LIMIT ?",
                (*params, disease, limit),
            ).fetchall()
            matched = [_row_to_dict(r) for r in rows]
        if len(matched) < limit:
            remaining = limit - len(matched)
            extra_params = list(params)
            extra_sql = base_sql + "ORDER BY created_at DESC LIMIT ?"
            extra_params.append(remaining + len(matched))
            rows = conn.execute(extra_sql, extra_params).fetchall()
            seen = {m["scan_id"] for m in matched}
            for r in rows:
                d = _row_to_dict(r)
                if d["scan_id"] in seen:
                    continue
                fillers.append(d)
                if len(matched) + len(fillers) >= limit:
                    break
    return matched + fillers


def region_summary(region: str) -> dict:
    """Aggregate the corpus for one region — totals + top-3 diseases.

    Used by the Android "your region" card to render a quick
    "5 scans nearby this week; mostly Powdery Mildew" snippet.
    """
    init_db()
    now = int(time.time())
    week_ago = now - 7 * 86_400
    with _connect() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM community_scans WHERE region = ?", (region,)
        ).fetchone()[0]
        last_week = conn.execute(
            "SELECT COUNT(*) FROM community_scans "
            "WHERE region = ? AND created_at >= ?",
            (region, week_ago),
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT disease, COUNT(*) AS n FROM community_scans "
            "WHERE region = ? AND created_at >= ? "
            "GROUP BY disease ORDER BY n DESC LIMIT 3",
            (region, week_ago),
        ).fetchall()
    top_diseases = [
        {"disease": str(r[0]), "count": int(r[1])} for r in rows
    ]
    return {
        "region": region,
        "total": int(total),
        "last_7d": int(last_week),
        "top_diseases": top_diseases,
    }


def delete_for_user(user_pseudo_id: str) -> int:
    """Erase every row + thumbnail attributed to one install. Returns
    the number of rows deleted. Honours the Android "clear my
    contributions" affordance.
    """
    init_db()
    if not user_pseudo_id:
        return 0
    with _write_lock, _connect() as conn:
        # Read the thumb paths first so we can delete the on-disk
        # files; the DB DELETE itself runs in the same transaction.
        rows = conn.execute(
            "SELECT thumb_path FROM community_scans WHERE user_pseudo_id = ?",
            (user_pseudo_id,),
        ).fetchall()
        deleted = conn.execute(
            "DELETE FROM community_scans WHERE user_pseudo_id = ?",
            (user_pseudo_id,),
        ).rowcount
    for row in rows:
        thumb = row[0]
        if not thumb:
            continue
        path = COMMUNITY_THUMB_DIR / thumb
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    return int(deleted or 0)


def prune_older_than(days: int) -> int:
    """Delete rows + thumbnails older than ``days`` days. Returns the
    number of rows pruned. Intended to be invoked from a daily cron;
    not wired into the request path.
    """
    init_db()
    if days <= 0:
        return 0
    cutoff = int(time.time()) - days * 86_400
    with _write_lock, _connect() as conn:
        rows = conn.execute(
            "SELECT thumb_path FROM community_scans WHERE created_at < ?",
            (cutoff,),
        ).fetchall()
        deleted = conn.execute(
            "DELETE FROM community_scans WHERE created_at < ?",
            (cutoff,),
        ).rowcount
    for row in rows:
        thumb = row[0]
        if not thumb:
            continue
        try:
            (COMMUNITY_THUMB_DIR / thumb).unlink(missing_ok=True)
        except OSError:
            pass
    return int(deleted or 0)


def _row_to_dict(row: tuple) -> dict:
    return {
        "scan_id": row[0],
        "region": row[1],
        "disease": row[2],
        "confidence": float(row[3]) if row[3] is not None else None,
        "species": row[4],
        "species_score": float(row[5]) if row[5] is not None else None,
        "thumb_path": row[6],
        "locale": row[7],
        "created_at": int(row[8]),
    }


def stats_snapshot() -> dict:
    """Aggregate counters for the /api/metrics surface."""
    init_db()
    now = int(time.time())
    day_ago = now - 86_400
    week_ago = now - 7 * 86_400
    with _connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM community_scans").fetchone()[0]
        last_24h = conn.execute(
            "SELECT COUNT(*) FROM community_scans WHERE created_at >= ?",
            (day_ago,),
        ).fetchone()[0]
        last_7d = conn.execute(
            "SELECT COUNT(*) FROM community_scans WHERE created_at >= ?",
            (week_ago,),
        ).fetchone()[0]
        region_count = conn.execute(
            "SELECT COUNT(DISTINCT region) FROM community_scans"
        ).fetchone()[0]
    return {
        "total": int(total),
        "last_24h": int(last_24h),
        "last_7d": int(last_7d),
        "regions": int(region_count),
    }


__all__ = [
    "COMMUNITY_DB_PATH",
    "COMMUNITY_THUMB_DIR",
    "delete_for_user",
    "init_db",
    "nearby_scans",
    "prune_older_than",
    "record_scan",
    "region_summary",
    "stats_snapshot",
]
