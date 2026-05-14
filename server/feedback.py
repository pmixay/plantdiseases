"""
User-feedback store for PlantVision diagnoses.

Append-only SQLite at ``server/feedback.db``. Each row captures a single
"was this diagnosis correct?" signal from the client, optionally with
the corrected class label and free-text. The store is intentionally
write-mostly — there is no update / delete path. A separate offline
tool exports the table for the retraining loop.

Threading model: SQLite handles cross-thread access if we open one
connection per call. A module-level RLock serialises writes so two
concurrent requests can't race on the (cheap) cursor / commit cycle.
The classifier inference loop dominates wall time, so the lock is
never the bottleneck.

Schema is forward-compatible: ``init_db()`` adds any missing columns
from ``_EXTRA_COLUMNS`` on startup so older DB files keep working when
the server is upgraded. We never drop columns — orphaned data stays
in place for the retraining loop to ignore.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Iterable

try:
    from .config import get_str  # type: ignore[import-not-found]
except ImportError:
    from config import get_str

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = Path(__file__).resolve().parent / "feedback.db"
FEEDBACK_DB_PATH = Path(get_str("FEEDBACK_DB_PATH", str(_DEFAULT_DB_PATH)))

# Stable IP hashing pepper. When the env var is unset we still hash so
# raw IPs never land on disk; the operator can rotate the pepper to
# break correlation across deployments.
_IP_HASH_PEPPER = get_str("FEEDBACK_IP_PEPPER", "plantvision-static-pepper-v1")

_CORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS feedback (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id         TEXT    NOT NULL,
    correct         INTEGER NOT NULL,
    corrected_class TEXT,
    note            TEXT,
    client_ip       TEXT,
    created_at      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_feedback_scan_id  ON feedback(scan_id);
CREATE INDEX IF NOT EXISTS ix_feedback_created  ON feedback(created_at);

-- Short-lived issuance log. /api/analyze inserts a row here per
-- response; /api/feedback consults it before accepting a payload so
-- attackers can't poison the corrections corpus with arbitrary
-- scan_ids. Rows older than the retention window are pruned by the
-- daily background task.
CREATE TABLE IF NOT EXISTS issued_scan_ids (
    scan_id    TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_issued_created ON issued_scan_ids(created_at);
"""

# Columns added in later schema revisions. Listed (name, DDL fragment)
# so init_db can ALTER TABLE on an existing DB without losing rows.
_EXTRA_COLUMNS: tuple[tuple[str, str], ...] = (
    ("predicted_class",       "TEXT"),
    ("predicted_confidence",  "REAL"),
    ("top3_json",             "TEXT"),
    ("model_version",         "TEXT"),
    ("app_version",           "TEXT"),
    ("locale",                "TEXT"),
    ("platform",              "TEXT"),
    ("user_pseudo_id",        "TEXT"),
    ("region_count",          "INTEGER"),
    ("primary_region_index",  "INTEGER"),
    ("feedback_latency_ms",   "INTEGER"),
    ("reason",                "TEXT"),
    ("image_hash",            "TEXT"),
    ("client_ip_hash",        "TEXT"),
)

_EXTRA_INDICES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS ix_feedback_user_pseudo ON feedback(user_pseudo_id);",
    "CREATE INDEX IF NOT EXISTS ix_feedback_model_version ON feedback(model_version);",
    "CREATE INDEX IF NOT EXISTS ix_feedback_predicted_class ON feedback(predicted_class);",
)

_write_lock = threading.RLock()
_init_lock = threading.Lock()
_initialised = False


