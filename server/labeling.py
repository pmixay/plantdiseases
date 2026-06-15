"""
Human data-labeling store + session auth + Kaggle export bundle.

A "Tinder-style" admin tool fed from three prioritised sources: user feedback
(``source='user_feedback'``, top priority), dataset candidates ingested from
the maintainer's Kaggle supplement (``ingest`` package), and recorded
anonymized user scans (``source='user_scan'``, deferred last). The model
auto-routes the confident majority (``auto_yes``/``auto_no``); admins verify
the uncertain tail (yes / no / not_sure) from the app, and verified images
export as a Kaggle-ready bundle for the next dataset version. See
``docs/DATASET_LABELING_PLAN.md`` for the why and the acceptance/leakage
gates this operationalises.

Storage mirrors ``feedback.py`` / ``community.py`` exactly: a module-level
``_write_lock`` (RLock) serialises writers, short-lived WAL connections, an
idempotent ``init_db`` and ``INSERT OR IGNORE`` for idempotency. Concurrency
across multiple admins is handled by an atomic claim/lease (``claim_next``):
the SELECT+UPDATE runs inside one held write lock so two admins can never claim
the same card, and abandoned claims requeue after a lease TTL.

Auth is a single shared admin password (no per-admin identity) exchanged for a
stateless HMAC-signed session token. The token's ``session_id`` is an ephemeral
value used ONLY for claim de-duplication, never for attribution.
"""

from __future__ import annotations

import csv
import hmac
import io
import json
import secrets
import sqlite3
import threading
import time
import zipfile
from hashlib import sha256
from pathlib import Path

try:  # package vs. script import (mirrors main.py / feedback.py)
    from .config import get_int, get_str
except ImportError:  # pragma: no cover - exercised only in script mode
    from config import get_int, get_str

try:
    from .feedback import hash_client_ip
except ImportError:  # pragma: no cover
    from feedback import hash_client_ip


# ── Paths & config ──────────────────────────────────────────────────
_MODULE_DIR = Path(__file__).resolve().parent
LABELING_DB_PATH = Path(get_str("LABELING_DB_PATH", "") or str(_MODULE_DIR / "labeling.db"))
LABELING_IMG_DIR = Path(get_str("LABELING_IMG_DIR", "") or str(_MODULE_DIR / "labeling_images"))
LABELING_EXPORT_DIR = Path(
    get_str("LABELING_EXPORT_DIR", "") or str(_MODULE_DIR / "labeling_exports")
)

# Authentication / session
_SESSION_TTL = get_int("LABELING_SESSION_TTL_SECONDS", 28800)  # 8h
_CLAIM_LEASE = get_int("LABELING_CLAIM_LEASE_SECONDS", 900)  # 15m
_LOGIN_MAX_ATTEMPTS = get_int("LABELING_LOGIN_MAX_ATTEMPTS", 10)
_LOGIN_WINDOW = get_int("LABELING_LOGIN_WINDOW_SECONDS", 300)


def _admin_password() -> str:
    """Read live so tests / config reloads see changes without re-import."""
    return get_str("LABELING_ADMIN_PASSWORD", "")


def _session_secret() -> bytes:
    """HMAC signing key. A blank config value falls back to a per-process
    random secret (sessions then don't survive a restart — acceptable)."""
    configured = get_str("LABELING_SESSION_SECRET", "")
    if configured:
        return configured.encode()
    return _PROCESS_SECRET


_PROCESS_SECRET = secrets.token_bytes(32)

VERDICTS = ("yes", "no", "not_sure")
CLAIM_PREFIX = {  # export filename prefixes per class
    "nutrient_deficiency": "nd",
    "leaf_scorch_underwatering": "ls",
    "gray_mold_botrytis": "gm",
    "powdery_mildew": "pm",
    "viral_disease": "vd",
    "leaf_spot": "lsp",
    "pests": "pe",
    "healthy": "he",
    "not_a_plant": "nap",
}


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

