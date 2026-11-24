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
* No precise location lands here. The opted-in Android client sends a
  city and coordinates rounded to roughly 1 km for the nearby radius.
* ``client_ip_hash`` is a nullable legacy column only. Account-era writes leave
  it NULL; abuse limiting uses the live connection address in memory.
* Optional thumbnails are decoded, orientation-normalized, resized, and
  re-encoded as fresh 512px RGB JPEGs without source metadata or filenames.
  The user can opt out from the Android app; uploads stay off until
  explicit consent.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import io
import logging
import math
import os
import secrets
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

try:
    from . import sqlite_store  # type: ignore[import-not-found]
    from .config import get_str  # type: ignore[import-not-found]
    from .countries import to_iso_alpha2
except ImportError:
    import sqlite_store
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
-- Anonymous popularity tally behind the worldwide "popular" lists: one row
-- per UTC day, identified species ('' when none) and diagnosis, holding only
-- a count. No photo, account, actor, device or location is ever stored here.
CREATE TABLE IF NOT EXISTS popular_scan_counts (
    day           INTEGER NOT NULL,
    species_lower TEXT    NOT NULL,
    species       TEXT    NOT NULL,
    disease       TEXT    NOT NULL,
    n             INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, species_lower, disease)
);
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
    author_id       TEXT,
    client_ip_hash  TEXT,
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

