"""
Community scan corpus for "similar scans near you".

Append-only SQLite at ``server/community.db``. Each row captures one
shared scan: the disease verdict, the auto-detected species, the
user's coarse-region string (city or admin region — never raw GPS),
the user's per-install pseudo-id, and an optional thumbnail path. The
store is intentionally write-mostly + retention-bounded — there is no
update path, only an explicit prune by age.

Threading model: mirrors ``feedback.py``. One connection per call (reused);
a module-level RLock serialises writes so concurrent uploads don't race
on the cursor.

Privacy posture
---------------
* No precise location lands here. The opted-in Android client sends a
  city and coordinates rounded to roughly 1 km for the nearby radius.
* `client_ip_hash` mirrors the feedback store: peppered SHA-256, 64
  bits, used for abuse-rate windows only.
* Thumbnails are 512px JPEGs uploaded by the client. The user can opt
  out from the Android app; uploads stay off until explicit consent.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import hmac
import io
import logging
import math
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

try:
    from .config import get_str  # type: ignore[import-not-found]
    from .countries import to_iso_alpha2
    from .feedback import hash_client_ip
except ImportError:
    from config import get_str
    from countries import to_iso_alpha2
    from feedback import hash_client_ip

logger = logging.getLogger(__name__)


class OpinionLockedError(RuntimeError):
    """The target scan has no live owner-requested second-opinion window."""


class OpinionOwnerError(RuntimeError):
    """A scan owner attempted to add an opinion to their own scan."""

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
    user_pseudo_id  TEXT    NOT NULL,
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
    author_pseudo_id  TEXT    NOT NULL,
    suggested_disease TEXT    NOT NULL,
    note              TEXT,
    nickname          TEXT,
    public_id         TEXT,
    client_ip_hash    TEXT,
    created_at        INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_opinions_scan   ON community_opinions(scan_id, created_at);
CREATE INDEX IF NOT EXISTS ix_opinions_author ON community_opinions(author_pseudo_id);

CREATE TABLE IF NOT EXISTS community_opinion_requests (
    scan_id        TEXT    PRIMARY KEY,
    user_pseudo_id TEXT    NOT NULL,
    created_at     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_opinion_requests_user ON community_opinion_requests(user_pseudo_id);

CREATE TABLE IF NOT EXISTS community_scan_views (
    scan_id          TEXT    NOT NULL,
    viewer_pseudo_id TEXT    NOT NULL,
    viewer_author_id TEXT,
    created_at       INTEGER NOT NULL,
    PRIMARY KEY (scan_id, viewer_pseudo_id)
);
CREATE INDEX IF NOT EXISTS ix_views_viewer_pseudo
    ON community_scan_views(viewer_pseudo_id);
CREATE INDEX IF NOT EXISTS ix_views_scan ON community_scan_views(scan_id);

CREATE TABLE IF NOT EXISTS community_likes (
    scan_id          TEXT    NOT NULL,
    user_pseudo_id   TEXT    NOT NULL,
    author_id        TEXT,
    client_ip_hash   TEXT,
    -- Unliking flips this to 0 rather than deleting the row: the daily
    -- like quota counts rows in a 24h window, so a delete would hand the
    -- quota back and a re-like would re-enter it with a fresh created_at.
    -- Every "is it liked" read therefore filters on ``liked = 1``.
    liked            INTEGER NOT NULL DEFAULT 1,
    created_at       INTEGER NOT NULL,
    PRIMARY KEY (scan_id, user_pseudo_id)
);
CREATE INDEX IF NOT EXISTS ix_likes_scan ON community_likes(scan_id);
CREATE INDEX IF NOT EXISTS ix_likes_ip   ON community_likes(client_ip_hash, created_at);

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
CREATE INDEX IF NOT EXISTS ix_likes_user_pseudo
    ON community_likes(user_pseudo_id);
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
    -- The community_scans.user_pseudo_id / community_opinions.author_pseudo_id
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
"""

_write_lock = threading.RLock()
_init_lock = threading.Lock()
_initialised = False


# ── Public scan tokens ────────────────────────────────────────────────
# The real ``scan_id`` is the server-issued id from /api/analyze — the
# SAME value that authorises /api/feedback writes. Exposing it verbatim
# in the community feed let any feed reader post feedback (and attach
# images) against other people's scans, partially defeating the
# anti-poisoning gate. Viewer-facing feed rows therefore carry a
# deterministic one-way PUBLIC token instead; the opinion / view
# endpoints accept either form and resolve the token back to the real
# id server-side, so existing app builds keep working unchanged.
_PUBLIC_TOKEN_PREFIX = "cs"

# Public identifiers rotate every 30 days so a user's community activity can't
# be linked across windows; a scan older than this also stops accepting new
# opinions (its public handle has rotated).
IDENTITY_ROTATION_DAYS = 30
_IDENTITY_ROTATION_S = IDENTITY_ROTATION_DAYS * 86_400

# An open "wants a second opinion" request auto-expires after this long: the
# feed badge drops and the owner stops being notified about new opinions, but
# opinions already collected stay visible. Re-requesting restarts the window.
OPINION_REQUEST_TTL_HOURS = 72
_OPINION_REQUEST_TTL_S = OPINION_REQUEST_TTL_HOURS * 3600

# Recommendation ranking weights. One point is ONE HOUR OF FRESHNESS, so every
# other term below reads directly as "worth N hours of being newer". That
# common unit is the point: freshness used to tick once per six hours while the
# other terms kept their own scale, which made a single like worth a month of
# recency, one opinion worth 100 days, and the exploration jitter alone worth
# 25 days — engagement and noise both buried the date order the feed is
# supposed to lead with.
_RANK_FRESHNESS_BUCKET_S = 3600
# Comfortably outlives OPINION_REQUEST_TTL_HOURS, so a post that is actively
# waiting on help leads the feed for the whole time the request is live —
# without the old 50000 (≈34 years) that pinned it effectively forever.
_RANK_LIVE_REQUEST = 14 * 24
_RANK_PER_OPINION = 24  # a distinct opinion is worth a day of freshness
_RANK_PER_LIKE = 6  # a distinct like, six hours
_RANK_PER_VIEW = 1  # a distinct view, an hour
_RANK_OPINION_CAP = 50
_RANK_LIKE_CAP = 100
_RANK_VIEW_CAP = 500
# Deterministic per-row wobble so equally fresh, equally quiet posts don't
# always come back in insertion order. Bounded to under a day: exploration
# should reshuffle within a morning, not reorder a week.
_RANK_JITTER_MOD = 7
# Likes, opinions, views and exploration share ONE promotion budget. The
# per-signal caps above protect each aggregate independently, but without a
# cap on their sum they could still buy almost 96 days of rank at once. That
# let a heavily engaged month-old post outrank a brand-new one even though the
# feed is meant to lead with freshness. Seven days leaves engagement meaningful
# within the current week while giving us a simple invariant: except for an
# actively open second-opinion request, a post at least a week newer always
# ranks first, even against maximum engagement.
_RANK_PROMOTION_CAP = 7 * 24

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


def public_opinion_token(_opinion_id: int) -> str:
    """Unpredictable opaque public id for moderation actions on one reply.

    The value is persisted immediately after insertion. Randomness matters:
    hashing the incremental SQLite id would merely disguise an enumerable id.
    """
    return "co" + secrets.token_hex(15)


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
            "SELECT scan_id FROM community_scans WHERE public_id = ? LIMIT 1",
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
            "SELECT 1 FROM community_scans WHERE scan_id = ? LIMIT 1", (sid,),
        ).fetchone() is not None


def opinion_open(scan_id: str, max_age_days: int = IDENTITY_ROTATION_DAYS) -> bool:
    """True when a shared scan still accepts opinions — it exists AND is newer
    than ``max_age_days``. Old scans go read-only (their public handle has
    rotated), so the community can't keep weighing in on stale photos."""
    sid = (scan_id or "").strip()
    if not sid:
        return False
    init_db()
    cutoff = int(time.time()) - max_age_days * 86_400
    with _connect() as conn:
        return conn.execute(
            "SELECT 1 FROM community_scans WHERE scan_id = ? AND created_at >= ? LIMIT 1",
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
    "user_pseudo_id, nickname"
)


def _qualified_feed_columns(alias: str) -> str:
    return ", ".join(f"{alias}.{name.strip()}" for name in _FEED_COLUMNS.split(","))


_RECOMMENDATION_CURSOR_PREFIX = "r1"
_RECOMMENDATION_CURSOR_SECRET = get_str(
    "COMMUNITY_CURSOR_SECRET",
    get_str("FEEDBACK_IP_PEPPER", "community-cursor-v1"),
).encode("utf-8")
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


def encode_recommendation_cursor(score: int, created_at: int, row_id: int) -> str:
    """Opaque, signed keyset cursor for the recommendation order."""
    payload = f"{int(score)}:{int(created_at)}:{int(row_id)}".encode("ascii")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    signature = hmac.new(
        _RECOMMENDATION_CURSOR_SECRET,
        payload,
        hashlib.sha256,
    ).hexdigest()[:24]
    return f"{_RECOMMENDATION_CURSOR_PREFIX}.{encoded}.{signature}"