CREATE TABLE IF NOT EXISTS verdicts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL,
    verdict      TEXT    NOT NULL,
    session_id   TEXT,
    created_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_verdicts_candidate ON verdicts(candidate_id);
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
        LABELING_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
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
            # Audit sampling: a deterministic fraction of would-be auto_yes /
            # auto_no dataset candidates is routed to the human queue with
            # audit=1 so confident model calls get spot-checked.
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
            # Tiny key-value table (e.g. last ingested Kaggle dataset version).
            conn.execute(
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"
            )
        _initialised = True


# Statuses set by a human verdict — these rows are never auto-rerouted,
# pruned, or overridden by feedback.
HUMAN_STATUSES = VERDICTS

# Card-source groups (admin queue priority order: feedback → dataset → scans).
SCAN_SOURCES = ("user_scan", "user_feedback")


# main.py imports this under the init_*_db convention.
init_labeling_db = init_db


# ── Meta key-value store ─────────────────────────────────────────────
def get_meta(key: str) -> str | None:
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(key: str, value: str) -> None:
    init_db()
    with _write_lock, _connect() as conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )


# ── Targets / supply accounting ─────────────────────────────────────
def _validated_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT class, COUNT(*) AS n FROM candidates "
        "WHERE status IN ('yes','auto_yes') GROUP BY class"
    ).fetchall()
    return {r["class"]: int(r["n"]) for r in rows}


# ── Ingestion writes ────────────────────────────────────────────────
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


def existing_phashes() -> list[str]:
    """All stored phashes — seed the in-memory near-dup set before a run."""
    with _connect() as conn:
        return [
            r["phash"]
            for r in conn.execute(
                "SELECT phash FROM candidates WHERE phash IS NOT NULL"
            ).fetchall()
        ]


# ── Claim / lease (the concurrency core) ────────────────────────────
def requeue_expired_claims(lease_seconds: int = _CLAIM_LEASE) -> int:
    """Return abandoned ``claimed`` rows to ``pending`` once their lease lapses."""
    init_db()
    cutoff = int(time.time()) - int(lease_seconds)
    with _write_lock, _connect() as conn:
        cur = conn.execute(
            "UPDATE candidates SET status='pending', claimed_by_session=NULL, "
            "claimed_at=NULL WHERE status='claimed' AND claimed_at < ?",
            (cutoff,),
        )
        return int(cur.rowcount or 0)


def _eligible_classes(conn: sqlite3.Connection, targets: dict[str, int]) -> list[str]:
    """Weighted (target>0) classes that are NOT yet satisfied."""
    validated = _validated_counts(conn)
    out = []
    for cls, target in targets.items():
        t = int(target or 0)
        if t > 0 and int(validated.get(cls, 0)) < t:
            out.append(cls)
    return out


# `?source=` group filters for claim_next / the admin UI chips.
SOURCE_GROUPS = ("feedback", "dataset", "scans")