CREATE TABLE IF NOT EXISTS community_scan_views (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id          TEXT    NOT NULL,
    viewer_pseudo_id TEXT,
    account_actor_id TEXT,
    created_at       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_views_viewer_pseudo
    ON community_scan_views(viewer_pseudo_id);
CREATE INDEX IF NOT EXISTS ix_views_scan ON community_scan_views(scan_id);

CREATE TABLE IF NOT EXISTS community_reports (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    reporter_pseudo_id TEXT    NOT NULL,
    reporter_author_id TEXT,
    target_type        TEXT    NOT NULL,
    target_id          TEXT    NOT NULL,
    reason             TEXT    NOT NULL,
    client_ip_hash     TEXT,
    created_at         INTEGER NOT NULL,
    UNIQUE(reporter_pseudo_id, target_type, target_id)
);
CREATE INDEX IF NOT EXISTS ix_community_reports_target
    ON community_reports(target_type, target_id, created_at);

CREATE TABLE IF NOT EXISTS community_blocks (
    blocker_pseudo_id TEXT    NOT NULL,
    blocked_pseudo_id TEXT    NOT NULL,
    blocker_author_id TEXT,
    blocked_author_id TEXT,
    client_ip_hash    TEXT,
    created_at        INTEGER NOT NULL,
    PRIMARY KEY (blocker_pseudo_id, blocked_pseudo_id)
);
CREATE INDEX IF NOT EXISTS ix_community_blocks_blocked
    ON community_blocks(blocked_pseudo_id, blocker_pseudo_id);

-- Durable per-device forum identity. ``key_hash`` is SHA-256 of a
-- 32-byte secret the client generates once and never sends in raw form —
-- the row is looked up by that hash, never by anything reversible.
CREATE TABLE IF NOT EXISTS community_authors (
    author_id        TEXT    PRIMARY KEY,
    key_hash         TEXT    UNIQUE NOT NULL,
    key_version      INTEGER NOT NULL DEFAULT 2,
    display_name     TEXT    NOT NULL,
    -- The community_scans.user_pseudo_id
    -- this device used before it had a durable identity. Recorded once, on
    -- first adopt only — this is what a later backfill joins old rows
    -- against; it is not touched again after that first insert.
    legacy_pseudo_id TEXT,
    -- Non-NULL after the legacy attachment suite commits successfully.
    -- Ordinary keyed actions only touch last_seen_at and never rerun it.
    legacy_backfilled_at INTEGER,
    created_at       INTEGER NOT NULL,
    last_seen_at     INTEGER NOT NULL,
    status           TEXT    NOT NULL DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS ix_authors_legacy_pseudo ON community_authors(legacy_pseudo_id);

-- Account-bound safety controls.  Clients submit only public content tokens;
-- the HTTP layer resolves the target author server-side, so an attacker cannot
-- spoof another account id into a block.  The scan report queue below takes no
-- new rows (the app no longer reports scans); what is already queued stays for
-- the operator tool until it is resolved or ages out.
CREATE TABLE IF NOT EXISTS community_content_reports (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    reporter_actor_id TEXT    NOT NULL,
    target_type       TEXT    NOT NULL CHECK(target_type IN ('scan')),
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
CREATE INDEX IF NOT EXISTS ix_community_actor_blocks_blocked
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

# Other stores in this database (the forum) register here so account
# deletion, nickname changes and blocks cover their content too.
#   media_files(conn, actor) -> list[Path]   files to unlink before purge
#   purge(conn, actor)                        delete rows inside the cleanup tx
#   rename(conn, actor, nickname) -> int      update denormalized nicknames
#   safety_targets[kind](conn, token)         -> (content_id, 0, actor, nick)
_actor_hooks: list[dict] = []
_extra_safety_targets: dict = {}


def register_actor_hooks(
    *,
    media_files=None,
    purge=None,
    rename=None,
    safety_targets: dict | None = None,
) -> None:
    """Let another store in ``community.db`` join deletion, rename and blocks."""
    _actor_hooks.append({"media_files": media_files, "purge": purge, "rename": rename})
    _extra_safety_targets.update(safety_targets or {})


class AnalysisTokenUnknownError(ValueError):
    """The publication capability was not issued here or has expired."""


class AnalysisTokenConflictError(ValueError):
    """The capability or scan id is already bound to another publication."""


class AnalysisImageMismatchError(ValueError):
    """The community image is not the exact upload that was analysed."""


# ── Public scan tokens ────────────────────────────────────────────────
# The stored ``scan_id`` is a server-issued community publication capability,
# never a feedback receipt. Viewer-facing feed rows carry a stable per-scan,
# deterministic one-way PUBLIC token so the publication capability itself is
# never exposed. It does not link different scans by the same actor. The token
# stays stable for the row's lifetime while age checks make old scans read-only.
# View endpoints accept either form for installed-client compatibility,
# while safety mutations below deliberately require the opaque public form.
_PUBLIC_TOKEN_PREFIX = "cs"
_PUBLIC_BLOCK_TOKEN_PREFIX = "cb"
_PUBLIC_ACCOUNT_AUTHOR_PREFIX = "ca"

COMMUNITY_BLOCK_LIMIT = 500
COMMUNITY_REPORT_RETENTION_DAYS = 180


def public_scan_token(scan_id: str) -> str:
    """Deterministic, non-reversible public token for one scan_id."""
    digest = hashlib.sha256(f"community-public-v1|{scan_id}".encode()).hexdigest()
    return _PUBLIC_TOKEN_PREFIX + digest[:30]


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


_AUTHOR_KEY_PEPPER = get_str(
    "COMMUNITY_AUTHOR_KEY_PEPPER",
    "community-author-storage-v2",
).encode("utf-8")


def _stored_author_key(client_key_hash: str) -> str:
    """Server-side credential digest; a DB read must not yield a bearer key."""
    return hmac.new(
        _AUTHOR_KEY_PEPPER,
        client_key_hash.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _normalise_actor_id(value: str | None) -> str | None:
    try:
        return str(uuid.UUID((value or "").strip()))
    except (TypeError, ValueError, AttributeError):
        return None


def public_account_author_token(account_actor_id: str | None) -> str | None:
    """Stable, opaque community handle for one verified account actor.

    Internal account UUIDs never cross the public API.  Unlike legacy install
    aliases, an account profile must remain addressable across retention
    windows, so this token intentionally does not rotate.
    """
    actor = _normalise_actor_id(account_actor_id)
    if actor is None:
        return None
    digest = hashlib.sha256(
        f"community-account-author-v1|{actor}".encode("utf-8")
    ).hexdigest()
    return _PUBLIC_ACCOUNT_AUTHOR_PREFIX + digest[:30]


def resolve_public_account_author_token(token: str | None) -> str | None:
    """Resolve an opaque account-author token without exposing account ids."""
    value = (token or "").strip()
    if not (value.startswith(_PUBLIC_ACCOUNT_AUTHOR_PREFIX) and len(value) == 32):
        return None
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT account_actor_id FROM community_scans "
            "WHERE account_actor_id IS NOT NULL "
            "AND account_actor_id NOT IN (SELECT actor_id FROM community_deleted_actors)"
        ).fetchall()
    for row in rows:
        actor = str(row[0])
        if public_account_author_token(actor) == value:
            return actor
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
        "s.account_actor_id",
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


def _actor_is_deleted(conn: sqlite3.Connection, actor_id: str | None) -> bool:
    if actor_id is None:
        return False
    return conn.execute(
        "SELECT 1 FROM community_deleted_actors WHERE actor_id = ?", (actor_id,)
    ).fetchone() is not None


def _register_functions(conn: sqlite3.Connection) -> None:
    conn.create_function("community_distance_km", 4, _haversine_km, deterministic=True)


def _connect() -> contextlib.AbstractContextManager[sqlite3.Connection]:
    return sqlite_store.connect(COMMUNITY_DB_PATH, setup=_register_functions)


_add_column_if_missing = sqlite_store.add_column_if_missing


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
            public_id TEXT,
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


def _drop_retired_opinions(conn: sqlite3.Connection) -> None:
    """One-time, idempotent removal of the retired scan second-opinion system.

    Drops the opinion and opinion-request tables with their indexes, deletes
    every report whose target was an opinion, and drops
    ``community_scans.wants_opinion`` (with its index, if any) when an older
    database still has it. On a database that was already cleaned, or created
    after the removal, every statement is a no-op.
    """
    for index in (
        "ix_opinions_scan",
        "ix_opinions_author",
        "ix_opinions_actor",
        "ix_opinions_public_id",
        "ix_opinions_author_id",
        "ix_opinion_requests_user",
        "ix_opinion_requests_actor",
    ):
        conn.execute(f"DROP INDEX IF EXISTS {index}")
    conn.execute("DROP TABLE IF EXISTS community_opinions")
    conn.execute("DROP TABLE IF EXISTS community_opinion_requests")
    conn.execute("DELETE FROM community_content_reports WHERE target_type = 'opinion'")
    conn.execute("DELETE FROM community_reports WHERE target_type = 'opinion'")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(community_scans)")}
    if "wants_opinion" in columns:
        for name, definition in conn.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type = 'index' AND tbl_name = 'community_scans' AND sql IS NOT NULL"
        ).fetchall():
            if "wants_opinion" in definition:
                conn.execute(f'DROP INDEX IF EXISTS "{name}"')
        conn.execute("ALTER TABLE community_scans DROP COLUMN wants_opinion")


def _drop_retired_scan_likes(conn: sqlite3.Connection) -> None:
    """One-time, idempotent removal of the retired scan likes.

    Scans are shown without an author or a like control, so both like tables
    go (their indexes go with them). A no-op on a database that never had
    them or was already cleaned.
    """
    conn.execute("DROP TABLE IF EXISTS community_likes")
    conn.execute("DROP TABLE IF EXISTS community_account_likes")


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
            _drop_retired_opinions(conn)
            _drop_retired_scan_likes(conn)
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
            _add_column_if_missing(conn, "community_scans", "account_actor_id", "TEXT")
            _add_column_if_missing(conn, "community_scan_views", "account_actor_id", "TEXT")
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
            # Every scan_id lookup (token resolution, view writes,
            # ownership, dedupe on upload) hit a full table scan without
            # this. Deliberately NOT unique — legacy rows may repeat a
            # scan_id (see ``record_scan``), so a UNIQUE index would fail
            # to build on an existing deployment and take boot down with it.
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_community_scan_id "
                "ON community_scans(scan_id)"
            )
            # Durable identity, resolved from X-Community-Author-Key. NULL on
            # legacy rows until that device adopts; the pseudo-id columns stay
            # as a compatibility fallback for already-installed clients.
            _add_column_if_missing(conn, "community_scans", "author_id", "TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_community_author_id "
                "ON community_scans(author_id)"
            )
            _add_column_if_missing(conn, "community_scan_views", "viewer_author_id", "TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_views_viewer_pseudo "
                "ON community_scan_views(viewer_pseudo_id)"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_views_durable_viewer "
                "ON community_scan_views(scan_id, viewer_author_id) "
                "WHERE viewer_author_id IS NOT NULL"
            )
            _add_column_if_missing(conn, "community_reports", "reporter_author_id", "TEXT")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_reports_durable_reporter "
                "ON community_reports(reporter_author_id, target_type, target_id) "
                "WHERE reporter_author_id IS NOT NULL"
            )
            _add_column_if_missing(conn, "community_blocks", "blocker_author_id", "TEXT")
            _add_column_if_missing(conn, "community_blocks", "blocked_author_id", "TEXT")
            _add_column_if_missing(conn, "community_blocks", "client_ip_hash", "TEXT")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_blocks_durable_authors "
                "ON community_blocks(blocker_author_id, blocked_author_id) "
                "WHERE blocker_author_id IS NOT NULL AND blocked_author_id IS NOT NULL"
            )
            _add_column_if_missing(
                conn,
                "community_authors",
                "key_version",
                "INTEGER NOT NULL DEFAULT 1",
            )
            _add_column_if_missing(
                conn,
                "community_authors",
                "legacy_backfilled_at",
                "INTEGER",
            )
            # V1 stored the client-supplied header verbatim, making a DB read
            # sufficient to impersonate an author. Transform every existing
            # value in place; the server can HMAC the old digest without ever
            # learning the device secret. New rows use version 2 directly.
            for old_key, author_id in conn.execute(
                "SELECT key_hash, author_id FROM community_authors WHERE key_version = 1"
            ).fetchall():
                conn.execute(
                    "UPDATE community_authors SET key_hash = ?, key_version = 2 "
                    "WHERE author_id = ?",
                    (_stored_author_key(str(old_key)), str(author_id)),
                )
            _backfill_public_ids(conn)
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


