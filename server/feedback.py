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

import hashlib
import json
import logging
import secrets
import sqlite3
import threading
import time
from collections.abc import Iterable
from pathlib import Path

try:
    from .config import get_str  # type: ignore[import-not-found]
except ImportError:
    from config import get_str

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = Path(__file__).resolve().parent / "feedback.db"
FEEDBACK_DB_PATH = Path(get_str("FEEDBACK_DB_PATH", str(_DEFAULT_DB_PATH)))

# Disk root for image bytes attached to negative-feedback rows.
# Filename is the SHA-256 of the image bytes, suffixed with the
# detected extension, so multiple feedback rows pointing at the same
# image collapse to one on-disk artefact. Bytes are only ever stored
# when the user submits a ``correct=false`` feedback — see the
# /api/feedback/image endpoint in main.py.
_DEFAULT_IMG_DIR = Path(__file__).resolve().parent / "feedback_images"
FEEDBACK_IMG_DIR = Path(get_str("FEEDBACK_IMG_DIR", str(_DEFAULT_IMG_DIR)))

# Stable IP hashing pepper. The operator can rotate it to break correlation
# across deployments.
#
# There is deliberately NO hard-coded default. IPv4 is a 32-bit space, so an
# unsalted-or-publicly-salted SHA-256 of an IP is not a one-way token at all —
# the whole space enumerates in seconds, which made every stored
# ``client_ip_hash`` reversible to the raw IP for anyone who read this file.
# When FEEDBACK_IP_PEPPER is unset we mint a random pepper instead and persist
# it beside the DB, so the hash is genuinely irreversible without the operator
# having to configure anything (and without the server refusing to boot).
_generated_pepper: str | None = None
_pepper_lock = threading.Lock()


def _ip_hash_pepper() -> str:
    """The active pepper: configured value, else a persisted random one.

    Resolved per call rather than at import so a deployment (or a test) can set
    FEEDBACK_IP_PEPPER at any point and have it take effect.
    """
    configured = get_str("FEEDBACK_IP_PEPPER", "").strip()
    if configured:
        return configured
    global _generated_pepper
    with _pepper_lock:
        if _generated_pepper:
            return _generated_pepper
        path = FEEDBACK_DB_PATH.parent / ".ip_hash_pepper"
        try:
            if path.is_file():
                existing = path.read_text(encoding="utf-8").strip()
                if existing:
                    _generated_pepper = existing
                    return _generated_pepper
        except OSError:
            pass
        _generated_pepper = secrets.token_hex(32)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_generated_pepper, encoding="utf-8")
            path.chmod(0o600)
            logger.warning(
                "FEEDBACK_IP_PEPPER is not set; generated a random pepper and "
                "stored it at %s. Set FEEDBACK_IP_PEPPER explicitly to control "
                "rotation and to share one pepper across replicas.", path,
            )
        except OSError as exc:
            logger.error(
                "FEEDBACK_IP_PEPPER is not set and the generated pepper could "
                "not be persisted (%s); falling back to a process-lifetime "
                "pepper, so IP hashes will not correlate across restarts.", exc,
            )
        return _generated_pepper