def claim_next(
    session_id: str, count: int, targets: dict[str, int],
    classes: list[str] | None = None, lease_seconds: int = _CLAIM_LEASE,
    source: str | None = None,
) -> list[dict]:
    """Atomically lease up to ``count`` pending cards for this session.

    Cards come from three groups served in priority order — user feedback
    first, dataset candidates second, recorded user scans deferred last.
    Target gating (skip satisfied / weight-0 classes) applies ONLY to the
    dataset group: a feedback correction or a suspicious scan for a satisfied
    class must still surface. ``classes`` (a single chosen class, or list)
    narrows the pool; ``source`` ('feedback'|'dataset'|'scans') restricts to
    one group; ``None`` = all groups in priority order.
    """
    init_db()
    count = max(1, min(int(count), 100))
    now = int(time.time())
    with _write_lock, _connect() as conn:
        # lazy reclaim first so a fresh request can re-serve abandoned cards
        conn.execute(
            "UPDATE candidates SET status='pending', claimed_by_session=NULL, "
            "claimed_at=NULL WHERE status='claimed' AND claimed_at < ?",
            (now - int(lease_seconds),),
        )
        eligible = _eligible_classes(conn, targets)
        params: list = []
        # Per-group predicates. Dataset rows stay target-gated; feedback /
        # scan rows bypass gating (they carry their own review value).
        feedback_pred = "source='user_feedback'"
        scans_pred = "source='user_scan'"
        if eligible:
            ph = ",".join("?" * len(eligible))
            dataset_pred = (
                f"(source NOT IN ('user_scan','user_feedback') AND class IN ({ph}))"
            )
            dataset_params = list(eligible)
        else:
            dataset_pred, dataset_params = "0", []
        if source == "feedback":
            where, params = feedback_pred, []
        elif source == "scans":
            where, params = scans_pred, []
        elif source == "dataset":
            where, params = dataset_pred, dataset_params
        else:
            where = f"({feedback_pred} OR {dataset_pred} OR {scans_pred})"
            params = dataset_params
        if classes:
            wanted = [classes] if isinstance(classes, str) else list(classes)
            ph = ",".join("?" * len(wanted))
            where = f"({where}) AND class IN ({ph})"
            params += wanted
        rows = conn.execute(
            f"SELECT id FROM candidates WHERE status='pending' AND {where} "
            "ORDER BY CASE source WHEN 'user_feedback' THEN 0 "
            "                     WHEN 'user_scan' THEN 2 ELSE 1 END, "
            "created_at LIMIT ?",
            (*params, count),
        ).fetchall()
        ids = [int(r["id"]) for r in rows]
        if not ids:
            return []
        idph = ",".join("?" * len(ids))
        conn.execute(
            f"UPDATE candidates SET status='claimed', claimed_by_session=?, claimed_at=? "
            f"WHERE id IN ({idph}) AND status='pending'",
            (session_id, now, *ids),
        )
        claimed = conn.execute(
            f"SELECT id, class, source, source_url, model_pred, model_conf, model_set, "
            f"feedback_note, user_correct, audit "
            f"FROM candidates WHERE id IN ({idph}) "
            "ORDER BY CASE source WHEN 'user_feedback' THEN 0 "
            "                     WHEN 'user_scan' THEN 2 ELSE 1 END, created_at",
            ids,
        ).fetchall()
        out = []
        for r in claimed:
            try:
                mset = json.loads(r["model_set"]) if r["model_set"] else []
            except Exception:  # noqa: BLE001
                mset = []
            out.append({
                "id": int(r["id"]),
                "class": r["class"],
                "suggested_class": r["class"],
                "source": r["source"],
                "source_url": r["source_url"],
                "image_url": f"/api/label/image/{int(r['id'])}",
                "model_pred": r["model_pred"],
                "model_conf": r["model_conf"],
                "model_set": mset,
                # user-feedback context (None for dataset/scan cards): lets the
                # admin card show "user proposed X / model said Y" + the note.
                "scan_feedback": (
                    None if r["user_correct"] is None else bool(r["user_correct"])
                ),
                "feedback_note": r["feedback_note"],
                # audit=true ⇒ this is a spot-check of a confident model call.
                "audit": bool(r["audit"]),
            })
        return out


def record_verdicts(session_id: str, items: list[tuple[int, str]]) -> int:
    """Resolve only the caller's still-claimed cards. Returns rows applied."""
    init_db()
    now = int(time.time())
    applied = 0
    with _write_lock, _connect() as conn:
        for cid, verdict in items:
            if verdict not in VERDICTS:
                continue
            cur = conn.execute(
                "UPDATE candidates SET status=? WHERE id=? AND claimed_by_session=? "
                "AND status='claimed'",
                (verdict, int(cid), session_id),
            )
            if cur.rowcount:
                conn.execute(
                    "INSERT INTO verdicts (candidate_id, verdict, session_id, created_at) "
                    "VALUES (?,?,?,?)",
                    (int(cid), verdict, session_id, now),
                )
                applied += 1
    return applied


def pending_candidates(limit: int = 500) -> list[dict]:
    """Pending (human-queue) rows for re-scoring against the current model."""
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, class, image_path FROM candidates WHERE status='pending' "
            "ORDER BY id LIMIT ?",
            (int(limit),),
        ).fetchall()
    return [dict(r) for r in rows]