def decode_recommendation_cursor(value: str | None) -> tuple[int, int, int] | None:
    raw = (value or "").strip()
    if not raw:
        return None
    parts = raw.split(".")
    if len(parts) != 3 or parts[0] != _RECOMMENDATION_CURSOR_PREFIX:
        raise ValueError("invalid recommendation cursor")
    try:
        payload = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        expected = hmac.new(
            _RECOMMENDATION_CURSOR_SECRET,
            payload,
            hashlib.sha256,
        ).hexdigest()[:24]
        if not hmac.compare_digest(parts[2], expected):
            raise ValueError("invalid recommendation cursor")
        score, created_at, row_id = (int(part) for part in payload.decode("ascii").split(":"))
    except (binascii.Error, UnicodeDecodeError, ValueError, TypeError) as exc:
        raise ValueError("invalid recommendation cursor") from exc
    if score < 0 or created_at < 0 or row_id <= 0:
        raise ValueError("invalid recommendation cursor")
    return score, created_at, row_id

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
    prefix = f"{table_alias}." if table_alias else ""
    placeholders = ", ".join(["?"] * len(_HIDDEN_COMMUNITY_DISEASES))
    return f" AND {prefix}disease NOT IN ({placeholders})"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(COMMUNITY_DB_PATH),
        check_same_thread=False,
        isolation_level=None,
    )
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.create_function("community_distance_km", 4, _haversine_km, deterministic=True)
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
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_opinions_public_id "
                "ON community_opinions(public_id) WHERE public_id IS NOT NULL"
            )
            for (opinion_id,) in conn.execute(
                "SELECT id FROM community_opinions WHERE public_id IS NULL"
            ).fetchall():
                conn.execute(
                    "UPDATE community_opinions SET public_id = ? WHERE id = ?",
                    (public_opinion_token(int(opinion_id)), int(opinion_id)),
                )
            # "Wants a second opinion" flag — set after upload by the
            # scanner via /api/community/request-opinion. Older rows
            # default to 0 (no request).
            _add_column_if_missing(conn, "community_scans", "wants_opinion", "INTEGER NOT NULL DEFAULT 0")
            # Public viewer-facing token (see ``public_scan_token``).
            _add_column_if_missing(conn, "community_scans", "public_id", "TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_community_public_id "
                "ON community_scans(public_id)"
            )
            # Every scan_id lookup (token resolution, view/like writes,
            # ownership, dedupe on upload) hit a full table scan without
            # this. Deliberately NOT unique — legacy rows may repeat a
            # scan_id (see ``record_scan``), so a UNIQUE index would fail
            # to build on an existing deployment and take boot down with it.
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_community_scan_id "
                "ON community_scans(scan_id)"
            )
            # Likes are soft-deleted (see the ``community_likes`` schema).
            _add_column_if_missing(
                conn, "community_likes", "liked", "INTEGER NOT NULL DEFAULT 1",
            )
            # Durable identity, resolved from X-Community-Author-Key. NULL on
            # legacy rows until that device adopts; the pseudo-id columns stay
            # as a compatibility fallback for already-installed clients.
            _add_column_if_missing(conn, "community_opinions", "author_id", "TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_opinions_author_id ON community_opinions(author_id)"
            )
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
            _add_column_if_missing(conn, "community_likes", "author_id", "TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_likes_user_pseudo "
                "ON community_likes(user_pseudo_id)"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_likes_durable_author "
                "ON community_likes(scan_id, author_id) WHERE author_id IS NOT NULL"
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
        logger.info("Community DB ready at %s", COMMUNITY_DB_PATH)


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
    form — the filename rode through the feed and leaked the real
    (feedback-authorising) scan_id just as much as the id field did.
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
# THUMB_QUALITY) — the honest client already produces exactly this, so a
# legitimate upload is unaffected; only a modified client sending something
# larger, EXIF-laden or non-JPEG actually gets clamped.
_THUMB_LONG_EDGE = 512
_THUMB_QUALITY = 78


def _save_thumbnail(scan_id: str, image_bytes: bytes) -> str | None:
    """Decode, downscale and re-encode a 512px JPEG thumbnail under
    COMMUNITY_THUMB_DIR, dropping EXIF (including GPS) by construction.

    Returns the relative file path (suitable for storage in the
    ``thumb_path`` column) or ``None`` when the bytes are missing,
    oversized, or don't decode as an image. The upload endpoint already
    magic-byte-gates to JPEG/PNG/WebP before this runs, but that only
    checks the header — decoding here is what actually enforces the
    512px/no-EXIF contract this filename and its callers depend on,
    rather than trusting an already-honest client to have done it.
    """
    if not image_bytes:
        return None
    if len(image_bytes) > COMMUNITY_MAX_THUMB_BYTES:
        logger.info(
            "Community thumb rejected (oversized: %d bytes)", len(image_bytes)
        )
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
    if max(img.size) > _THUMB_LONG_EDGE:
        img.thumbnail((_THUMB_LONG_EDGE, _THUMB_LONG_EDGE), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=_THUMB_QUALITY)  # no exif= => stripped
    encoded = buf.getvalue()
    # Hash-prefix the filename so two scans with the same scan_id (a
    # mis-behaved client) can't collide. Truncate to 24 hex chars —
    # plenty of collision resistance for a per-pseudo-id namespace.
    # Named by the PUBLIC token, not the raw scan_id: the filename is
    # served to every feed reader via ``thumb_path`` and must not leak
    # the feedback-authorising id. Hashed on the RE-ENCODED bytes, not the
    # upload, so an identical re-upload still dedupes to the same file.
    digest = hashlib.sha256(encoded).hexdigest()[:24]
    filename = f"{public_scan_token(scan_id)}_{digest}.jpg"
    target = COMMUNITY_THUMB_DIR / filename
    try:
        target.write_bytes(encoded)
    except OSError as exc:
        logger.warning("Failed to write community thumb %s: %s", target, exc)
        return None
    return filename


def record_scan(
    *,
    scan_id: str,
    user_pseudo_id: str,
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
    now = int(time.time())
    ip_hash = hash_client_ip(client_ip)
    # Canonicalize the country bucket to an ISO alpha-2 code at the write so
    # the corpus never splits one country across code / name / localized-name
    # representations (the HTTP layer already canonicalizes, but a direct
    # caller — tests, future code — gets the same guarantee).
    country = to_iso_alpha2(country)
    region_value = region or ""
    species_lower = species.lower() if species else None
    with _write_lock, _connect() as conn:
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
            if author_id:
                conn.execute(
                    "UPDATE community_scans SET author_id = COALESCE(author_id, ?) "
                    "WHERE id = ?",
                    (author_id, int(existing[0])),
                )
            return int(existing[0])
        thumb_path = _save_thumbnail(scan_id, image_bytes) if image_bytes else None
        # Honour a second-opinion request that arrived BEFORE this upload
        # landed (the durable request store decouples the two orderings).
        # Expired requests don't count — the ask has lapsed by then.
        pending = conn.execute(
            "SELECT 1 FROM community_opinion_requests "
            "WHERE scan_id = ? AND user_pseudo_id = ? AND created_at >= ? LIMIT 1",
            (scan_id, user_pseudo_id, now - _OPINION_REQUEST_TTL_S),
        ).fetchone()
        wants_opinion = 1 if pending else 0
        cur = conn.execute(
            "INSERT INTO community_scans ("
            "  scan_id, user_pseudo_id, country, region, city, lat, lon, "
            "  disease, confidence, "
            "  species, species_lower, species_score, thumb_path, locale, "
            "  nickname, author_id, client_ip_hash, wants_opinion, public_id, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                scan_id,
                user_pseudo_id,
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
                wants_opinion,
                public_scan_token(scan_id),
                now,
            ),
        )
        return int(cur.lastrowid or 0)


def set_wants_opinion(scan_id: str, user_pseudo_id: str) -> bool:
    """Durably record that the scan's owner wants a community second opinion.

    Owner-scoped: a user can only flag their own scan (the request is
    keyed by ``user_pseudo_id`` and the scan-row update matches on it
    too). The intent is written to ``community_opinion_requests`` FIRST,
    so it survives even when the community upload hasn't landed yet — the
    flag is then materialised onto the scan row either here (if it
    already exists) or by :func:`record_scan` when the upload arrives.
    This removes the upload/flag race that used to make the request a
    silent no-op.

    Re-requesting refreshes ``created_at``, restarting the
    ``OPINION_REQUEST_TTL_HOURS`` auto-expiry window.

    Returns ``True`` once the intent is recorded (i.e. for any non-blank
    id pair). The caller can therefore trust the "waiting" state.
    """
    init_db()
    if not scan_id or not user_pseudo_id:
        return False
    now = int(time.time())
    with _write_lock, _connect() as conn:
        owner = conn.execute(
            "SELECT user_pseudo_id FROM community_scans WHERE scan_id = ? LIMIT 1",
            (scan_id,),
        ).fetchone()
        # Once the scan exists its stored owner is authoritative. Previously a
        # different pseudo could overwrite the one-row request and thereby
        # unlock opinions on someone else's scan.
        if owner is not None and str(owner[0]) != user_pseudo_id:
            return False
        pending = conn.execute(
            "SELECT user_pseudo_id FROM community_opinion_requests WHERE scan_id = ?",
            (scan_id,),
        ).fetchone()
        # Before upload there is no owner row to prove a replacement. Preserve
        # the first pending intent; the eventual upload only materialises it
        # when its pseudo matches.
        if owner is None and pending is not None and str(pending[0]) != user_pseudo_id:
            return False
        # Durable record of intent (idempotent — one request per scan).
        conn.execute(
            "INSERT INTO community_opinion_requests "
            "(scan_id, user_pseudo_id, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(scan_id) DO UPDATE SET "
            "  user_pseudo_id = excluded.user_pseudo_id, "
            "  created_at = excluded.created_at",
            (scan_id, user_pseudo_id, now),
        )
        # Materialise onto the scan row if the upload has already landed.
        conn.execute(
            "UPDATE community_scans SET wants_opinion = 1 "
            "WHERE scan_id = ? AND user_pseudo_id = ?",
            (scan_id, user_pseudo_id),
        )
        return True


def clear_wants_opinion(scan_id: str, user_pseudo_id: str) -> bool:
    """Withdraw the owner's open second-opinion request on their own scan.

    Owner-scoped like :func:`set_wants_opinion`: both the durable request
    row and the materialised scan-row flag are only touched when
    ``user_pseudo_id`` matches. Opinions already left stay visible — this
    only stops the "wants opinion" badge and further notifications.

    Returns ``True`` when an open request was actually removed.
    """
    init_db()
    sid = (scan_id or "").strip()
    pid = (user_pseudo_id or "").strip()
    if not sid or not pid:
        return False
    with _write_lock, _connect() as conn:
        cleared = conn.execute(
            "DELETE FROM community_opinion_requests "
            "WHERE scan_id = ? AND user_pseudo_id = ?",
            (sid, pid),
        ).rowcount
        conn.execute(
            "UPDATE community_scans SET wants_opinion = 0 "
            "WHERE scan_id = ? AND user_pseudo_id = ?",
            (sid, pid),
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


def record_opinion(
    *,
    scan_id: str,
    author_pseudo_id: str,
    suggested_disease: str,
    author_id: str | None = None,
    note: str | None = None,
    nickname: str | None = None,
    client_ip: str | None = None,
) -> int:
    """Append one community 'second opinion' (comment) on a shared scan.
    Returns the new row id.

    UNGATED, and deliberately not in ``__all__``: it performs none of the
    active-request / ownership / block checks. Nothing in the HTTP layer
    calls it — ``/api/community/opinion`` goes through
    :func:`record_requested_opinion`, which is the authority. This form
    survives only as a test-seeding helper for the read paths; new callers
    must use :func:`record_requested_opinion`.

    An author may post more than once on the same scan — the thread is a
    running comment list, not a single vote slot. ``opinions_for`` folds
    repeat authors down to their latest pick for the consensus tally so a
    talkative commenter can't inflate it; the comment list itself keeps
    every row.

    ``author_id`` is the durable identity resolved from
    ``X-Community-Author-Key`` (NULL for a caller with no adopted identity —
    an old app build, or a malformed/missing header). When present it's what
    the consensus tally dedupes on instead of ``author_pseudo_id``, which the
    client chooses and can vary per request — see ``opinions_for``."""
    init_db()
    now = int(time.time())
    ip_hash = hash_client_ip(client_ip)
    note_clean = (note or "").strip()[:500] or None
    nick_clean = (nickname or "").strip()[:48] or None
    with _write_lock, _connect() as conn:
        cur = conn.execute(
            "INSERT INTO community_opinions ("
            "  scan_id, author_pseudo_id, author_id, suggested_disease, note, "
            "  nickname, client_ip_hash, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (scan_id, author_pseudo_id, author_id, suggested_disease, note_clean,
             nick_clean, ip_hash, now),
        )
        row_id = int(cur.lastrowid or 0)
        conn.execute(
            "UPDATE community_opinions SET public_id = ? WHERE id = ?",
            (public_opinion_token(row_id), row_id),
        )
        return row_id


def record_requested_opinion(
    *,
    scan_id: str,
    author_pseudo_id: str,
    suggested_disease: str,
    author_id: str | None = None,
    note: str | None = None,
    nickname: str | None = None,
    client_ip: str | None = None,
) -> int:
    """Atomically add an opinion only while the owner's request is live.

    The HTTP layer performs friendly early validation, but this transaction is
    the authority: a request can be cancelled or expire between an endpoint
    check and the insert. Existing opinions remain readable after closing; only
    new mutations are refused. The owner is also excluded from their own
    consensus so a direct client cannot bias the result.
    """
    init_db()
    sid = (scan_id or "").strip()
    pseudo = (author_pseudo_id or "").strip()
    if not sid or not pseudo:
        raise OpinionLockedError("opinion request is not active")
    now = int(time.time())
    cutoff = now - _OPINION_REQUEST_TTL_S
    note_clean = (note or "").strip()[:500] or None
    nick_clean = (nickname or "").strip()[:48] or None
    with _write_lock, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            target = conn.execute(
                "SELECT user_pseudo_id, created_at, author_id FROM community_scans "
                "WHERE scan_id = ? LIMIT 1",
                (sid,),
            ).fetchone()
            if target is None or int(target[1]) < now - _IDENTITY_ROTATION_S:
                raise OpinionLockedError("opinion target is missing or stale")
            same_durable_author = bool(
                author_id and target[2] and str(author_id) == str(target[2])
            )
            if str(target[0]) == pseudo or same_durable_author:
                raise OpinionOwnerError("owners cannot opine on their own scan")
            blocked = conn.execute(
                "SELECT 1 FROM community_blocks WHERE "
                "(blocker_pseudo_id = ? AND blocked_pseudo_id = ?) OR "
                "(blocker_pseudo_id = ? AND blocked_pseudo_id = ?) OR "
                "(blocker_author_id = ? AND blocked_author_id = ?) OR "
                "(blocker_author_id = ? AND blocked_author_id = ?) LIMIT 1",
                (
                    str(target[0]),
                    pseudo,
                    pseudo,
                    str(target[0]),
                    target[2],
                    author_id,
                    author_id,
                    target[2],
                ),
            ).fetchone()
            if blocked is not None:
                # Use the same externally indistinguishable locked response as
                # a closed thread: a cached public token must not let either
                # side of a block keep mutating or notifying the other.
                raise OpinionLockedError("opinion interaction is blocked")
            active = conn.execute(
                "SELECT 1 FROM community_opinion_requests "
                "WHERE scan_id = ? AND user_pseudo_id = ? "
                "AND created_at >= ? LIMIT 1",
                (sid, str(target[0]), cutoff),
            ).fetchone()
            if active is None:
                raise OpinionLockedError("opinion request is not active")
            cur = conn.execute(
                "INSERT INTO community_opinions ("
                "  scan_id, author_pseudo_id, author_id, suggested_disease, note, "
                "  nickname, client_ip_hash, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    sid,
                    pseudo,
                    author_id,
                    suggested_disease,
                    note_clean,
                    nick_clean,
                    hash_client_ip(client_ip),
                    now,
                ),
            )
            row_id = int(cur.lastrowid or 0)
            conn.execute(
                "UPDATE community_opinions SET public_id = ? WHERE id = ?",
                (public_opinion_token(row_id), row_id),
            )
            conn.execute("COMMIT")
            return row_id
        except Exception:
            with contextlib.suppress(sqlite3.OperationalError):
                conn.execute("ROLLBACK")
            raise


