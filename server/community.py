"""
Community scan corpus for "similar scans near you".

Append-only SQLite at ``server/community.db``. Each row captures one
shared scan: the disease verdict, the auto-detected species, the
user's coarse-region string (city or admin region — never raw GPS), a signed
account-era actor (or an unlinked legacy pseudo-id), and an optional thumbnail
path. The
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
* ``client_ip_hash`` is a nullable legacy column only. Account-era writes leave
  it NULL; abuse limiting uses the live connection address in memory.
* Optional thumbnails are decoded, orientation-normalized, resized, and
  re-encoded as fresh 512px RGB JPEGs without source metadata or filenames.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import logging
import math
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

try:
    from .config import get_str  # type: ignore[import-not-found]
    from .countries import to_iso_alpha2
except ImportError:
    from config import get_str
    from countries import to_iso_alpha2

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
    -- Legacy install identity is nullable. Account-era rows use only the
    -- server-signed actor id; the two identities are never linked/claimed.
    user_pseudo_id  TEXT,
    account_actor_id TEXT,
    country         TEXT,
    -- ``region`` holds the city (the display locality); ``city`` mirrors it.
    -- lat/lon are the device's COARSE location rounded to ~1 km (never precise
    -- GPS) for the ~30 km "nearby" radius + the scans heat-map. All are
    -- client-supplied by the opted-in uploader. ``nickname`` is the author's
    -- generated plant-vibe handle shown in the feed.
    region          TEXT    NOT NULL,
    city            TEXT,
    lat             REAL,
    lon             REAL,
    disease         TEXT    NOT NULL,
    confidence      REAL,
    species         TEXT,
    species_lower   TEXT,
    species_score   REAL,
    thumb_path      TEXT,
    locale          TEXT,
    nickname        TEXT,
    client_ip_hash  TEXT,
    wants_opinion   INTEGER NOT NULL DEFAULT 0,
    public_id       TEXT,
    created_at      INTEGER NOT NULL
);
-- NOTE: the ix_community_public_id index is created in init_db AFTER the
-- public_id column migration — putting it here would crash executescript
-- on a legacy DB whose table predates the column.
CREATE INDEX IF NOT EXISTS ix_community_region          ON community_scans(region, created_at);
CREATE INDEX IF NOT EXISTS ix_community_user            ON community_scans(user_pseudo_id);
CREATE INDEX IF NOT EXISTS ix_community_created         ON community_scans(created_at);
CREATE INDEX IF NOT EXISTS ix_community_country         ON community_scans(country, created_at);
CREATE INDEX IF NOT EXISTS ix_community_country_region  ON community_scans(country, region, created_at);
CREATE INDEX IF NOT EXISTS ix_community_species_lower   ON community_scans(species_lower);
-- NOTE: ix_community_geo is created in init_db AFTER the lat/lon column
-- migration — putting it here crashes executescript on a legacy DB whose
-- table predates those columns (same reason as ix_community_public_id).

CREATE TABLE IF NOT EXISTS community_opinions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id           TEXT    NOT NULL,
    author_pseudo_id  TEXT,
    account_actor_id  TEXT,
    public_id          TEXT,
    suggested_disease TEXT    NOT NULL,
    note              TEXT,
    nickname          TEXT,
    client_ip_hash    TEXT,
    created_at        INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_opinions_scan   ON community_opinions(scan_id, created_at);
CREATE INDEX IF NOT EXISTS ix_opinions_author ON community_opinions(author_pseudo_id);

CREATE TABLE IF NOT EXISTS community_opinion_requests (
    scan_id        TEXT    PRIMARY KEY,
    user_pseudo_id TEXT,
    account_actor_id TEXT,
    created_at     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_opinion_requests_user ON community_opinion_requests(user_pseudo_id);

CREATE TABLE IF NOT EXISTS community_scan_views (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id          TEXT    NOT NULL,
    viewer_pseudo_id TEXT,
    account_actor_id TEXT,
    created_at       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_views_scan ON community_scan_views(scan_id);

-- Account-bound safety controls.  Clients submit only public content tokens;
-- the HTTP layer resolves the target author server-side, so an attacker cannot
-- spoof another account id into a report or block.  Reports intentionally keep
-- no free-form reporter text or content snapshot.
CREATE TABLE IF NOT EXISTS community_content_reports (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    reporter_actor_id TEXT    NOT NULL,
    target_type       TEXT    NOT NULL CHECK(target_type IN ('scan', 'opinion')),
    target_scan_id    TEXT    NOT NULL,
    target_opinion_id INTEGER NOT NULL DEFAULT 0,
    target_actor_id   TEXT,
    reason            TEXT    NOT NULL,
    state             TEXT    NOT NULL DEFAULT 'pending'
                              CHECK(state IN ('pending', 'dismissed', 'removed')),
    created_at        INTEGER NOT NULL,
    resolved_at       INTEGER,
    UNIQUE(reporter_actor_id, target_type, target_scan_id, target_opinion_id)
);
CREATE INDEX IF NOT EXISTS ix_community_reports_queue
    ON community_content_reports(state, created_at);
CREATE INDEX IF NOT EXISTS ix_community_reports_reporter
    ON community_content_reports(reporter_actor_id, created_at);
CREATE INDEX IF NOT EXISTS ix_community_reports_target_actor
    ON community_content_reports(target_actor_id);

CREATE TABLE IF NOT EXISTS community_actor_blocks (
    public_id         TEXT    NOT NULL UNIQUE,
    blocker_actor_id  TEXT    NOT NULL,
    blocked_actor_id  TEXT    NOT NULL,
    blocked_nickname  TEXT,
    created_at        INTEGER NOT NULL,
    PRIMARY KEY(blocker_actor_id, blocked_actor_id),
    CHECK(blocker_actor_id != blocked_actor_id)
);
CREATE INDEX IF NOT EXISTS ix_community_blocks_blocked
    ON community_actor_blocks(blocked_actor_id);

CREATE TABLE IF NOT EXISTS community_deleted_actors (
    actor_id       TEXT PRIMARY KEY,
    requested_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS community_cleanup_jobs (
    actor_id        TEXT PRIMARY KEY,
    state           TEXT NOT NULL DEFAULT 'pending',
    attempt_count   INTEGER NOT NULL DEFAULT 0,
    next_attempt_at INTEGER NOT NULL,
    last_error_code TEXT,
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL,
    completed_at    INTEGER
);

-- Short-lived proof that an analysis token was minted by this server. It is
-- used only to authorize a later community publication; feedback receipts are
-- separate and are deliberately never accepted by community APIs.
CREATE TABLE IF NOT EXISTS community_analysis_tokens (
    token             TEXT PRIMARY KEY,
    expires_at        INTEGER NOT NULL,
    disease           TEXT,
    confidence        REAL,
    species           TEXT,
    species_score     REAL,
    image_binding     TEXT,
    claimed_actor_id  TEXT,
    claimed_at        INTEGER
);
CREATE INDEX IF NOT EXISTS ix_community_analysis_tokens_expiry
    ON community_analysis_tokens(expires_at);
"""

_IDENTITY_INDEXES = """
CREATE INDEX IF NOT EXISTS ix_community_actor ON community_scans(account_actor_id);
CREATE INDEX IF NOT EXISTS ix_opinions_actor ON community_opinions(account_actor_id);
CREATE UNIQUE INDEX IF NOT EXISTS ix_opinions_public_id
    ON community_opinions(public_id)
    WHERE public_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_opinion_requests_actor ON community_opinion_requests(account_actor_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_views_legacy_identity
    ON community_scan_views(scan_id, viewer_pseudo_id)
    WHERE viewer_pseudo_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_views_account_identity
    ON community_scan_views(scan_id, account_actor_id)
    WHERE account_actor_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_cleanup_jobs_due
    ON community_cleanup_jobs(state, next_attempt_at);
"""

_write_lock = threading.RLock()
_init_lock = threading.Lock()
_initialised = False


class AnalysisTokenUnknownError(ValueError):
    """The publication capability was not issued here or has expired."""


class AnalysisTokenConflictError(ValueError):
    """The capability or scan id is already bound to another publication."""


class AnalysisImageMismatchError(ValueError):
    """The community image is not the exact upload that was analysed."""


class OpinionTargetUnavailableError(ValueError):
    """The target scan no longer exists or no longer accepts opinions."""


# ── Public scan tokens ────────────────────────────────────────────────
# The stored ``scan_id`` is a server-issued community publication capability,
# never a feedback receipt. Viewer-facing feed rows carry a stable per-scan,
# deterministic one-way PUBLIC token so the publication capability itself is
# never exposed. It does not link different scans by the same actor. The token
# stays stable for the row's lifetime while age checks make old scans read-only.
# Opinion/view endpoints accept either form for installed-client compatibility,
# while safety mutations below deliberately require the opaque public form.
_PUBLIC_TOKEN_PREFIX = "cs"
_PUBLIC_OPINION_TOKEN_PREFIX = "co"
_PUBLIC_BLOCK_TOKEN_PREFIX = "cb"

# Fixed report taxonomy: no free-form reporter text is retained, which keeps
# the moderation store bounded and avoids turning it into a new PII channel.
COMMUNITY_REPORT_REASONS = frozenset({
    "sexual_content",
    "violence_or_threats",
    "hate_or_harassment",
    "illegal_or_dangerous",
    "spam_or_misleading",
    "privacy_or_impersonation",
    "other_rules_violation",
})
COMMUNITY_REPORT_DAILY_LIMIT = 20
COMMUNITY_BLOCK_LIMIT = 500
COMMUNITY_REPORT_RETENTION_DAYS = 180

# Anonymous display identifiers rotate every 30 days so activity cannot be
# linked across windows. Independently, an older scan stops accepting new
# opinions while its public read link remains stable for the retention window.
IDENTITY_ROTATION_DAYS = 30
_IDENTITY_ROTATION_S = IDENTITY_ROTATION_DAYS * 86_400

# An open "wants a second opinion" request auto-expires after this long: the
# feed badge drops and the owner stops being notified about new opinions, but
# opinions already collected stay visible. Re-requesting restarts the window.
OPINION_REQUEST_TTL_HOURS = 72
_OPINION_REQUEST_TTL_S = OPINION_REQUEST_TTL_HOURS * 3600

# Expired requests are swept lazily from the hot read paths, throttled so the
# sweep doesn't run on every feed request.
_EXPIRY_SWEEP_INTERVAL_S = 300
_expiry_sweep_lock = threading.Lock()
_last_expiry_sweep = 0.0


def _identity_epoch() -> int:
    return int(time.time() // _IDENTITY_ROTATION_S)


def public_scan_token(scan_id: str) -> str:
    """Deterministic, non-reversible public token for one scan_id."""
    digest = hashlib.sha256(f"community-public-v1|{scan_id}".encode()).hexdigest()
    return _PUBLIC_TOKEN_PREFIX + digest[:30]


def public_opinion_token(scan_id: str, opinion_id: int) -> str:
    """Non-reversible viewer token for one opinion row."""
    digest = hashlib.sha256(
        f"community-opinion-public-v1|{scan_id}|{int(opinion_id)}".encode()
    ).hexdigest()
    return _PUBLIC_OPINION_TOKEN_PREFIX + digest[:30]


def resolve_scan_token(token: str | None) -> str:
    """Map a public token back to its real scan_id.

    Real scan_ids (the owner's own app uses them directly) and ids of
    scans that haven't been shared yet pass through unchanged, so every
    historical client call keeps its meaning.
    """
    tok = (token or "").strip()
    if not (tok.startswith(_PUBLIC_TOKEN_PREFIX) and len(tok) == 32):
        return tok
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT scan_id FROM community_scans WHERE public_id = ? "
            "AND (account_actor_id IS NULL OR account_actor_id NOT IN "
            "(SELECT actor_id FROM community_deleted_actors)) LIMIT 1",
            (tok,),
        ).fetchone()
    return str(row[0]) if row else tok


def scan_exists(scan_id: str) -> bool:
    """True when a community row exists for ``scan_id`` (already resolved)."""
    sid = (scan_id or "").strip()
    if not sid:
        return False
    init_db()
    with _connect() as conn:
        return conn.execute(
            "SELECT 1 FROM community_scans WHERE scan_id = ? "
            "AND (account_actor_id IS NULL OR account_actor_id NOT IN "
            "(SELECT actor_id FROM community_deleted_actors)) LIMIT 1", (sid,),
        ).fetchone() is not None


def record_analysis_token(
    token: str,
    *,
    disease: str,
    confidence: float | None,
    species: str | None,
    species_score: float | None,
    image_sha256: str,
    ttl_days: int = 30,
) -> None:
    """Persist immutable server-derived claims for a publication capability."""
    value = (token or "").strip()
    if not value or len(value) > 128:
        raise ValueError("invalid analysis token")
    disease_value = (disease or "").strip()
    if not disease_value or len(disease_value) > 256:
        raise ValueError("invalid analysis disease")
    confidence_value = float(confidence) if confidence is not None else None
    species_value = (species or "").strip() or None
    species_score_value = float(species_score) if species_score is not None else None
    image_sha256_value = (image_sha256 or "").strip().lower()
    if confidence_value is not None and (
        not math.isfinite(confidence_value) or not 0.0 <= confidence_value <= 1.0
    ):
        raise ValueError("invalid analysis confidence")
    if species_value is not None and len(species_value) > 256:
        raise ValueError("invalid analysis species")
    if species_score_value is not None and (
        not math.isfinite(species_score_value) or not 0.0 <= species_score_value <= 1.0
    ):
        raise ValueError("invalid analysis species score")
    if len(image_sha256_value) != 64:
        raise ValueError("invalid analysis image digest")
    try:
        bytes.fromhex(image_sha256_value)
    except ValueError as exc:
        raise ValueError("invalid analysis image digest") from exc
    # Salt the exact-byte digest with the random capability so identical
    # photos do not create a reusable, cross-token fingerprint in SQLite.
    image_binding = hashlib.sha256(
        value.encode("utf-8") + b"\0" + bytes.fromhex(image_sha256_value)
    ).hexdigest()
    init_db()
    expires_at = int(time.time()) + max(1, min(int(ttl_days), 90)) * 86_400
    with _write_lock, _connect() as connection:
        connection.execute(
            "INSERT INTO community_analysis_tokens ("
            "token, expires_at, disease, confidence, species, species_score, image_binding"
            ") VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                value,
                expires_at,
                disease_value,
                confidence_value,
                species_value,
                species_score_value,
                image_binding,
            ),
        )