def apply_auto_status(
    candidate_id: int, status: str, pred: str | None,
    conf: float | None, mset: list[str] | None,
) -> bool:
    """Re-route a still-pending candidate after a model re-score. Only touches
    rows still ``pending`` (never overrides a human verdict)."""
    set_json = json.dumps(mset) if mset is not None else None
    with _write_lock, _connect() as conn:
        cur = conn.execute(
            "UPDATE candidates SET status=?, model_pred=?, model_conf=?, model_set=? "
            "WHERE id=? AND status='pending'",
            (status, pred, conf, set_json, int(candidate_id)),
        )
        return bool(cur.rowcount)


def image_path_for(candidate_id: int) -> Path | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT image_path FROM candidates WHERE id=?", (int(candidate_id),)
        ).fetchone()
    if not row:
        return None
    p = LABELING_IMG_DIR / row["image_path"]
    return p if p.is_file() else None


# ── Stats ───────────────────────────────────────────────────────────
def stats(targets: dict[str, int]) -> dict:
    """Per-class merged counts (train/val split is deferred) + goal + satisfied."""
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT class, status, COUNT(*) AS n FROM candidates GROUP BY class, status"
        ).fetchall()
    grid: dict[str, dict[str, int]] = {}
    for r in rows:
        grid.setdefault(r["class"], {})[r["status"]] = int(r["n"])
    per_class: dict[str, dict] = {}
    totals = {"validated": 0, "pending": 0, "target": 0}
    for cls, target in targets.items():
        t = int(target or 0)
        g = grid.get(cls, {})
        auto_yes = g.get("auto_yes", 0)
        human_yes = g.get("yes", 0)
        validated = auto_yes + human_yes
        pending = g.get("pending", 0)
        per_class[cls] = {
            "weight": t,
            "validated": validated,        # approved = model auto-accept + human yes
            "auto_yes": auto_yes,          # model-trusted
            "human_yes": human_yes,        # human-confirmed (the confusing tail)
            "pending": pending,            # confusing, awaiting a human
            "claimed": g.get("claimed", 0),
            "auto_no": g.get("auto_no", 0),
            "no": g.get("no", 0),
            "not_sure": g.get("not_sure", 0),
            "satisfied": (t > 0 and validated >= t),
            "remaining": max(t - validated, 0),
        }
        totals["validated"] += validated
        totals["pending"] += pending
        totals["target"] += t
    # Additive per-source-group block (feedback / dataset / scans). The
    # pre-existing shape above is unchanged for Android back-compat.
    with _connect() as conn:
        srows = conn.execute(
            "SELECT CASE source WHEN 'user_feedback' THEN 'feedback' "
            "                   WHEN 'user_scan' THEN 'scans' ELSE 'dataset' END AS grp, "
            "status, COUNT(*) AS n FROM candidates GROUP BY grp, status"
        ).fetchall()
    sources: dict[str, dict[str, int]] = {
        g: {"pending": 0, "claimed": 0, "resolved": 0} for g in SOURCE_GROUPS
    }
    for r in srows:
        g = sources.setdefault(
            r["grp"], {"pending": 0, "claimed": 0, "resolved": 0}
        )
        if r["status"] in ("pending", "claimed"):
            g[r["status"]] += int(r["n"])
        else:
            g["resolved"] += int(r["n"])
    return {
        "per_class": per_class,
        "totals": totals,
        "sources": sources,
        "feedback_pending": sources["feedback"]["pending"],
        "dataset_pending": sources["dataset"]["pending"],
        "scan_pending": sources["scans"]["pending"],
    }


# ── Login throttle (mirror main.py abuse machinery, kept local) ─────
_login_lock = threading.Lock()
_login_attempts: dict[str, tuple[float, int]] = {}  # ip_hash -> (window_reset, count)


def login_allowed(client_ip: str | None) -> bool:
    """Token-bucket-ish login throttle by hashed IP. False ⇒ over limit (429)."""
    ident = hash_client_ip(client_ip) or "unknown"
    now = time.time()
    with _login_lock:
        reset, count = _login_attempts.get(ident, (now + _LOGIN_WINDOW, 0))
        if now > reset:
            reset, count = now + _LOGIN_WINDOW, 0
        count += 1
        _login_attempts[ident] = (reset, count)
        return count <= _LOGIN_MAX_ATTEMPTS