def opinions_for(
    scan_id: str, *, limit: int = 50, user_pseudo_id: str | None = None,
    author_id: str | None = None,
) -> dict:
    """Opinions for one scan plus a per-disease consensus tally.

    Shape::

        {"opinions":  [{"suggested_disease", "note", "created_at"}, ...],
         "consensus": [{"disease", "count"}, ...],    # most-voted first
         "my_opinion": "leaf_spot" | None}

    ``opinions`` lists every comment, newest first — an author may have
    posted more than once. ``consensus`` folds each author down to their
    MOST RECENT pick first (a window-function dedupe, not a plain
    GROUP BY) so a repeat commenter contributes one vote, not one per post.

    The dedupe key is ``COALESCE(author_id, author_pseudo_id)``: author_id
    is the durable identity resolved from X-Community-Author-Key, and
    author_pseudo_id is the client-chosen legacy id that key exists to stop
    being forgeable — a caller who varies the pseudo id per request no
    longer counts as a fresh voter each time once they have a real
    author_id. Rows written before the identity existed have a NULL
    author_id and fall back to the legacy id, so nothing needs a backfill.

    ``my_opinion`` is the caller's own most recent pick under that same
    coalesced identity (``author_id`` if the caller resolved one this
    request, else ``user_pseudo_id``) — None if they haven't voted. Reuses
    the tally's identity logic rather than a second, potentially
    inconsistent notion of "who is this".

    Also carries ``wants_opinion`` (is there a live, non-expired request?)
    plus ``opinion_requested_at`` (epoch seconds of that request, else
    null) so the owner's app can restore its "waiting" state across
    restarts and render a time-left countdown toward the 72 h expiry.
    """
    init_db()
    _sweep_expired_requests()
    limit = max(1, min(int(limit), 100))
    request_cutoff = int(time.time()) - _OPINION_REQUEST_TTL_S
    my_key = (author_id or user_pseudo_id or "").strip() or None
    with _connect() as conn:
        blocked_clause = ""
        blocked_params: tuple[str, ...] = ()
        viewer_pseudo = (user_pseudo_id or "").strip()
        if viewer_pseudo:
            blocked_clause = (
                " AND author_pseudo_id NOT IN ("
                "SELECT blocked_pseudo_id FROM community_blocks WHERE blocker_pseudo_id = ? "
                "UNION SELECT blocker_pseudo_id FROM community_blocks WHERE blocked_pseudo_id = ?"
                ")"
            )
            blocked_params = (viewer_pseudo, viewer_pseudo)
        durable_viewer = (author_id or "").strip()
        if durable_viewer:
            blocked_clause += (
                " AND (author_id IS NULL OR author_id NOT IN ("
                "SELECT blocked_author_id FROM community_blocks "
                "WHERE blocker_author_id = ? AND blocked_author_id IS NOT NULL "
                "UNION SELECT blocker_author_id FROM community_blocks "
                "WHERE blocked_author_id = ? AND blocker_author_id IS NOT NULL))"
            )
            blocked_params = (*blocked_params, durable_viewer, durable_viewer)
        rows = conn.execute(
            "SELECT suggested_disease, note, created_at, nickname, public_id, "
            "author_pseudo_id, author_id "
            "FROM community_opinions WHERE scan_id = ? "
            + blocked_clause
            + " ORDER BY created_at DESC LIMIT ?",
            (scan_id, *blocked_params, limit),
        ).fetchall()
        tally = conn.execute(
            "SELECT suggested_disease, COUNT(*) AS n FROM ("
            "  SELECT suggested_disease,"
            "         ROW_NUMBER() OVER ("
            "           PARTITION BY COALESCE(author_id, author_pseudo_id)"
            "           ORDER BY created_at DESC, id DESC"
            "         ) AS rn"
            "  FROM community_opinions WHERE scan_id = ?"
            + blocked_clause
            + ") WHERE rn = 1 GROUP BY suggested_disease ORDER BY n DESC",
            (scan_id, *blocked_params),
        ).fetchall()
        my_row = conn.execute(
            "SELECT suggested_disease FROM community_opinions "
            "WHERE scan_id = ? AND COALESCE(author_id, author_pseudo_id) = ? "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            (scan_id, my_key),
        ).fetchone() if my_key else None
        request_row = conn.execute(
            "SELECT r.created_at FROM community_opinion_requests r "
            "JOIN community_scans s ON s.scan_id = r.scan_id "
            "AND s.user_pseudo_id = r.user_pseudo_id "
            "WHERE r.scan_id = ? AND r.created_at >= ? LIMIT 1",
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
                (viewer_pseudo and str(r[5]) == viewer_pseudo)
                or (durable_viewer and r[6] and str(r[6]) == durable_viewer)
            ),
        }
        for r in rows
    ]
    consensus = [{"disease": str(t[0]), "count": int(t[1])} for t in tally]
    uid = (user_pseudo_id or "").strip()
    return {
        "opinions": opinions,
        "consensus": consensus,
        "my_opinion": str(my_row[0]) if my_row else None,
        "views": view_count_for(scan_id),
        "wants_opinion": request_row is not None,
        "opinion_requested_at": int(request_row[0]) if request_row else None,
        "like_count": like_counts([scan_id]).get(scan_id, 0),
        "liked_by_me": bool(uid or author_id)
        and scan_id in liked_scan_ids(uid, [scan_id], author_id=author_id),
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
            "  (SELECT COUNT(DISTINCT CASE "
            "       WHEN v.viewer_author_id IS NOT NULL THEN 'a:' || v.viewer_author_id "
            "       ELSE 'p:' || v.viewer_pseudo_id END) "
            "     FROM community_scan_views v WHERE v.scan_id = s.scan_id) AS views, "
            "  (SELECT o2.nickname FROM community_opinions o2 "
            "     WHERE o2.scan_id = s.scan_id AND o2.author_pseudo_id != s.user_pseudo_id "
            "       AND NOT EXISTS (SELECT 1 FROM community_blocks b2 WHERE "
            "            (b2.blocker_pseudo_id = s.user_pseudo_id "
            "             AND b2.blocked_pseudo_id = o2.author_pseudo_id) OR "
            "            (b2.blocker_pseudo_id = o2.author_pseudo_id "
            "             AND b2.blocked_pseudo_id = s.user_pseudo_id) OR "
            "            (b2.blocker_author_id = s.author_id "
            "             AND b2.blocked_author_id = o2.author_id) OR "
            "            (b2.blocker_author_id = o2.author_id "
            "             AND b2.blocked_author_id = s.author_id)) "
            "     ORDER BY o2.created_at DESC LIMIT 1) AS latest_nick "
            "FROM community_scans s "
            "JOIN community_opinions o "
            "  ON o.scan_id = s.scan_id AND o.author_pseudo_id != s.user_pseudo_id "
            " AND NOT EXISTS (SELECT 1 FROM community_blocks b WHERE "
            "      (b.blocker_pseudo_id = s.user_pseudo_id "
            "       AND b.blocked_pseudo_id = o.author_pseudo_id) OR "
            "      (b.blocker_pseudo_id = o.author_pseudo_id "
            "       AND b.blocked_pseudo_id = s.user_pseudo_id) OR "
            "      (b.blocker_author_id = s.author_id "
            "       AND b.blocked_author_id = o.author_id) OR "
            "      (b.blocker_author_id = o.author_id "
            "       AND b.blocked_author_id = s.author_id)) "
            "WHERE s.user_pseudo_id = ? "
            "  AND EXISTS (SELECT 1 FROM community_opinion_requests r "
            "              WHERE r.scan_id = s.scan_id "
            "                AND r.user_pseudo_id = s.user_pseudo_id "
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


def opinion_counts(scan_ids: list[str]) -> dict[str, int]:
    """Map each scan_id to its number of community opinions (zeros omitted)."""
    ids = [s for s in scan_ids if s]
    if not ids:
        return {}
    init_db()
    placeholders = ",".join("?" * len(ids))
    with _connect() as conn:
        rows = conn.execute(
            "SELECT scan_id, COUNT(*) FROM community_opinions "
            f"WHERE scan_id IN ({placeholders}) GROUP BY scan_id",
            tuple(ids),
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
            "SELECT 1 FROM community_opinion_requests r "
            "JOIN community_scans s ON s.scan_id = r.scan_id "
            "AND s.user_pseudo_id = r.user_pseudo_id "
            "WHERE r.scan_id = ? AND r.created_at >= ? LIMIT 1",
            (sid, cutoff),
        ).fetchone()
    return row is not None


def record_views(
    tokens: list[str],
    viewer_pseudo_id: str,
    viewer_author_id: str | None = None,
) -> int:
    """Record deduplicated views for a batch of scans (feed impressions).

    Takes RAW client tokens — public tokens and real scan_ids mix freely in
    one batch, exactly as :func:`resolve_scan_token` tolerates them — and
    resolves, filters and inserts the whole batch under ONE connection and
    ONE lock acquisition instead of one of each per id. Unknown scans and the
    viewer's own scans never count. New clients dedupe on the server-issued durable
    ``viewer_author_id`` even if a caller varies its body pseudo id; legacy
    clients retain the old pseudo-id fallback. Returns the number of NEW views.
    """
    ids = list(dict.fromkeys((t or "").strip() for t in (tokens or []) if (t or "").strip()))
    vid = (viewer_pseudo_id or "").strip()
    durable = (viewer_author_id or "").strip() or None
    if not ids or not vid:
        return 0
    init_db()
    now = int(time.time())
    placeholders = ",".join("?" * len(ids))
    with _write_lock, _connect() as conn:
        rows = conn.execute(
            "SELECT scan_id, user_pseudo_id, author_id FROM community_scans "
            f"WHERE public_id IN ({placeholders}) OR scan_id IN ({placeholders})",
            (*ids, *ids),
        ).fetchall()
        # Dedupe on the REAL scan_id: one batch can carry both forms of the
        # same scan (and legacy rows may repeat a scan_id). Ownership is also
        # durable once both sides have adopted, so changing a pseudo id cannot
        # turn viewing one's own post into engagement.
        targets = {
            str(r[0])
            for r in rows
            if str(r[1]) != vid
            and not (durable and r[2] and str(r[2]) == durable)
        }
        if not targets:
            return 0
        counted = 0
        for sid in sorted(targets):
            if durable:
                if conn.execute(
                    "SELECT 1 FROM community_scan_views "
                    "WHERE scan_id = ? AND viewer_author_id = ? LIMIT 1",
                    (sid, durable),
                ).fetchone():
                    continue
                # Adopt an existing legacy row in place. It was already a
                # counted view, so this upgrades identity without growing the
                # metric.
                upgraded = conn.execute(
                    "UPDATE community_scan_views SET viewer_author_id = ? "
                    "WHERE scan_id = ? AND viewer_pseudo_id = ? "
                    "AND viewer_author_id IS NULL",
                    (durable, sid, vid),
                ).rowcount
                if upgraded:
                    continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO community_scan_views "
                "(scan_id, viewer_pseudo_id, viewer_author_id, created_at) "
                "VALUES (?, ?, ?, ?)",
                (sid, vid, durable, now),
            )
            counted += int(cur.rowcount or 0)
        return counted