def is_analysis_token_issued(token: str) -> bool:
    """Whether a token is current and eligible for community publication."""
    value = (token or "").strip()
    if not value:
        return False
    init_db()
    with _connect() as connection:
        return connection.execute(
            "SELECT 1 FROM community_analysis_tokens "
            "WHERE token = ? AND expires_at > ? AND disease IS NOT NULL "
            "AND image_binding IS NOT NULL LIMIT 1",
            (value, int(time.time())),
        ).fetchone() is not None


def opinion_open(scan_id: str, max_age_days: int = IDENTITY_ROTATION_DAYS) -> bool:
    """True when a shared scan still accepts opinions — it exists AND is newer
    than ``max_age_days``. Old scans go read-only so the community cannot keep
    weighing in on stale photos; their public read link remains stable."""
    sid = (scan_id or "").strip()
    if not sid:
        return False
    init_db()
    cutoff = int(time.time()) - max_age_days * 86_400
    with _connect() as conn:
        return conn.execute(
            "SELECT 1 FROM community_scans WHERE scan_id = ? AND created_at >= ? "
            "AND (account_actor_id IS NULL OR account_actor_id NOT IN "
            "(SELECT actor_id FROM community_deleted_actors)) LIMIT 1",
            (sid, cutoff),
        ).fetchone() is not None

# Disease classes that should never surface in the community feed.
# ``not_a_plant`` rows are uninteresting to other users (and often
# faces / objects / random photos), so we silently hide them from
# both the carousel and the summary aggregates. Existing rows stay
# on disk for retraining; only the read paths filter them out.
_HIDDEN_COMMUNITY_DISEASES = ("not_a_plant",)

# Column list shared by every feed query so the positional ``_row_to_dict``
# reader can't drift between callers. Order is load-bearing — keep in sync.
_FEED_COLUMNS = (
    "scan_id, country, region, city, lat, lon, disease, confidence, "
    "species, species_score, thumb_path, locale, created_at, wants_opinion, "
    "user_pseudo_id, nickname, account_actor_id"
)

# "Nearby" radius for the coords-based feed + popular scope (km). The client
# resolves coarse location; we group within this radius of it.
NEARBY_RADIUS_KM = 30.0


def _bbox(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    """(min_lat, max_lat, min_lon, max_lon) box ~``radius_km`` around a point.
    A cheap prefilter for the indexed lat/lon columns; callers refine to a true
    circle with :func:`_haversine_km` where exactness matters."""
    dlat = radius_km / 111.0
    coslat = math.cos(math.radians(lat))
    dlon = radius_km / (111.0 * coslat) if abs(coslat) > 1e-6 else 180.0
    return (lat - dlat, lat + dlat, lon - dlon, lon + dlon)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _hidden_clause(table_alias: str = "") -> str:
    """SQL fragment + placeholders for the hidden-disease filter.

    Returns a string starting with " AND " so callers can append
    directly to existing WHERE conditions.
    """
    if table_alias not in {"", "s"}:
        raise ValueError("invalid SQL table alias")
    prefix = f"{table_alias}." if table_alias else ""
    placeholders = ", ".join(["?"] * len(_HIDDEN_COMMUNITY_DISEASES))
    # SQL identifiers are allowlisted above; only the number of bound
    # placeholders is generated dynamically.
    return (
        f" AND {prefix}disease NOT IN ({placeholders})"  # nosec B608
        f" AND ({prefix}account_actor_id IS NULL OR {prefix}account_actor_id "
        "NOT IN (SELECT actor_id FROM community_deleted_actors))"
    )


def _normalise_actor_id(value: str | None) -> str | None:
    try:
        return str(uuid.UUID((value or "").strip()))
    except (TypeError, ValueError, AttributeError):
        return None


def _blocked_actor_clause(
    viewer_account_actor_id: str | None,
    *,
    actor_column: str = "account_actor_id",
) -> tuple[str, tuple[str, str]]:
    """SQL predicate hiding either side of a block relationship.

    Blocking is deliberately mutual for visibility and interaction: the
    blocker no longer sees the blocked actor, and the blocked actor cannot use
    public feeds to follow or interact with the blocker.
    """
    allowed_columns = {
        "account_actor_id",
        "v.account_actor_id",
        "o.account_actor_id",
        "o2.account_actor_id",
    }
    if actor_column not in allowed_columns:
        raise ValueError("invalid SQL actor column")
    viewer = _normalise_actor_id(viewer_account_actor_id)
    if viewer is None:
        return "", ()
    # The only interpolated identifier is selected from allowed_columns.
    return (
        " AND (" + actor_column + " IS NULL OR NOT EXISTS ("  # nosec B608
        "SELECT 1 FROM community_actor_blocks b WHERE "
        "(b.blocker_actor_id = ? AND b.blocked_actor_id = " + actor_column + ") OR "
        "(b.blocker_actor_id = " + actor_column + " AND b.blocked_actor_id = ?)"
        "))",
        (viewer, viewer),
    )


def _identity(
    legacy_id: str | None,
    account_actor_id: str | None,
) -> tuple[str | None, str | None]:
    legacy = (legacy_id or "").strip() or None
    actor = (account_actor_id or "").strip() or None
    if (legacy is None) == (actor is None):
        raise ValueError("exactly one community identity is required")
    if actor is not None:
        actor = str(uuid.UUID(actor))
    return legacy, actor


def _identity_predicate(
    legacy_column: str,
    actor_column: str,
    legacy_id: str | None,
    account_actor_id: str | None,
) -> tuple[str, tuple[str]]:
    legacy, actor = _identity(legacy_id, account_actor_id)
    if actor is not None:
        return f"{actor_column} = ?", (actor,)
    return f"{legacy_column} = ?", (legacy or "",)


def _actor_is_deleted(conn: sqlite3.Connection, actor_id: str | None) -> bool:
    if actor_id is None:
        return False
    return conn.execute(
        "SELECT 1 FROM community_deleted_actors WHERE actor_id = ?", (actor_id,)
    ).fetchone() is not None


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


_IDENTITY_TABLE_DEFINITIONS = {
    "community_scans": """
        CREATE TABLE community_scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id TEXT NOT NULL,
            user_pseudo_id TEXT,
            account_actor_id TEXT,
            country TEXT,
            region TEXT NOT NULL,
            city TEXT,
            lat REAL,
            lon REAL,
            disease TEXT NOT NULL,
            confidence REAL,
            species TEXT,
            species_lower TEXT,
            species_score REAL,
            thumb_path TEXT,
            locale TEXT,
            nickname TEXT,
            client_ip_hash TEXT,
            wants_opinion INTEGER NOT NULL DEFAULT 0,
            public_id TEXT,
            created_at INTEGER NOT NULL
        )
    """,
    "community_opinions": """
        CREATE TABLE community_opinions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id TEXT NOT NULL,
            author_pseudo_id TEXT,
            account_actor_id TEXT,
            public_id TEXT,
            suggested_disease TEXT NOT NULL,
            note TEXT,
            nickname TEXT,
            client_ip_hash TEXT,
            created_at INTEGER NOT NULL
        )
    """,
    "community_opinion_requests": """
        CREATE TABLE community_opinion_requests (
            scan_id TEXT PRIMARY KEY,
            user_pseudo_id TEXT,
            account_actor_id TEXT,
            created_at INTEGER NOT NULL
        )
    """,
    "community_scan_views": """
        CREATE TABLE community_scan_views (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id TEXT NOT NULL,
            viewer_pseudo_id TEXT,
            account_actor_id TEXT,
            created_at INTEGER NOT NULL
        )
    """,
}


def _migrate_legacy_identity_columns(conn: sqlite3.Connection) -> None:
    """Make legacy pseudo-id columns nullable without claiming old rows.

    SQLite cannot drop a NOT NULL constraint in place, so each affected table
    is transactionally rebuilt. Only columns with the same name are copied;
    newly introduced account actor columns remain NULL for every legacy row.
    """
    legacy_identity = {
        "community_scans": "user_pseudo_id",
        "community_opinions": "author_pseudo_id",
        "community_opinion_requests": "user_pseudo_id",
        "community_scan_views": "viewer_pseudo_id",
    }
    for table, identity_column in legacy_identity.items():
        info = conn.execute(f"PRAGMA table_info({table})").fetchall()
        if not info:
            continue
        identity = next((column for column in info if column[1] == identity_column), None)
        if identity is None or not bool(identity[3]):
            continue
        old_table = f"{table}_legacy_identity_migration"
        target_columns = [row[1] for row in conn.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()]
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(f"ALTER TABLE {table} RENAME TO {old_table}")
            conn.execute(_IDENTITY_TABLE_DEFINITIONS[table])
            new_columns = {
                row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            copied = [column for column in target_columns if column in new_columns]
            names = ", ".join(copied)
            # Every identifier comes from the fixed migration definitions.
            conn.execute(
                f"INSERT INTO {table} ({names}) SELECT {names} FROM {old_table}"  # nosec B608
            )
            conn.execute(f"DROP TABLE {old_table}")
            conn.execute("COMMIT")
        except Exception:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise


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
            # MaxMind-derived precise location (city + centroid) for the
            # "nearby" feed/popular grouping and the scans heat-map. Legacy
            # rows leave these NULL — they group by their old ``region`` only.
            _add_column_if_missing(conn, "community_scans", "city", "TEXT")
            _add_column_if_missing(conn, "community_scans", "lat", "REAL")
            _add_column_if_missing(conn, "community_scans", "lon", "REAL")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_community_geo ON community_scans(lat, lon)")
            # Author's generated plant-vibe handle (community display name).
            _add_column_if_missing(conn, "community_scans", "nickname", "TEXT")
            _add_column_if_missing(conn, "community_opinions", "nickname", "TEXT")
            _add_column_if_missing(conn, "community_opinions", "public_id", "TEXT")
            _add_column_if_missing(conn, "community_scans", "account_actor_id", "TEXT")
            _add_column_if_missing(conn, "community_opinions", "account_actor_id", "TEXT")
            _add_column_if_missing(
                conn, "community_opinion_requests", "account_actor_id", "TEXT"
            )
            _add_column_if_missing(conn, "community_scan_views", "account_actor_id", "TEXT")
            # "Wants a second opinion" flag — set after upload by the
            # scanner via /api/community/request-opinion. Older rows
            # default to 0 (no request).
            _add_column_if_missing(conn, "community_scans", "wants_opinion", "INTEGER NOT NULL DEFAULT 0")
            # Public viewer-facing token (see ``public_scan_token``).
            _add_column_if_missing(conn, "community_scans", "public_id", "TEXT")
            # Publication capabilities originally stored only an expiry. New
            # rows bind the model verdict and exact analyzed image immutably;
            # legacy rows retain NULL claims and are no longer publishable.
            _add_column_if_missing(conn, "community_analysis_tokens", "disease", "TEXT")
            _add_column_if_missing(conn, "community_analysis_tokens", "confidence", "REAL")
            _add_column_if_missing(conn, "community_analysis_tokens", "species", "TEXT")
            _add_column_if_missing(conn, "community_analysis_tokens", "species_score", "REAL")
            _add_column_if_missing(conn, "community_analysis_tokens", "image_binding", "TEXT")
            _add_column_if_missing(
                conn, "community_analysis_tokens", "claimed_actor_id", "TEXT"
            )
            _add_column_if_missing(conn, "community_analysis_tokens", "claimed_at", "INTEGER")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_community_analysis_tokens_actor "
                "ON community_analysis_tokens(claimed_actor_id)"
            )
            _migrate_legacy_identity_columns(conn)
            conn.executescript(_SCHEMA)
            conn.executescript(_IDENTITY_INDEXES)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_community_public_id "
                "ON community_scans(public_id)"
            )
            _backfill_public_ids(conn)
            _backfill_opinion_public_ids(conn)
            _canonicalize_countries(conn)
        _initialised = True
        logger.info("Community DB ready")


def _canonicalize_countries(conn: sqlite3.Connection) -> None:
    """One-off migration: fold legacy country buckets to ISO alpha-2 codes.

    Older rows stored whatever the client sent — an ISO code ("RU"), an
    English name ("Russia") or a localized name ("Россия") — so one country
    was split across several buckets and the popular / nearby aggregation
    disagreed per locale. Canonicalizing here (and on every write going
    forward) collapses them into one ``RU`` bucket. Idempotent: a code maps
    to itself, so re-running is a no-op.
    """
    rows = conn.execute(
        "SELECT DISTINCT country FROM community_scans "
        "WHERE country IS NOT NULL AND country != ''",
    ).fetchall()
    changed = 0
    for (country,) in rows:
        canon = to_iso_alpha2(str(country))
        if canon and canon != country:
            conn.execute(
                "UPDATE community_scans SET country = ? WHERE country = ?",
                (canon, country),
            )
            changed += 1
    if changed:
        logger.info("Community: canonicalized %d legacy country bucket(s)", changed)