# ── Session auth (shared password → stateless signed token) ─────────
def feature_enabled() -> bool:
    try:
        from .config import get_bool
    except ImportError:  # pragma: no cover
        from config import get_bool
    return get_bool("LABELING_ENABLED", True)


def verify_admin_password(supplied: str | None) -> bool:
    pw = _admin_password()
    if not pw or not feature_enabled():
        return False  # blank password / disabled feature ⇒ no access
    return hmac.compare_digest((supplied or "").encode(), pw.encode())


def _sign(payload: str) -> str:
    return hmac.new(_session_secret(), payload.encode(), sha256).hexdigest()


def issue_session_token() -> tuple[str, int]:
    sid = secrets.token_urlsafe(16)
    exp = int(time.time()) + _SESSION_TTL
    payload = f"{sid}.{exp}"
    return f"{payload}.{_sign(payload)}", exp


def verify_session_token(token: str | None) -> str | None:
    """Return the session_id if the token is well-formed, unexpired and signed."""
    if not token or token.count(".") != 2:
        return None
    sid, exp, sig = token.split(".")
    payload = f"{sid}.{exp}"
    if not hmac.compare_digest(sig, _sign(payload)):
        return None
    try:
        if int(exp) < int(time.time()):
            return None
    except ValueError:
        return None
    return sid


# ── Kaggle export bundle ────────────────────────────────────────────
# The bundle is BOTH the next dataset version's payload and the offline
# collection workflow's reference set: ``sha256``/``phash`` columns act as a
# dedup index (the scraper never re-collects what's already verified) and the
# verified positives (+ optional hard negatives) serve as reference exemplars
# for the offline vision-agent judge. No scan_id / feedback text / user data
# ever reaches a bundle.

# Calibration fraction of human-verified PRODUCTION items (real phone photos
# are exactly what temperature scaling + RAPS conformal calibration need).
_CALIBRATION_PCT = 20


def _verified_by(row: dict) -> str:
    if row.get("status") == "auto_yes":
        return "model+user" if row.get("user_correct") == 1 else "model"
    return "human"


# Strongest provenance wins the near-dup sweep.
_PROVENANCE_RANK = {"human": 0, "model+user": 1, "model": 2}


def _split_for(row: dict) -> str:
    """Eval-integrity rule, made mechanical (DATASET_LABELING_PLAN.md):
    auto-labeled rows (any ``auto_yes`` — the model grading itself) are
    train-ONLY. Held-out splits draw exclusively from human-verified rows;
    a deterministic ~20% (by sha) of human-verified production scans goes to
    ``calibration``, the rest of the human-verified rows to ``eval``."""
    if row.get("status") == "auto_yes":
        return "train"
    if row.get("source") in SCAN_SOURCES:
        bucket = int((row.get("sha") or "0" * 8)[:8], 16) % 100
        if bucket < _CALIBRATION_PCT:
            return "calibration"
    return "eval"


def approved_for_export() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, class, image_path, source, source_url, subject_plant, "
            "status, model_pred, model_conf, sha, phash, user_correct "
            "FROM candidates WHERE status IN ('yes','auto_yes') ORDER BY class, id"
        ).fetchall()
    return [dict(r) for r in rows]


def hard_negatives_for_export() -> list[dict]:
    """Human-``no`` rows: confirmed false positives of their proposed class —
    reference *rejects* for the offline vision-agent judge."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, class, image_path, source, source_url, subject_plant, "
            "status, model_pred, model_conf, sha, phash, user_correct "
            "FROM candidates WHERE status='no' ORDER BY class, id"
        ).fetchall()
    return [dict(r) for r in rows]


def _near_dup_sweep(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Drop pHash near-dups (Hamming ≤ 6) at bundle build, keeping the row
    with the strongest provenance (human > model+user > model). Returns
    ``(kept, skipped)``. Rows stay in the DB — this only shapes the bundle."""
    try:
        import imagehash
    except Exception:  # noqa: BLE001 - declared dep; degrade to no sweep
        return rows, []
    ordered = sorted(
        rows, key=lambda r: (_PROVENANCE_RANK.get(_verified_by(r), 3), r["id"]),
    )
    kept: list[dict] = []
    kept_hashes: list = []
    skipped: list[dict] = []
    for r in ordered:
        ph = r.get("phash")
        h = None
        if ph:
            try:
                h = imagehash.hex_to_hash(ph)
            except Exception:  # noqa: BLE001
                h = None
        if h is not None and any(h - k <= 6 for k in kept_hashes):
            skipped.append(r)
            continue
        kept.append(r)
        if h is not None:
            kept_hashes.append(h)
    kept.sort(key=lambda r: (r["class"], r["id"]))
    return kept, skipped


