"""
Human data-labeling store + session auth + Kaggle export bundle.

A "Tinder-style" admin tool: the server ingests candidate images for the
classes that still need supply (``ingest`` package), admins verify each
candidate (yes / no / not_sure) from the app, and approved images export as a
Kaggle-ready bundle. See ``docs/DATASET_LABELING_PLAN.md`` for the why and the
acceptance/leakage gates this operationalises.

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
        _initialised = True


# Statuses that count as an approved (validated) image: a model auto-accept
# or a human "yes". Both feed the exported dataset.
APPROVED_STATUSES = ("yes", "auto_yes")


# main.py imports this under the init_*_db convention.
init_labeling_db = init_db


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
) -> int | None:
    """Append a candidate at the given status (model auto-label or 'pending' for
    the human queue). ``INSERT OR IGNORE`` on the unique sha makes ingestion
    idempotent (returns None on a duplicate)."""
    init_db()
    now = int(time.time())
    set_json = json.dumps(model_set) if model_set is not None else None
    with _write_lock, _connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO candidates "
            "(class, image_path, source, source_url, subject_plant, sha, phash, "
            " status, model_pred, model_conf, model_set, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (cls, image_path, source, source_url, subject_plant, sha, phash,
             status, model_pred, model_conf, set_json, now),
        )
        return int(cur.lastrowid) if cur.rowcount else None


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


def claim_next(
    session_id: str, count: int, targets: dict[str, int],
    classes: list[str] | None = None, lease_seconds: int = _CLAIM_LEASE,
) -> list[dict]:
    """Atomically lease up to ``count`` pending cards for this session.

    Skips satisfied / weight-0 classes. ``classes`` (a single chosen class, or
    list) narrows the pool; ``None``/empty = any eligible class ("random").
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
        if classes:
            wanted = {classes} if isinstance(classes, str) else set(classes)
            eligible = [c for c in eligible if c in wanted]
        if not eligible:
            return []
        placeholders = ",".join("?" * len(eligible))
        rows = conn.execute(
            f"SELECT id FROM candidates WHERE status='pending' AND class IN ({placeholders}) "
            "ORDER BY created_at LIMIT ?",
            (*eligible, count),
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
            f"SELECT id, class, source, source_url, model_pred, model_conf, model_set "
            f"FROM candidates WHERE id IN ({idph})",
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


def delete_candidate(candidate_id: int) -> None:
    """Remove a candidate + its image (used when a re-score yields auto_no)."""
    with _write_lock, _connect() as conn:
        row = conn.execute(
            "SELECT image_path FROM candidates WHERE id=?", (int(candidate_id),)
        ).fetchone()
        conn.execute("DELETE FROM candidates WHERE id=?", (int(candidate_id),))
    if row:
        try:
            (LABELING_IMG_DIR / row["image_path"]).unlink(missing_ok=True)
        except OSError:
            pass


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
    return {"per_class": per_class, "totals": totals}


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
def approved_for_export() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, class, image_path, source, source_url, subject_plant, "
            "status, model_pred, model_conf "
            "FROM candidates WHERE status IN ('yes','auto_yes') ORDER BY class, id"
        ).fetchall()
    return [dict(r) for r in rows]


def build_export_bundle(slug: str) -> Path | None:
    """Zip all approved images as ``<class>/<prefix>_NNNN.jpg`` + manifest.csv +
    dataset-metadata.json. Returns the zip path, or None if nothing approved."""
    init_db()
    rows = approved_for_export()
    if not rows:
        return None
    ts = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    bundle = LABELING_EXPORT_DIR / f"labeling_export_{ts}.zip"
    LABELING_EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    manifest = io.StringIO()
    writer = csv.writer(manifest)
    writer.writerow(
        ["filename", "class", "source_url", "subject_plant", "verified_by", "notes"]
    )
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
            verified_by = "model" if r.get("status") == "auto_yes" else "human"
            note = r.get("source") or ""
            if r.get("model_pred"):
                note = f"{note}; model={r.get('model_pred')}@{r.get('model_conf')}"
            writer.writerow(
                [arc, cls, r.get("source_url") or "", r.get("subject_plant") or "",
                 verified_by, note]
            )
        zf.writestr("manifest.csv", manifest.getvalue())
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