# Matches the client's own CommunityUploader.makeThumbnail (THUMB_LONG_EDGE /
# THUMB_QUALITY) — an honest client already produces exactly this; only a
# modified client sending something larger or EXIF-laden gets clamped.
_THUMB_LONG_EDGE = 512
_THUMB_QUALITY = 78


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
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.load()
        # Orientation tag applied BEFORE convert("RGB"), which drops EXIF —
        # same order main.py's _open_image uses, for the same reason: skip
        # this and a portrait phone photo decodes sideways.
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Image.DecompressionBombError:
        logger.info("Community thumb rejected (decompression bomb): scan_id=%s", scan_id[:16])
        return None
    except UnidentifiedImageError:
        logger.info("Community thumb rejected (not a decodable image): scan_id=%s", scan_id[:16])
        return None
    except Exception as exc:  # noqa: BLE001 — any decode failure is a rejection, not a 500
        logger.info("Community thumb rejected (decode error: %s): scan_id=%s", exc, scan_id[:16])
        return None
    # ``info`` carries a JPEG COM comment through convert(), and Pillow would
    # write it back; drop every upload-side metadata field before saving.
    img.info = {}
    if max(img.size) > _THUMB_LONG_EDGE:
        img.thumbnail((_THUMB_LONG_EDGE, _THUMB_LONG_EDGE), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=_THUMB_QUALITY)  # no exif= => stripped
    encoded = buf.getvalue()
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
    author_id: str | None = None,
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
        # Dedup by scan_id. The community upload is fire-and-forget, so the
        # same scan can be offered twice; we never want two feed rows for one
        # scan. (There's no UNIQUE constraint on
        # scan_id for back-compat with legacy rows, so we guard here.)
        # The dedup check runs BEFORE the thumbnail write — writing first
        # leaked an orphan thumbnail file per duplicate upload that no
        # prune path ever swept.
        existing = conn.execute(
            "SELECT id FROM community_scans WHERE scan_id = ? LIMIT 1",
            (scan_id,),
        ).fetchone()
        if existing:
            if author_id:
                conn.execute(
                    "UPDATE community_scans SET author_id = COALESCE(author_id, ?) "
                    "WHERE id = ?",
                    (author_id, int(existing[0])),
                )
            return int(existing[0])
        thumb_path = _save_thumbnail(scan_id, image_bytes) if image_bytes else None
        cur = conn.execute(
            "INSERT INTO community_scans ("
            "  scan_id, user_pseudo_id, account_actor_id, country, region, city, lat, lon, "
            "  disease, confidence, "
            "  species, species_lower, species_score, thumb_path, locale, "
            "  nickname, author_id, client_ip_hash, public_id, created_at"
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
                author_id,
                ip_hash,
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
            cur = conn.execute(
                "INSERT INTO community_scans ("
                "scan_id, user_pseudo_id, account_actor_id, country, region, city, lat, lon, "
                "disease, confidence, species, species_lower, species_score, thumb_path, "
                "locale, nickname, client_ip_hash, public_id, created_at"
                ") VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
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

    Returns ``(content_id, 0, actor_id, nickname)`` from the resolver another
    store registered for ``target_type`` (the forum's posts and answers); the
    middle slot is the retired opinion id, kept as zero. Shared scans are not
    a target: they are shown without an author. Actor ids never cross the HTTP
    boundary; callers use them only to bind the block to the stored author.
    """
    kind = (target_type or "").strip().lower()
    token = (target_id or "").strip()
    if not token or len(token) > 128:
        return None
    resolver = _extra_safety_targets.get(kind)
    if resolver is not None:
        return resolver(conn, token)
    return None


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
            _content_id, _retired_opinion_id, target_actor, nickname = target
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
            "r.target_scan_id, s.nickname, s.disease "
            "FROM community_content_reports r "
            "LEFT JOIN community_scans s ON s.scan_id = r.target_scan_id "
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
            "nickname": row[5] or None,
            "disease": row[6] or None,
        }
        for row in rows
    ]


def _delete_scan(conn: sqlite3.Connection, scan_id: str, *, now: int) -> str | None:
    """Delete one scan with its views inside the caller's transaction and
    resolve every pending report about it. Returns the thumbnail to unlink."""
    thumb = conn.execute(
        "SELECT thumb_path FROM community_scans WHERE scan_id = ? LIMIT 1",
        (scan_id,),
    ).fetchone()
    conn.execute("DELETE FROM community_scan_views WHERE scan_id = ?", (scan_id,))
    conn.execute("DELETE FROM community_scans WHERE scan_id = ?", (scan_id,))
    conn.execute(
        "UPDATE community_content_reports SET state = 'removed', "
        "resolved_at = ? WHERE target_scan_id = ? AND state = 'pending'",
        (now, scan_id),
    )
    return str(thumb[0]) if thumb and thumb[0] else None


def remove_scan(public_id: str, *, now: int | None = None) -> bool:
    """Take one shared scan down from the local operator tool.

    The app has no Report control on a scan, so a scan flagged to the
    moderation contact is removed here by its public id (``cs…``, also the
    start of its thumbnail filename). Removes the row, its views and its
    thumbnail. Returns ``False`` when no such scan exists.
    """
    init_db()
    token = (public_id or "").strip()
    if len(token) != 32 or not token.startswith(_PUBLIC_TOKEN_PREFIX):
        return False
    timestamp = int(time.time() if now is None else now)
    with _write_lock, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT scan_id FROM community_scans WHERE public_id = ? LIMIT 1",
                (token,),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return False
            thumb_path = _delete_scan(conn, str(row[0]), now=timestamp)
            conn.execute("COMMIT")
        except Exception:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise
    if thumb_path:
        with contextlib.suppress(OSError):
            (COMMUNITY_THUMB_DIR / thumb_path).unlink(missing_ok=True)
    return True


def moderate_content_report(
    report_id: int, action: str, *, now: int | None = None
) -> bool:
    """Resolve a queued report from the local operator tool.

    Supported actions are ``dismiss`` and ``remove_content``.  Removing a scan
    also removes its thumbnail and views transactionally;
    other reports for the same removed content are resolved at the same time.
    The queue takes no new reports; this drains what was filed before.
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
                "SELECT target_type, target_scan_id "
                "FROM community_content_reports WHERE id = ? "
                "AND state = 'pending' LIMIT 1",
                (int(report_id),),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return False
            target_type, scan_id = str(row[0]), str(row[1])
            next_state = "dismissed"
            if resolution == "remove_content":
                next_state = "removed"
                if target_type == "scan":
                    thumb_path = _delete_scan(conn, scan_id, now=timestamp)
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


def record_views(
    tokens: list[str],
    viewer_pseudo_id: str | None = None,
    *,
    account_actor_id: str | None = None,
) -> int:
    """Batch form of :func:`record_view` for feed-impression reporting.

    Takes RAW client tokens — public tokens and real scan_ids mix freely in
    one batch, exactly as :func:`resolve_scan_token` tolerates them — and
    resolves, filters and inserts the whole batch under ONE connection and
    ONE lock acquisition instead of one of each per id. Semantics per id are
    identical to :func:`record_view`: unknown scans and the viewer's own
    scans never count, and a repeat view is deduped by the primary key.
    Returns the number of NEW view rows.
    """
    ids = list(dict.fromkeys((t or "").strip() for t in (tokens or []) if (t or "").strip()))
    if not ids:
        return 0
    try:
        legacy_id, actor_id = _identity(viewer_pseudo_id, account_actor_id)
    except ValueError:
        return 0
    init_db()
    now = int(time.time())
    placeholders = ",".join("?" * len(ids))
    with _write_lock, _connect() as conn:
        if _actor_is_deleted(conn, actor_id):
            return 0
        rows = conn.execute(
            "SELECT scan_id, user_pseudo_id, account_actor_id FROM community_scans "
            f"WHERE public_id IN ({placeholders}) OR scan_id IN ({placeholders})",
            (*ids, *ids),
        ).fetchall()
        # Dedupe on the REAL scan_id: one batch can carry both forms of the
        # same scan (and legacy rows may repeat a scan_id).
        targets = {
            str(row[0])
            for row in rows
            if not (actor_id is not None and row[2] is None)
            and not (row[1] == legacy_id and row[2] == actor_id)
            and not (
                actor_id is not None
                and row[2] is not None
                and _actors_blocked(conn, actor_id, str(row[2]))
            )
        }
        if not targets:
            return 0
        cur = conn.executemany(
            "INSERT OR IGNORE INTO community_scan_views "
            "(scan_id, viewer_pseudo_id, account_actor_id, created_at) "
            "VALUES (?, ?, ?, ?)",
            [(sid, legacy_id, actor_id, now) for sid in sorted(targets)],
        )
        return int(cur.rowcount or 0)


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


# Disclosed in privacy.html / release-policy-gates.json (deleted-actor marker: 30
# days). The marker suppresses the deleted actor's content while its cleanup
# runs, so late or retried writes cannot bring removed material back.
DELETED_ACTOR_MARKER_RETENTION_DAYS = 30

# A marker may only expire once its physical cleanup finished: while a cleanup
# job is pending or retrying, the marker is still the replay-suppression record.
_EXPIRE_DELETED_ACTOR_MARKERS_SQL = (
    "DELETE FROM community_deleted_actors WHERE requested_at < ? AND NOT EXISTS ("
    "SELECT 1 FROM community_cleanup_jobs j "
    "WHERE j.actor_id = community_deleted_actors.actor_id AND j.state != 'completed')"
)


def prune_expired_deleted_actor_markers(*, now: int | None = None) -> int:
    """Erase deleted-actor markers older than the disclosed retention period."""
    init_db()
    timestamp = int(time.time() if now is None else now)
    cutoff = timestamp - DELETED_ACTOR_MARKER_RETENTION_DAYS * 86_400
    with _write_lock, _connect() as conn:
        return int(conn.execute(_EXPIRE_DELETED_ACTOR_MARKERS_SQL, (cutoff,)).rowcount or 0)


def prune_older_than(days: int) -> int:
    """Delete rows + thumbnails older than ``days`` days.

    Covers every table in this store: the scans themselves, their orphaned
    companion rows (views) and unreferenced
    ``community_authors`` rows. Returns the number of scans deleted."""
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
        conn.execute(
            _EXPIRE_DELETED_ACTOR_MARKERS_SQL,
            (int(time.time()) - DELETED_ACTOR_MARKER_RETENTION_DAYS * 86_400,),
        )
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
        # transaction shares the same SQLite write reservation as the other
        # writers, so whichever operation wins leaves a consistent result.
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
                    conn.execute(
                        "DELETE FROM community_scan_views WHERE scan_id = ?",
                        (scan_id,),
                    )
            # Sweep old orphan rows left by historical versions. The age guard
            # retains a fresh row that legitimately arrives before upload.
            conn.execute(
                "DELETE FROM community_scan_views WHERE created_at < ? "
                "AND scan_id NOT IN (SELECT scan_id FROM community_scans)",
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
        extra = sum(
            int(hook["rename"](conn, actor, cleaned) or 0)
            for hook in _actor_hooks
            if hook.get("rename") is not None
        )
    return int(scans or 0) + extra


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
            extra_files = [
                path
                for hook in _actor_hooks
                if hook.get("media_files") is not None
                for path in hook["media_files"](conn, actor)
            ]
        filesystem_failed = False
        for thumb in thumbs:
            try:
                (COMMUNITY_THUMB_DIR / thumb).unlink(missing_ok=True)
            except OSError:
                filesystem_failed = True
        for path in extra_files:
            try:
                Path(path).unlink(missing_ok=True)
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
                    conn.execute(
                        "DELETE FROM community_scan_views "  # nosec B608
                        f"WHERE scan_id IN ({placeholders})",
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
                    "DELETE FROM community_scan_views WHERE account_actor_id = ?", (actor,)
                )
                conn.execute(
                    "DELETE FROM community_scans WHERE account_actor_id = ?", (actor,)
                )
                conn.execute(
                    "DELETE FROM community_analysis_tokens WHERE claimed_actor_id = ?",
                    (actor,),
                )
                for hook in _actor_hooks:
                    if hook.get("purge") is not None:
                        hook["purge"](conn, actor)
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


# ── Durable forum identity (community_authors) ───────────────────────
#
# The forum's durable, account-free identity: one author_id per device.

_AUTHOR_ID_PREFIX = "au_"


def _new_author_id() -> str:
    return _AUTHOR_ID_PREFIX + secrets.token_hex(12)


def _author_row(conn: sqlite3.Connection, author_id: str) -> dict:
    row = conn.execute(
        "SELECT author_id, display_name, created_at, last_seen_at, status "
        "FROM community_authors WHERE author_id = ?",
        (author_id,),
    ).fetchone()
    return {
        "author_id": row[0],
        "display_name": row[1],
        "created_at": row[2],
        "last_seen_at": row[3],
        "status": row[4],
    }


def touch_author(key_hash: str) -> dict | None:
    """Refresh one already-adopted author using only its indexed key row.

    Ordinary keyed writes use this path. It never creates an identity and
    never scans or rewrites legacy Community tables; the attachment suite is
    reserved for the explicit adoption transaction above.
    """
    kh = (key_hash or "").strip()
    if not kh:
        return None
    stored_key = _stored_author_key(kh)
    init_db()
    with _write_lock, _connect() as conn:
        updated = conn.execute(
            "UPDATE community_authors SET last_seen_at = ? WHERE key_hash = ?",
            (int(time.time()), stored_key),
        ).rowcount
        if not updated:
            return None
        row = conn.execute(
            "SELECT author_id FROM community_authors WHERE key_hash = ?",
            (stored_key,),
        ).fetchone()
        return _author_row(conn, str(row[0]))


def lookup_author(key_hash: str) -> dict | None:
    """Return an already-adopted durable author without creating one.

    Read endpoints use this lookup-only path so merely opening the feed with a
    new client key cannot win the race against ``/authors/adopt`` and seed a
    blank identity before legacy Community rows are attached.
    """
    kh = (key_hash or "").strip()
    if not kh:
        return None
    stored_key = _stored_author_key(kh)
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT author_id FROM community_authors WHERE key_hash = ?",
            (stored_key,),
        ).fetchone()
        return _author_row(conn, str(row[0])) if row else None


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
    "account_owner_of",
    "daily_write_count_for_ip_hash",
    "init_db",
    "process_cleanup_jobs",
    "prune_older_than",
    "public_scan_token",
    "record_analysis_scan",
    "record_analysis_token",
    "record_scan",
    "record_view",
    "request_actor_deletion",
    "prune_expired_deleted_actor_markers",
    "DELETED_ACTOR_MARKER_RETENTION_DAYS",
    "resolve_scan_token",
    "scan_exists",
    "stats_snapshot",
    "thumbnail_is_visible",
    "update_actor_nickname",
]