def view_counts(scan_ids: list[str]) -> dict[str, int]:
    """Map each scan_id to its number of distinct viewers (zeros omitted)."""
    ids = [s for s in scan_ids if s]
    if not ids:
        return {}
    init_db()
    placeholders = ",".join("?" * len(ids))
    with _connect() as conn:
        rows = conn.execute(
            "SELECT scan_id, COUNT(DISTINCT CASE "
            "WHEN viewer_author_id IS NOT NULL THEN 'a:' || viewer_author_id "
            "ELSE 'p:' || viewer_pseudo_id END) FROM community_scan_views "
            f"WHERE scan_id IN ({placeholders}) GROUP BY scan_id",
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
        return int(conn.execute(
            "SELECT COUNT(DISTINCT CASE "
            "WHEN viewer_author_id IS NOT NULL THEN 'a:' || viewer_author_id "
            "ELSE 'p:' || viewer_pseudo_id END) "
            "FROM community_scan_views WHERE scan_id = ?",
            (sid,),
        ).fetchone()[0])


def set_like(
    scan_id: str,
    user_pseudo_id: str,
    liked: bool,
    client_ip: str | None = None,
    author_id: str | None = None,
) -> bool:
    """Like or unlike a shared scan on behalf of one anonymous install.

    Mirrors ``record_views``: a scan owner can't like their own scan. The
    durable author id is authoritative for current clients, preventing a
    changed body pseudo id from minting an extra like; the legacy composite
    primary key remains the compatibility fallback.

    Unliking flips ``liked`` to 0 instead of deleting the row, and re-liking
    keeps the ORIGINAL ``created_at``: the daily like quota counts rows in a
    24h window, so deleting (or re-stamping) would let a toggle loop hand the
    quota back to itself indefinitely."""
    sid = (scan_id or "").strip()
    uid = (user_pseudo_id or "").strip()
    durable = (author_id or "").strip() or None
    if not sid or not uid:
        return False
    init_db()
    with _write_lock, _connect() as conn:
        owner = conn.execute(
            "SELECT user_pseudo_id, author_id FROM community_scans WHERE scan_id = ?",
            (sid,),
        ).fetchone()
        if (
            owner is None
            or str(owner[0]) == uid
            or (durable and owner[1] and str(owner[1]) == durable)
        ):
            return False
        if durable:
            durable_row = conn.execute(
                "SELECT 1 FROM community_likes "
                "WHERE scan_id = ? AND author_id = ? LIMIT 1",
                (sid, durable),
            ).fetchone()
            if durable_row:
                conn.execute(
                    "UPDATE community_likes SET liked = ? "
                    "WHERE scan_id = ? AND author_id = ?",
                    (int(bool(liked)), sid, durable),
                )
                return bool(liked)
            # Upgrade this pseudo's legacy row before considering an insert.
            # Its original timestamp is deliberately retained for quota
            # accounting, just like an unlike/re-like toggle.
            upgraded = conn.execute(
                "UPDATE community_likes SET author_id = ?, liked = ? "
                "WHERE scan_id = ? AND user_pseudo_id = ? AND author_id IS NULL",
                (durable, int(bool(liked)), sid, uid),
            ).rowcount
            if upgraded:
                return bool(liked)
        if liked:
            conn.execute(
                "INSERT INTO community_likes "
                "(scan_id, user_pseudo_id, author_id, client_ip_hash, liked, created_at) "
                "VALUES (?, ?, ?, ?, 1, ?) "
                "ON CONFLICT(scan_id, user_pseudo_id) DO UPDATE SET liked = 1",
                (sid, uid, durable, hash_client_ip(client_ip), int(time.time())),
            )
        else:
            # UPDATE, not INSERT: unliking something never liked stays a
            # no-op, so the unmetered unlike direction can't mint rows.
            conn.execute(
                "UPDATE community_likes SET liked = 0 "
                "WHERE scan_id = ? AND user_pseudo_id = ?",
                (sid, uid),
            )
        return bool(liked)


def like_counts(scan_ids: list[str]) -> dict[str, int]:
    """Map each scan_id to its like count (zeros omitted). Withdrawn likes
    stay in the table as ``liked = 0`` rows and never count here."""
    ids = [s for s in scan_ids if s]
    if not ids:
        return {}
    init_db()
    placeholders = ",".join("?" * len(ids))
    with _connect() as conn:
        rows = conn.execute(
            "SELECT scan_id, COUNT(DISTINCT CASE "
            "WHEN author_id IS NOT NULL THEN 'a:' || author_id "
            "ELSE 'p:' || user_pseudo_id END) FROM community_likes "
            f"WHERE scan_id IN ({placeholders}) AND liked = 1 GROUP BY scan_id",
            tuple(ids),
        ).fetchall()
    return {str(r[0]): int(r[1]) for r in rows}


def liked_scan_ids(
    user_pseudo_id: str,
    scan_ids: list[str],
    author_id: str | None = None,
) -> set[str]:
    """Which of ``scan_ids`` this user has already liked."""
    uid = (user_pseudo_id or "").strip()
    durable = (author_id or "").strip() or None
    ids = [s for s in scan_ids if s]
    if not (uid or durable) or not ids:
        return set()
    init_db()
    placeholders = ",".join("?" * len(ids))
    with _connect() as conn:
        rows = conn.execute(
            "SELECT scan_id FROM community_likes "
            f"WHERE (user_pseudo_id = ? OR author_id = ?) "
            f"AND liked = 1 AND scan_id IN ({placeholders})",
            (uid, durable, *ids),
        ).fetchall()
    return {str(r[0]) for r in rows}


def _content_author(
    conn: sqlite3.Connection,
    target_type: str,
    target_id: str,
) -> tuple[str, str | None, str, str | None] | None:
    """Resolve a target to (pseudo, nickname, public id, durable author id)."""
    kind = (target_type or "").strip().lower()
    token = (target_id or "").strip()
    if kind == "scan":
        row = conn.execute(
            "SELECT user_pseudo_id, nickname, public_id, author_id FROM community_scans "
            "WHERE public_id = ? OR scan_id = ? LIMIT 1",
            (token, token),
        ).fetchone()
    elif kind == "opinion":
        row = conn.execute(
            "SELECT author_pseudo_id, nickname, public_id, author_id FROM community_opinions "
            "WHERE public_id = ? LIMIT 1",
            (token,),
        ).fetchone()
    else:
        return None
    if row is None:
        return None
    return (
        str(row[0]),
        (str(row[1]) if row[1] else None),
        str(row[2] or token),
        (str(row[3]) if row[3] else None),
    )


def report_content(
    *,
    reporter_pseudo_id: str,
    reporter_author_id: str | None = None,
    target_type: str,
    target_id: str,
    reason: str,
    client_ip: str | None = None,
) -> bool:
    """Record one deduplicated anonymous moderation report.

    Target authors are resolved server-side from opaque public tokens. Returning
    ``False`` means the target is unknown or belongs to the reporter; repeated
    reports from the same install are successful no-ops rather than extra rows.
    """
    reporter = (reporter_pseudo_id or "").strip()
    durable = (reporter_author_id or "").strip() or None
    kind = (target_type or "").strip().lower()
    token = (target_id or "").strip()
    why = (reason or "").strip()[:64]
    if not reporter or not token or not why or kind not in {"scan", "opinion"}:
        return False
    init_db()
    with _write_lock, _connect() as conn:
        target = _content_author(conn, kind, token)
        if target is None or target[0] == reporter or (
            durable and target[3] and durable == target[3]
        ):
            return False
        cur = conn.execute(
            "INSERT OR IGNORE INTO community_reports ("
            "reporter_pseudo_id, reporter_author_id, target_type, target_id, "
            "reason, client_ip_hash, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                reporter,
                durable,
                kind,
                target[2],
                why,
                hash_client_ip(client_ip),
                int(time.time()),
            ),
        )
        return int(cur.rowcount or 0) > 0


