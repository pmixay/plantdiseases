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
* No raw lat/lon ever lands here. The Android client reverse-geocodes
  the device's *coarse* location to a `City, Country` string and that
  string is what we store.
* `client_ip_hash` mirrors the feedback store: peppered SHA-256, 64
  bits, used for abuse-rate windows only.
* Thumbnails are 512px JPEGs uploaded by the client. The user can opt
  out from the Android app; uploads default to ON after the consent
  acknowledgement on first scan.
* `delete_for_user(user_pseudo_id)` lets us honour deletion requests
  without joining against any other table.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import math
import sqlite3
import threading
import time
from pathlib import Path

try:
    from .config import get_str  # type: ignore[import-not-found]
    from .countries import to_iso_alpha2
    from .feedback import hash_client_ip
except ImportError:
    from config import get_str
    from countries import to_iso_alpha2
    from feedback import hash_client_ip

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
    created_at       INTEGER NOT NULL,
    PRIMARY KEY (scan_id, viewer_pseudo_id)
);
CREATE INDEX IF NOT EXISTS ix_views_scan ON community_scan_views(scan_id);
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


def _identity_epoch() -> int:
    return int(time.time() // _IDENTITY_ROTATION_S)


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


def _save_thumbnail(scan_id: str, image_bytes: bytes) -> str | None:
    """Persist a 512px JPEG thumbnail under COMMUNITY_THUMB_DIR.

    Returns the relative file path (suitable for storage in the
    ``thumb_path`` column) or ``None`` when the bytes are missing /
    oversized. Image decoding is the caller's job — we only validate
    the byte ceiling and write the bytes straight through.
    """
    if not image_bytes:
        return None
    if len(image_bytes) > COMMUNITY_MAX_THUMB_BYTES:
        logger.info(
            "Community thumb rejected (oversized: %d bytes)", len(image_bytes)
        )
        return None
    # Hash-prefix the filename so two scans with the same scan_id (a
    # mis-behaved client) can't collide. Truncate to 24 hex chars —
    # plenty of collision resistance for a per-pseudo-id namespace.
    # Named by the PUBLIC token, not the raw scan_id: the filename is
    # served to every feed reader via ``thumb_path`` and must not leak
    # the feedback-authorising id.
    digest = hashlib.sha256(image_bytes).hexdigest()[:24]
    filename = f"{public_scan_token(scan_id)}_{digest}.jpg"
    target = COMMUNITY_THUMB_DIR / filename
    try:
        target.write_bytes(image_bytes)
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
            return int(existing[0])
        thumb_path = _save_thumbnail(scan_id, image_bytes) if image_bytes else None
        # Honour a second-opinion request that arrived BEFORE this upload
        # landed (the durable request store decouples the two orderings).
        pending = conn.execute(
            "SELECT 1 FROM community_opinion_requests "
            "WHERE scan_id = ? AND user_pseudo_id = ? LIMIT 1",
            (scan_id, user_pseudo_id),
        ).fetchone()
        wants_opinion = 1 if pending else 0
        cur = conn.execute(
            "INSERT INTO community_scans ("
            "  scan_id, user_pseudo_id, country, region, city, lat, lon, "
            "  disease, confidence, "
            "  species, species_lower, species_score, thumb_path, locale, "
            "  nickname, client_ip_hash, wants_opinion, public_id, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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

    Returns ``True`` once the intent is recorded (i.e. for any non-blank
    id pair). The caller can therefore trust the "waiting" state.
    """
    init_db()
    if not scan_id or not user_pseudo_id:
        return False
    now = int(time.time())
    with _write_lock, _connect() as conn:
        # Durable record of intent (idempotent — one request per scan).
        conn.execute(
            "INSERT INTO community_opinion_requests "
            "(scan_id, user_pseudo_id, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(scan_id) DO UPDATE SET user_pseudo_id = excluded.user_pseudo_id",
            (scan_id, user_pseudo_id, now),
        )
        # Materialise onto the scan row if the upload has already landed.
        conn.execute(
            "UPDATE community_scans SET wants_opinion = 1 "
            "WHERE scan_id = ? AND user_pseudo_id = ?",
            (scan_id, user_pseudo_id),
        )
        return True


def record_opinion(
    *,
    scan_id: str,
    author_pseudo_id: str,
    suggested_disease: str,
    note: str | None = None,
    nickname: str | None = None,
    client_ip: str | None = None,
) -> int:
    """Append one community 'second opinion' on a shared scan. Returns row id."""
    init_db()
    now = int(time.time())
    ip_hash = hash_client_ip(client_ip)
    note_clean = (note or "").strip()[:500] or None
    nick_clean = (nickname or "").strip()[:48] or None
    with _write_lock, _connect() as conn:
        # One opinion per author per scan. If this install already weighed in,
        # keep the original (the client locks its input too) and return that
        # row's id rather than appending a duplicate.
        existing = conn.execute(
            "SELECT id FROM community_opinions "
            "WHERE scan_id = ? AND author_pseudo_id = ? LIMIT 1",
            (scan_id, author_pseudo_id),
        ).fetchone()
        if existing is not None:
            return int(existing[0])
        cur = conn.execute(
            "INSERT INTO community_opinions ("
            "  scan_id, author_pseudo_id, suggested_disease, note, "
            "  nickname, client_ip_hash, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?)",
            (scan_id, author_pseudo_id, suggested_disease, note_clean,
             nick_clean, ip_hash, now),
        )
        return int(cur.lastrowid or 0)


def opinions_for(scan_id: str, *, limit: int = 50) -> dict:
    """Opinions for one scan plus a per-disease consensus tally.

    Shape::

        {"opinions":  [{"suggested_disease", "note", "created_at"}, ...],
         "consensus": [{"disease", "count"}, ...]}   # most-voted first
    """
    init_db()
    limit = max(1, min(int(limit), 100))
    with _connect() as conn:
        rows = conn.execute(
            "SELECT suggested_disease, note, created_at, nickname "
            "FROM community_opinions WHERE scan_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (scan_id, limit),
        ).fetchall()
        tally = conn.execute(
            "SELECT suggested_disease, COUNT(*) AS n "
            "FROM community_opinions WHERE scan_id = ? "
            "GROUP BY suggested_disease ORDER BY n DESC",
            (scan_id,),
        ).fetchall()
    opinions = [
        {"suggested_disease": str(r[0]), "note": r[1] or "",
         "created_at": int(r[2]), "nickname": (r[3] if len(r) > 3 else None) or None}
        for r in rows
    ]
    consensus = [{"disease": str(t[0]), "count": int(t[1])} for t in tally]
    return {
        "opinions": opinions,
        "consensus": consensus,
        "views": view_count_for(scan_id),
    }


def opinion_counts_for_user(user_pseudo_id: str, *, limit: int = 200) -> dict:
    """Opinion counts on a user's OWN shared scans — drives the client's
    "someone left a second opinion" notification poll.

    Each of the caller's scans that has at least one opinion FROM SOMEONE ELSE
    (your own opinions on your scan don't notify you), with the count, the
    latest opinion timestamp, and the scan's disease so the client can say
    "someone weighed in on your <disease> scan".

    Shape::
        {"scans": [{"scan_id", "disease", "opinion_count", "latest_at",
                    "view_count", "latest_nickname"}, ...],
         "total_opinions": N}
    """
    init_db()
    uid = (user_pseudo_id or "").strip()
    if not uid:
        return {"scans": [], "total_opinions": 0, "total_views": 0}
    limit = max(1, min(int(limit), 500))
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
            "GROUP BY s.scan_id, s.disease "
            "ORDER BY latest DESC LIMIT ?",
            (uid, limit),
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


def record_view(scan_id: str, viewer_pseudo_id: str) -> bool:
    """Record that ``viewer_pseudo_id`` opened the shared scan ``scan_id``.

    Deduped per viewer by the (scan_id, viewer_pseudo_id) primary key, so a
    given user only ever counts once no matter how many times they reopen the
    scan. A scan owner viewing their OWN scan never counts. Returns True only
    when a NEW view row was inserted (the count actually grew)."""
    sid = (scan_id or "").strip()
    vid = (viewer_pseudo_id or "").strip()
    if not sid or not vid:
        return False
    init_db()
    with _write_lock, _connect() as conn:
        owner = conn.execute(
            "SELECT user_pseudo_id FROM community_scans WHERE scan_id = ?",
            (sid,),
        ).fetchone()
        # Only count views on a scan that exists and isn't the viewer's own.
        if owner is None or str(owner[0]) == vid:
            return False
        cur = conn.execute(
            "INSERT OR IGNORE INTO community_scan_views "
            "(scan_id, viewer_pseudo_id, created_at) VALUES (?, ?, ?)",
            (sid, vid, int(time.time())),
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
        rows = conn.execute(
            "SELECT scan_id, COUNT(*) FROM community_scan_views "
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
            "SELECT COUNT(*) FROM community_scan_views WHERE scan_id = ?",
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


def opinions_by_author(author_pseudo_id: str, *, limit: int = 100) -> list[dict]:
    """Opinions one install authored, newest first, each with its scan_id."""
    init_db()
    if not author_pseudo_id:
        return []
    limit = max(1, min(int(limit), 200))
    with _connect() as conn:
        rows = conn.execute(
            "SELECT scan_id, suggested_disease, note, created_at "
            "FROM community_opinions WHERE author_pseudo_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (author_pseudo_id, limit),
        ).fetchall()
    return [
        {
            "scan_id": str(r[0]),
            "suggested_disease": str(r[1]),
            "note": r[2] or "",
            "created_at": int(r[3]),
        }
        for r in rows
    ]


def scans_by_species(
    scientific: str,
    *,
    limit: int = 12,
    exclude_user_pseudo_id: str | None = None,
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
    scope: str = "nearby",
) -> list[dict]:
    """Most recent shared scans for one scope.

    ``scope="nearby"`` (default) selects scans within ``radius_km`` of
    ``lat``/``lon`` (the requester's coarse location); ``scope="country"``
    selects the whole ``country``. A *disease-scoped* call (the result-screen
    "similar scans" carousel) widens nearby→country to stay filled but never
    crosses to a different disease; the disease-agnostic feed stays strictly
    in its scope so the Nearby/Country toggle actually changes what's shown.
    """
    init_db()
    if country:
        country = to_iso_alpha2(country)
    limit = max(1, min(int(limit), 50))
    has_coords = lat is not None and lon is not None
    nearby = scope != "country"

    with _connect() as conn:
        common = ""
        cparams: list = []
        if exclude_user_pseudo_id:
            common += " AND user_pseudo_id != ?"
            cparams.append(exclude_user_pseudo_id)
        if _HIDDEN_COMMUNITY_DISEASES:
            common += _hidden_clause()
            cparams.extend(_HIDDEN_COMMUNITY_DISEASES)

        # Geo scopes, most specific first. A disease-scoped carousel widens
        # nearby→country; the plain feed stays in a single scope.
        widen = disease is not None
        geo_scopes: list[tuple[str, list, bool]] = []
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
                f"SELECT {_FEED_COLUMNS} FROM community_scans WHERE 1=1"
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


def delete_for_user(user_pseudo_id: str) -> int:
    """Erase every row + thumbnail attributed to one install."""
    init_db()
    if not user_pseudo_id:
        return 0
    with _write_lock, _connect() as conn:
        rows = conn.execute(
            "SELECT scan_id, thumb_path FROM community_scans WHERE user_pseudo_id = ?",
            (user_pseudo_id,),
        ).fetchall()
        deleted = conn.execute(
            "DELETE FROM community_scans WHERE user_pseudo_id = ?",
            (user_pseudo_id,),
        ).rowcount
        # Opinions / views left by OTHER users on this install's scans
        # reference rows that no longer exist — sweep them so the tables
        # don't accumulate orphans (and the deleted scans stop showing up
        # in opinion/view count queries keyed by scan_id).
        scan_ids = [str(r[0]) for r in rows if r[0]]
        if scan_ids:
            placeholders = ",".join("?" * len(scan_ids))
            conn.execute(
                f"DELETE FROM community_opinions WHERE scan_id IN ({placeholders})",
                tuple(scan_ids),
            )
            conn.execute(
                f"DELETE FROM community_scan_views WHERE scan_id IN ({placeholders})",
                tuple(scan_ids),
            )
        # Views this install left on other people's scans also go.
        conn.execute(
            "DELETE FROM community_scan_views WHERE viewer_pseudo_id = ?",
            (user_pseudo_id,),
        )
        # Opinions this install authored on *other* people's scans also go.
        conn.execute(
            "DELETE FROM community_opinions WHERE author_pseudo_id = ?",
            (user_pseudo_id,),
        )
        # Drop any pending second-opinion requests this install made.
        conn.execute(
            "DELETE FROM community_opinion_requests WHERE user_pseudo_id = ?",
            (user_pseudo_id,),
        )
    for row in rows:
        thumb = row[1]
        if not thumb:
            continue
        with contextlib.suppress(OSError):
            (COMMUNITY_THUMB_DIR / thumb).unlink(missing_ok=True)
    return int(deleted or 0)


def prune_older_than(days: int) -> int:
    """Delete rows + thumbnails older than ``days`` days."""
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
            "community_opinion_requests",
        ):
            conn.execute(
                f"DELETE FROM {table} WHERE created_at < ? AND scan_id NOT IN "
                "(SELECT scan_id FROM community_scans)",
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


def get_user_data(user_pseudo_id: str) -> list[dict]:
    """Retrieve all community scans for a user. Part of GDPR data portability."""
    init_db()
    if not user_pseudo_id:
        return []
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM community_scans WHERE user_pseudo_id = ? ORDER BY created_at DESC",
            (user_pseudo_id,),
        ).fetchall()
        return [dict(r) for r in rows]


__all__ = [
    "COMMUNITY_DB_PATH",
    "COMMUNITY_THUMB_DIR",
    "daily_write_count_for_ip_hash",
    "delete_for_user",
    "get_user_data",
    "density_grid",
    "init_db",
    "map_scans",
    "nearby_scans",
    "opinion_counts",
    "opinion_counts_for_user",
    "opinion_open",
    "opinions_by_author",
    "opinions_for",
    "owner_of",
    "popular_snapshot",
    "prune_older_than",
    "public_scan_token",
    "record_opinion",
    "record_scan",
    "record_view",
    "region_summary",
    "resolve_scan_token",
    "scan_counts",
    "scan_exists",
    "scans_by_species",
    "set_wants_opinion",
    "stats_snapshot",
]
