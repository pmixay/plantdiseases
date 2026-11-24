"""
Community scan corpus for "similar scans near you".

Append-only SQLite at ``server/community.db``. Each row captures one
shared scan: the disease verdict, the auto-detected species, the
user's coarse-region string (city or admin region — never raw GPS), a signed
account-era actor (or an unlinked legacy pseudo-id), and an optional thumbnail
path. The
store is intentionally write-mostly + retention-bounded — there is no
update path, only an explicit prune by age.

Module layout: this file keeps the shared core (config and paths, schema,
``init_db`` and migrations, ``_connect`` and the write lock, identity and
public-token helpers, the actor-hook registry). The areas built on it live in
siblings that reach this core as ``community.<name>`` at call time:
``community_scans`` (publication, thumbnails, analysis tokens),
``community_views``, ``community_feeds``, ``community_popular``,
``community_safety`` (blocks, reports, removal), ``community_deletion``
(retention, account deletion, cleanup jobs) and ``community_authors``.

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
import logging
import math
import sqlite3
import threading
import time
import uuid
from pathlib import Path

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


# ── Public scan tokens ────────────────────────────────────────────────
# The stored ``scan_id`` is a server-issued community publication capability,
# never a feedback receipt. Viewer-facing feed rows carry a stable per-scan,
# deterministic one-way PUBLIC token so the publication capability itself is
# never exposed. It does not link different scans by the same actor. The token
# stays stable for the row's lifetime while age checks make old scans read-only.
# View endpoints accept either form for installed-client compatibility,
# while safety mutations below deliberately require the opaque public form.
_PUBLIC_TOKEN_PREFIX = "cs"
_PUBLIC_ACCOUNT_AUTHOR_PREFIX = "ca"


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


def _actors_blocked(
    conn: sqlite3.Connection, first_actor_id: str, second_actor_id: str
) -> bool:
    return conn.execute(
        "SELECT 1 FROM community_actor_blocks WHERE "
        "(blocker_actor_id = ? AND blocked_actor_id = ?) OR "
        "(blocker_actor_id = ? AND blocked_actor_id = ?) LIMIT 1",
        (first_actor_id, second_actor_id, second_actor_id, first_actor_id),
    ).fetchone() is not None


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
    "COMMUNITY_DB_PATH",
    "COMMUNITY_THUMB_DIR",
    "daily_write_count_for_ip_hash",
    "init_db",
    "public_scan_token",
    "resolve_scan_token",
    "stats_snapshot",
]