def block_content_author(
    *, blocker_pseudo_id: str, target_type: str, target_id: str,
    blocker_author_id: str | None = None, client_ip: str | None = None,
) -> dict | None:
    """Block the server-resolved author of a public scan or reply token."""
    blocker = (blocker_pseudo_id or "").strip()
    durable = (blocker_author_id or "").strip() or None
    kind = (target_type or "").strip().lower()
    token = (target_id or "").strip()
    if not blocker or not token or kind not in {"scan", "opinion"}:
        return None
    init_db()
    with _write_lock, _connect() as conn:
        target = _content_author(conn, kind, token)
        if target is None or target[0] == blocker or (
            durable and target[3] and durable == target[3]
        ):
            return None
        conn.execute(
            "INSERT OR IGNORE INTO community_blocks "
            "(blocker_pseudo_id, blocked_pseudo_id, blocker_author_id, "
            " blocked_author_id, client_ip_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                blocker,
                target[0],
                durable,
                target[3],
                hash_client_ip(client_ip),
                int(time.time()),
            ),
        )
        # Do not return the anonymous storage identity. The public contract
        # only confirms the state change; resolution stays server-side.
        return {"blocked": True, "nickname": target[1]}


def daily_safety_count_for_ip_hash(ip_hash: str | None) -> int:
    """Moderation writes from one network in the last 24 hours."""
    if not ip_hash:
        return 0
    init_db()
    cutoff = int(time.time()) - 86_400
    with _connect() as conn:
        reports = conn.execute(
            "SELECT COUNT(*) FROM community_reports "
            "WHERE client_ip_hash = ? AND created_at >= ?",
            (ip_hash, cutoff),
        ).fetchone()[0]
        blocks = conn.execute(
            "SELECT COUNT(*) FROM community_blocks "
            "WHERE client_ip_hash = ? AND created_at >= ?",
            (ip_hash, cutoff),
        ).fetchone()[0]
    return int(reports) + int(blocks)


def daily_like_count_for_ip_hash(ip_hash: str | None) -> int:
    """Likes from this IP in the last 24h — bounds abuse across many scans,
    the same shape as ``daily_opinion_count_for_ip_hash``.

    Counts withdrawn likes (``liked = 0``) too: the row is what costs
    storage, and not counting them is exactly what would let an unlike hand
    the quota slot back."""
    if not ip_hash:
        return 0
    init_db()
    cutoff = int(time.time()) - 86_400
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM community_likes "
            "WHERE client_ip_hash = ? AND created_at >= ?",
            (ip_hash, cutoff),
        ).fetchone()
    return int(row[0]) if row else 0


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


