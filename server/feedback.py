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
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path

try:
    from .config import get_str  # type: ignore[import-not-found]
except ImportError:
    from config import get_str

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = Path(__file__).resolve().parent / "feedback.db"
FEEDBACK_DB_PATH = Path(get_str("FEEDBACK_DB_PATH", str(_DEFAULT_DB_PATH)))

_SCHEMA = """
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
"""

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


def init_db() -> None:
    """Create the schema if it doesn't already exist. Idempotent."""
    global _initialised
    with _init_lock:
        if _initialised:
            return
        FEEDBACK_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _connect() as conn:
            conn.executescript(_SCHEMA)
        _initialised = True
        logger.info("Feedback DB ready at %s", FEEDBACK_DB_PATH)


def record_feedback(
    *,
    scan_id: str,
    correct: bool,
    corrected_class: str | None = None,
    note: str | None = None,
    client_ip: str | None = None,
) -> int:
    """Append a feedback row. Returns the new row id."""
    init_db()
    now = int(time.time())
    with _write_lock, _connect() as conn:
        cur = conn.execute(
            "INSERT INTO feedback "
            "(scan_id, correct, corrected_class, note, client_ip, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                scan_id,
                1 if correct else 0,
                corrected_class,
                note,
                client_ip,
                now,
            ),
        )
        return int(cur.lastrowid or 0)


def feedback_counts() -> dict:
    """Return aggregate counters for the /api/metrics surface.

    Counts only — never returns row content, so this is safe to expose
    on a public metrics endpoint.
    """
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT "
            "  COUNT(*) AS total, "
            "  COALESCE(SUM(correct), 0) AS correct, "
            "  COALESCE(SUM(1 - correct), 0) AS incorrect "
            "FROM feedback"
        ).fetchone()
    total = int(row[0])
    correct = int(row[1])
    incorrect = int(row[2])
    return {
        "total": total,
        "correct": correct,
        "incorrect": incorrect,
    }
