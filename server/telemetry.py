"""
Client error/problem telemetry store for PlantVision.

Append-only SQLite at ``server/telemetry.db``. Each row is one client-side
*problem* — a handled error (analyze timeout, network failure, image decode,
on-device inference failure) or an uncaught crash — bundled with the small
diagnostic context the app collected around it (stage, kind, model family,
HTTP status, app version, Android API level, a short breadcrumb trail).

This is deliberately errors-only: the app sends nothing on the happy path and
"sends more when there's a problem". No images, no free text beyond a capped
message, no precise location — only enums/buckets + breadcrumbs. The only
identifier is a peppered IP hash used purely for the per-IP daily cap.

Threading model mirrors feedback.py: one connection per call, a module-level
lock serialising writes.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

try:
    from .config import get_str  # type: ignore[import-not-found]
except ImportError:
    from config import get_str

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = Path(__file__).resolve().parent / "telemetry.db"
TELEMETRY_DB_PATH = Path(get_str("TELEMETRY_DB_PATH", str(_DEFAULT_DB_PATH)))

# Field length caps — a report is diagnostic context, never a payload.
MAX_MESSAGE_LEN = 2000
MAX_BREADCRUMB_LEN = 200
MAX_BREADCRUMBS = 40

_write_lock = threading.RLock()
_initialised = False

_SCHEMA = """
CREATE TABLE IF NOT EXISTS error_reports (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at     INTEGER NOT NULL,
    stage          TEXT    NOT NULL,
    kind           TEXT    NOT NULL,
    fatal          INTEGER NOT NULL DEFAULT 0,
    http_status    INTEGER,
    family         TEXT,
    app_version    TEXT,
    android_api    INTEGER,
    message        TEXT,
    breadcrumbs    TEXT,
    client_ip_hash TEXT
);
CREATE INDEX IF NOT EXISTS ix_telemetry_created ON error_reports(created_at);
CREATE INDEX IF NOT EXISTS ix_telemetry_stage   ON error_reports(stage);
CREATE INDEX IF NOT EXISTS ix_telemetry_kind    ON error_reports(kind);
"""


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(TELEMETRY_DB_PATH), check_same_thread=False, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    global _initialised
    if _initialised:
        return
    TELEMETRY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _write_lock, _connect() as conn:
        conn.executescript(_SCHEMA)
    _initialised = True


def _clean_breadcrumbs(breadcrumbs: list | None) -> str | None:
    """Trim the breadcrumb trail to a bounded, string-only JSON array."""
    if not breadcrumbs:
        return None
    trail = [str(b)[:MAX_BREADCRUMB_LEN] for b in breadcrumbs[:MAX_BREADCRUMBS]]
    return json.dumps(trail, ensure_ascii=False)


def record_error(
    *,
    stage: str,
    kind: str,
    fatal: bool = False,
    http_status: int | None = None,
    family: str | None = None,
    app_version: str | None = None,
    android_api: int | None = None,
    message: str | None = None,
    breadcrumbs: list | None = None,
    client_ip_hash: str | None = None,
) -> int | None:
    """Append one error report. Returns the row id, or None on a failed write
    (recording must never raise into the request path)."""
    init_db()
    now = int(time.time())
    msg = (message or "").strip()[:MAX_MESSAGE_LEN] or None
    try:
        with _write_lock, _connect() as conn:
            cur = conn.execute(
                "INSERT INTO error_reports "
                "(created_at, stage, kind, fatal, http_status, family, app_version, "
                " android_api, message, breadcrumbs, client_ip_hash) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    now, stage[:64], kind[:64], 1 if fatal else 0, http_status,
                    (family or None), (app_version or None), android_api, msg,
                    _clean_breadcrumbs(breadcrumbs), client_ip_hash,
                ),
            )
            return int(cur.lastrowid)
    except Exception as exc:  # noqa: BLE001 — telemetry must never break the endpoint
        logger.warning("Telemetry write failed: %s", exc)
        return None


def daily_writes(ip_hash: str | None) -> int:
    """Reports from one IP hash since UTC midnight (per-IP daily-cap check)."""
    if not ip_hash:
        return 0
    init_db()
    midnight = int(time.time()) // 86400 * 86400
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM error_reports "
            "WHERE client_ip_hash = ? AND created_at >= ?",
            (ip_hash, midnight),
        ).fetchone()
    return int(row["n"])


def prune_older_than(days: int) -> int:
    """Drop reports older than ``days``. Returns the row count pruned."""
    init_db()
    cutoff = int(time.time()) - days * 86400
    with _write_lock, _connect() as conn:
        cur = conn.execute("DELETE FROM error_reports WHERE created_at < ?", (cutoff,))
        return int(cur.rowcount)