def _connect() -> sqlite3.Connection:
    """Open a short-lived SQLite connection.

    ``check_same_thread=False`` is safe here because every call site
    closes the connection synchronously; the connection never leaves
    the calling thread's scope.
    """
    conn = sqlite3.connect(
        str(FEEDBACK_DB_PATH),
        check_same_thread=False,
        isolation_level=None,  # autocommit; we use explicit transactions below
    )
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _existing_columns(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("PRAGMA table_info(feedback)").fetchall()
    return {row[1] for row in rows}


def init_db() -> None:
    """Create the schema if it doesn't already exist. Idempotent.

    Also runs a forward-only migration step: any column in
    ``_EXTRA_COLUMNS`` that's missing from an existing DB is added
    with ``ALTER TABLE``. SQLite ``ADD COLUMN`` is O(1) on append-only
    tables, so this stays cheap even with millions of rows.
    """
    global _initialised
    with _init_lock:
        if _initialised:
            return
        FEEDBACK_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _connect() as conn:
            conn.executescript(_CORE_SCHEMA)
            present = _existing_columns(conn)
            for name, ddl in _EXTRA_COLUMNS:
                if name not in present:
                    conn.execute(f"ALTER TABLE feedback ADD COLUMN {name} {ddl}")
            for index_sql in _EXTRA_INDICES:
                conn.execute(index_sql)
        _initialised = True
        logger.info("Feedback DB ready at %s", FEEDBACK_DB_PATH)


def hash_client_ip(client_ip: str | None) -> str | None:
    """Return a peppered SHA-256 of the client IP, or None.

    We never store raw IPs on disk — only a short, irreversible token.
    Same IP + same pepper → same hash, so abuse-rate analysis still
    works without keeping PII at rest.
    """
    if not client_ip:
        return None
    digest = hashlib.sha256(
        f"{_IP_HASH_PEPPER}|{client_ip}".encode("utf-8")
    ).hexdigest()
    # 16 hex chars (64 bits) is plenty of entropy for abuse tracking
    # without giving a colour-by-numbers re-identification surface.
    return digest[:16]


def _normalise_top3(top3: Iterable[tuple[str, float]] | dict | str | None) -> str | None:
    """Coerce the various shapes the client sends into a compact JSON.

    Accepts:
      * already-serialised JSON string (passes through)
      * list/tuple of (class, prob) pairs
      * dict mapping class → prob (we sort & take top-3 here as a guard)
    Returns ``None`` when input is empty or invalid.
    """
    if top3 is None:
        return None
    if isinstance(top3, str):
        # Trust the caller to have already validated length upstream.
        return top3[:1024] or None
    try:
        if isinstance(top3, dict):
            pairs = sorted(top3.items(), key=lambda kv: kv[1], reverse=True)[:3]
        else:
            pairs = list(top3)[:3]
        cleaned = [
            {"class": str(k)[:64], "prob": round(float(v), 6)}
            for k, v in pairs
            if isinstance(k, (str, bytes)) or v is not None
        ]
        return json.dumps(cleaned, separators=(",", ":")) if cleaned else None
    except (TypeError, ValueError):
        return None


def record_feedback(
    *,
    scan_id: str,
    correct: bool,
    corrected_class: str | None = None,
    note: str | None = None,
    client_ip: str | None = None,
    predicted_class: str | None = None,
    predicted_confidence: float | None = None,
    top3: Iterable[tuple[str, float]] | dict | str | None = None,
    model_version: str | None = None,
    app_version: str | None = None,
    locale: str | None = None,
    platform: str | None = None,
    user_pseudo_id: str | None = None,
    region_count: int | None = None,
    primary_region_index: int | None = None,
    feedback_latency_ms: int | None = None,
    reason: str | None = None,
    image_hash: str | None = None,
) -> int:
    """Append a feedback row. Returns the new row id."""
    init_db()
    now = int(time.time())
    top3_json = _normalise_top3(top3)
    ip_hash = hash_client_ip(client_ip)
    with _write_lock, _connect() as conn:
        cur = conn.execute(
            "INSERT INTO feedback ("
            "  scan_id, correct, corrected_class, note, client_ip, created_at, "
            "  predicted_class, predicted_confidence, top3_json, model_version, "
            "  app_version, locale, platform, user_pseudo_id, region_count, "
            "  primary_region_index, feedback_latency_ms, reason, image_hash, "
            "  client_ip_hash"
            ") VALUES ("
            "  ?, ?, ?, ?, ?, ?,"
            "  ?, ?, ?, ?,"
            "  ?, ?, ?, ?, ?,"
            "  ?, ?, ?, ?,"
            "  ?"
            ")",
            (
                scan_id,
                1 if correct else 0,
                corrected_class,
                note,
                # Keep the raw IP column NULL going forward — only the
                # hashed value is persisted. Existing rows with the
                # raw IP stay untouched.
                None,
                now,
                predicted_class,
                predicted_confidence,
                top3_json,
                model_version,
                app_version,
                locale,
                platform,
                user_pseudo_id,
                region_count,
                primary_region_index,
                feedback_latency_ms,
                reason,
                image_hash,
                ip_hash,
            ),
        )
        return int(cur.lastrowid or 0)


def record_issued_scan_id(scan_id: str) -> None:
    """Mark a scan_id as legitimately issued by /api/analyze.

    Idempotent: a duplicate insert is silently ignored so a retried
    analyse call can't fail on the dup-key. The companion
    ``is_scan_id_issued`` check is what the feedback endpoint uses.
    """
    if not scan_id:
        return
    init_db()
    now = int(time.time())
    with _write_lock, _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO issued_scan_ids (scan_id, created_at) "
            "VALUES (?, ?)",
            (scan_id, now),
        )


def is_scan_id_issued(scan_id: str, *, ttl_days: int = 30) -> bool:
    """True when ``scan_id`` was issued by /api/analyze in the last
    ``ttl_days`` days. Returns False for empty strings, unknown ids,
    and ids older than the TTL.

    The TTL guard means a one-off leak of a scan_id can't be used
    to spray feedback for months — it expires.
    """
    if not scan_id:
        return False
    init_db()
    cutoff = int(time.time()) - ttl_days * 86_400
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM issued_scan_ids WHERE scan_id = ? AND created_at >= ?",
            (scan_id, cutoff),
        ).fetchone()
    return bool(row)