def _backfill_public_ids(conn: sqlite3.Connection) -> None:
    """One-off migration for rows that predate the public token.

    Computes ``public_id`` for every legacy row, and renames legacy
    thumbnails from ``<scan_id>_<digest>.jpg`` to the token-prefixed
    form so the publication capability does not ride through the feed.
    """
    rows = conn.execute(
        "SELECT id, scan_id, thumb_path FROM community_scans "
        "WHERE public_id IS NULL",
    ).fetchall()
    for row_id, scan_id, thumb_path in rows:
        token = public_scan_token(str(scan_id))
        new_thumb = thumb_path
        if thumb_path and str(thumb_path).startswith(f"{scan_id}_"):
            new_thumb = token + str(thumb_path)[len(str(scan_id)):]
            old_file = COMMUNITY_THUMB_DIR / str(thumb_path)
            with contextlib.suppress(OSError):
                if old_file.exists():
                    old_file.rename(COMMUNITY_THUMB_DIR / new_thumb)
        conn.execute(
            "UPDATE community_scans SET public_id = ?, thumb_path = ? WHERE id = ?",
            (token, new_thumb, row_id),
        )
    if rows:
        logger.info("Community: backfilled public ids for %d legacy rows", len(rows))


def _backfill_opinion_public_ids(conn: sqlite3.Connection) -> None:
    """Give pre-safety-release opinions opaque viewer-facing identifiers."""
    rows = conn.execute(
        "SELECT id, scan_id FROM community_opinions WHERE public_id IS NULL"
    ).fetchall()
    for opinion_id, scan_id in rows:
        conn.execute(
            "UPDATE community_opinions SET public_id = ? WHERE id = ?",
            (public_opinion_token(str(scan_id), int(opinion_id)), int(opinion_id)),
        )
    if rows:
        logger.info(
            "Community: backfilled public ids for %d legacy opinions", len(rows)
        )


def _save_thumbnail(scan_id: str, image_bytes: bytes) -> str | None:
    """Decode and persist a fresh, metadata-free 512px RGB JPEG."""
    if not image_bytes:
        return None
    if len(image_bytes) > COMMUNITY_MAX_THUMB_BYTES:
        logger.info("Community thumbnail rejected as oversized")
        return None
    try:
        with Image.open(io.BytesIO(image_bytes)) as opened:
            width, height = opened.size
            if (
                width < 32
                or height < 32
                or width > 8_192
                or height > 8_192
                or width * height > 20_000_000
            ):
                return None
            opened.load()
            image = ImageOps.exif_transpose(opened).convert("RGB")
    except (Image.DecompressionBombError, OSError, UnidentifiedImageError, ValueError):
        return None
    if max(image.size) > 512:
        image.thumbnail((512, 512), Image.Resampling.LANCZOS)
    clean = Image.new("RGB", image.size)
    clean.paste(image)
    encoded = io.BytesIO()
    try:
        clean.save(
            encoded,
            format="JPEG",
            quality=85,
            optimize=True,
            progressive=False,
            subsampling=2,
        )
    except (OSError, ValueError):
        return None
    body = encoded.getvalue()
    if len(body) > COMMUNITY_MAX_THUMB_BYTES:
        return None
    # Hash-prefix the filename so two scans with the same scan_id (a
    # mis-behaved client) can't collide. Truncate to 24 hex chars —
    # plenty of collision resistance for this bounded corpus.
    # Named by the PUBLIC token, not the raw scan_id: the filename is
    # served to every feed reader via ``thumb_path`` and must not leak
    # the publication capability.
    digest = hashlib.sha256(body).hexdigest()[:24]
    filename = f"{public_scan_token(scan_id)}_{digest}.jpg"
    target = COMMUNITY_THUMB_DIR / filename
    temporary = COMMUNITY_THUMB_DIR / f".{filename}.tmp"
    try:
        COMMUNITY_THUMB_DIR.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(body)
        temporary.chmod(0o600)
        os.replace(temporary, target)
    except OSError:
        temporary.unlink(missing_ok=True)
        logger.warning("Failed to write community thumbnail")
        return None
    return filename


def record_scan(
    *,
    scan_id: str,
    user_pseudo_id: str | None = None,
    account_actor_id: str | None = None,
    country: str,
    region: str = "",
    city: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    disease: str,
    confidence: float | None = None,
    species: str | None = None,
    species_score: float | None = None,
    image_bytes: bytes | None = None,
    locale: str | None = None,
    nickname: str | None = None,
    client_ip: str | None = None,
) -> int:
    """Append a community-shared scan row. Returns the new row id.

    ``region``/``city`` are the display locality and ``lat``/``lon`` the
    rounded coarse coords — all client-supplied. ``nickname`` is the author's
    generated plant-vibe handle shown in the feed.
    """
    init_db()
    legacy_id, actor_id = _identity(user_pseudo_id, account_actor_id)
    now = int(time.time())
    # The connection address may be used by the in-memory HTTP limiter, but it
    # is never persisted on account-era community rows.
    ip_hash = None
    # Canonicalize the country bucket to an ISO alpha-2 code at the write so
    # the corpus never splits one country across code / name / localized-name
    # representations (the HTTP layer already canonicalizes, but a direct
    # caller — tests, future code — gets the same guarantee).
    country = to_iso_alpha2(country)
    region_value = region or ""
    species_lower = species.lower() if species else None
    with _write_lock, _connect() as conn:
        if _actor_is_deleted(conn, actor_id):
            raise ValueError("deleted account actor")
        # Dedup by scan_id. The community upload is fire-and-forget and a
        # "request a second opinion" tap can also trigger an on-demand
        # upload, so the same scan can be offered twice; we never want
        # two feed rows for one scan. (There's no UNIQUE constraint on
        # scan_id for back-compat with legacy rows, so we guard here.)
        # The dedup check runs BEFORE the thumbnail write — writing first
        # leaked an orphan thumbnail file per duplicate upload that no
        # prune path ever swept.
        existing = conn.execute(
            "SELECT id FROM community_scans WHERE scan_id = ? LIMIT 1",
            (scan_id,),
        ).fetchone()
        if existing:
            return int(existing[0])
        thumb_path = _save_thumbnail(scan_id, image_bytes) if image_bytes else None
        # Honour a second-opinion request that arrived BEFORE this upload
        # landed (the durable request store decouples the two orderings).
        # Expired requests don't count — the ask has lapsed by then.
        identity_sql, identity_params = _identity_predicate(
            "user_pseudo_id", "account_actor_id", legacy_id, actor_id
        )
        # identity_sql is generated exclusively from fixed identity columns.
        pending = conn.execute(
            "SELECT 1 FROM community_opinion_requests "  # nosec B608
            f"WHERE scan_id = ? AND {identity_sql} AND created_at >= ? LIMIT 1",
            (scan_id, *identity_params, now - _OPINION_REQUEST_TTL_S),
        ).fetchone()
        wants_opinion = 1 if pending else 0
        cur = conn.execute(
            "INSERT INTO community_scans ("
            "  scan_id, user_pseudo_id, account_actor_id, country, region, city, lat, lon, "
            "  disease, confidence, "
            "  species, species_lower, species_score, thumb_path, locale, "
            "  nickname, client_ip_hash, wants_opinion, public_id, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                scan_id,
                legacy_id,
                actor_id,
                country,
                region_value,
                city,
                lat,
                lon,
                disease,
                confidence,
                species,
                species_lower,
                species_score,
                thumb_path,
                locale,
                (nickname or None),
                ip_hash,
                wants_opinion,
                public_scan_token(scan_id),
                now,
            ),
        )
        return int(cur.lastrowid or 0)


def record_analysis_scan(
    *,
    scan_id: str,
    account_actor_id: str,
    country: str,
    region: str = "",
    city: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    image_bytes: bytes | None = None,
    locale: str | None = None,
    nickname: str | None = None,
    client_disease: str | None = None,
    client_confidence: float | None = None,
    client_species: str | None = None,
    client_species_score: float | None = None,
) -> int:
    """Publish one server analysis with its immutable stored verdict.

    The uploaded image must be byte-for-byte identical to the upload analysed
    when the capability was issued. Claiming the capability and inserting the
    scan share one SQLite transaction. A database failure therefore leaves the
    capability retryable; after success, only the same signed actor receives
    the existing row id.
    The ``client_*`` diagnosis fields remain accepted for wire/backward test
    compatibility but are deliberately ignored in favor of the stored claims.
    """
    del client_disease, client_confidence, client_species, client_species_score
    init_db()
    token = (scan_id or "").strip()
    if not token or len(token) > 128:
        raise AnalysisTokenUnknownError("unknown analysis token")
    if not image_bytes:
        raise AnalysisImageMismatchError("analysis image is required")
    image_binding = hashlib.sha256(
        token.encode("utf-8") + b"\0" + hashlib.sha256(image_bytes).digest()
    ).hexdigest()
    _legacy_id, actor_id = _identity(None, account_actor_id)
    assert actor_id is not None
    now = int(time.time())
    country_value = to_iso_alpha2(country)
    region_value = region or ""
    thumb_path: str | None = None

    with _write_lock, _connect() as conn:
        try:
            # Serialize token claims and scan-id collision checks even when
            # multiple application threads/processes reach SQLite together.
            conn.execute("BEGIN IMMEDIATE")
            if _actor_is_deleted(conn, actor_id):
                raise ValueError("deleted account actor")
            if actor_id is not None:
                owner_row = conn.execute(
                    "SELECT account_actor_id FROM community_scans "
                    "WHERE scan_id = ? LIMIT 1",
                    (scan_id,),
                ).fetchone()
                owner_actor = (
                    str(owner_row[0]) if owner_row and owner_row[0] else None
                )
                if owner_actor and owner_actor != actor_id:
                    blocked = conn.execute(
                        "SELECT 1 FROM community_actor_blocks WHERE "
                        "(blocker_actor_id = ? AND blocked_actor_id = ?) OR "
                        "(blocker_actor_id = ? AND blocked_actor_id = ?) LIMIT 1",
                        (actor_id, owner_actor, owner_actor, actor_id),
                    ).fetchone()
                    if blocked is not None:
                        raise ValueError("blocked_interaction")
            claim = conn.execute(
                "SELECT expires_at, disease, confidence, species, species_score, image_binding, "
                "claimed_actor_id FROM community_analysis_tokens WHERE token = ?",
                (token,),
            ).fetchone()
            if (
                claim is None
                or int(claim[0]) <= now
                or claim[1] is None
                or claim[5] is None
            ):
                raise AnalysisTokenUnknownError("unknown analysis token")
            if image_binding != str(claim[5]).lower():
                raise AnalysisImageMismatchError("analysis image does not match")

            existing_rows = conn.execute(
                "SELECT id, account_actor_id FROM community_scans "
                "WHERE scan_id = ? LIMIT 2",
                (token,),
            ).fetchall()
            claimed_actor = str(claim[6]) if claim[6] is not None else None
            if claimed_actor is not None:
                if (
                    claimed_actor != actor_id
                    or len(existing_rows) != 1
                    or existing_rows[0][1] != actor_id
                ):
                    raise AnalysisTokenConflictError("analysis token already claimed")
                conn.execute("COMMIT")
                return int(existing_rows[0][0])

            # An unclaimed token must not adopt an unrelated/pre-existing row,
            # even if that row happens to have the same actor id.
            if existing_rows:
                raise AnalysisTokenConflictError("scan id collision")

            disease = str(claim[1])
            confidence = float(claim[2]) if claim[2] is not None else None
            species = str(claim[3]) if claim[3] is not None else None
            species_score = float(claim[4]) if claim[4] is not None else None
            species_lower = species.lower() if species else None
            thumb_path = _save_thumbnail(token, image_bytes) if image_bytes else None
            identity_sql, identity_params = _identity_predicate(
                "user_pseudo_id", "account_actor_id", None, actor_id
            )
            pending = conn.execute(
                "SELECT 1 FROM community_opinion_requests "
                f"WHERE scan_id = ? AND {identity_sql} AND created_at >= ? LIMIT 1",
                (token, *identity_params, now - _OPINION_REQUEST_TTL_S),
            ).fetchone()
            cur = conn.execute(
                "INSERT INTO community_scans ("
                "scan_id, user_pseudo_id, account_actor_id, country, region, city, lat, lon, "
                "disease, confidence, species, species_lower, species_score, thumb_path, "
                "locale, nickname, client_ip_hash, wants_opinion, public_id, created_at"
                ") VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)",
                (
                    token,
                    actor_id,
                    country_value,
                    region_value,
                    city,
                    lat,
                    lon,
                    disease,
                    confidence,
                    species,
                    species_lower,
                    species_score,
                    thumb_path,
                    locale,
                    (nickname or None),
                    1 if pending else 0,
                    public_scan_token(token),
                    now,
                ),
            )
            claimed = conn.execute(
                "UPDATE community_analysis_tokens "
                "SET claimed_actor_id = ?, claimed_at = ? "
                "WHERE token = ? AND claimed_actor_id IS NULL",
                (actor_id, now, token),
            ).rowcount
            if claimed != 1:
                raise AnalysisTokenConflictError("analysis token already claimed")
            conn.execute("COMMIT")
            return int(cur.lastrowid or 0)
        except Exception:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            if thumb_path:
                with contextlib.suppress(OSError):
                    (COMMUNITY_THUMB_DIR / thumb_path).unlink(missing_ok=True)
            raise