_SCHEMA = """
CREATE TABLE IF NOT EXISTS feedback (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id               TEXT    NOT NULL,
    correct               INTEGER NOT NULL,
    corrected_class       TEXT,
    note                  TEXT,
    -- Raw IP: no write path sets it any more (only client_ip_hash is
    -- persisted); retained for existing databases and their historical rows.
    client_ip             TEXT,
    created_at            INTEGER NOT NULL,
    predicted_class       TEXT,
    predicted_confidence  REAL,
    top3_json             TEXT,
    model_version         TEXT,
    app_version           TEXT,
    locale                TEXT,
    platform              TEXT,
    user_pseudo_id        TEXT,
    region_count          INTEGER,
    primary_region_index  INTEGER,
    feedback_latency_ms   INTEGER,
    reason                TEXT,
    image_hash            TEXT,
    client_ip_hash        TEXT
);
CREATE INDEX IF NOT EXISTS ix_feedback_scan_id        ON feedback(scan_id);
CREATE INDEX IF NOT EXISTS ix_feedback_created        ON feedback(created_at);
CREATE INDEX IF NOT EXISTS ix_feedback_user_pseudo    ON feedback(user_pseudo_id);
CREATE INDEX IF NOT EXISTS ix_feedback_model_version  ON feedback(model_version);
CREATE INDEX IF NOT EXISTS ix_feedback_predicted_class ON feedback(predicted_class);

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
    """Create the schema if it doesn't already exist. Idempotent.

    Late-added columns (``out_of_head``, ``prediction_set_json``,
    ``image_path``) are applied via ``ALTER TABLE ADD COLUMN`` rather
    than a separate migration step — SQLite accepts the no-op when
    the column already exists, so the call is idempotent across
    fresh installs (where the column is missing) and rolling
    deployments (where prior schemas may already have it).
    """
    global _initialised
    with _init_lock:
        if _initialised:
            return
        FEEDBACK_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _connect() as conn:
            conn.executescript(_SCHEMA)
            _add_column_if_missing(conn, "feedback", "out_of_head", "INTEGER")
            _add_column_if_missing(
                conn, "feedback", "prediction_set_json", "TEXT",
            )
            _add_column_if_missing(conn, "feedback", "image_path", "TEXT")
        _initialised = True
        logger.info("Feedback DB ready at %s", FEEDBACK_DB_PATH)


def _add_column_if_missing(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    decl: str,
) -> None:
    """Idempotent ``ALTER TABLE ADD COLUMN``.

    SQLite has no ``ADD COLUMN IF NOT EXISTS`` so we probe the
    PRAGMA table_info first. Existing rows get NULL for the new
    column, which is what every late-added column here needs.
    """
    cols = {
        row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column in cols:
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def hash_client_ip(client_ip: str | None) -> str | None:
    """Return a peppered SHA-256 of the client IP, or None.

    We never store raw IPs on disk — only a short, irreversible token.
    Same IP + same pepper → same hash, so abuse-rate analysis still
    works without keeping PII at rest.
    """
    if not client_ip:
        return None
    digest = hashlib.sha256(
        f"{_ip_hash_pepper()}|{client_ip}".encode()
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
    out_of_head: bool | None = None,
    prediction_set: Iterable[str] | None = None,
) -> int:
    """Append a feedback row. Returns the new row id.

    ``out_of_head`` and ``prediction_set`` are late-added columns:
    ``out_of_head`` flags a correction whose class isn't in the
    production head (retraining uses it to prioritise label-set
    expansion), and ``prediction_set`` is the conformal set the
    classifier returned — persisting it lets us answer "was the
    correct answer in the 90 % set?" after the fact.
    """
    init_db()
    now = int(time.time())
    top3_json = _normalise_top3(top3)
    ip_hash = hash_client_ip(client_ip)
    prediction_set_json = _normalise_prediction_set(prediction_set)
    out_of_head_int: int | None = None
    if out_of_head is not None:
        out_of_head_int = 1 if out_of_head else 0
    with _write_lock, _connect() as conn:
        cur = conn.execute(
            # The legacy raw-IP column is deliberately left out of the column
            # list: new rows leave it NULL and only client_ip_hash is stored.
            "INSERT INTO feedback ("
            "  scan_id, correct, corrected_class, note, created_at, "
            "  predicted_class, predicted_confidence, top3_json, model_version, "
            "  app_version, locale, platform, user_pseudo_id, region_count, "
            "  primary_region_index, feedback_latency_ms, reason, image_hash, "
            "  client_ip_hash, out_of_head, prediction_set_json"
            ") VALUES ("
            "  ?, ?, ?, ?, ?,"
            "  ?, ?, ?, ?,"
            "  ?, ?, ?, ?, ?,"
            "  ?, ?, ?, ?,"
            "  ?, ?, ?"
            ")",
            (
                scan_id,
                1 if correct else 0,
                corrected_class,
                note,
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
                out_of_head_int,
                prediction_set_json,
            ),
        )
        return int(cur.lastrowid or 0)


def _normalise_prediction_set(
    prediction_set: Iterable[str] | None,
) -> str | None:
    """Compact-JSON of the conformal prediction set.

    Accepts an iterable of class keys. Returns ``None`` for empty
    inputs so the column stays NULL when the upstream call had no
    set to record.
    """
    if prediction_set is None:
        return None
    cleaned = [
        str(c)[:64] for c in prediction_set
        if isinstance(c, str) and c
    ][:16]
    if not cleaned:
        return None
    return json.dumps(cleaned, separators=(",", ":"))


def image_ext_from_magic(data: bytes) -> str | None:
    """File extension for a raster image, sniffed from the leading bytes.

    ``None`` for anything not recognised — which is the point. The extension
    used to come from the request's ``Content-Type``, i.e. from the client,
    with a ``.bin`` fallback for unknown types; between them that accepted
    ARBITRARY bytes into the retraining corpus under a filename of the
    sender's choosing. The bytes decide now.
    """
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:2] == b"BM":
        return "bmp"
    return None


def save_feedback_image(
    image_bytes: bytes,
    *,
    content_type: str | None = None,
    max_bytes: int = 4 * 1024 * 1024,
) -> tuple[str, str] | None:
    """Persist the bytes of a negative-feedback image under
    ``FEEDBACK_IMG_DIR``. Returns ``(filename, sha256_hex)`` on
    success, or ``None`` when the bytes are empty / oversized / not a
    recognised image / fail to write.

    The filename is ``<sha256>.<ext>`` with ``<ext>`` sniffed from the bytes
    themselves (see :func:`image_ext_from_magic`); the hash collapses
    duplicate uploads to a single artefact. ``content_type`` is accepted for
    call-compatibility and logged on a mismatch, but is never trusted to
    name the file.
    """
    if not image_bytes:
        return None
    if len(image_bytes) > max_bytes:
        logger.info(
            "Feedback image rejected (oversized: %d bytes)", len(image_bytes),
        )
        return None
    ext = image_ext_from_magic(image_bytes)
    if ext is None:
        logger.info(
            "Feedback image rejected (not a JPEG/PNG/WebP/BMP; declared %r)",
            content_type,
        )
        return None
    digest = hashlib.sha256(image_bytes).hexdigest()
    filename = f"{digest}.{ext}"
    FEEDBACK_IMG_DIR.mkdir(parents=True, exist_ok=True)
    target = FEEDBACK_IMG_DIR / filename
    if target.exists():
        # Same bytes already on disk — skip the write but still
        # return the filename so the caller can backfill the row.
        return filename, digest
    try:
        target.write_bytes(image_bytes)
    except OSError as exc:
        logger.warning("Failed to write feedback image %s: %s", target, exc)
        return None
    return filename, digest


def attach_image_path(scan_id: str, image_path: str) -> bool:
    """Backfill ``image_path`` on the most recent feedback row for
    ``scan_id``. Returns True on a successful update, False when no
    matching row exists.

    Called from the /api/feedback/image upload endpoint after the
    image bytes have been written to disk. We update rather than
    insert because the JSON /api/feedback call lands first and we
    don't want a second row for the same scan.
    """
    if not scan_id or not image_path:
        return False
    init_db()
    with _write_lock, _connect() as conn:
        cur = conn.execute(
            "UPDATE feedback SET image_path = ? "
            "WHERE id = (SELECT id FROM feedback "
            "            WHERE scan_id = ? AND image_path IS NULL "
            "            ORDER BY created_at DESC LIMIT 1)",
            (image_path, scan_id),
        )
        return bool(cur.rowcount)


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


def latest_for_scan(scan_id: str) -> dict | None:
    """Most recent feedback row for a scan_id (read helper for the labeling
    wiring — lets the feedback-image path propose a candidate class)."""
    init_db()
    if not scan_id:
        return None
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM feedback WHERE scan_id = ? "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            (scan_id,),
        ).fetchone()
    return dict(row) if row else None