def prune_older_than(days: int) -> int:
    """Drop feedback rows older than ``days`` days. Returns the
    number of rows pruned. Intended to run from the in-process
    daily scheduler; never invoked from the request path. The
    append-only contract still holds for everything inside the
    retention window — we just keep the table from growing
    unbounded under spam.
    """
    init_db()
    if days <= 0:
        return 0
    cutoff = int(time.time()) - days * 86_400
    with _write_lock, _connect() as conn:
        deleted = conn.execute(
            "DELETE FROM feedback WHERE created_at < ?", (cutoff,),
        ).rowcount
        # Drop issued-scan-id rows past the same window. Their TTL is
        # the issuance-log TTL — keeping them indefinitely would
        # both grow the file unbounded and let a leaked scan_id be
        # reused months later.
        conn.execute(
            "DELETE FROM issued_scan_ids WHERE created_at < ?", (cutoff,),
        )
    return int(deleted or 0)


def daily_write_count_for_ip_hash(ip_hash: str | None) -> int:
    """Number of feedback rows the given IP hash has produced in the
    last 24 hours. Used by the per-IP daily quota guard in
    ``main.py`` so one client can't flood the corrections table.
    """
    if not ip_hash:
        return 0
    init_db()
    cutoff = int(time.time()) - 86_400
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM feedback "
            "WHERE client_ip_hash = ? AND created_at >= ?",
            (ip_hash, cutoff),
        ).fetchone()
    return int(row[0]) if row else 0


def feedback_counts() -> dict:
    """Aggregate counters for the /api/metrics surface.

    Returns:
      * ``total`` / ``correct`` / ``incorrect``  — append-only counters.
      * ``last_24h``                             — rows in the last day.
      * ``last_7d``                              — rows in the last week.
      * ``avg_latency_ms``                       — mean client-side latency
                                                   between scan + feedback.
      * ``per_class``                            — {class: {correct, total, accuracy}}
      * ``top_corrections``                      — {predicted_class: {corrected_class: count}}
                                                   (top 5 most common corrections only).
      * ``reasons``                              — {reason_label: count}.

    Counts only — no row contents are surfaced — so this stays safe to
    expose on a public metrics endpoint.
    """
    init_db()
    now = int(time.time())
    day_ago = now - 86_400
    week_ago = now - 7 * 86_400
    with _connect() as conn:
        row = conn.execute(
            "SELECT "
            "  COUNT(*) AS total, "
            "  COALESCE(SUM(correct), 0) AS correct, "
            "  COALESCE(SUM(1 - correct), 0) AS incorrect "
            "FROM feedback"
        ).fetchone()
        last_24h = conn.execute(
            "SELECT COUNT(*) FROM feedback WHERE created_at >= ?", (day_ago,)
        ).fetchone()[0]
        last_7d = conn.execute(
            "SELECT COUNT(*) FROM feedback WHERE created_at >= ?", (week_ago,)
        ).fetchone()[0]
        avg_latency = conn.execute(
            "SELECT AVG(feedback_latency_ms) FROM feedback "
            "WHERE feedback_latency_ms IS NOT NULL"
        ).fetchone()[0]
        per_class_rows = conn.execute(
            "SELECT predicted_class, "
            "       COUNT(*) AS total, "
            "       COALESCE(SUM(correct), 0) AS correct "
            "FROM feedback "
            "WHERE predicted_class IS NOT NULL "
            "GROUP BY predicted_class"
        ).fetchall()
        correction_rows = conn.execute(
            "SELECT predicted_class, corrected_class, COUNT(*) AS n "
            "FROM feedback "
            "WHERE correct = 0 "
            "  AND predicted_class IS NOT NULL "
            "  AND corrected_class IS NOT NULL "
            "GROUP BY predicted_class, corrected_class "
            "ORDER BY n DESC "
            "LIMIT 50"
        ).fetchall()
        reason_rows = conn.execute(
            "SELECT reason, COUNT(*) AS n "
            "FROM feedback "
            "WHERE reason IS NOT NULL "
            "GROUP BY reason "
            "ORDER BY n DESC"
        ).fetchall()

    per_class = {
        str(r[0]): {
            "total": int(r[1]),
            "correct": int(r[2]),
            "accuracy": round(int(r[2]) / int(r[1]), 4) if int(r[1]) else 0.0,
        }
        for r in per_class_rows
    }

    top_corrections: dict[str, dict[str, int]] = {}
    for predicted, corrected, n in correction_rows:
        pred_key = str(predicted)
        # Keep at most 5 corrections per predicted class.
        bucket = top_corrections.setdefault(pred_key, {})
        if len(bucket) < 5:
            bucket[str(corrected)] = int(n)

    reasons = {str(r[0]): int(r[1]) for r in reason_rows}

    return {
        "total": int(row[0]),
        "correct": int(row[1]),
        "incorrect": int(row[2]),
        "last_24h": int(last_24h),
        "last_7d": int(last_7d),
        "avg_latency_ms": round(float(avg_latency), 1) if avg_latency is not None else None,
        "per_class": per_class,
        "top_corrections": top_corrections,
        "reasons": reasons,
    }