def set_wants_opinion(
    scan_id: str,
    user_pseudo_id: str | None = None,
    *,
    account_actor_id: str | None = None,
) -> bool:
    """Durably record that the scan's owner wants a community second opinion.

    Owner-scoped: a caller can only flag a scan with the same selected
    legacy-or-account identity. The intent is written to
    ``community_opinion_requests`` FIRST,
    so it survives even when the community upload hasn't landed yet — the
    flag is then materialised onto the scan row either here (if it
    already exists) or by :func:`record_scan` when the upload arrives.
    This removes the upload/flag race that used to make the request a
    silent no-op.

    Re-requesting refreshes ``created_at``, restarting the
    ``OPINION_REQUEST_TTL_HOURS`` auto-expiry window.

    Returns ``True`` once an owner-matching intent is recorded. A request for
    an already-shared row owned by another identity returns ``False``.
    """
    init_db()
    if not scan_id:
        return False
    try:
        legacy_id, actor_id = _identity(user_pseudo_id, account_actor_id)
    except ValueError:
        return False
    now = int(time.time())
    with _write_lock, _connect() as conn:
        if _actor_is_deleted(conn, actor_id):
            return False
        owner_sql, owner_params = _identity_predicate(
            "user_pseudo_id", "account_actor_id", legacy_id, actor_id
        )
        existing_owner = conn.execute(
            "SELECT user_pseudo_id, account_actor_id FROM community_scans "
            "WHERE scan_id = ? LIMIT 1",
            (scan_id,),
        ).fetchone()
        if existing_owner is not None and not (
            existing_owner[0] == legacy_id and existing_owner[1] == actor_id
        ):
            return False
        # Durable record of intent (idempotent — one request per scan).
        conn.execute(
            "INSERT INTO community_opinion_requests "
            "(scan_id, user_pseudo_id, account_actor_id, created_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(scan_id) DO UPDATE SET "
            "  user_pseudo_id = excluded.user_pseudo_id, "
            "  account_actor_id = excluded.account_actor_id, "
            "  created_at = excluded.created_at",
            (scan_id, legacy_id, actor_id, now),
        )
        # Materialise onto the scan row if the upload has already landed.
        conn.execute(
            "UPDATE community_scans SET wants_opinion = 1 "  # nosec B608
            f"WHERE scan_id = ? AND {owner_sql}",
            (scan_id, *owner_params),
        )
        return True


def clear_wants_opinion(
    scan_id: str,
    user_pseudo_id: str | None = None,
    *,
    account_actor_id: str | None = None,
) -> bool:
    """Withdraw the owner's open second-opinion request on their own scan.

    Owner-scoped like :func:`set_wants_opinion`: both the durable request
    row and the materialised scan-row flag are only touched when the selected
    identity matches. Opinions already left stay visible — this
    only stops the "wants opinion" badge and further notifications.

    Returns ``True`` when an open request was actually removed.
    """
    init_db()
    sid = (scan_id or "").strip()
    if not sid:
        return False
    try:
        legacy_id, actor_id = _identity(user_pseudo_id, account_actor_id)
    except ValueError:
        return False
    owner_sql, owner_params = _identity_predicate(
        "user_pseudo_id", "account_actor_id", legacy_id, actor_id
    )
    with _write_lock, _connect() as conn:
        # owner_sql uses fixed identity columns and all values remain bound.
        cleared = conn.execute(
            "DELETE FROM community_opinion_requests "  # nosec B608
            f"WHERE scan_id = ? AND {owner_sql}",
            (sid, *owner_params),
        ).rowcount
        conn.execute(
            "UPDATE community_scans SET wants_opinion = 0 "  # nosec B608
            f"WHERE scan_id = ? AND {owner_sql}",
            (sid, *owner_params),
        )
    return bool(cleared)


def expire_stale_opinion_requests(now: int | None = None) -> int:
    """Drop opinion requests older than ``OPINION_REQUEST_TTL_HOURS`` and
    clear the materialised ``wants_opinion`` flag on scans left without a
    live request. Returns the number of requests dropped."""
    init_db()
    ts = int(now if now is not None else time.time())
    cutoff = ts - _OPINION_REQUEST_TTL_S
    with _write_lock, _connect() as conn:
        dropped = conn.execute(
            "DELETE FROM community_opinion_requests WHERE created_at < ?",
            (cutoff,),
        ).rowcount
        if dropped:
            conn.execute(
                "UPDATE community_scans SET wants_opinion = 0 "
                "WHERE wants_opinion = 1 AND scan_id NOT IN "
                "(SELECT scan_id FROM community_opinion_requests)",
            )
    return int(dropped or 0)


def _sweep_expired_requests() -> None:
    """Throttled lazy sweep — called from the hot read paths so expiry works
    without a dedicated scheduler; the periodic pruner still backstops it."""
    global _last_expiry_sweep
    now = time.time()
    with _expiry_sweep_lock:
        if now - _last_expiry_sweep < _EXPIRY_SWEEP_INTERVAL_S:
            return
        _last_expiry_sweep = now
    with contextlib.suppress(Exception):
        expire_stale_opinion_requests(int(now))


def record_opinion_with_status(
    *,
    scan_id: str,
    author_pseudo_id: str | None = None,
    account_actor_id: str | None = None,
    suggested_disease: str,
    note: str | None = None,
    nickname: str | None = None,
    client_ip: str | None = None,
) -> tuple[int, bool]:
    """Append an opinion and return ``(row_id, created)`` atomically."""
    init_db()
    legacy_id, actor_id = _identity(author_pseudo_id, account_actor_id)
    now = int(time.time())
    ip_hash = None
    note_clean = (note or "").strip()[:500] or None
    nick_clean = (nickname or "").strip()[:48] or None
    with _write_lock, _connect() as conn:
        # The endpoint's existence/owner/freshness checks happen before this
        # call and can race account cleanup or retention pruning. Acquire a
        # database-wide write reservation before repeating the target check,
        # then keep it through the insert. This works across app processes;
        # the in-process RLock alone cannot close that boundary.
        conn.execute("BEGIN IMMEDIATE")
        try:
            cutoff = now - _IDENTITY_ROTATION_S
            target_exists = conn.execute(
                "SELECT 1 FROM community_scans WHERE scan_id = ? AND created_at >= ? "
                "AND (account_actor_id IS NULL OR account_actor_id NOT IN "
                "(SELECT actor_id FROM community_deleted_actors)) LIMIT 1",
                (scan_id, cutoff),
            ).fetchone()
            if target_exists is None:
                raise OpinionTargetUnavailableError(
                    "opinion target is absent, deleted, or stale"
                )
            if _actor_is_deleted(conn, actor_id):
                raise ValueError("deleted account actor")
            # One opinion per author per scan. If this install already weighed in,
            # keep the original (the client locks its input too) and return that
            # row's id rather than appending a duplicate.
            author_sql, author_params = _identity_predicate(
                "author_pseudo_id", "account_actor_id", legacy_id, actor_id
            )
            existing = conn.execute(
                "SELECT id FROM community_opinions "
                f"WHERE scan_id = ? AND {author_sql} LIMIT 1",
                (scan_id, *author_params),
            ).fetchone()
            if existing is not None:
                conn.execute("COMMIT")
                return int(existing[0]), False
            cur = conn.execute(
                "INSERT INTO community_opinions ("
                "  scan_id, author_pseudo_id, account_actor_id, suggested_disease, note, "
                "  nickname, client_ip_hash, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (scan_id, legacy_id, actor_id, suggested_disease, note_clean,
                 nick_clean, ip_hash, now),
            )
            opinion_id = int(cur.lastrowid or 0)
            conn.execute(
                "UPDATE community_opinions SET public_id = ? WHERE id = ?",
                (public_opinion_token(scan_id, opinion_id), opinion_id),
            )
            conn.execute("COMMIT")
            return opinion_id, True
        except Exception:
            conn.execute("ROLLBACK")
            raise


def record_opinion(
    *,
    scan_id: str,
    author_pseudo_id: str | None = None,
    account_actor_id: str | None = None,
    suggested_disease: str,
    note: str | None = None,
    nickname: str | None = None,
    client_ip: str | None = None,
) -> int:
    """Backward-compatible row-id-only opinion writer."""
    row_id, _created = record_opinion_with_status(
        scan_id=scan_id,
        author_pseudo_id=author_pseudo_id,
        account_actor_id=account_actor_id,
        suggested_disease=suggested_disease,
        note=note,
        nickname=nickname,
        client_ip=client_ip,
    )
    return row_id


def opinions_for(
    scan_id: str,
    *,
    limit: int = 50,
    viewer_account_actor_id: str | None = None,
) -> dict:
    """Opinions for one scan plus a per-disease consensus tally.

    Shape::

        {"opinions":  [{"suggested_disease", "note", "created_at"}, ...],
         "consensus": [{"disease", "count"}, ...]}   # most-voted first

    Also carries ``wants_opinion`` (is there a live, non-expired request?)
    plus ``opinion_requested_at`` (epoch seconds of that request, else
    null) so the owner's app can restore its "waiting" state across
    restarts and render a time-left countdown toward the 72 h expiry.
    """
    init_db()
    _sweep_expired_requests()
    limit = max(1, min(int(limit), 100))
    request_cutoff = int(time.time()) - _OPINION_REQUEST_TTL_S
    with _connect() as conn:
        scan_owner = conn.execute(
            "SELECT account_actor_id FROM community_scans WHERE scan_id = ? LIMIT 1",
            (scan_id,),
        ).fetchone()
        if scan_owner is None or (
            scan_owner[0] is not None
            and _actor_is_deleted(conn, str(scan_owner[0]))
        ):
            return {
                "opinions": [],
                "consensus": [],
                "views": 0,
                "wants_opinion": False,
                "opinion_requested_at": None,
            }
        viewer = _normalise_actor_id(viewer_account_actor_id)
        if (
            viewer is not None
            and scan_owner is not None
            and scan_owner[0] is not None
            and str(scan_owner[0]) != viewer
            and _actors_blocked(conn, viewer, str(scan_owner[0]))
        ):
            return {
                "opinions": [],
                "consensus": [],
                "views": 0,
                "wants_opinion": False,
                "opinion_requested_at": None,
            }
        blocked_clause, blocked_params = _blocked_actor_clause(
            viewer_account_actor_id
        )
        # blocked_clause contains only allowlisted columns and bound actor IDs.
        rows = conn.execute(
            "SELECT suggested_disease, note, created_at, nickname, public_id, "  # nosec B608
            "account_actor_id "
            "FROM community_opinions WHERE scan_id = ? "
            "AND (account_actor_id IS NULL OR account_actor_id NOT IN "
            "(SELECT actor_id FROM community_deleted_actors)) "
            + blocked_clause
            + " ORDER BY created_at DESC LIMIT ?",
            (scan_id, *blocked_params, limit),
        ).fetchall()
        tally = conn.execute(
            "SELECT suggested_disease, COUNT(*) AS n "  # nosec B608
            "FROM community_opinions WHERE scan_id = ? "
            "AND (account_actor_id IS NULL OR account_actor_id NOT IN "
            "(SELECT actor_id FROM community_deleted_actors)) "
            + blocked_clause
            + " GROUP BY suggested_disease ORDER BY n DESC",
            (scan_id, *blocked_params),
        ).fetchall()
        request_row = conn.execute(
            "SELECT created_at FROM community_opinion_requests "
            "WHERE scan_id = ? AND created_at >= ? LIMIT 1",
            (scan_id, request_cutoff),
        ).fetchone()
    opinions = [
        {
            "suggested_disease": str(r[0]),
            "note": r[1] or "",
            "created_at": int(r[2]),
            "nickname": r[3] or None,
            "content_id": r[4] or None,
            "is_mine": bool(
                viewer_account_actor_id
                and r[5] == _normalise_actor_id(viewer_account_actor_id)
            ),
        }
        for r in rows
    ]
    consensus = [{"disease": str(t[0]), "count": int(t[1])} for t in tally]
    return {
        "opinions": opinions,
        "consensus": consensus,
        "views": view_count_for(scan_id),
        "wants_opinion": request_row is not None,
        "opinion_requested_at": int(request_row[0]) if request_row else None,
    }


def opinion_counts_for_user(user_pseudo_id: str, *, limit: int = 200) -> dict:
    """Opinion counts on a user's OWN shared scans — drives the client's
    "someone left a second opinion" notification poll.

    Restricted to scans whose owner explicitly REQUESTED a second opinion
    (see :func:`set_wants_opinion`): sharing a scan to the corpus is not a
    request to be pinged, so opinions on scans the owner never asked about
    don't drive notifications.

    Each of the caller's requested scans that has at least one opinion FROM
    SOMEONE ELSE (your own opinions on your scan don't notify you), with the
    count, the latest opinion timestamp, and the scan's disease so the client
    can say "someone weighed in on your <disease> scan".

    Shape::
        {"scans": [{"scan_id", "disease", "opinion_count", "latest_at",
                    "view_count", "latest_nickname"}, ...],
         "total_opinions": N}
    """
    init_db()
    _sweep_expired_requests()
    uid = (user_pseudo_id or "").strip()
    if not uid:
        return {"scans": [], "total_opinions": 0, "total_views": 0}
    limit = max(1, min(int(limit), 500))
    request_cutoff = int(time.time()) - _OPINION_REQUEST_TTL_S
    with _connect() as conn:
        rows = conn.execute(
            "SELECT s.scan_id, s.disease, COUNT(o.id) AS n, MAX(o.created_at) AS latest, "
            "  (SELECT COUNT(*) FROM community_scan_views v WHERE v.scan_id = s.scan_id) AS views, "
            "  (SELECT o2.nickname FROM community_opinions o2 "
            "     WHERE o2.scan_id = s.scan_id AND o2.author_pseudo_id != s.user_pseudo_id "
            "     ORDER BY o2.created_at DESC LIMIT 1) AS latest_nick "
            "FROM community_scans s "
            "JOIN community_opinions o "
            "  ON o.scan_id = s.scan_id AND o.author_pseudo_id != s.user_pseudo_id "
            "WHERE s.user_pseudo_id = ? "
            "  AND EXISTS (SELECT 1 FROM community_opinion_requests r "
            "              WHERE r.scan_id = s.scan_id "
            "                AND r.created_at >= ?) "
            "GROUP BY s.scan_id, s.disease "
            "ORDER BY latest DESC LIMIT ?",
            (uid, request_cutoff, limit),
        ).fetchall()
    scans = [
        {"scan_id": str(r[0]), "disease": str(r[1]),
         "opinion_count": int(r[2]), "latest_at": int(r[3]),
         "view_count": int(r[4]),
         "latest_nickname": (r[5] if len(r) > 5 else None) or None}
        for r in rows
    ]
    return {
        "scans": scans,
        "total_opinions": sum(s["opinion_count"] for s in scans),
        "total_views": sum(s["view_count"] for s in scans),
    }