def build_export_bundle(slug: str, include_hard_negatives: bool = False) -> Path | None:
    """Zip all approved images as ``<class>/<prefix>_NNNN.jpg`` + manifest.csv +
    dataset-metadata.json. With ``include_hard_negatives`` a
    ``hard_negatives/<proposed_class>/`` tree of human-rejected rows is added
    (flagged ``split='hard_negative'`` in the manifest) — excluded by default
    so training bundles stay positives-only. Returns the zip path, or None if
    nothing approved."""
    init_db()
    rows = approved_for_export()
    if not rows:
        return None
    rows, skipped = _near_dup_sweep(rows)
    negatives = hard_negatives_for_export() if include_hard_negatives else []
    ts = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    bundle = LABELING_EXPORT_DIR / f"labeling_export_{ts}.zip"
    LABELING_EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    manifest = io.StringIO()
    writer = csv.writer(manifest)
    writer.writerow(
        ["filename", "class", "source", "source_url", "subject_plant",
         "verified_by", "split", "sha256", "phash", "notes"]
    )

    def _note_for(r: dict) -> str:
        note = r.get("source") or ""
        if r.get("model_pred"):
            note = f"{note}; model={r.get('model_pred')}@{r.get('model_conf')}"
        return note

    per_class_n: dict[str, int] = {}
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as zf:
        for r in rows:
            cls = r["class"]
            src_img = LABELING_IMG_DIR / r["image_path"]
            if not src_img.is_file():
                continue
            per_class_n[cls] = per_class_n.get(cls, 0) + 1
            prefix = CLAIM_PREFIX.get(cls, cls[:3])
            ext = src_img.suffix.lower() or ".jpg"
            arc = f"{cls}/{prefix}_{per_class_n[cls]:04d}{ext}"
            zf.write(src_img, arc)
            writer.writerow(
                [arc, cls, r.get("source") or "", r.get("source_url") or "",
                 r.get("subject_plant") or "", _verified_by(r), _split_for(r),
                 r.get("sha") or "", r.get("phash") or "", _note_for(r)]
            )
        for r in negatives:
            cls = r["class"]
            src_img = LABELING_IMG_DIR / r["image_path"]
            if not src_img.is_file():
                continue
            key = f"hn_{cls}"
            per_class_n[key] = per_class_n.get(key, 0) + 1
            ext = src_img.suffix.lower() or ".jpg"
            arc = f"hard_negatives/{cls}/hn_{per_class_n[key]:04d}{ext}"
            zf.write(src_img, arc)
            writer.writerow(
                [arc, cls, r.get("source") or "", r.get("source_url") or "",
                 r.get("subject_plant") or "", "human", "hard_negative",
                 r.get("sha") or "", r.get("phash") or "", _note_for(r)]
            )
        zf.writestr("manifest.csv", manifest.getvalue())
        if skipped:
            sk = io.StringIO()
            sw = csv.writer(sk)
            sw.writerow(["class", "sha256", "phash", "verified_by", "reason"])
            for r in skipped:
                sw.writerow(
                    [r["class"], r.get("sha") or "", r.get("phash") or "",
                     _verified_by(r), "near_dup"]
                )
            zf.writestr("skipped_dups.csv", sk.getvalue())
        zf.writestr(
            "dataset-metadata.json",
            json.dumps(
                {
                    "title": "Houseplant Disease Eval Supplement",
                    "id": slug,
                    "licenses": [{"name": "CC0-1.0"}],
                },
                indent=2,
            ),
        )
    return bundle
