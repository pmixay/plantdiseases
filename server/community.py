"""
Community scan corpus for "similar scans near you".

Append-only SQLite at ``server/community.db``. Each row captures one
shared scan: the disease verdict, the auto-detected species, the
user's coarse-region string (city or admin region — never raw GPS),
the user's per-install pseudo-id, and an optional thumbnail path. The
store is intentionally write-mostly + retention-bounded — there is no
update path, only an explicit prune by age.

Threading model: mirrors ``feedback.py``. One connection per call (reused);
a module-level RLock serialises writes so concurrent uploads don't race
on the cursor.

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

import contextlib
import hashlib
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
    country         TEXT,
    region          TEXT    NOT NULL,
    disease         TEXT    NOT NULL,
    confidence      REAL,
    species         TEXT,
    species_lower   TEXT,
    species_score   REAL,
    thumb_path      TEXT,
    locale          TEXT,
    client_ip_hash  TEXT,
    created_at      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_community_region          ON community_scans(region, created_at);
CREATE INDEX IF NOT EXISTS ix_community_user            ON community_scans(user_pseudo_id);
CREATE INDEX IF NOT EXISTS ix_community_created         ON community_scans(created_at);
CREATE INDEX IF NOT EXISTS ix_community_country         ON community_scans(country, created_at);
CREATE INDEX IF NOT EXISTS ix_community_country_region  ON community_scans(country, region, created_at);
CREATE INDEX IF NOT EXISTS ix_community_species_lower   ON community_scans(species_lower);

CREATE TABLE IF NOT EXISTS community_opinions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id           TEXT    NOT NULL,
    author_pseudo_id  TEXT    NOT NULL,
    suggested_disease TEXT    NOT NULL,
    note              TEXT,
    client_ip_hash    TEXT,
    created_at        INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_opinions_scan   ON community_opinions(scan_id, created_at);
CREATE INDEX IF NOT EXISTS ix_opinions_author ON community_opinions(author_pseudo_id);
"""

_write_lock = threading.RLock()
_init_lock = threading.Lock()
_initialised = False

# Disease classes that should never surface in the community feed.
# ``not_a_plant`` rows are uninteresting to other users (and often
# faces / objects / random photos), so we silently hide them from
# both the carousel and the summary aggregates. Existing rows stay
# on disk for retraining; only the read paths filter them out.
_HIDDEN_COMMUNITY_DISEASES = ("not_a_plant",)


def _hidden_clause(table_alias: str = "") -> str:
    """SQL fragment + placeholders for the hidden-disease filter.

    Returns a string starting with " AND " so callers can append
    directly to existing WHERE conditions.
    """
    prefix = f"{table_alias}." if table_alias else ""
    placeholders = ", ".join(["?"] * len(_HIDDEN_COMMUNITY_DISEASES))
    return f" AND {prefix}disease NOT IN ({placeholders})"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(COMMUNITY_DB_PATH),
        check_same_thread=False,
        isolation_level=None,
    )
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _add_column_if_missing(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    decl: str,
) -> None:
    """Idempotent ALTER TABLE ADD COLUMN helper."""
    cols = {
        row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column in cols:
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


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
            _add_column_if_missing(conn, "community_scans", "species_lower", "TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_community_species_lower ON community_scans(species_lower)")
            conn.execute("UPDATE community_scans SET species_lower = LOWER(species) WHERE species IS NOT NULL AND species_lower IS NULL")
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
    country: str,
    region: str = "",
    disease: str,
    confidence: float | None = None,
    species: str | None = None,
    species_score: float | None = None,
    image_bytes: bytes | None = None,
    locale: str | None = None,
    client_ip: str | None = None,
) -> int:
    """Append a community-shared scan row. Returns the new row id."""
    init_db()
    now = int(time.time())
    thumb_path = _save_thumbnail(scan_id, image_bytes) if image_bytes else None
    ip_hash = hash_client_ip(client_ip)
    region_value = region or ""
    species_lower = species.lower() if species else None
    with _write_lock, _connect() as conn:
        cur = conn.execute(
            "INSERT INTO community_scans ("
            "  scan_id, user_pseudo_id, country, region, disease, confidence, "
            "  species, species_lower, species_score, thumb_path, locale, "
            "  client_ip_hash, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                scan_id,
                user_pseudo_id,
                country,
                region_value,
                disease,
                confidence,
                species,
                species_lower,
                species_score,
                thumb_path,
                locale,
                ip_hash,
                now,
            ),
        )
        return int(cur.lastrowid or 0)