def scans_by_species(
    scientific: str,
    *,
    limit: int = 12,
    exclude_user_pseudo_id: str | None = None,
    viewer_author_id: str | None = None,
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
        extra += (
            " AND user_pseudo_id NOT IN ("
            "SELECT blocked_pseudo_id FROM community_blocks WHERE blocker_pseudo_id = ? "
            "UNION SELECT blocker_pseudo_id FROM community_blocks WHERE blocked_pseudo_id = ?"
            ")"
        )
        params.extend((exclude_user_pseudo_id, exclude_user_pseudo_id))
    durable_viewer = (viewer_author_id or "").strip()
    if durable_viewer:
        extra += " AND (author_id IS NULL OR author_id != ?)"
        params.append(durable_viewer)
        extra += (
            " AND (author_id IS NULL OR author_id NOT IN ("
            "SELECT blocked_author_id FROM community_blocks "
            "WHERE blocker_author_id = ? AND blocked_author_id IS NOT NULL "
            "UNION SELECT blocker_author_id FROM community_blocks "
            "WHERE blocked_author_id = ? AND blocker_author_id IS NOT NULL))"
        )
        params.extend((durable_viewer, durable_viewer))
    if _HIDDEN_COMMUNITY_DISEASES:
        extra += _hidden_clause()
        params.extend(_HIDDEN_COMMUNITY_DISEASES)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {_FEED_COLUMNS} "
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


def recommended_scans(
    *,
    country: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    radius_km: float = NEARBY_RADIUS_KM,
    limit: int = 20,
    exclude_user_pseudo_id: str | None = None,
    viewer_author_id: str | None = None,
    scope: str = "nearby",
    cursor: tuple[int, int, int] | None = None,
) -> list[dict]:
    """Ranked Community feed with composite keyset pagination.

    Ranking is deterministic for a database snapshot and deliberately easy to
    explain, because every term is denominated in hours of freshness (see the
    ``_RANK_*`` weights): recency is the baseline, live second-opinion requests
    lead while their request is open, and engagement (distinct opinions, likes
    and views) plus the deterministic exploration term share a seven-day
    promotion cap. Thus an ordinary post at least a week newer always wins,
    even against maximum engagement. The cursor carries the complete ``(score,
    created_at, row_id)`` order key, so tied timestamps never repeat or
    disappear between pages.
    """
    init_db()
    _sweep_expired_requests()
    country_norm = to_iso_alpha2(country) if country else None
    scope_norm = (scope or "nearby").strip().lower()
    if scope_norm not in {"nearby", "country", "worldwide"}:
        scope_norm = "nearby"
    limit = max(1, min(int(limit), 100))
    has_coords = lat is not None and lon is not None

    where = ["1=1"]
    params: list = []
    if scope_norm == "nearby" and has_coords:
        min_lat, max_lat, min_lon, max_lon = _bbox(float(lat), float(lon), radius_km)
        where.append("s.lat BETWEEN ? AND ? AND s.lon BETWEEN ? AND ?")
        params.extend((min_lat, max_lat, min_lon, max_lon))
        where.append("community_distance_km(?, ?, s.lat, s.lon) <= ?")
        params.extend((float(lat), float(lon), float(radius_km)))
    elif scope_norm in {"nearby", "country"} and country_norm:
        where.append("s.country = ?")
        params.append(country_norm)
    elif scope_norm != "worldwide":
        return []

    viewer = (exclude_user_pseudo_id or "").strip()
    durable_viewer = (viewer_author_id or "").strip()
    if viewer:
        where.append("s.user_pseudo_id != ?")
        params.append(viewer)
        where.append(
            "s.user_pseudo_id NOT IN ("
            "SELECT blocked_pseudo_id FROM community_blocks WHERE blocker_pseudo_id = ? "
            "UNION SELECT blocker_pseudo_id FROM community_blocks WHERE blocked_pseudo_id = ?"
            ")"
        )
        params.extend((viewer, viewer))
    if durable_viewer:
        where.append("(s.author_id IS NULL OR s.author_id != ?)")
        params.append(durable_viewer)
        where.append(
            "(s.author_id IS NULL OR s.author_id NOT IN ("
            "SELECT blocked_author_id FROM community_blocks "
            "WHERE blocker_author_id = ? AND blocked_author_id IS NOT NULL "
            "UNION SELECT blocker_author_id FROM community_blocks "
            "WHERE blocked_author_id = ? AND blocker_author_id IS NOT NULL))"
        )
        params.extend((durable_viewer, durable_viewer))
    if _HIDDEN_COMMUNITY_DISEASES:
        where.append(
            "s.disease NOT IN ("
            + ", ".join("?" for _ in _HIDDEN_COMMUNITY_DISEASES)
            + ")"
        )
        params.extend(_HIDDEN_COMMUNITY_DISEASES)

    promotion_sql = (
        "MIN("
        f"MIN(COALESCE(oc.n, 0), {_RANK_OPINION_CAP}) * {_RANK_PER_OPINION} "
        f"+ MIN(COALESCE(lc.n, 0), {_RANK_LIKE_CAP}) * {_RANK_PER_LIKE} "
        f"+ MIN(COALESCE(vc.n, 0), {_RANK_VIEW_CAP}) * {_RANK_PER_VIEW} "
        f"+ ((s.id * 37) % {_RANK_JITTER_MOD}), "
        f"{_RANK_PROMOTION_CAP})"
    )
    score_sql = (
        f"CAST(s.created_at / {_RANK_FRESHNESS_BUCKET_S} AS INTEGER) "
        f"+ CASE WHEN req.scan_id IS NOT NULL THEN {_RANK_LIVE_REQUEST} ELSE 0 END "
        f"+ {promotion_sql}"
    )
    cursor_sql = ""
    cursor_params: list[int] = []
    if cursor is not None:
        score, created_at, row_id = cursor
        cursor_sql = (
            "WHERE recommendation_score < ? "
            "OR (recommendation_score = ? AND created_at < ?) "
            "OR (recommendation_score = ? AND created_at = ? AND row_id < ?)"
        )
        cursor_params.extend((score, score, created_at, score, created_at, row_id))

    sql = f"""
        WITH opinion_counts AS (
            SELECT scan_id, COUNT(DISTINCT CASE
                WHEN author_id IS NOT NULL THEN 'a:' || author_id
                ELSE 'p:' || author_pseudo_id END) AS n
            FROM community_opinions GROUP BY scan_id
        ), like_counts_cte AS (
            SELECT scan_id, COUNT(DISTINCT CASE
                WHEN author_id IS NOT NULL THEN 'a:' || author_id
                ELSE 'p:' || user_pseudo_id END) AS n FROM community_likes
            WHERE liked = 1 GROUP BY scan_id
        ), view_counts_cte AS (
            SELECT scan_id, COUNT(DISTINCT CASE
                WHEN viewer_author_id IS NOT NULL THEN 'a:' || viewer_author_id
                ELSE 'p:' || viewer_pseudo_id END) AS n
            FROM community_scan_views GROUP BY scan_id
        ), ranked AS (
            SELECT {_qualified_feed_columns('s')},
                   s.id AS row_id,
                   {score_sql} AS recommendation_score,
                   COALESCE(oc.n, 0) AS opinion_count,
                   COALESCE(lc.n, 0) AS like_count,
                   COALESCE(vc.n, 0) AS view_count,
                   CASE WHEN req.scan_id IS NOT NULL THEN 1 ELSE 0 END AS live_request
            FROM community_scans s
            LEFT JOIN opinion_counts oc ON oc.scan_id = s.scan_id
            LEFT JOIN like_counts_cte lc ON lc.scan_id = s.scan_id
            LEFT JOIN view_counts_cte vc ON vc.scan_id = s.scan_id
            LEFT JOIN community_opinion_requests req
              ON req.scan_id = s.scan_id
             AND req.user_pseudo_id = s.user_pseudo_id
             AND req.created_at >= ?
            WHERE {' AND '.join(where)}
        )
        SELECT * FROM ranked
        {cursor_sql}
        ORDER BY recommendation_score DESC, created_at DESC, row_id DESC
        LIMIT ?
    """
    cutoff = int(time.time()) - _OPINION_REQUEST_TTL_S
    with _connect() as conn:
        raw_rows = conn.execute(
            sql,
            (cutoff, *params, *cursor_params, limit),
        ).fetchall()

    rows: list[dict] = []
    for raw in raw_rows:
        row = _row_to_dict(raw[:16])
        row_id = int(raw[16])
        score = int(raw[17])
        row["opinion_count"] = int(raw[18])
        row["like_count"] = int(raw[19])
        row["view_count"] = int(raw[20])
        row["wants_opinion"] = bool(raw[21])
        row["recommendation_score"] = score
        row["recommendation_reason"] = (
            "needs_opinion"
            if row["wants_opinion"]
            else "popular"
            if row["opinion_count"] + row["like_count"] >= 3
            else "fresh"
        )
        row["_cursor_score"] = score
        row["_cursor_row_id"] = row_id
        rlat, rlon = row.get("lat"), row.get("lon")
        row["distance_km"] = (
            round(_haversine_km(float(lat), float(lon), rlat, rlon), 1)
            if has_coords and rlat is not None and rlon is not None
            else None
        )
        row.pop("lat", None)
        row.pop("lon", None)
        rows.append(row)

    liked = liked_scan_ids(
        viewer,
        [r["scan_id"] for r in rows],
        author_id=durable_viewer,
    ) if (viewer or durable_viewer) else set()
    for row in rows:
        row["liked_by_me"] = row["scan_id"] in liked
    return rows


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
    viewer_author_id: str | None = None,
    scope: str = "nearby",
    cursor: tuple[int, int] | None = None,
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

    ``cursor`` is a keyset-pagination cursor — the previous page's last
    ``(created_at, id)`` pair, past which only OLDER rows are returned.
    ``None`` (default) fetches the newest page.

    A cursor is SINGLE-PASS ONLY — it must not be combined with ``disease`` or
    ``species``. Those widen the query into several independently ordered
    passes concatenated together, so the last row of a page is not the page's
    global minimum: paging it would repeat rows across pages and drop a later
    pass's rows entirely. The caller (``/api/community/nearby``) rejects the
    combination with a 422; a proper cursor for it would need one UNION ALL
    with a single global ORDER BY, or a per-pass watermark token.
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
            common += (
                " AND user_pseudo_id NOT IN ("
                "SELECT blocked_pseudo_id FROM community_blocks WHERE blocker_pseudo_id = ? "
                "UNION SELECT blocker_pseudo_id FROM community_blocks WHERE blocked_pseudo_id = ?"
                ")"
            )
            cparams.extend((exclude_user_pseudo_id, exclude_user_pseudo_id))
        durable_viewer = (viewer_author_id or "").strip()
        if durable_viewer:
            common += " AND (author_id IS NULL OR author_id != ?)"
            cparams.append(durable_viewer)
            common += (
                " AND (author_id IS NULL OR author_id NOT IN ("
                "SELECT blocked_author_id FROM community_blocks "
                "WHERE blocker_author_id = ? AND blocked_author_id IS NOT NULL "
                "UNION SELECT blocker_author_id FROM community_blocks "
                "WHERE blocked_author_id = ? AND blocker_author_id IS NOT NULL))"
            )
            cparams.extend((durable_viewer, durable_viewer))
        if _HIDDEN_COMMUNITY_DISEASES:
            common += _hidden_clause()
            cparams.extend(_HIDDEN_COMMUNITY_DISEASES)
        if cursor is not None:
            cursor_created, cursor_row_id = cursor
            common += " AND (created_at < ? OR (created_at = ? AND id < ?))"
            cparams.extend((int(cursor_created), int(cursor_created), int(cursor_row_id)))

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
            sql = (
                f"SELECT {_FEED_COLUMNS}, id FROM community_scans WHERE 1=1"
                + geo_clause + common + dz_clause
                + " ORDER BY created_at DESC, id DESC LIMIT ?"
            )
            rows = [
                {**_row_to_dict(r[:16]), "_cursor_row_id": int(r[16])}
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
        result_ids = [r["scan_id"] for r in result]
        counts = like_counts(result_ids)
        # ``exclude_user_pseudo_id`` doubles as the viewer's own pseudo id —
        # the feed already excludes their own scans by it, so it's the right
        # identity to check "did I like this one" against.
        liked = (
            liked_scan_ids(
                exclude_user_pseudo_id or "",
                result_ids,
                author_id=durable_viewer,
            )
            if (exclude_user_pseudo_id or durable_viewer) else set()
        )
        for row in result:
            row["like_count"] = counts.get(row["scan_id"], 0)
            row["liked_by_me"] = row["scan_id"] in liked
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
    viewer_author_id: str | None = None,
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
        clause += (
            " AND user_pseudo_id NOT IN ("
            "SELECT blocked_pseudo_id FROM community_blocks WHERE blocker_pseudo_id = ? "
            "UNION SELECT blocker_pseudo_id FROM community_blocks WHERE blocked_pseudo_id = ?"
            ")"
        )
        params.extend((exclude_user_pseudo_id, exclude_user_pseudo_id))
    durable_viewer = (viewer_author_id or "").strip()
    if durable_viewer:
        clause += " AND (author_id IS NULL OR author_id != ?)"
        params.append(durable_viewer)
        clause += (
            " AND (author_id IS NULL OR author_id NOT IN ("
            "SELECT blocked_author_id FROM community_blocks "
            "WHERE blocker_author_id = ? AND blocked_author_id IS NOT NULL "
            "UNION SELECT blocker_author_id FROM community_blocks "
            "WHERE blocked_author_id = ? AND blocker_author_id IS NOT NULL))"
        )
        params.extend((durable_viewer, durable_viewer))
    if disease:
        clause += " AND disease = ?"
        params.append(disease)
    clause += _hidden_clause()
    params.extend(_HIDDEN_COMMUNITY_DISEASES)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {_FEED_COLUMNS} FROM community_scans WHERE 1=1"
            + clause
            + " ORDER BY created_at DESC LIMIT ?",
            (*params, limit + 1),
        ).fetchall()
    truncated = len(rows) > limit
    # _row_to_dict keeps lat/lon — the map needs them (the feed strips them).
    items = [_row_to_dict(r) for r in rows[:limit]]
    # Same attachment nearby_scans does — exclude_user_pseudo_id doubles as
    # the viewer's own id here too, so this needs no extra parameter.
    item_ids = [item["scan_id"] for item in items]
    counts = like_counts(item_ids)
    liked = (
        liked_scan_ids(
            exclude_user_pseudo_id or "",
            item_ids,
            author_id=durable_viewer,
        )
        if (exclude_user_pseudo_id or durable_viewer) else set()
    )
    for item in items:
        item["like_count"] = counts.get(item["scan_id"], 0)
        item["liked_by_me"] = item["scan_id"] in liked
    return {"items": items, "truncated": truncated}


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
        rows = conn.execute(
            "SELECT CAST(ROUND(lat/? - 0.5) AS INTEGER) AS ilat,"
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
    viewer_author_id: str | None = None,
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
        exclude_clause = (
            " AND user_pseudo_id != ?"
            " AND user_pseudo_id NOT IN ("
            "SELECT blocked_pseudo_id FROM community_blocks WHERE blocker_pseudo_id = ? "
            "UNION SELECT blocker_pseudo_id FROM community_blocks WHERE blocked_pseudo_id = ?"
            ")"
        )
        exclude_params = (
            exclude_user_pseudo_id,
            exclude_user_pseudo_id,
            exclude_user_pseudo_id,
        )
    durable_viewer = (viewer_author_id or "").strip()
    if durable_viewer:
        exclude_clause += (
            " AND (author_id IS NULL OR author_id != ?)"
            " AND (author_id IS NULL OR author_id NOT IN ("
            "SELECT blocked_author_id FROM community_blocks "
            "WHERE blocker_author_id = ? AND blocked_author_id IS NOT NULL "
            "UNION SELECT blocker_author_id FROM community_blocks "
            "WHERE blocked_author_id = ? AND blocker_author_id IS NOT NULL))"
        )
        exclude_params = (
            *exclude_params,
            durable_viewer,
            durable_viewer,
            durable_viewer,
        )
    if _HIDDEN_COMMUNITY_DISEASES:
        exclude_clause += _hidden_clause()
        exclude_params = (*exclude_params, *_HIDDEN_COMMUNITY_DISEASES)
    with _connect() as conn:
        if region_clean:
            total = conn.execute(
                "SELECT COUNT(*) FROM community_scans "
                "WHERE country = ? AND region = ?" + exclude_clause,
                (country, region_clean, *exclude_params),
            ).fetchone()[0]
            last_week = conn.execute(
                "SELECT COUNT(*) FROM community_scans "
                "WHERE country = ? AND region = ? AND created_at >= ?"
                + exclude_clause,
                (country, region_clean, week_ago, *exclude_params),
            ).fetchone()[0]
            disease_rows = conn.execute(
                "SELECT disease, COUNT(*) AS n FROM community_scans "
                "WHERE country = ? AND region = ? AND created_at >= ?"
                + exclude_clause
                + " GROUP BY disease ORDER BY n DESC LIMIT 3",
                (country, region_clean, week_ago, *exclude_params),
            ).fetchall()
            top_region_rows: list[tuple] = []
        else:
            total = conn.execute(
                "SELECT COUNT(*) FROM community_scans WHERE country = ?"
                + exclude_clause,
                (country, *exclude_params),
            ).fetchone()[0]
            last_week = conn.execute(
                "SELECT COUNT(*) FROM community_scans "
                "WHERE country = ? AND created_at >= ?" + exclude_clause,
                (country, week_ago, *exclude_params),
            ).fetchone()[0]
            disease_rows = conn.execute(
                "SELECT disease, COUNT(*) AS n FROM community_scans "
                "WHERE country = ? AND created_at >= ?" + exclude_clause
                + " GROUP BY disease ORDER BY n DESC LIMIT 3",
                (country, week_ago, *exclude_params),
            ).fetchall()
            top_region_rows = conn.execute(
                "SELECT region, COUNT(*) AS n FROM community_scans "
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
        total = int(conn.execute(
            "SELECT COUNT(*) FROM community_scans WHERE 1=1"
            + where + hidden_clause,
            (*params, *_HIDDEN_COMMUNITY_DISEASES),
        ).fetchone()[0])
        species_rows = conn.execute(
            "SELECT MAX(species) AS sp, COUNT(*) AS n FROM community_scans "
            "WHERE species_lower IS NOT NULL AND species_lower != ''"
            + where + hidden_clause
            + " GROUP BY species_lower ORDER BY n DESC LIMIT ?",
            (*params, *_HIDDEN_COMMUNITY_DISEASES, limit),
        ).fetchall()
        disease_rows = conn.execute(
            "SELECT disease, COUNT(*) AS n FROM community_scans "
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
    """Delete rows + thumbnails older than ``days`` days.

    Covers every table in this store: the scans themselves, their orphaned
    companion rows (opinions / views / opinion requests) and unreferenced
    ``community_authors`` rows. Returns the number of scans deleted."""
    init_db()
    if days <= 0:
        return 0
    cutoff = int(time.time()) - days * 86_400
    with _write_lock, _connect() as conn:
        rows = conn.execute(
            "SELECT thumb_path FROM community_scans WHERE created_at < ?",
            (cutoff,),
        ).fetchall()
        deleted = conn.execute(
            "DELETE FROM community_scans WHERE created_at < ?",
            (cutoff,),
        ).rowcount
        # Sweep companion-table rows that have aged past retention AND no
        # longer reference a live scan — without this, opinions / views /
        # requests outlive the scan they belong to indefinitely (unbounded
        # growth + data retained past the stated window). The age guard
        # keeps a request or opinion that legitimately arrived BEFORE its
        # scan upload (the supported ordering) from being swept early.
        for table in (
            "community_opinions",
            "community_scan_views",
            "community_likes",
            "community_opinion_requests",
        ):
            conn.execute(
                f"DELETE FROM {table} WHERE created_at < ? AND scan_id NOT IN "
                "(SELECT scan_id FROM community_scans)",
                (cutoff,),
            )
        conn.execute("DELETE FROM community_reports WHERE created_at < ?", (cutoff,))
        conn.execute("DELETE FROM community_blocks WHERE created_at < ?", (cutoff,))
        # Durable author rows are on the same retention clock as everything
        # else here. ``last_seen_at`` is refreshed on every adopt, and the app
        # only adopts on a real community write, so a row past the cutoff means
        # the device has shared nothing in the retention window — by which
        # point its own community_scans are being deleted by the statements
        # above. That, not the legacy clause, is what makes the row safe to
        # drop. The legacy clause only protects rows written by OLDER clients,
        # which still send legacy_pseudo_id, so a later backfill can join them;
        # current clients send none, so those rows take the IS NULL arm. Scan
        # uploads and opinions now carry author_id, so both live-reference
        # checks are required before the durable row can be removed.
        conn.execute(
            "DELETE FROM community_authors WHERE last_seen_at < ? "
            "AND author_id NOT IN (SELECT author_id FROM community_scans "
            "                      WHERE author_id IS NOT NULL) "
            "AND author_id NOT IN (SELECT author_id FROM community_opinions "
            "                      WHERE author_id IS NOT NULL) "
            "AND (legacy_pseudo_id IS NULL OR legacy_pseudo_id NOT IN "
            "(SELECT user_pseudo_id FROM community_scans))",
            (cutoff,),
        )
    for row in rows:
        thumb = row[0]
        if not thumb:
            continue
        with contextlib.suppress(OSError):
            (COMMUNITY_THUMB_DIR / thumb).unlink(missing_ok=True)
    return int(deleted or 0)


def _author_token(user_pseudo_id: str | None) -> str | None:
    """Rotating, non-reversible per-author token for anonymous avatars. Mixes
    the 30-day epoch so the same user maps to a new token each window — the
    pseudo id can't be recovered, and activity isn't linkable across windows."""
    if not user_pseudo_id:
        return None
    seed = f"{user_pseudo_id}|{_identity_epoch()}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


# ── Durable forum identity (community_authors) ───────────────────────
#
# Deliberately separate from _author_token above: that one is the existing
# feed's per-window anonymous avatar hash and stays exactly as it is. This
# is the forum's durable, account-free identity — one author_id per device.

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


def resolve_author(
    key_hash: str,
    seed_display_name: str,
    legacy_pseudo_id: str | None = None,
) -> dict:
    """Create-or-return the durable author row for one device's key hash.

    Idempotent by design: a second call with the same ``key_hash`` returns
    the existing row untouched except for ``last_seen_at`` — it does NOT
    overwrite an established ``display_name`` or ``legacy_pseudo_id`` on
    repeat calls. A legacy id may fill an existing NULL left by an older keyed
    write/read ordering, after which it is immutable; profile editing, when it
    exists, is the only thing allowed to change ``display_name``.

    The upsert, legacy attachment, and ``legacy_backfilled_at`` marker run in
    one ``BEGIN IMMEDIATE`` transaction. Concurrent workers therefore cannot
    both execute the attachment suite, and later adopts only refresh the row.
    """
    kh = (key_hash or "").strip()
    if not kh:
        raise ValueError("key_hash is required")
    stored_key = _stored_author_key(kh)
    name = (seed_display_name or "").strip()[:80] or "Plant grower"
    legacy = (legacy_pseudo_id or "").strip() or None

    init_db()
    now = int(time.time())
    with _write_lock, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO community_authors "
            "(author_id, key_hash, key_version, display_name, legacy_pseudo_id, "
            " created_at, last_seen_at) VALUES (?, ?, 2, ?, ?, ?, ?) "
            "ON CONFLICT(key_hash) DO UPDATE SET "
            "last_seen_at = excluded.last_seen_at, "
            "legacy_pseudo_id = COALESCE(community_authors.legacy_pseudo_id, "
            "                            excluded.legacy_pseudo_id)",
            (_new_author_id(), stored_key, name, legacy, now, now),
        )
        # The generated id is discarded on the conflict path, so read the
        # surviving row's id back rather than assuming we won the insert.
        adopted = conn.execute(
            "SELECT author_id, legacy_pseudo_id, legacy_backfilled_at "
            "FROM community_authors WHERE key_hash = ?",
            (stored_key,),
        ).fetchone()
        author_id = str(adopted[0])
        adopted_legacy = (str(adopted[1]) if adopted[1] else None)
        if adopted_legacy and adopted[2] is None:
            # Attach pre-adoption activity to the durable account-free
            # identity. If a current row already exists for the same
            # (content, author), discard the legacy duplicate first so the
            # partial UNIQUE indexes remain satisfiable.
            conn.execute(
                "DELETE FROM community_scan_views "
                "WHERE viewer_pseudo_id = ? AND viewer_author_id IS NULL "
                "AND scan_id IN (SELECT scan_id FROM community_scan_views "
                "                WHERE viewer_author_id = ?)",
                (adopted_legacy, author_id),
            )
            conn.execute(
                "UPDATE community_scan_views SET viewer_author_id = ? "
                "WHERE viewer_pseudo_id = ? AND viewer_author_id IS NULL",
                (author_id, adopted_legacy),
            )
            conn.execute(
                "DELETE FROM community_likes "
                "WHERE user_pseudo_id = ? AND author_id IS NULL "
                "AND scan_id IN (SELECT scan_id FROM community_likes "
                "                WHERE author_id = ?)",
                (adopted_legacy, author_id),
            )
            conn.execute(
                "UPDATE community_likes SET author_id = ? "
                "WHERE user_pseudo_id = ? AND author_id IS NULL",
                (author_id, adopted_legacy),
            )
            conn.execute(
                "UPDATE community_scans SET author_id = ? "
                "WHERE user_pseudo_id = ? AND author_id IS NULL",
                (author_id, adopted_legacy),
            )
            conn.execute(
                "UPDATE community_opinions SET author_id = ? "
                "WHERE author_pseudo_id = ? AND author_id IS NULL",
                (author_id, adopted_legacy),
            )
            conn.execute(
                "DELETE FROM community_reports "
                "WHERE reporter_pseudo_id = ? AND reporter_author_id IS NULL "
                "AND (target_type, target_id) IN ("
                "    SELECT target_type, target_id FROM community_reports "
                "    WHERE reporter_author_id = ?)",
                (adopted_legacy, author_id),
            )
            conn.execute(
                "UPDATE community_reports SET reporter_author_id = ? "
                "WHERE reporter_pseudo_id = ? AND reporter_author_id IS NULL",
                (author_id, adopted_legacy),
            )
            # A block may predate adoption on either side. OR IGNORE keeps a
            # pre-existing durable duplicate authoritative while upgrading all
            # non-conflicting legacy rows.
            conn.execute(
                "UPDATE OR IGNORE community_blocks SET blocker_author_id = ? "
                "WHERE blocker_pseudo_id = ? AND blocker_author_id IS NULL",
                (author_id, adopted_legacy),
            )
            conn.execute(
                "UPDATE OR IGNORE community_blocks SET blocked_author_id = ? "
                "WHERE blocked_pseudo_id = ? AND blocked_author_id IS NULL",
                (author_id, adopted_legacy),
            )
            conn.execute(
                "UPDATE community_authors SET legacy_backfilled_at = ? "
                "WHERE author_id = ?",
                (now, author_id),
            )
        return _author_row(conn, author_id)


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
        "author_token": _author_token(row[14]) if len(row) > 14 else None,
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
    "COMMUNITY_DB_PATH",
    "COMMUNITY_THUMB_DIR",
    "OpinionLockedError",
    "OpinionOwnerError",
    "OPINION_REQUEST_TTL_HOURS",
    "clear_wants_opinion",
    "daily_safety_count_for_ip_hash",
    "daily_write_count_for_ip_hash",
    "expire_stale_opinion_requests",
    "density_grid",
    "decode_recommendation_cursor",
    "encode_recommendation_cursor",
    "init_db",
    "map_scans",
    "nearby_scans",
    "opinion_counts",
    "opinion_counts_for_user",
    "opinion_open",
    "opinions_for",
    "owner_of",
    "popular_snapshot",
    "public_opinion_token",
    "prune_older_than",
    "public_scan_token",
    "record_requested_opinion",
    "record_scan",
    "record_views",
    "region_summary",
    "recommended_scans",
    "report_content",
    "block_content_author",
    "resolve_scan_token",
    "scan_counts",
    "scan_exists",
    "scans_by_species",
    "set_wants_opinion",
    "stats_snapshot",
    "wants_opinion",
]
