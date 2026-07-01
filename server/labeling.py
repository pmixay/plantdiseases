"""
Anonymized production-scan + feedback collection store.

Records every kept ``/api/analyze`` scan (``source='user_scan'``) and the
false-positive corrections folded back from feedback (``source='user_feedback'``)
into one SQLite table, with the model's own prediction kept as a weak label
prior. There is **no** human admin review queue — the maintainer exports this
store (``tools/export_data.py``) and judges the images offline with the same
vision-agent that grades scraped images. See ``docs/DATASET_LABELING_PLAN.md``.

Storage mirrors ``feedback.py`` / ``community.py``: a module-level ``_write_lock``
(RLock) serialises writers, short-lived WAL connections, an idempotent
``init_db`` and ``INSERT OR IGNORE`` for idempotency. The only user linkage is
the response ``scan_id`` (the user↔scan_id join lives solely in feedback.db,
swept by the GDPR delete cascade).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from io import BytesIO
from pathlib import Path

try:  # package vs. script import (mirrors main.py / feedback.py)
    from .config import get_str
except ImportError:  # pragma: no cover - exercised only in script mode
    from config import get_str


# ── Paths & config ──────────────────────────────────────────────────
_MODULE_DIR = Path(__file__).resolve().parent
LABELING_DB_PATH = Path(get_str("LABELING_DB_PATH", "") or str(_MODULE_DIR / "labeling.db"))
LABELING_IMG_DIR = Path(get_str("LABELING_IMG_DIR", "") or str(_MODULE_DIR / "labeling_images"))


def phash_bytes(body: bytes) -> str | None:
    """Perceptual hash of JPEG bytes (near-dup signal stored with each
    candidate). Returns None if imagehash is unavailable or decode fails."""
    try:
        import imagehash
        from PIL import Image
    except Exception:  # noqa: BLE001 - declared deps; degrade to no phash
        return None
    try:
        return str(imagehash.phash(Image.open(BytesIO(body)).convert("RGB")))
    except Exception:  # noqa: BLE001
        return None

# Legacy human-verdict statuses — no new rows take these (the admin queue was
# removed), but existing DBs may carry them so mark_feedback still guards them.
VERDICTS = ("yes", "no", "not_sure")


# ── SQLite store (feedback.py idiom) ────────────────────────────────
_write_lock = threading.RLock()
_init_lock = threading.Lock()
_initialised = False

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    class              TEXT    NOT NULL,
    image_path         TEXT    NOT NULL,
    source             TEXT    NOT NULL,
    source_url         TEXT,
    subject_plant      TEXT,
    sha                TEXT    NOT NULL,
    phash              TEXT,
    status             TEXT    NOT NULL DEFAULT 'pending',
    claimed_by_session TEXT,
    claimed_at         INTEGER,
    created_at         INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_candidates_sha ON candidates(sha);
CREATE INDEX IF NOT EXISTS ix_candidates_class_status ON candidates(class, status);
CREATE INDEX IF NOT EXISTS ix_candidates_claim ON candidates(status, claimed_at);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(LABELING_DB_PATH), check_same_thread=False, isolation_level=None,
    )
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    return conn


def _add_column_if_missing(conn, table, column, decl) -> None:
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def init_db() -> None:
    """Create schema + image/export dirs. Idempotent."""
    global _initialised
    with _init_lock:
        if _initialised:
            return
        LABELING_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        LABELING_IMG_DIR.mkdir(parents=True, exist_ok=True)
        with _connect() as conn:
            conn.executescript(_SCHEMA)
            # Model-auto-labeling provenance (DATASET_LABELING_PLAN.md): added via
            # ALTER so rolling deploys with an existing DB migrate in place.
            _add_column_if_missing(conn, "candidates", "model_pred", "TEXT")
            _add_column_if_missing(conn, "candidates", "model_conf", "REAL")
            _add_column_if_missing(conn, "candidates", "model_set", "TEXT")
            # Production scan-recording loop: links a recorded candidate to the
            # scan_id minted by /api/analyze (the ONLY linkage — no user data
            # lives in this DB; the user↔scan_id join is in feedback.db).
            _add_column_if_missing(conn, "candidates", "scan_id", "TEXT")
            _add_column_if_missing(conn, "candidates", "feedback_note", "TEXT")
            _add_column_if_missing(conn, "candidates", "user_correct", "INTEGER")
            # Vestigial audit flag — kept for schema back-compat with existing
            # DBs; the dataset audit-sampling that populated it was removed, so
            # new rows leave it NULL.
            _add_column_if_missing(conn, "candidates", "audit", "INTEGER")
            # Partial unique index: one candidate per scan, and a race-safe
            # anchor for the feedback→candidate linkage.
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_candidates_scan "
                "ON candidates(scan_id) WHERE scan_id IS NOT NULL"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_candidates_source_status "
                "ON candidates(source, status)"
            )
        _initialised = True


# Statuses set by a human verdict — these rows are never auto-rerouted,
# pruned, or overridden by feedback.
HUMAN_STATUSES = VERDICTS

# main.py imports this under the init_*_db convention.
init_labeling_db = init_db


# ── Candidate writes ────────────────────────────────────────────────
def insert_candidate(
    *, cls: str, image_path: str, source: str, source_url: str | None,
    subject_plant: str | None, sha: str, phash: str | None,
    status: str = "pending", model_pred: str | None = None,
    model_conf: float | None = None, model_set: list[str] | None = None,
    scan_id: str | None = None, feedback_note: str | None = None,
    audit: bool = False,
) -> int | None:
    """Append a candidate at the given status (model auto-label or 'pending' for
    the human queue). ``INSERT OR IGNORE`` on the unique sha (and the partial
    unique scan_id index) makes ingestion idempotent (returns None on a
    duplicate)."""
    init_db()
    now = int(time.time())
    set_json = json.dumps(model_set) if model_set is not None else None
    with _write_lock, _connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO candidates "
            "(class, image_path, source, source_url, subject_plant, sha, phash, "
            " status, model_pred, model_conf, model_set, scan_id, feedback_note, "
            " audit, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cls, image_path, source, source_url, subject_plant, sha, phash,
             status, model_pred, model_conf, set_json, scan_id, feedback_note,
             1 if audit else None, now),
        )
        return int(cur.lastrowid) if cur.rowcount else None


def candidate_for_scan(scan_id: str) -> dict | None:
    """The recorded candidate row for a scan_id, or None."""
    if not scan_id:
        return None
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM candidates WHERE scan_id=?", (scan_id,)
        ).fetchone()
    return dict(row) if row else None


def mark_feedback(
    scan_id: str, correct: bool, corrected_class: str | None = None,
    note: str | None = None,
) -> bool:
    """Fold a user's thumbs-up / thumbs-down into the recorded scan candidate.

    Positive: stamp ``user_correct=1`` and promote a still-``pending`` row to
    ``auto_yes`` when the user confirms the model's own prediction (model+user
    agreement is trustworthy enough to skip the human queue).
    Negative: the canonical false-positive signal — re-open the candidate
    (even an ``auto_yes`` one) as top-priority ``pending`` under
    ``source='user_feedback'``, retargeted at the user's corrected class when
    given. Never overrides a resolved human verdict. Returns True when a
    candidate row was found (and updated)."""
    row = candidate_for_scan(scan_id)
    if row is None:
        return False
    if row["status"] in HUMAN_STATUSES:
        return True  # human already ruled — feedback noted nowhere, by design
    note = (note or "").strip() or None
    with _write_lock, _connect() as conn:
        if correct:
            conn.execute(
                "UPDATE candidates SET user_correct=1, "
                "feedback_note=COALESCE(?, feedback_note), "
                "status=CASE WHEN status='pending' AND model_pred=class "
                "            THEN 'auto_yes' ELSE status END "
                "WHERE scan_id=? AND status NOT IN ('yes','no','not_sure')",
                (note, scan_id),
            )
        else:
            conn.execute(
                "UPDATE candidates SET user_correct=0, source='user_feedback', "
                "status='pending', claimed_by_session=NULL, claimed_at=NULL, "
                "class=COALESCE(?, class), feedback_note=COALESCE(?, feedback_note) "
                "WHERE scan_id=? AND status NOT IN ('yes','no','not_sure')",
                (corrected_class, note, scan_id),
            )
    return True


def delete_candidates_for_scan_ids(scan_ids: list[str]) -> int:
    """GDPR cascade: remove candidates (rows + image files) recorded from the
    given scans. Returns the number of rows deleted."""
    ids = [s for s in (scan_ids or []) if s]
    if not ids:
        return 0
    init_db()
    deleted = 0
    with _write_lock, _connect() as conn:
        ph = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT id, image_path FROM candidates WHERE scan_id IN ({ph})", ids,
        ).fetchall()
        if rows:
            idph = ",".join("?" * len(rows))
            conn.execute(
                f"DELETE FROM candidates WHERE id IN ({idph})",
                [int(r["id"]) for r in rows],
            )
            deleted = len(rows)
    for r in rows:
        try:
            (LABELING_IMG_DIR / r["image_path"]).unlink(missing_ok=True)
        except OSError:
            pass
    return deleted


def count_user_scans_today(cls: str) -> int:
    """Recorded user scans of ``cls`` since UTC midnight (daily-cap check)."""
    init_db()
    midnight = int(time.time()) // 86400 * 86400
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM candidates "
            "WHERE source='user_scan' AND class=? AND created_at >= ?",
            (cls, midnight),
        ).fetchone()
    return int(row["n"])


def prune_user_scans(
    retention_days: int, max_images: int, weak_classes: set[str] | None = None,
) -> int:
    """Bound the recorded-scan footprint. Prune order protects hard cases:

    1. ``pending`` (suspicious) ``user_scan`` rows are dropped only once they
       age past ``retention_days`` — nobody reviewed them in time.
    2. If the ``user_scan`` row count still exceeds ``max_images``, the oldest
       redundant ``auto_yes`` rows go first — common classes before weak
       (target>0) classes.

    ``user_feedback`` rows and human-verdict rows are NEVER pruned: confirmed
    false positives and human decisions are the most valuable data we hold."""
    init_db()
    weak = set(weak_classes or ())
    cutoff = int(time.time()) - int(retention_days) * 86400
    doomed: list[sqlite3.Row] = []
    with _write_lock, _connect() as conn:
        doomed += conn.execute(
            "SELECT id, image_path FROM candidates WHERE source='user_scan' "
            "AND status='pending' AND created_at < ?",
            (cutoff,),
        ).fetchall()
        already = {int(r["id"]) for r in doomed}
        total = int(conn.execute(
            "SELECT COUNT(*) AS n FROM candidates WHERE source='user_scan'"
        ).fetchone()["n"]) - len(already)
        excess = total - int(max_images)
        if excess > 0:
            for weak_pass in (False, True):
                if excess <= 0:
                    break
                rows = conn.execute(
                    "SELECT id, image_path, class FROM candidates "
                    "WHERE source='user_scan' AND status='auto_yes' "
                    "ORDER BY created_at",
                ).fetchall()
                for r in rows:
                    if excess <= 0:
                        break
                    if int(r["id"]) in already or ((r["class"] in weak) is not weak_pass):
                        continue
                    doomed.append(r)
                    already.add(int(r["id"]))
                    excess -= 1
        if doomed:
            idph = ",".join("?" * len(doomed))
            conn.execute(
                f"DELETE FROM candidates WHERE id IN ({idph})",
                [int(r["id"]) for r in doomed],
            )
    for r in doomed:
        try:
            (LABELING_IMG_DIR / r["image_path"]).unlink(missing_ok=True)
        except OSError:
            pass
    return len(doomed)