def record_opinion(
    *,
    scan_id: str,
    author_pseudo_id: str,
    suggested_disease: str,
    note: str | None = None,
    client_ip: str | None = None,
) -> int:
    """Append one community 'second opinion' on a shared scan. Returns row id."""
    init_db()
    now = int(time.time())
    ip_hash = hash_client_ip(client_ip)
    note_clean = (note or "").strip()[:500] or None
    with _write_lock, _connect() as conn:
        cur = conn.execute(
            "INSERT INTO community_opinions ("
            "  scan_id, author_pseudo_id, suggested_disease, note, "
            "  client_ip_hash, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?)",
            (scan_id, author_pseudo_id, suggested_disease, note_clean, ip_hash, now),
        )
        return int(cur.lastrowid or 0)


def opinions_for(scan_id: str, *, limit: int = 50) -> dict:
    """Opinions for one scan plus a per-disease consensus tally.

    Shape::

        {"opinions":  [{"suggested_disease", "note", "created_at"}, ...],
         "consensus": [{"disease", "count"}, ...]}   # most-voted first
    """
    init_db()
    limit = max(1, min(int(limit), 100))
    with _connect() as conn:
        rows = conn.execute(
            "SELECT suggested_disease, note, created_at "
            "FROM community_opinions WHERE scan_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (scan_id, limit),
        ).fetchall()
        tally = conn.execute(
            "SELECT suggested_disease, COUNT(*) AS n "
            "FROM community_opinions WHERE scan_id = ? "
            "GROUP BY suggested_disease ORDER BY n DESC",
            (scan_id,),
        ).fetchall()
    opinions = [
        {"suggested_disease": str(r[0]), "note": r[1] or "", "created_at": int(r[2])}
        for r in rows
    ]
    consensus = [{"disease": str(t[0]), "count": int(t[1])} for t in tally]
    return {"opinions": opinions, "consensus": consensus}


def daily_opinion_count_for_ip_hash(ip_hash: str | None) -> int:
    """Opinions submitted from one IP hash in the last 24h (abuse cap)."""
    if not ip_hash:
        return 0
    init_db()
    cutoff = int(time.time()) - 86_400
    with _connect() as conn:
        return int(conn.execute(
            "SELECT COUNT(*) FROM community_opinions "
            "WHERE client_ip_hash = ? AND created_at >= ?",
            (ip_hash, cutoff),
        ).fetchone()[0])


def opinion_counts(scan_ids: list[str]) -> dict[str, int]:
    """Map each scan_id to its number of community opinions (zeros omitted)."""
    ids = [s for s in scan_ids if s]
    if not ids:
        return {}
    init_db()
    placeholders = ",".join("?" * len(ids))
    with _connect() as conn:
        rows = conn.execute(
            "SELECT scan_id, COUNT(*) FROM community_opinions "
            f"WHERE scan_id IN ({placeholders}) GROUP BY scan_id",
            tuple(ids),
        ).fetchall()
    return {str(r[0]): int(r[1]) for r in rows}


def opinions_by_author(author_pseudo_id: str, *, limit: int = 100) -> list[dict]:
    """Opinions one install authored, newest first, each with its scan_id."""
    init_db()
    if not author_pseudo_id:
        return []
    limit = max(1, min(int(limit), 200))
    with _connect() as conn:
        rows = conn.execute(
            "SELECT scan_id, suggested_disease, note, created_at "
            "FROM community_opinions WHERE author_pseudo_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (author_pseudo_id, limit),
        ).fetchall()
    return [
        {
            "scan_id": str(r[0]),
            "suggested_disease": str(r[1]),
            "note": r[2] or "",
            "created_at": int(r[3]),
        }
        for r in rows
    ]