def opinion_counts_for_actor(account_actor_id: str, *, limit: int = 200) -> dict:
    """Private opinion/view summary for scans owned by one signed actor."""
    init_db()
    _sweep_expired_requests()
    try:
        actor = str(uuid.UUID(account_actor_id))
    except (TypeError, ValueError, AttributeError):
        return {"scans": [], "total_opinions": 0, "total_views": 0}
    limit = max(1, min(int(limit), 500))
    request_cutoff = int(time.time()) - _OPINION_REQUEST_TTL_S
    with _connect() as conn:
        if _actor_is_deleted(conn, actor):
            return {"scans": [], "total_opinions": 0, "total_views": 0}
        view_block_clause, view_block_params = _blocked_actor_clause(
            actor, actor_column="v.account_actor_id"
        )
        latest_block_clause, latest_block_params = _blocked_actor_clause(
            actor, actor_column="o2.account_actor_id"
        )
        opinion_block_clause, opinion_block_params = _blocked_actor_clause(
            actor, actor_column="o.account_actor_id"
        )
        # Each appended block predicate uses an allowlisted actor column and
        # bound actor IDs; the rest of this query is fixed server SQL.
        rows = conn.execute(
            "SELECT s.scan_id, s.disease, COUNT(o.id) AS n, MAX(o.created_at) AS latest, "  # nosec B608
            "  (SELECT COUNT(*) FROM community_scan_views v "
            "     WHERE v.scan_id = s.scan_id "
            "       AND (v.account_actor_id IS NULL OR v.account_actor_id NOT IN "
            "            (SELECT actor_id FROM community_deleted_actors))"
            + view_block_clause
            + ") AS views, "  # nosec B608
            "  (SELECT o2.nickname FROM community_opinions o2 "
            "     WHERE o2.scan_id = s.scan_id "
            "       AND (o2.account_actor_id IS NULL OR o2.account_actor_id != s.account_actor_id) "
            "       AND (o2.account_actor_id IS NULL OR o2.account_actor_id NOT IN "
            "            (SELECT actor_id FROM community_deleted_actors))"
            + latest_block_clause
            + " "  # nosec B608
            "     ORDER BY o2.created_at DESC LIMIT 1) AS latest_nick "
            "FROM community_scans s "
            "JOIN community_opinions o ON o.scan_id = s.scan_id "
            "  AND (o.account_actor_id IS NULL OR o.account_actor_id != s.account_actor_id) "
            "  AND (o.account_actor_id IS NULL OR o.account_actor_id NOT IN "
            "       (SELECT actor_id FROM community_deleted_actors))"
            + opinion_block_clause
            + " "  # nosec B608
            "WHERE s.account_actor_id = ? "
            "  AND EXISTS (SELECT 1 FROM community_opinion_requests r "
            "              WHERE r.scan_id = s.scan_id AND r.account_actor_id = ? "
            "                AND r.created_at >= ?) "
            "GROUP BY s.scan_id, s.disease "
            "ORDER BY latest DESC LIMIT ?",
            (
                *view_block_params,
                *latest_block_params,
                *opinion_block_params,
                actor,
                actor,
                request_cutoff,
                limit,
            ),
        ).fetchall()
    scans = [
        {
            "scan_id": str(row[0]),
            "disease": str(row[1]),
            "opinion_count": int(row[2]),
            "latest_at": int(row[3]),
            "view_count": int(row[4]),
            "latest_nickname": row[5] or None,
        }
        for row in rows
    ]
    return {
        "scans": scans,
        "total_opinions": sum(scan["opinion_count"] for scan in scans),
        "total_views": sum(scan["view_count"] for scan in scans),
    }


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


def opinion_counts(
    scan_ids: list[str], *, viewer_account_actor_id: str | None = None
) -> dict[str, int]:
    """Map each scan_id to its number of community opinions (zeros omitted)."""
    ids = [s for s in scan_ids if s]
    if not ids:
        return {}
    init_db()
    placeholders = ",".join("?" * len(ids))
    with _connect() as conn:
        blocked_clause, blocked_params = _blocked_actor_clause(
            viewer_account_actor_id
        )
        # Only the count-derived placeholder list is dynamic; values are bound.
        rows = conn.execute(
            "SELECT scan_id, COUNT(*) FROM community_opinions "  # nosec B608
            f"WHERE scan_id IN ({placeholders}) "
            "AND (account_actor_id IS NULL OR account_actor_id NOT IN "
            "(SELECT actor_id FROM community_deleted_actors))"
            + blocked_clause
            + " GROUP BY scan_id",
            (*ids, *blocked_params),
        ).fetchall()
    return {str(r[0]): int(r[1]) for r in rows}


def owner_of(scan_id: str) -> str | None:
    """The pseudo-id that shared ``scan_id`` (None if the scan isn't ours).
    Used by the push path to decide who to notify about a new opinion."""
    sid = (scan_id or "").strip()
    if not sid:
        return None
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT user_pseudo_id FROM community_scans WHERE scan_id = ?", (sid,),
        ).fetchone()
    return str(row[0]) if row else None


def account_owner_of(scan_id: str) -> str | None:
    """Signed actor that owns a new account-era scan; legacy rows return None."""
    sid = (scan_id or "").strip()
    if not sid:
        return None
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT account_actor_id FROM community_scans WHERE scan_id = ? "
            "AND account_actor_id IS NOT NULL AND account_actor_id NOT IN "
            "(SELECT actor_id FROM community_deleted_actors) LIMIT 1",
            (sid,),
        ).fetchone()
    return str(row[0]) if row else None


def _actors_blocked(
    conn: sqlite3.Connection, first_actor_id: str, second_actor_id: str
) -> bool:
    return conn.execute(
        "SELECT 1 FROM community_actor_blocks WHERE "
        "(blocker_actor_id = ? AND blocked_actor_id = ?) OR "
        "(blocker_actor_id = ? AND blocked_actor_id = ?) LIMIT 1",
        (first_actor_id, second_actor_id, second_actor_id, first_actor_id),
    ).fetchone() is not None


def _safety_target(
    conn: sqlite3.Connection, target_type: str, target_id: str
) -> tuple[str, int, str | None, str | None] | None:
    """Resolve a public content token to internal ownership.

    Returns ``(scan_id, opinion_id_or_zero, actor_id, nickname)``.  Actor ids
    never cross the HTTP boundary; callers use them only to bind the safety
    mutation to the actual stored author.
    """
    kind = (target_type or "").strip().lower()
    token = (target_id or "").strip()
    if not token or len(token) > 128:
        return None
    if kind == "scan":
        if len(token) != 32 or not token.startswith(_PUBLIC_TOKEN_PREFIX):
            return None
        row = conn.execute(
            "SELECT scan_id, account_actor_id, nickname FROM community_scans "
            "WHERE public_id = ? "
            "AND (account_actor_id IS NULL OR account_actor_id NOT IN "
            "(SELECT actor_id FROM community_deleted_actors)) LIMIT 1",
            (token,),
        ).fetchone()
        if row is None:
            return None
        return str(row[0]), 0, (str(row[1]) if row[1] else None), row[2] or None
    if kind == "opinion":
        if len(token) != 32 or not token.startswith(_PUBLIC_OPINION_TOKEN_PREFIX):
            return None
        row = conn.execute(
            "SELECT o.scan_id, o.id, o.account_actor_id, o.nickname "
            "FROM community_opinions o "
            "JOIN community_scans s ON s.scan_id = o.scan_id "
            "WHERE o.public_id = ? "
            "AND (o.account_actor_id IS NULL OR o.account_actor_id NOT IN "
            "(SELECT actor_id FROM community_deleted_actors)) "
            "AND (s.account_actor_id IS NULL OR s.account_actor_id NOT IN "
            "(SELECT actor_id FROM community_deleted_actors)) LIMIT 1",
            (token,),
        ).fetchone()
        if row is None:
            return None
        return (
            str(row[0]),
            int(row[1]),
            (str(row[2]) if row[2] else None),
            row[3] or None,
        )
    return None


def record_content_report(
    *,
    reporter_actor_id: str,
    target_type: str,
    target_id: str,
    reason: str,
    now: int | None = None,
) -> bool:
    """Queue an idempotent, account-bound report.

    Returns ``True`` for a newly queued report and ``False`` when this account
    already reported the same target.  The client cannot select the target
    actor, and no free-form text is accepted or stored.
    """
    init_db()
    reporter = _normalise_actor_id(reporter_actor_id)
    reason_clean = (reason or "").strip().lower()
    kind = (target_type or "").strip().lower()
    if reporter is None:
        raise ValueError("invalid_actor")
    if reason_clean not in COMMUNITY_REPORT_REASONS:
        raise ValueError("invalid_reason")
    timestamp = int(time.time() if now is None else now)
    with _write_lock, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if _actor_is_deleted(conn, reporter):
                raise ValueError("deleted account actor")
            target = _safety_target(conn, kind, target_id)
            if target is None:
                raise ValueError("target_not_found")
            scan_id, opinion_id, target_actor, _nickname = target
            if target_actor == reporter:
                raise ValueError("self_target")
            existing = conn.execute(
                "SELECT 1 FROM community_content_reports "
                "WHERE reporter_actor_id = ? AND target_type = ? "
                "AND target_scan_id = ? AND target_opinion_id = ? LIMIT 1",
                (reporter, kind, scan_id, opinion_id),
            ).fetchone()
            if existing is not None:
                conn.execute("COMMIT")
                return False
            recent = int(conn.execute(
                "SELECT COUNT(*) FROM community_content_reports "
                "WHERE reporter_actor_id = ? AND created_at >= ?",
                (reporter, timestamp - 86_400),
            ).fetchone()[0])
            if recent >= COMMUNITY_REPORT_DAILY_LIMIT:
                raise ValueError("report_limit")
            conn.execute(
                "INSERT INTO community_content_reports ("
                "reporter_actor_id, target_type, target_scan_id, "
                "target_opinion_id, target_actor_id, reason, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    reporter,
                    kind,
                    scan_id,
                    opinion_id,
                    target_actor,
                    reason_clean,
                    timestamp,
                ),
            )
            conn.execute("COMMIT")
            return True
        except Exception:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise


def block_content_author(
    *,
    blocker_actor_id: str,
    target_type: str,
    target_id: str,
    now: int | None = None,
) -> dict:
    """Block the verified author of a public content token."""
    init_db()
    blocker = _normalise_actor_id(blocker_actor_id)
    if blocker is None:
        raise ValueError("invalid_actor")
    timestamp = int(time.time() if now is None else now)
    with _write_lock, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if _actor_is_deleted(conn, blocker):
                raise ValueError("deleted account actor")
            target = _safety_target(conn, target_type, target_id)
            if target is None:
                raise ValueError("target_not_found")
            _scan_id, _opinion_id, target_actor, nickname = target
            if target_actor is None:
                raise ValueError("target_not_blockable")
            if target_actor == blocker:
                raise ValueError("self_target")
            existing = conn.execute(
                "SELECT public_id, blocked_nickname, created_at "
                "FROM community_actor_blocks WHERE blocker_actor_id = ? "
                "AND blocked_actor_id = ? LIMIT 1",
                (blocker, target_actor),
            ).fetchone()
            if existing is not None:
                conn.execute("COMMIT")
                return {
                    "created": False,
                    "block_id": str(existing[0]),
                    "nickname": existing[1] or nickname,
                    "created_at": int(existing[2]),
                }
            count = int(conn.execute(
                "SELECT COUNT(*) FROM community_actor_blocks "
                "WHERE blocker_actor_id = ?",
                (blocker,),
            ).fetchone()[0])
            if count >= COMMUNITY_BLOCK_LIMIT:
                raise ValueError("block_limit")
            public_id = _PUBLIC_BLOCK_TOKEN_PREFIX + uuid.uuid4().hex[:30]
            conn.execute(
                "INSERT INTO community_actor_blocks ("
                "public_id, blocker_actor_id, blocked_actor_id, "
                "blocked_nickname, created_at) VALUES (?, ?, ?, ?, ?)",
                (public_id, blocker, target_actor, nickname, timestamp),
            )
            conn.execute("COMMIT")
            return {
                "created": True,
                "block_id": public_id,
                "nickname": nickname,
                "created_at": timestamp,
            }
        except Exception:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise


def actor_blocks(account_actor_id: str, *, limit: int = COMMUNITY_BLOCK_LIMIT) -> list[dict]:
    """Opaque block-management list for one signed actor."""
    init_db()
    actor = _normalise_actor_id(account_actor_id)
    if actor is None:
        return []
    bounded_limit = max(1, min(int(limit), COMMUNITY_BLOCK_LIMIT))
    with _connect() as conn:
        rows = conn.execute(
            "SELECT public_id, blocked_nickname, created_at "
            "FROM community_actor_blocks WHERE blocker_actor_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (actor, bounded_limit),
        ).fetchall()
    return [
        {
            "block_id": str(row[0]),
            "nickname": row[1] or None,
            "created_at": int(row[2]),
        }
        for row in rows
    ]