def nearby_scans(
    *,
    country: str,
    region: str | None = None,
    disease: str | None = None,
    species: str | None = None,
    limit: int = 20,
    exclude_user_pseudo_id: str | None = None,
) -> list[dict]:
    """Return the most recent shared scans within ``country``.

    Optimized to reuse a single database connection and execute sargable
    queries using the indexed ``species_lower`` column.
    """
    init_db()
    limit = max(1, min(int(limit), 50))

    with _connect() as conn:
        def _query(where_extra: str, params: list, take: int) -> list[dict]:
            sql = (
                "SELECT scan_id, country, region, disease, confidence, species, "
                "species_score, thumb_path, locale, created_at "
                "FROM community_scans WHERE country = ? "
                + where_extra
                + " ORDER BY created_at DESC LIMIT ?"
            )
            rows = conn.execute(sql, (*params, take)).fetchall()
            return [_row_to_dict(r) for r in rows]

        base_params: list = [country]
        base_extra = ""
        if exclude_user_pseudo_id:
            base_extra += " AND user_pseudo_id != ?"
            base_params.append(exclude_user_pseudo_id)
        if _HIDDEN_COMMUNITY_DISEASES:
            base_extra += _hidden_clause()
            base_params.extend(_HIDDEN_COMMUNITY_DISEASES)
        region_clean = (region or "").strip()
        region_extra = base_extra
        region_params = list(base_params)
        if region_clean:
            region_extra += " AND region = ?"
            region_params.append(region_clean)
        species_clean = (species or "").strip()
        species_lower = species_clean.lower() if species_clean else ""

        seen: set[str] = set()
        out: list[dict] = []

        def _append_unique(rows: list[dict]) -> None:
            for row in rows:
                sid = row["scan_id"]
                if sid in seen:
                    continue
                out.append(row)
                seen.add(sid)

        # Buckets ordered most → least specific
        if disease and species_clean and region_clean:
            _append_unique(_query(
                region_extra + " AND disease = ? AND species_lower = ?",
                region_params + [disease, species_lower],
                limit,
            ))
        if len(out) < limit and disease and species_clean:
            _append_unique(_query(
                base_extra + " AND disease = ? AND species_lower = ?",
                base_params + [disease, species_lower],
                limit,
            ))
        if len(out) < limit and disease and region_clean:
            _append_unique(_query(
                region_extra + " AND disease = ?",
                region_params + [disease],
                limit,
            ))
        if len(out) < limit and disease:
            _append_unique(_query(
                base_extra + " AND disease = ?",
                base_params + [disease],
                limit,
            ))
        if len(out) < limit and region_clean:
            _append_unique(_query(region_extra, region_params, limit))
        if len(out) < limit:
            _append_unique(_query(base_extra, base_params, limit))
        return out[:limit]


def region_summary(
    country: str,
    region: str | None = None,
    *,
    exclude_user_pseudo_id: str | None = None,
) -> dict:
    """Aggregate the corpus for one country (or country+region)."""
    init_db()
    now = int(time.time())
    week_ago = now - 7 * 86_400
    region_clean = (region or "").strip()
    exclude_clause = ""
    exclude_params: tuple = ()
    if exclude_user_pseudo_id:
        exclude_clause = " AND user_pseudo_id != ?"
        exclude_params = (exclude_user_pseudo_id,)
    if _HIDDEN_COMMUNITY_DISEASES:
        exclude_clause += _hidden_clause()
        exclude_params = (*exclude_params, *_HIDDEN_COMMUNITY_DISEASES)
    with _connect() as conn:
        if region_clean:
            total = conn.execute(
                "SELECT COUNT(*) FROM community_scans "
                "WHERE country = ? AND region = ?" + exclude_clause,
                (country, region_clean, *exclude_params),
            ).fetchone()[0]
            last_week = conn.execute(
                "SELECT COUNT(*) FROM community_scans "
                "WHERE country = ? AND region = ? AND created_at >= ?"
                + exclude_clause,
                (country, region_clean, week_ago, *exclude_params),
            ).fetchone()[0]
            disease_rows = conn.execute(
                "SELECT disease, COUNT(*) AS n FROM community_scans "
                "WHERE country = ? AND region = ? AND created_at >= ?"
                + exclude_clause
                + " GROUP BY disease ORDER BY n DESC LIMIT 3",
                (country, region_clean, week_ago, *exclude_params),
            ).fetchall()
            top_region_rows: list[tuple] = []
        else:
            total = conn.execute(
                "SELECT COUNT(*) FROM community_scans WHERE country = ?"
                + exclude_clause,
                (country, *exclude_params),
            ).fetchone()[0]
            last_week = conn.execute(
                "SELECT COUNT(*) FROM community_scans "
                "WHERE country = ? AND created_at >= ?" + exclude_clause,
                (country, week_ago, *exclude_params),
            ).fetchone()[0]
            disease_rows = conn.execute(
                "SELECT disease, COUNT(*) AS n FROM community_scans "
                "WHERE country = ? AND created_at >= ?" + exclude_clause
                + " GROUP BY disease ORDER BY n DESC LIMIT 3",
                (country, week_ago, *exclude_params),
            ).fetchall()
            top_region_rows = conn.execute(
                "SELECT region, COUNT(*) AS n FROM community_scans "
                "WHERE country = ? AND region != '' AND created_at >= ?"
                + exclude_clause
                + " GROUP BY region ORDER BY n DESC LIMIT 3",
                (country, week_ago, *exclude_params),
            ).fetchall()
    top_diseases = [
        {"disease": str(r[0]), "count": int(r[1])} for r in disease_rows
    ]
    top_regions = [
        {"region": str(r[0]), "count": int(r[1])} for r in top_region_rows
    ]
    return {
        "country": country,
        "region": region_clean or None,
        "total": int(total),
        "last_7d": int(last_week),
        "top_diseases": top_diseases,
        "top_regions": top_regions,
    }