def unblock_actor(account_actor_id: str, block_id: str) -> bool:
    """Remove only the caller-owned block selected by its opaque id."""
    init_db()
    actor = _normalise_actor_id(account_actor_id)
    public_id = (block_id or "").strip()
    if (
        actor is None
        or not public_id.startswith(_PUBLIC_BLOCK_TOKEN_PREFIX)
        or len(public_id) != 32
    ):
        return False
    with _write_lock, _connect() as conn:
        deleted = conn.execute(
            "DELETE FROM community_actor_blocks "
            "WHERE blocker_actor_id = ? AND public_id = ?",
            (actor, public_id),
        ).rowcount
    return int(deleted or 0) > 0


def pending_content_reports(*, limit: int = 100) -> list[dict]:
    """Local-operator moderation queue; never wired to a public route."""
    init_db()
    bounded_limit = max(1, min(int(limit), 500))
    with _connect() as conn:
        rows = conn.execute(
            "SELECT r.id, r.target_type, r.reason, r.created_at, "
            "r.target_scan_id, r.target_opinion_id, "
            "COALESCE(o.nickname, s.nickname), s.disease, o.note "
            "FROM community_content_reports r "
            "LEFT JOIN community_scans s ON s.scan_id = r.target_scan_id "
            "LEFT JOIN community_opinions o ON o.id = r.target_opinion_id "
            "WHERE r.state = 'pending' ORDER BY r.created_at LIMIT ?",
            (bounded_limit,),
        ).fetchall()
    return [
        {
            "report_id": int(row[0]),
            "target_type": str(row[1]),
            "reason": str(row[2]),
            "created_at": int(row[3]),
            "scan_id": str(row[4]),
            "opinion_id": int(row[5]),
            "nickname": row[6] or None,
            "disease": row[7] or None,
            "note": row[8] or None,
        }
        for row in rows
    ]


def moderate_content_report(
    report_id: int, action: str, *, now: int | None = None
) -> bool:
    """Resolve a queued report from the local operator tool.

    Supported actions are ``dismiss`` and ``remove_content``.  Removing a scan
    also removes its thumbnail, replies, requests, and views transactionally;
    other reports for the same removed content are resolved at the same time.
    """
    init_db()
    resolution = (action or "").strip().lower()
    if resolution not in {"dismiss", "remove_content"}:
        raise ValueError("invalid_action")
    timestamp = int(time.time() if now is None else now)
    thumb_path: str | None = None
    with _write_lock, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT target_type, target_scan_id, target_opinion_id "
                "FROM community_content_reports WHERE id = ? "
                "AND state = 'pending' LIMIT 1",
                (int(report_id),),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return False
            target_type, scan_id, opinion_id = str(row[0]), str(row[1]), int(row[2])
            next_state = "dismissed"
            if resolution == "remove_content":
                next_state = "removed"
                if target_type == "scan":
                    thumb = conn.execute(
                        "SELECT thumb_path FROM community_scans "
                        "WHERE scan_id = ? LIMIT 1",
                        (scan_id,),
                    ).fetchone()
                    thumb_path = str(thumb[0]) if thumb and thumb[0] else None
                    for table in (
                        "community_opinions",
                        "community_opinion_requests",
                        "community_scan_views",
                    ):
                        # table comes from the fixed tuple immediately above.
                        conn.execute(
                            f"DELETE FROM {table} WHERE scan_id = ?",  # nosec B608
                            (scan_id,),
                        )
                    conn.execute(
                        "DELETE FROM community_scans WHERE scan_id = ?", (scan_id,)
                    )
                    conn.execute(
                        "UPDATE community_content_reports SET state = 'removed', "
                        "resolved_at = ? WHERE target_scan_id = ? AND state = 'pending'",
                        (timestamp, scan_id),
                    )
                else:
                    conn.execute(
                        "DELETE FROM community_opinions WHERE id = ?", (opinion_id,)
                    )
                    conn.execute(
                        "UPDATE community_content_reports SET state = 'removed', "
                        "resolved_at = ? WHERE target_type = 'opinion' "
                        "AND target_opinion_id = ? AND state = 'pending'",
                        (timestamp, opinion_id),
                    )
            conn.execute(
                "UPDATE community_content_reports SET state = ?, resolved_at = ? "
                "WHERE id = ?",
                (next_state, timestamp, int(report_id)),
            )
            conn.execute("COMMIT")
        except Exception:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise
    if thumb_path:
        with contextlib.suppress(OSError):
            (COMMUNITY_THUMB_DIR / thumb_path).unlink(missing_ok=True)
    return True


def wants_opinion(scan_id: str) -> bool:
    """True iff the scan's owner explicitly asked the community to weigh in
    and the request hasn't expired (``OPINION_REQUEST_TTL_HOURS``).

    Notifications about new opinions are only sent for scans whose owner
    raised their hand via :func:`set_wants_opinion` — merely sharing a scan
    to the corpus is not a request to be pinged. The durable
    ``community_opinion_requests`` store is the source of truth (it survives
    the upload/flag race), so we check it rather than the materialised
    ``wants_opinion`` column. The TTL is enforced in the query itself so a
    stale row that the lazy sweep hasn't reaped yet still reads as expired."""
    sid = (scan_id or "").strip()
    if not sid:
        return False
    init_db()
    _sweep_expired_requests()
    cutoff = int(time.time()) - _OPINION_REQUEST_TTL_S
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM community_opinion_requests "
            "WHERE scan_id = ? AND created_at >= ? LIMIT 1",
            (sid, cutoff),
        ).fetchone()
    return row is not None


def record_view(
    scan_id: str,
    viewer_pseudo_id: str | None = None,
    *,
    account_actor_id: str | None = None,
) -> bool:
    """Record that the selected legacy-or-account actor opened ``scan_id``.

    Deduped per viewer by partial unique identity indexes, so a
    given user only ever counts once no matter how many times they reopen the
    scan. A scan owner viewing their OWN scan never counts. Returns True only
    when a NEW view row was inserted (the count actually grew)."""
    sid = (scan_id or "").strip()
    if not sid:
        return False
    try:
        legacy_id, actor_id = _identity(viewer_pseudo_id, account_actor_id)
    except ValueError:
        return False
    init_db()
    with _write_lock, _connect() as conn:
        if _actor_is_deleted(conn, actor_id):
            return False
        owner = conn.execute(
            "SELECT user_pseudo_id, account_actor_id FROM community_scans "
            "WHERE scan_id = ? AND (account_actor_id IS NULL OR account_actor_id NOT IN "
            "(SELECT actor_id FROM community_deleted_actors))",
            (sid,),
        ).fetchone()
        # Only count views on a scan that exists and isn't the viewer's own.
        if owner is None or (owner[0] == legacy_id and owner[1] == actor_id):
            return False
        if actor_id is not None and owner[1] is not None:
            blocked = conn.execute(
                "SELECT 1 FROM community_actor_blocks WHERE "
                "(blocker_actor_id = ? AND blocked_actor_id = ?) OR "
                "(blocker_actor_id = ? AND blocked_actor_id = ?) LIMIT 1",
                (actor_id, owner[1], owner[1], actor_id),
            ).fetchone()
            if blocked is not None:
                return False
        cur = conn.execute(
            "INSERT OR IGNORE INTO community_scan_views "
            "(scan_id, viewer_pseudo_id, account_actor_id, created_at) VALUES (?, ?, ?, ?)",
            (sid, legacy_id, actor_id, int(time.time())),
        )
        return int(cur.rowcount or 0) > 0


def view_counts(scan_ids: list[str]) -> dict[str, int]:
    """Map each scan_id to its number of distinct viewers (zeros omitted)."""
    ids = [s for s in scan_ids if s]
    if not ids:
        return {}
    init_db()
    placeholders = ",".join("?" * len(ids))
    with _connect() as conn:
        # Only the count-derived placeholder list is dynamic; values are bound.
        rows = conn.execute(
            "SELECT scan_id, COUNT(*) FROM community_scan_views "  # nosec B608
            f"WHERE scan_id IN ({placeholders}) "
            "AND (account_actor_id IS NULL OR account_actor_id NOT IN "
            "(SELECT actor_id FROM community_deleted_actors)) GROUP BY scan_id",
            tuple(ids),
        ).fetchall()
    return {str(r[0]): int(r[1]) for r in rows}


def view_count_for(scan_id: str) -> int:
    """Distinct-viewer count for a single shared scan (0 if none)."""
    sid = (scan_id or "").strip()
    if not sid:
        return 0
    init_db()
    with _connect() as conn:
        visible = conn.execute(
            "SELECT 1 FROM community_scans WHERE scan_id = ? "
            "AND (account_actor_id IS NULL OR account_actor_id NOT IN "
            "(SELECT actor_id FROM community_deleted_actors)) LIMIT 1",
            (sid,),
        ).fetchone()
        if visible is None:
            return 0
        return int(conn.execute(
            "SELECT COUNT(*) FROM community_scan_views WHERE scan_id = ? "
            "AND (account_actor_id IS NULL OR account_actor_id NOT IN "
            "(SELECT actor_id FROM community_deleted_actors))",
            (sid,),
        ).fetchone()[0])


def scan_counts(scan_ids: list[str]) -> dict[str, dict]:
    """Batched per-scan ``{opinions, views}`` map for a set of scan_ids — the
    Gallery surfaces these badges on the caller's own rows. Scans with neither
    are omitted so the client can default them to zero."""
    op = opinion_counts(scan_ids)
    vw = view_counts(scan_ids)
    return {
        sid: {"opinions": op.get(sid, 0), "views": vw.get(sid, 0)}
        for sid in (set(op) | set(vw))
    }


def scan_counts_for_actor(account_actor_id: str, scan_ids: list[str]) -> dict[str, dict]:
    """Counts only for ids owned by the signed actor (unknown ids are omitted)."""
    try:
        actor = str(uuid.UUID(account_actor_id))
    except (TypeError, ValueError, AttributeError):
        return {}
    ids = [scan_id for scan_id in scan_ids if scan_id]
    if not ids:
        return {}
    init_db()
    placeholders = ",".join("?" * len(ids))
    with _connect() as conn:
        owned = [
            str(row[0])
            # Only the count-derived placeholder list is dynamic; values are bound.
            for row in conn.execute(
                "SELECT scan_id FROM community_scans WHERE account_actor_id = ? "  # nosec B608
                f"AND scan_id IN ({placeholders}) AND account_actor_id NOT IN "
                "(SELECT actor_id FROM community_deleted_actors)",
                (actor, *ids),
            ).fetchall()
        ]
    return scan_counts(owned)


def scans_by_species(
    scientific: str,
    *,
    limit: int = 12,
    exclude_user_pseudo_id: str | None = None,
    exclude_account_actor_id: str | None = None,
    viewer_account_actor_id: str | None = None,
) -> list[dict]:
    """Community scans of one species (by scientific name), newest first,
    across all regions — for the result screen's 'other <species> in the
    community' strip."""
    init_db()
    sci = (scientific or "").strip().lower()
    if not sci:
        return []
    limit = max(1, min(int(limit), 50))
    extra = ""
    params: list = [sci]
    if exclude_user_pseudo_id:
        extra += " AND user_pseudo_id != ?"
        params.append(exclude_user_pseudo_id)
    if exclude_account_actor_id:
        extra += " AND (account_actor_id IS NULL OR account_actor_id != ?)"
        params.append(exclude_account_actor_id)
    blocked_clause, blocked_params = _blocked_actor_clause(
        viewer_account_actor_id
    )
    extra += blocked_clause
    params.extend(blocked_params)
    if _HIDDEN_COMMUNITY_DISEASES:
        extra += _hidden_clause()
        params.extend(_HIDDEN_COMMUNITY_DISEASES)
    with _connect() as conn:
        # _FEED_COLUMNS and all clauses are fixed server-owned SQL.
        rows = conn.execute(
            f"SELECT {_FEED_COLUMNS} "  # nosec B608
            "FROM community_scans WHERE species_lower = ?"
            + extra
            + " ORDER BY created_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
    out = [_row_to_dict(r) for r in rows]
    # This cross-region strip has no requester anchor to measure from, so we
    # just drop the raw coords (never leaked to viewers); no distance shown.
    for row in out:
        row.pop("lat", None)
        row.pop("lon", None)
    return out


def nearby_scans(
    *,
    country: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    radius_km: float = NEARBY_RADIUS_KM,
    disease: str | None = None,
    species: str | None = None,
    limit: int = 20,
    exclude_user_pseudo_id: str | None = None,
    exclude_account_actor_id: str | None = None,
    viewer_account_actor_id: str | None = None,
    scope: str = "nearby",
) -> list[dict]:
    """Most recent shared scans for one scope.

    ``scope="nearby"`` (default) selects scans within ``radius_km`` of
    ``lat``/``lon`` (the requester's coarse location); ``scope="country"``
    selects the whole ``country``; ``scope="worldwide"`` drops the geo
    predicate entirely (the whole corpus, most recent first — the requester's
    location is irrelevant). A *disease-scoped* call (the result-screen
    "similar scans" carousel) widens nearby→country to stay filled but never
    crosses to a different disease; the disease-agnostic feed stays strictly
    in its scope so the Nearby/Country/Worldwide toggle actually changes what's
    shown.
    """
    init_db()
    # Keep the feed's "wants opinion" badge honest: reap expired requests
    # (and their materialised scan-row flags) before reading.
    _sweep_expired_requests()
    if country:
        country = to_iso_alpha2(country)
    limit = max(1, min(int(limit), 100))
    has_coords = lat is not None and lon is not None
    scope = (scope or "nearby").strip().lower()
    if scope not in ("nearby", "country", "worldwide"):
        scope = "nearby"
    nearby = scope == "nearby"

    with _connect() as conn:
        common = ""
        cparams: list = []
        if exclude_user_pseudo_id:
            common += " AND user_pseudo_id != ?"
            cparams.append(exclude_user_pseudo_id)
        if exclude_account_actor_id:
            common += " AND (account_actor_id IS NULL OR account_actor_id != ?)"
            cparams.append(exclude_account_actor_id)
        blocked_clause, blocked_params = _blocked_actor_clause(
            viewer_account_actor_id
        )
        common += blocked_clause
        cparams.extend(blocked_params)
        if _HIDDEN_COMMUNITY_DISEASES:
            common += _hidden_clause()
            cparams.extend(_HIDDEN_COMMUNITY_DISEASES)

        # Geo scopes, most specific first. A disease-scoped carousel widens
        # nearby→country; the plain feed stays in a single scope.
        widen = disease is not None
        geo_scopes: list[tuple[str, list, bool]] = []
        if scope == "worldwide":
            # No geo predicate at all — every country, newest first. The
            # requester's coords/country are intentionally ignored so the
            # feed works even without a location fix.
            geo_scopes.append(("", [], False))
        else:
            if nearby and has_coords:
                min_lat, max_lat, min_lon, max_lon = _bbox(lat, lon, radius_km)
                geo_scopes.append((
                    " AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
                    [min_lat, max_lat, min_lon, max_lon], True,
                ))
            if country and (scope == "country" or widen or not has_coords):
                geo_scopes.append((" AND country = ?", [country], False))

        species_lower = (species or "").strip().lower()
        seen: set[str] = set()
        out: list[dict] = []

        def _run(geo_clause, geo_params, dz_clause, dz_params, refine):
            # Over-fetch when we'll refine the box to a true circle so the
            # post-filter still has ~limit rows to offer.
            take = limit * 3 if refine else limit
            # The column list and clauses are fixed; all request values are bound.
            sql = (
                f"SELECT {_FEED_COLUMNS} FROM community_scans WHERE 1=1"  # nosec B608
                + geo_clause + common + dz_clause
                + " ORDER BY created_at DESC LIMIT ?"
            )
            rows = [
                _row_to_dict(r)
                for r in conn.execute(
                    sql, (*geo_params, *cparams, *dz_params, take),
                ).fetchall()
            ]
            if refine:
                rows = [
                    r for r in rows
                    if r["lat"] is not None and r["lon"] is not None
                    and _haversine_km(lat, lon, r["lat"], r["lon"]) <= radius_km
                ]
            for row in rows:
                if row["scan_id"] in seen:
                    continue
                out.append(row)
                seen.add(row["scan_id"])

        for geo_clause, geo_params, refine in geo_scopes:
            if len(out) >= limit:
                break
            if disease and species_lower:
                _run(geo_clause, geo_params,
                     " AND disease = ? AND species_lower = ?",
                     [disease, species_lower], refine)
            if len(out) < limit and disease:
                _run(geo_clause, geo_params, " AND disease = ?", [disease], refine)
            if len(out) < limit and not disease:
                _run(geo_clause, geo_params, "", [], refine)
        result = out[:limit]
        # Attach an approximate distance from the requester ("~5 km near you")
        # and DROP the raw coords — a viewer gets the distance, never another
        # user's ~1 km location. Distance is omitted when we lack either point.
        for row in result:
            rlat, rlon = row.get("lat"), row.get("lon")
            if has_coords and rlat is not None and rlon is not None:
                row["distance_km"] = round(_haversine_km(lat, lon, rlat, rlon), 1)
            else:
                row["distance_km"] = None
            row.pop("lat", None)
            row.pop("lon", None)
        return result


def map_scans(
    min_lat: float,
    max_lat: float,
    min_lon: float,
    max_lon: float,
    *,
    disease: str | None = None,
    days: int = 30,
    limit: int = 300,
    exclude_user_pseudo_id: str | None = None,
    exclude_account_actor_id: str | None = None,
    viewer_account_actor_id: str | None = None,
) -> dict:
    """Individual shared scans inside a bbox — the zoomed-in map markers.

    Unlike the feed (:func:`nearby_scans`), this KEEPS each scan's ~1 km-rounded
    ``lat``/``lon`` so the client can pin a marker: the user explicitly wants
    individual scans on the map, and the coords are already coarse + anonymous
    (public token + nickname, never the real scan_id). ``not_a_plant`` excluded;
    newest-first, capped at ``limit`` (``truncated`` flags an over-full bbox so
    the UI can prompt "zoom in to see all")."""
    init_db()
    limit = max(1, min(int(limit), 500))
    days = max(1, min(int(days), 90))
    cutoff = int(time.time()) - days * 86_400
    clause = (
        " AND lat IS NOT NULL AND lon IS NOT NULL"
        " AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? AND created_at >= ?"
    )
    params: list = [min_lat, max_lat, min_lon, max_lon, cutoff]
    if exclude_user_pseudo_id:
        clause += " AND user_pseudo_id != ?"
        params.append(exclude_user_pseudo_id)
    if exclude_account_actor_id:
        clause += " AND (account_actor_id IS NULL OR account_actor_id != ?)"
        params.append(exclude_account_actor_id)
    blocked_clause, blocked_params = _blocked_actor_clause(
        viewer_account_actor_id
    )
    clause += blocked_clause
    params.extend(blocked_params)
    if disease:
        clause += " AND disease = ?"
        params.append(disease)
    clause += _hidden_clause()
    params.extend(_HIDDEN_COMMUNITY_DISEASES)
    with _connect() as conn:
        # The column list and clauses are fixed or allowlisted; values are bound.
        rows = conn.execute(
            f"SELECT {_FEED_COLUMNS} FROM community_scans WHERE 1=1"  # nosec B608
            + clause
            + " ORDER BY created_at DESC LIMIT ?",
            (*params, limit + 1),
        ).fetchall()
    truncated = len(rows) > limit
    # _row_to_dict keeps lat/lon — the map needs them (the feed strips them).
    return {"items": [_row_to_dict(r) for r in rows[:limit]], "truncated": truncated}


_DENSITY_K = 3


def _density_cell_size(lat_span: float) -> float:
    """Grid-cell size (deg) for a bbox's latitude span — coarser when zoomed
    out, floored at 0.05° (~5.5 km, ~5x the ~1.1 km storage rounding) so a cell
    always blends many distinct positions and no single ~1 km scan is
    pinpointable."""
    if lat_span > 40:
        return 1.0
    if lat_span > 15:
        return 0.5
    if lat_span > 5:
        return 0.2
    if lat_span > 2:
        return 0.1
    return 0.05


def density_grid(
    min_lat: float,
    max_lat: float,
    min_lon: float,
    max_lon: float,
    *,
    disease: str | None = None,
    days: int = 30,
    k: int = _DENSITY_K,
) -> dict:
    """K-anonymized scan density for the zoomed-out heat layer.

    Buckets the (already 2-dp-rounded) coords into grid cells and returns only
    cells holding >= ``k`` scans AFTER the disease/time filter, so a returned
    cell never represents fewer than ``k`` scans; ``not_a_plant`` excluded. The
    map receives cell centers + counts only — never per-scan coords."""
    init_db()
    days = max(1, min(int(days), 90))
    cutoff = int(time.time()) - days * 86_400
    cell = _density_cell_size(max_lat - min_lat)
    disease_clause = ""
    disease_params: list = []
    if disease:
        disease_clause = " AND disease = ?"
        disease_params.append(disease)
    hidden_clause = _hidden_clause()
    # SQLite CAST(.. AS INTEGER) truncates toward zero — wrong floor for negative
    # (W/S) coords — so floor with ROUND(z/cell - 0.5) to bucket on a stable grid.
    with _connect() as conn:
        # The optional disease clause is fixed and all request values are bound.
        rows = conn.execute(
            "SELECT CAST(ROUND(lat/? - 0.5) AS INTEGER) AS ilat,"  # nosec B608
            "       CAST(ROUND(lon/? - 0.5) AS INTEGER) AS ilon, COUNT(*) AS n "
            "FROM community_scans "
            "WHERE lat IS NOT NULL AND lon IS NOT NULL "
            "  AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? AND created_at >= ?"
            + disease_clause + hidden_clause
            + " GROUP BY ilat, ilon HAVING n >= ?",
            (cell, cell, min_lat, max_lat, min_lon, max_lon, cutoff,
             *disease_params, *_HIDDEN_COMMUNITY_DISEASES, int(k)),
        ).fetchall()
    cells = [
        {"lat": round(r[0] * cell, 6), "lon": round(r[1] * cell, 6), "count": int(r[2])}
        for r in rows
    ]
    return {
        "cells": cells,
        "cell_size_deg": cell,
        "max_count": max((c["count"] for c in cells), default=0),
        "total": sum(c["count"] for c in cells),
        "k": int(k),
        "days": days,
    }


def region_summary(
    country: str,
    region: str | None = None,
    *,
    exclude_user_pseudo_id: str | None = None,
    exclude_account_actor_id: str | None = None,
    viewer_account_actor_id: str | None = None,
) -> dict:
    """Aggregate the corpus for one country (or country+region)."""
    init_db()
    country = to_iso_alpha2(country)
    now = int(time.time())
    week_ago = now - 7 * 86_400
    region_clean = (region or "").strip()
    exclude_clause = ""
    exclude_params: tuple = ()
    if exclude_user_pseudo_id:
        exclude_clause = " AND user_pseudo_id != ?"
        exclude_params = (exclude_user_pseudo_id,)
    if exclude_account_actor_id:
        exclude_clause += " AND (account_actor_id IS NULL OR account_actor_id != ?)"
        exclude_params = (*exclude_params, exclude_account_actor_id)
    blocked_clause, blocked_params = _blocked_actor_clause(
        viewer_account_actor_id
    )
    exclude_clause += blocked_clause
    exclude_params = (*exclude_params, *blocked_params)
    if _HIDDEN_COMMUNITY_DISEASES:
        exclude_clause += _hidden_clause()
        exclude_params = (*exclude_params, *_HIDDEN_COMMUNITY_DISEASES)
    with _connect() as conn:
        if region_clean:
            # exclude_clause is assembled from fixed predicates; values are bound.
            total = conn.execute(
                "SELECT COUNT(*) FROM community_scans "  # nosec B608
                "WHERE country = ? AND region = ?" + exclude_clause,
                (country, region_clean, *exclude_params),
            ).fetchone()[0]
            last_week = conn.execute(
                "SELECT COUNT(*) FROM community_scans "  # nosec B608
                "WHERE country = ? AND region = ? AND created_at >= ?"
                + exclude_clause,
                (country, region_clean, week_ago, *exclude_params),
            ).fetchone()[0]
            disease_rows = conn.execute(
                "SELECT disease, COUNT(*) AS n FROM community_scans "  # nosec B608
                "WHERE country = ? AND region = ? AND created_at >= ?"
                + exclude_clause
                + " GROUP BY disease ORDER BY n DESC LIMIT 3",
                (country, region_clean, week_ago, *exclude_params),
            ).fetchall()
            top_region_rows: list[tuple] = []
        else:
            total = conn.execute(
                "SELECT COUNT(*) FROM community_scans WHERE country = ?"  # nosec B608
                + exclude_clause,
                (country, *exclude_params),
            ).fetchone()[0]
            last_week = conn.execute(
                "SELECT COUNT(*) FROM community_scans "  # nosec B608
                "WHERE country = ? AND created_at >= ?" + exclude_clause,
                (country, week_ago, *exclude_params),
            ).fetchone()[0]
            disease_rows = conn.execute(
                "SELECT disease, COUNT(*) AS n FROM community_scans "  # nosec B608
                "WHERE country = ? AND created_at >= ?" + exclude_clause
                + " GROUP BY disease ORDER BY n DESC LIMIT 3",
                (country, week_ago, *exclude_params),
            ).fetchall()
            top_region_rows = conn.execute(
                "SELECT region, COUNT(*) AS n FROM community_scans "  # nosec B608
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


def popular_snapshot(
    country: str | None = None,
    *,
    lat: float | None = None,
    lon: float | None = None,
    radius_km: float = NEARBY_RADIUS_KM,
    limit: int = 10,
) -> dict:
    """Live popular species + diseases for one scope.

    Scope precedence: coords (the ~30 km "nearby" box) → ``country`` →
    worldwide (no filter). The caller runs the nearby → country → worldwide
    ladder by calling this with progressively wider arguments.

    The nearby scope uses the bounding box (not a refined circle) — for a
    popularity *aggregate* the square is plenty; the visible feed
    (:func:`nearby_scans`) is the one that refines to a true radius.
    """
    init_db()
    country_clean = to_iso_alpha2(country)
    limit = max(1, min(int(limit), 20))
    where = ""
    params: tuple = ()
    if lat is not None and lon is not None:
        min_lat, max_lat, min_lon, max_lon = _bbox(lat, lon, radius_km)
        where = " AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?"
        params = (min_lat, max_lat, min_lon, max_lon)
    elif country_clean:
        where = " AND country = ?"
        params = (country_clean,)
    hidden_clause = _hidden_clause()
    with _connect() as conn:
        # where/hidden_clause use fixed predicates and allowlisted identifiers.
        total = int(conn.execute(
            "SELECT COUNT(*) FROM community_scans WHERE 1=1"  # nosec B608
            + where + hidden_clause,
            (*params, *_HIDDEN_COMMUNITY_DISEASES),
        ).fetchone()[0])
        species_rows = conn.execute(
            "SELECT MAX(species) AS sp, COUNT(*) AS n FROM community_scans "  # nosec B608
            "WHERE species_lower IS NOT NULL AND species_lower != ''"
            + where + hidden_clause
            + " GROUP BY species_lower ORDER BY n DESC LIMIT ?",
            (*params, *_HIDDEN_COMMUNITY_DISEASES, limit),
        ).fetchall()
        disease_rows = conn.execute(
            "SELECT disease, COUNT(*) AS n FROM community_scans "  # nosec B608
            "WHERE 1=1" + where + hidden_clause
            + " GROUP BY disease ORDER BY n DESC LIMIT ?",
            (*params, *_HIDDEN_COMMUNITY_DISEASES, limit),
        ).fetchall()

    def pct(n: int) -> int:
        return int(round((n / total) * 100)) if total > 0 else 0

    return {
        "total": total,
        "top_species": [
            {"species": str(r[0]), "count": int(r[1]), "percent": pct(int(r[1]))}
            for r in species_rows
        ],
        "top_diseases": [
            {"disease": str(r[0]), "count": int(r[1]), "percent": pct(int(r[1]))}
            for r in disease_rows
        ],
    }


def prune_older_than(days: int) -> int:
    """Delete rows + thumbnails older than ``days`` days."""
    init_db()
    if days <= 0:
        return 0
    cutoff = int(time.time()) - days * 86_400
    with _write_lock, _connect() as conn:
        conn.execute(
            "DELETE FROM community_analysis_tokens WHERE expires_at <= ?",
            (int(time.time()),),
        )
        # The durable deletion tombstone is the sole replay-suppression record.
        # Completed cleanup jobs duplicate the same actor UUID without serving
        # a retry purpose, so remove rows left by older deployments.
        conn.execute("DELETE FROM community_cleanup_jobs WHERE state = 'completed'")
        rows = conn.execute(
            "SELECT id, thumb_path FROM community_scans WHERE created_at < ?",
            (cutoff,),
        ).fetchall()
        removable: list[int] = []
        # Keep the path discoverable in SQLite until its file is gone. If an
        # unlink fails, retaining the row lets the next retention/account-
        # deletion pass retry instead of orphaning private media forever.
        for row_id, thumb_path in rows:
            if thumb_path:
                try:
                    (COMMUNITY_THUMB_DIR / str(thumb_path)).unlink(missing_ok=True)
                except OSError:
                    continue
            removable.append(int(row_id))
        # Coordinate each scan removal with all of its companion rows. This
        # transaction shares the same SQLite write reservation as the opinion
        # writer, so whichever operation wins leaves a consistent result.
        deleted = 0
        conn.execute("BEGIN IMMEDIATE")
        try:
            for row_id in removable:
                row = conn.execute(
                    "SELECT scan_id FROM community_scans "
                    "WHERE id = ? AND created_at < ?",
                    (row_id, cutoff),
                ).fetchone()
                if row is None:
                    continue
                scan_id = str(row[0])
                removed = int(
                    conn.execute(
                        "DELETE FROM community_scans WHERE id = ? AND created_at < ?",
                        (row_id, cutoff),
                    ).rowcount
                    or 0
                )
                deleted += removed
                if removed and conn.execute(
                    "SELECT 1 FROM community_scans WHERE scan_id = ? LIMIT 1",
                    (scan_id,),
                ).fetchone() is None:
                    # Delete every companion for the scan, including one that
                    # raced in immediately before this transaction. A writer
                    # that starts later waits for COMMIT and then rejects the
                    # absent target in its own BEGIN IMMEDIATE transaction.
                    for table in (
                        "community_opinions",
                        "community_scan_views",
                        "community_opinion_requests",
                    ):
                        conn.execute(
                            f"DELETE FROM {table} WHERE scan_id = ?",
                            (scan_id,),
                        )
            # Sweep old orphan rows left by historical versions. The age guard
            # retains a fresh request that legitimately arrives before upload.
            for table in (
                "community_opinions",
                "community_scan_views",
                "community_opinion_requests",
            ):
                conn.execute(
                    f"DELETE FROM {table} WHERE created_at < ? AND scan_id NOT IN "
                    "(SELECT scan_id FROM community_scans)",
                    (cutoff,),
                )
            report_cutoff = (
                int(time.time()) - COMMUNITY_REPORT_RETENTION_DAYS * 86_400
            )
            conn.execute(
                "DELETE FROM community_content_reports WHERE created_at < ?",
                (report_cutoff,),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return int(deleted or 0)


def thumbnail_is_visible(filename: str) -> bool:
    """Whether a thumbnail belongs to a currently visible community row."""
    name = (filename or "").strip()
    if not name:
        return False
    init_db()
    with _connect() as conn:
        return conn.execute(
            "SELECT 1 FROM community_scans WHERE thumb_path = ? "
            "AND (account_actor_id IS NULL OR account_actor_id NOT IN "
            "(SELECT actor_id FROM community_deleted_actors)) LIMIT 1",
            (name,),
        ).fetchone() is not None


def request_actor_deletion(account_actor_id: str, *, now: int | None = None) -> bool:
    """Tombstone an actor immediately and enqueue idempotent physical cleanup."""
    init_db()
    try:
        actor = str(uuid.UUID(account_actor_id))
    except (TypeError, ValueError, AttributeError):
        return False
    timestamp = int(time.time() if now is None else now)
    with _write_lock, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "INSERT OR IGNORE INTO community_deleted_actors (actor_id, requested_at) "
                "VALUES (?, ?)",
                (actor, timestamp),
            )
            # Claimed publication capabilities contain the private account
            # actor link. Erase those rows in the same transaction as the
            # tombstone so the account service can treat acceptance as the
            # privacy boundary; never touch unrelated unclaimed capabilities.
            conn.execute(
                "DELETE FROM community_analysis_tokens WHERE claimed_actor_id = ?",
                (actor,),
            )
            conn.execute(
                "INSERT INTO community_cleanup_jobs "
                "(actor_id, state, attempt_count, next_attempt_at, created_at, updated_at) "
                "VALUES (?, 'pending', 0, ?, ?, ?) "
                "ON CONFLICT(actor_id) DO UPDATE SET "
                "state = CASE WHEN state = 'completed' THEN state ELSE 'pending' END, "
                "next_attempt_at = CASE WHEN state = 'completed' THEN next_attempt_at ELSE excluded.next_attempt_at END, "
                "updated_at = excluded.updated_at",
                (actor, timestamp, timestamp, timestamp),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return True


def update_actor_nickname(account_actor_id: str, nickname: str) -> int:
    """Replace denormalized historical nicknames for one stable account actor."""
    init_db()
    try:
        actor = str(uuid.UUID(account_actor_id))
    except (TypeError, ValueError, AttributeError):
        return 0
    cleaned = (nickname or "").strip()[:48]
    if not cleaned:
        return 0
    with _write_lock, _connect() as conn:
        if _actor_is_deleted(conn, actor):
            return 0
        scans = conn.execute(
            "UPDATE community_scans SET nickname = ? WHERE account_actor_id = ?",
            (cleaned, actor),
        ).rowcount
        opinions = conn.execute(
            "UPDATE community_opinions SET nickname = ? WHERE account_actor_id = ?",
            (cleaned, actor),
        ).rowcount
    return int(scans or 0) + int(opinions or 0)


def process_cleanup_jobs(*, now: int | None = None, limit: int = 20) -> dict[str, int]:
    """Remove tombstoned actor content, retrying transient filesystem failures."""
    init_db()
    timestamp = int(time.time() if now is None else now)
    bounded_limit = max(1, min(int(limit), 100))
    with _connect() as conn:
        jobs = conn.execute(
            "SELECT actor_id, attempt_count FROM community_cleanup_jobs "
            "WHERE state IN ('pending', 'retry') AND next_attempt_at <= ? "
            "ORDER BY next_attempt_at, created_at LIMIT ?",
            (timestamp, bounded_limit),
        ).fetchall()
    completed = 0
    retried = 0
    for actor, attempt_count in jobs:
        with _connect() as conn:
            thumbs = [
                str(row[0])
                for row in conn.execute(
                    "SELECT thumb_path FROM community_scans "
                    "WHERE account_actor_id = ? AND thumb_path IS NOT NULL",
                    (actor,),
                ).fetchall()
            ]
        filesystem_failed = False
        for thumb in thumbs:
            try:
                (COMMUNITY_THUMB_DIR / thumb).unlink(missing_ok=True)
            except OSError:
                filesystem_failed = True
        if filesystem_failed:
            attempts = int(attempt_count) + 1
            delay = min(86_400, 60 * (2 ** min(attempts - 1, 10)))
            with _write_lock, _connect() as conn:
                conn.execute(
                    "UPDATE community_cleanup_jobs SET state = 'retry', "
                    "attempt_count = ?, next_attempt_at = ?, last_error_code = 'thumbnail_io', "
                    "updated_at = ? WHERE actor_id = ?",
                    (attempts, timestamp + delay, timestamp, actor),
                )
            retried += 1
            continue

        with _write_lock, _connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                scan_ids = [
                    str(row[0])
                    for row in conn.execute(
                        "SELECT scan_id FROM community_scans WHERE account_actor_id = ?",
                        (actor,),
                    ).fetchall()
                ]
                if scan_ids:
                    placeholders = ",".join("?" * len(scan_ids))
                    for table in (
                        "community_opinions",
                        "community_opinion_requests",
                        "community_scan_views",
                    ):
                        # table is fixed and placeholders are count-derived.
                        conn.execute(
                            f"DELETE FROM {table} WHERE scan_id IN ({placeholders})",  # nosec B608
                            tuple(scan_ids),
                        )
                    conn.execute(
                        "DELETE FROM community_content_reports "  # nosec B608
                        f"WHERE target_scan_id IN ({placeholders})",
                        tuple(scan_ids),
                    )
                conn.execute(
                    "DELETE FROM community_content_reports "
                    "WHERE reporter_actor_id = ? OR target_actor_id = ?",
                    (actor, actor),
                )
                conn.execute(
                    "DELETE FROM community_actor_blocks "
                    "WHERE blocker_actor_id = ? OR blocked_actor_id = ?",
                    (actor, actor),
                )
                conn.execute(
                    "DELETE FROM community_opinions WHERE account_actor_id = ?", (actor,)
                )
                conn.execute(
                    "DELETE FROM community_opinion_requests WHERE account_actor_id = ?",
                    (actor,),
                )
                conn.execute(
                    "DELETE FROM community_scan_views WHERE account_actor_id = ?", (actor,)
                )
                conn.execute(
                    "DELETE FROM community_scans WHERE account_actor_id = ?", (actor,)
                )
                conn.execute(
                    "DELETE FROM community_analysis_tokens WHERE claimed_actor_id = ?",
                    (actor,),
                )
                # community_deleted_actors is the durable suppression marker.
                # Once physical cleanup succeeds this retry row has no further
                # purpose and would only duplicate the deleted actor UUID.
                conn.execute(
                    "DELETE FROM community_cleanup_jobs WHERE actor_id = ?",
                    (actor,),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        completed += 1
    return {"completed": completed, "retried": retried}


def _author_token(user_pseudo_id: str | None) -> str | None:
    """Rotating, non-reversible per-author token for anonymous avatars. Mixes
    the 30-day epoch so the same user maps to a new token each window — the
    pseudo id can't be recovered, and activity isn't linkable across windows."""
    if not user_pseudo_id:
        return None
    seed = f"{user_pseudo_id}|{_identity_epoch()}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def _row_to_dict(row: tuple) -> dict:
    return {
        # Real scan_id — INTERNAL ONLY. Viewer-facing endpoints must
        # replace it with ``public_id`` before serialising (see the
        # nearby / by-species handlers in main.py). Column order matches
        # ``_FEED_COLUMNS``.
        "scan_id": row[0],
        "public_id": public_scan_token(str(row[0])),
        "country": row[1] or "",
        "region": row[2] or "",
        "city": row[3] or "",
        "lat": float(row[4]) if row[4] is not None else None,
        "lon": float(row[5]) if row[5] is not None else None,
        "disease": row[6],
        "confidence": float(row[7]) if row[7] is not None else None,
        "species": row[8],
        "species_score": float(row[9]) if row[9] is not None else None,
        "thumb_path": row[10],
        "locale": row[11],
        "created_at": int(row[12]),
        "wants_opinion": bool(row[13]) if len(row) > 13 else False,
        # One-way, stable per author so the client can show a
        # consistent (but anonymous) avatar — never the raw pseudo id.
        "author_token": _author_token(
            (row[16] if len(row) > 16 else None) or (row[14] if len(row) > 14 else None)
        ),
        # Author's generated plant-vibe handle (community display name); the
        # client also derives the avatar monogram/colour from it on-device.
        "nickname": (row[15] if len(row) > 15 else None) or None,
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


__all__ = [
    "AnalysisTokenConflictError",
    "AnalysisImageMismatchError",
    "AnalysisTokenUnknownError",
    "COMMUNITY_DB_PATH",
    "COMMUNITY_THUMB_DIR",
    "OPINION_REQUEST_TTL_HOURS",
    "account_owner_of",
    "clear_wants_opinion",
    "daily_write_count_for_ip_hash",
    "expire_stale_opinion_requests",
    "density_grid",
    "init_db",
    "map_scans",
    "nearby_scans",
    "opinion_counts",
    "opinion_counts_for_user",
    "opinion_counts_for_actor",
    "opinion_open",
    "opinions_for",
    "owner_of",
    "popular_snapshot",
    "process_cleanup_jobs",
    "prune_older_than",
    "public_scan_token",
    "record_analysis_scan",
    "record_analysis_token",
    "record_opinion",
    "record_opinion_with_status",
    "record_scan",
    "record_view",
    "region_summary",
    "request_actor_deletion",
    "resolve_scan_token",
    "scan_counts",
    "scan_counts_for_actor",
    "scan_exists",
    "scans_by_species",
    "set_wants_opinion",
    "stats_snapshot",
    "thumbnail_is_visible",
    "update_actor_nickname",
    "wants_opinion",
]