def delete_for_user(user_pseudo_id: str) -> int:
    """Erase every row + thumbnail attributed to one install."""
    init_db()
    if not user_pseudo_id:
        return 0
    with _write_lock, _connect() as conn:
        rows = conn.execute(
            "SELECT thumb_path FROM community_scans WHERE user_pseudo_id = ?",
            (user_pseudo_id,),
        ).fetchall()
        deleted = conn.execute(
            "DELETE FROM community_scans WHERE user_pseudo_id = ?",
            (user_pseudo_id,),
        ).rowcount
        # Opinions this install authored on *other* people's scans also go.
        conn.execute(
            "DELETE FROM community_opinions WHERE author_pseudo_id = ?",
            (user_pseudo_id,),
        )
    for row in rows:
        thumb = row[0]
        if not thumb:
            continue
        with contextlib.suppress(OSError):
            (COMMUNITY_THUMB_DIR / thumb).unlink(missing_ok=True)
    return int(deleted or 0)


def prune_older_than(days: int) -> int:
    """Delete rows + thumbnails older than ``days`` days."""
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
        with contextlib.suppress(OSError):
            (COMMUNITY_THUMB_DIR / thumb).unlink(missing_ok=True)
    return int(deleted or 0)


def _row_to_dict(row: tuple) -> dict:
    return {
        "scan_id": row[0],
        "country": row[1] or "",
        "region": row[2] or "",
        "disease": row[3],
        "confidence": float(row[4]) if row[4] is not None else None,
        "species": row[5],
        "species_score": float(row[6]) if row[6] is not None else None,
        "thumb_path": row[7],
        "locale": row[8],
        "created_at": int(row[9]),
    }


def daily_write_count_for_ip_hash(ip_hash: str | None) -> int:
    if not ip_hash:
        return 0
    init_db()
    cutoff = int(time.time()) - 86_400
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM community_scans "
            "WHERE client_ip_hash = ? AND created_at >= ?",
            (ip_hash, cutoff),
        ).fetchone()
    return int(row[0]) if row else 0


def stats_snapshot() -> dict:
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


def get_user_data(user_pseudo_id: str) -> list[dict]:
    """Retrieve all community scans for a user. Part of GDPR data portability."""
    init_db()
    if not user_pseudo_id:
        return []
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM community_scans WHERE user_pseudo_id = ? ORDER BY created_at DESC",
            (user_pseudo_id,),
        ).fetchall()
        return [dict(r) for r in rows]


__all__ = [
    "COMMUNITY_DB_PATH",
    "COMMUNITY_THUMB_DIR",
    "daily_write_count_for_ip_hash",
    "delete_for_user",
    "init_db",
    "nearby_scans",
    "prune_older_than",
    "record_scan",
    "region_summary",
    "stats_snapshot",
    "get_user_data",
]
