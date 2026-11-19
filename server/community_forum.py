"""Community forum: posts, answers, likes, photos and search.

This is the Reddit-style layer on top of the community store. It shares
``community.db`` (and its connection helper, write lock, deleted-actor
tombstones and block list) with ``community.py`` so account deletion, blocks
and nickname changes cover forum content through the same paths.

Model
-----
* A **post** is a question, a shared scan ("is the AI right?"), a recovery
  update on an earlier post, or a tip. It can carry up to four photos and,
  when a server-issued analysis token is attached, an immutable copy of the
  AI verdict, which is shown as the first answer (``is_ai``).
* An **answer** explains what to do: free text, an optional list of steps,
  an optional structured diagnosis tag and up to three photos. Answers nest
  three levels deep.
* Posts can only be liked. Answers can be liked or marked "not helpful" — the
  only downvote in the community.
* Photos are uploaded first (``store_media``), decoded, orientation-fixed,
  resized and re-encoded without metadata, then attached to a post or answer.
* A member may publish one profile photo (``set_avatar``): a square,
  re-encoded JPEG served by author token, shown beside their posts and
  answers. Authors carry ``avatar_version`` so clients can cache-bust.
* Search uses SQLite FTS5 with the trigram tokenizer, so Russian word endings
  still match; it degrades to ``LIKE`` when FTS5 is unavailable.

Privacy posture
---------------
Actor ids never leave this module: callers see opaque ``fp``/``fa``/``fm``
ids and the stable ``ca`` author token. Training consent is stored per post
and per answer and is read only by the consent-gated training export.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
import unicodedata
from collections.abc import Iterable
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

try:
    from . import community  # type: ignore[import-not-found]
except ImportError:
    import community

logger = logging.getLogger(__name__)

POST_KINDS = ("question", "scan", "recovery", "tip")
FEED_TABS = ("all", "needs_help", "solved", "mine")
FEED_SORTS = ("hot", "new", "top")
ANSWER_REACTIONS = ("like", "not_helpful")
REPORT_REASONS = frozenset(
    {
        "sexual_content",
        "violence_or_threats",
        "hate_or_harassment",
        "illegal_or_dangerous",
        "spam_or_misleading",
        "privacy_or_impersonation",
        "other_rules_violation",
    }
)

TITLE_MAX = 140
POST_BODY_MAX = 4_000
ANSWER_BODY_MAX = 2_000
STEP_MAX = 200
STEPS_MAX = 8
POST_MEDIA_MAX = 4
ANSWER_MEDIA_MAX = 3
SPECIES_MAX = 80
MAX_ANSWER_DEPTH = 2  # 0 = top level; replies nest at most three levels.
NEEDS_HELP_WINDOW_DAYS = 14
TOP_WINDOW_DAYS = 7
PAGE_MAX = 50
OFFSET_MAX = 1_000
EXCERPT_CHARS = 280
UNATTACHED_MEDIA_TTL_S = 24 * 3_600
REMOVED_CONTENT_PURGE_DAYS = 30
REPORTS_PER_DAY = 30
LEGACY_SYNC_WINDOW_DAYS = 90

MEDIA_LONG_EDGE = 1_600
MEDIA_MIN_EDGE = 32
MEDIA_MAX_SOURCE_EDGE = 8_192
MEDIA_MAX_SOURCE_PIXELS = 40_000_000
MEDIA_MAX_SOURCE_BYTES = 10 * 1024 * 1024
MEDIA_MAX_STORED_BYTES = 1_500 * 1024
MEDIA_JPEG_QUALITY = 85
AVATAR_EDGE = 512

_POST_PREFIX = "fp"
_ANSWER_PREFIX = "fa"
_MEDIA_PREFIX = "fm"
_PUBLIC_ID = re.compile(r"(fp|fa|fm)[0-9a-f]{30}\Z")
_AUTHOR_TOKEN = re.compile(r"ca[0-9a-f]{30}\Z")
_SLUG = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")
_LABEL = re.compile(r"[a-z][a-z0-9_]{0,47}\Z")
_LOCALE = re.compile(r"[A-Za-z]{2,3}([-_][A-Za-z0-9]{2,8})?\Z")
_SEARCH_TERM = re.compile(r"\w+", re.UNICODE)

# Curated at launch: plant families plus the two biggest problem topics.
# ``icon`` names an Android vector drawable (``ic_space_<icon>``).
DEFAULT_SPACES: tuple[tuple[str, str, str, str, str], ...] = (
    ("general", "General care", "Общий уход", "sprout", "topic"),
    ("pests", "Pests", "Вредители", "bug", "topic"),
    ("fungal", "Fungus and mold", "Грибки и плесень", "spores", "topic"),
    ("aroids", "Aroids", "Ароидные", "leaf", "plant"),
    ("succulents", "Succulents and cacti", "Суккуленты и кактусы", "succulent", "plant"),
    ("orchids", "Orchids", "Орхидеи", "flower", "plant"),
    ("ficus", "Ficus", "Фикусы", "leaf", "plant"),
    ("ferns", "Ferns", "Папоротники", "fern", "plant"),
    ("palms", "Palms", "Пальмы", "palm", "plant"),
    ("prayer_plants", "Prayer plants", "Марантовые", "leaf", "plant"),
    ("begonias", "Begonias", "Бегонии", "flower", "plant"),
    ("flowering", "Flowering houseplants", "Цветущие растения", "flower", "plant"),
    ("herbs", "Herbs and edibles", "Зелень и пряности", "sprout", "plant"),
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS forum_spaces (
    slug        TEXT PRIMARY KEY,
    name_en     TEXT NOT NULL,
    name_ru     TEXT NOT NULL,
    icon        TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK(kind IN ('plant', 'topic')),
    sort_order  INTEGER NOT NULL DEFAULT 0,
    archived    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS forum_posts (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id          TEXT    NOT NULL UNIQUE,
    author_actor_id    TEXT    NOT NULL,
    author_token       TEXT    NOT NULL,
    nickname           TEXT,
    kind               TEXT    NOT NULL
                       CHECK(kind IN ('question', 'scan', 'recovery', 'tip')),
    space              TEXT    NOT NULL DEFAULT 'general',
    title              TEXT    NOT NULL DEFAULT '',
    body               TEXT    NOT NULL DEFAULT '',
    species            TEXT,
    species_key        TEXT,
    condition          TEXT,
    urgent             INTEGER NOT NULL DEFAULT 0,
    -- SHA-256 of the analysis token; the raw publication capability is never
    -- stored here. One forum post per analysed photo.
    scan_ref           TEXT    UNIQUE,
    ai_disease         TEXT,
    ai_confidence      REAL,
    ai_species         TEXT,
    parent_post_id     INTEGER,
    -- Random per-plant key shared by a post and its recovery updates; the
    -- training export splits on it so one plant never straddles splits.
    plant_group        TEXT    NOT NULL,
    training_consent   INTEGER NOT NULL DEFAULT 0,
    training_excluded  INTEGER NOT NULL DEFAULT 0,
    state              TEXT    NOT NULL DEFAULT 'visible'
                       CHECK(state IN ('visible', 'hidden', 'removed', 'deleted')),
    locked             INTEGER NOT NULL DEFAULT 0,
    pinned             INTEGER NOT NULL DEFAULT 0,
    like_count         INTEGER NOT NULL DEFAULT 0,
    answer_count       INTEGER NOT NULL DEFAULT 0,
    accepted_answer_id INTEGER,
    accepted_at        INTEGER,
    legacy_scan_id     TEXT    UNIQUE,
    locale             TEXT,
    created_at         INTEGER NOT NULL,
    edited_at          INTEGER,
    last_activity_at   INTEGER NOT NULL,
    state_changed_at   INTEGER
);
CREATE INDEX IF NOT EXISTS ix_forum_posts_feed   ON forum_posts(state, created_at);
CREATE INDEX IF NOT EXISTS ix_forum_posts_space  ON forum_posts(space, state, created_at);
CREATE INDEX IF NOT EXISTS ix_forum_posts_author ON forum_posts(author_actor_id);
CREATE INDEX IF NOT EXISTS ix_forum_posts_token  ON forum_posts(author_token);
CREATE INDEX IF NOT EXISTS ix_forum_posts_parent ON forum_posts(parent_post_id);
CREATE INDEX IF NOT EXISTS ix_forum_posts_group  ON forum_posts(plant_group);

CREATE TABLE IF NOT EXISTS forum_answers (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id         TEXT    NOT NULL UNIQUE,
    post_id           INTEGER NOT NULL,
    parent_id         INTEGER,
    depth             INTEGER NOT NULL DEFAULT 0,
    author_actor_id   TEXT,
    author_token      TEXT,
    nickname          TEXT,
    is_ai             INTEGER NOT NULL DEFAULT 0,
    body              TEXT    NOT NULL DEFAULT '',
    steps_json        TEXT,
    diagnosis         TEXT,
    training_consent  INTEGER NOT NULL DEFAULT 0,
    state             TEXT    NOT NULL DEFAULT 'visible'
                      CHECK(state IN ('visible', 'hidden', 'removed', 'deleted')),
    like_count        INTEGER NOT NULL DEFAULT 0,
    not_helpful_count INTEGER NOT NULL DEFAULT 0,
    legacy_opinion_id INTEGER UNIQUE,
    verified_by_actor_id TEXT,
    verified_at       INTEGER,
    created_at        INTEGER NOT NULL,
    edited_at         INTEGER,
    state_changed_at  INTEGER
);
CREATE INDEX IF NOT EXISTS ix_forum_answers_post   ON forum_answers(post_id, created_at);
CREATE INDEX IF NOT EXISTS ix_forum_answers_author ON forum_answers(author_actor_id);
CREATE INDEX IF NOT EXISTS ix_forum_answers_token  ON forum_answers(author_token);

CREATE TABLE IF NOT EXISTS forum_post_likes (
    post_id    INTEGER NOT NULL,
    actor_id   TEXT    NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (post_id, actor_id)
);
CREATE INDEX IF NOT EXISTS ix_forum_post_likes_actor ON forum_post_likes(actor_id);

CREATE TABLE IF NOT EXISTS forum_answer_reactions (
    answer_id  INTEGER NOT NULL,
    actor_id   TEXT    NOT NULL,
    reaction   TEXT    NOT NULL CHECK(reaction IN ('like', 'not_helpful')),
    created_at INTEGER NOT NULL,
    PRIMARY KEY (answer_id, actor_id)
);
CREATE INDEX IF NOT EXISTS ix_forum_reactions_actor ON forum_answer_reactions(actor_id);

CREATE TABLE IF NOT EXISTS forum_media (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id      TEXT    NOT NULL UNIQUE,
    owner_actor_id TEXT    NOT NULL,
    post_id        INTEGER,
    answer_id      INTEGER,
    position       INTEGER NOT NULL DEFAULT 0,
    filename       TEXT    NOT NULL,
    width          INTEGER NOT NULL,
    height         INTEGER NOT NULL,
    phash          TEXT,
    scan_ref       TEXT,
    flag           TEXT,
    created_at     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_forum_media_post   ON forum_media(post_id, position);
CREATE INDEX IF NOT EXISTS ix_forum_media_answer ON forum_media(answer_id, position);
CREATE INDEX IF NOT EXISTS ix_forum_media_owner  ON forum_media(owner_actor_id, created_at);

CREATE TABLE IF NOT EXISTS forum_avatars (
    actor_id     TEXT    PRIMARY KEY,
    author_token TEXT    NOT NULL UNIQUE,
    filename     TEXT    NOT NULL,
    phash        TEXT,
    version      INTEGER NOT NULL,
    updated_at   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS forum_reports (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    reporter_actor_id TEXT    NOT NULL,
    target_type       TEXT    NOT NULL CHECK(target_type IN ('post', 'answer')),
    post_id           INTEGER NOT NULL,
    answer_id         INTEGER NOT NULL DEFAULT 0,
    target_actor_id   TEXT,
    reason            TEXT    NOT NULL,
    state             TEXT    NOT NULL DEFAULT 'pending'
                      CHECK(state IN ('pending', 'dismissed', 'actioned')),
    created_at        INTEGER NOT NULL,
    resolved_at       INTEGER,
    UNIQUE(reporter_actor_id, target_type, post_id, answer_id)
);
CREATE INDEX IF NOT EXISTS ix_forum_reports_queue  ON forum_reports(state, created_at);
CREATE INDEX IF NOT EXISTS ix_forum_reports_target ON forum_reports(post_id, answer_id);
CREATE INDEX IF NOT EXISTS ix_forum_reports_actor  ON forum_reports(reporter_actor_id, created_at);
"""

_FTS_SCHEMA = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS forum_search USING fts5("
    "title, body, answers, tokenize='trigram')"
)


class ForumError(Exception):
    """A forum rule refused the request; ``status`` is the HTTP status to use."""

    def __init__(self, code: str, status: int = 422) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


_schema_lock = threading.Lock()
_schema_ready_for: str | None = None
_fts_enabled: dict[str, bool] = {}

# Optional hooks set by later phases (levels gate "not helpful"; achievements
# and moderation observe events). Each is called with plain values only.
can_mark_not_helpful = None  # Callable[[sqlite3.Connection, str], bool] | None
event_listeners: list = []  # Callable[[str, dict], None]
# Guards run inside the write transaction and raise ForumError to refuse:
#   write_guards(conn, actor, operation)  operation: "write" | "react"
#   text_guards(conn, actor, text)        new or edited user text
#   media_guards(conn, actor, dhash)      a photo about to be stored
write_guards: list = []
text_guards: list = []
media_guards: list = []


def _guard(
    conn: sqlite3.Connection,
    actor: str,
    operation: str,
    *,
    text: str | None = None,
) -> None:
    for guard in list(write_guards):
        guard(conn, actor, operation)
    if text:
        for guard in list(text_guards):
            guard(conn, actor, text)


def _emit(event: str, payload: dict) -> None:
    for listener in list(event_listeners):
        try:
            listener(event, payload)
        except Exception:  # noqa: BLE001 — observers must never break a write
            logger.warning("Forum event listener failed for %s", event)


# ── schema ──────────────────────────────────────────────────────────────


def media_dir() -> Path:
    """Forum photos live beside the community thumbnails, in their own folder."""
    return Path(community.COMMUNITY_THUMB_DIR) / "forum"


def init_db() -> None:
    """Create the forum schema in the current community DB. Idempotent.

    Keyed by the community DB path rather than a boolean so a test that
    redirects ``community.COMMUNITY_DB_PATH`` gets the schema in its own DB.
    """
    global _schema_ready_for
    community.init_db()
    key = str(community.COMMUNITY_DB_PATH)
    if _schema_ready_for == key:
        return
    with _schema_lock:
        if _schema_ready_for == key:
            return
        with community._write_lock, community._connect() as conn:
            conn.executescript(_SCHEMA)
            for table, column, decl in (
                ("forum_posts", "training_excluded", "INTEGER NOT NULL DEFAULT 0"),
                ("forum_posts", "accepted_at", "INTEGER"),
                ("forum_answers", "verified_by_actor_id", "TEXT"),
                ("forum_answers", "verified_at", "INTEGER"),
            ):
                community._add_column_if_missing(conn, table, column, decl)
            try:
                conn.execute(_FTS_SCHEMA)
                _fts_enabled[key] = True
            except sqlite3.OperationalError:
                logger.warning("FTS5 trigram search unavailable; forum search uses LIKE")
                _fts_enabled[key] = False
            for order, (slug, name_en, name_ru, icon, kind) in enumerate(DEFAULT_SPACES):
                conn.execute(
                    "INSERT OR IGNORE INTO forum_spaces "
                    "(slug, name_en, name_ru, icon, kind, sort_order) VALUES (?, ?, ?, ?, ?, ?)",
                    (slug, name_en, name_ru, icon, kind, order),
                )
        # The photo folder is created on first write, not here, so the
        # thumbnail volume stays untouched until the forum is used.
        _schema_ready_for = key


def _fts_on() -> bool:
    return _fts_enabled.get(str(community.COMMUNITY_DB_PATH), False)


# ── small helpers ───────────────────────────────────────────────────────


def _now(now: int | None) -> int:
    return int(time.time() if now is None else now)


def _new_public_id(prefix: str) -> str:
    return prefix + secrets.token_hex(15)


def _valid_public_id(value: str | None, prefix: str) -> str | None:
    token = (value or "").strip()
    if _PUBLIC_ID.fullmatch(token) and token.startswith(prefix):
        return token
    return None


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏‪-‮⁦-⁩]")


def clean_text(value: str | None, *, limit: int, multiline: bool = True) -> str:
    """NFC-normalise, drop control/bidi characters, trim and bound user text."""
    text = unicodedata.normalize("NFC", value or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL.sub("", text)
    if multiline:
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
    else:
        text = re.sub(r"\s+", " ", text)
    return text.strip()[:limit].strip()


def _clean_steps(steps: Iterable[str] | None) -> list[str]:
    cleaned: list[str] = []
    for step in steps or []:
        value = clean_text(step, limit=STEP_MAX, multiline=False)
        if value:
            cleaned.append(value)
    if len(cleaned) > STEPS_MAX:
        raise ForumError("too_many_steps")
    return cleaned


def _clean_label(value: str | None) -> str | None:
    label = (value or "").strip().lower()
    if not label:
        return None
    if not _LABEL.fullmatch(label):
        raise ForumError("invalid_label")
    return label


def _clean_locale(value: str | None) -> str | None:
    locale = (value or "").strip()
    return locale if locale and _LOCALE.fullmatch(locale) else None


def _actor(value: str | None) -> str:
    actor = community._normalise_actor_id(value)
    if actor is None:
        raise ForumError("invalid_actor", 401)
    return actor


def _require_live_actor(conn: sqlite3.Connection, actor: str) -> None:
    if community._actor_is_deleted(conn, actor):
        raise ForumError("account_deleted", 403)


def _blocked(conn: sqlite3.Connection, first: str | None, second: str | None) -> bool:
    if not first or not second or first == second:
        return False
    return community._actors_blocked(conn, first, second)


def _author_token(actor: str | None) -> str | None:
    return community.public_account_author_token(actor) if actor else None


def _visible_author_clause(column: str, viewer: str | None) -> tuple[str, tuple]:
    """SQL hiding deleted actors and, for a signed viewer, blocks either way."""
    clause = f"{column} NOT IN (SELECT actor_id FROM community_deleted_actors)"
    params: tuple = ()
    if viewer:
        clause += (
            f" AND {column} NOT IN (SELECT blocked_actor_id FROM community_actor_blocks "
            "WHERE blocker_actor_id = ?)"
            f" AND {column} NOT IN (SELECT blocker_actor_id FROM community_actor_blocks "
            "WHERE blocked_actor_id = ?)"
        )
        params = (viewer, viewer)
    return clause, params


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(f"o:{offset}".encode()).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        prefix, _, value = raw.partition(":")
        offset = int(value)
    except (ValueError, UnicodeDecodeError, TypeError):
        raise ForumError("invalid_cursor") from None
    if prefix != "o" or not 0 <= offset <= OFFSET_MAX:
        raise ForumError("invalid_cursor")
    return offset


def _page_limit(limit: int | None) -> int:
    return max(1, min(int(limit or 20), PAGE_MAX))


# ── media ───────────────────────────────────────────────────────────────


def _dhash(image: Image.Image) -> str:
    """64-bit difference hash, for duplicate and banned-image matching."""
    small = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = small.tobytes()  # mode "L": one byte per pixel, row-major
    bits = 0
    for row in range(8):
        for col in range(8):
            left = pixels[row * 9 + col]
            right = pixels[row * 9 + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return f"{bits:016x}"


def _open_photo(image_bytes: bytes) -> Image.Image:
    """Decode and orient an upload as RGB, refusing anything unreasonable."""
    if not image_bytes or len(image_bytes) > MEDIA_MAX_SOURCE_BYTES:
        raise ForumError("invalid_image", 413 if image_bytes else 422)
    try:
        with Image.open(io.BytesIO(image_bytes)) as opened:
            width, height = opened.size
            if (
                min(width, height) < MEDIA_MIN_EDGE
                or max(width, height) > MEDIA_MAX_SOURCE_EDGE
                or width * height > MEDIA_MAX_SOURCE_PIXELS
            ):
                raise ForumError("invalid_image")
            opened.load()
            return ImageOps.exif_transpose(opened).convert("RGB")
    except ForumError:
        raise
    except (Image.DecompressionBombError, OSError, UnidentifiedImageError, ValueError):
        raise ForumError("invalid_image") from None


def _encode_clean(image: Image.Image) -> tuple[bytes, Image.Image]:
    """Copy pixels into a fresh image (no metadata) and encode a bounded JPEG."""
    clean = Image.new("RGB", image.size)
    clean.paste(image)
    out = io.BytesIO()
    quality = MEDIA_JPEG_QUALITY
    while True:
        out.seek(0)
        out.truncate()
        clean.save(out, format="JPEG", quality=quality, optimize=True, progressive=False)
        if out.tell() <= MEDIA_MAX_STORED_BYTES or quality <= 60:
            break
        quality -= 10
    body = out.getvalue()
    if len(body) > MEDIA_MAX_STORED_BYTES:
        raise ForumError("invalid_image", 413)
    return body, clean


def sanitize_image(image_bytes: bytes) -> tuple[bytes, int, int, str]:
    """Decode, orient, bound and re-encode a photo with no source metadata.

    Returns ``(jpeg_bytes, width, height, dhash)``. Raises ``ForumError`` for
    anything that is not a reasonable photo.
    """
    image = _open_photo(image_bytes)
    if max(image.size) > MEDIA_LONG_EDGE:
        image.thumbnail((MEDIA_LONG_EDGE, MEDIA_LONG_EDGE), Image.Resampling.LANCZOS)
    body, clean = _encode_clean(image)
    return body, clean.size[0], clean.size[1], _dhash(clean)


def sanitize_avatar(image_bytes: bytes) -> tuple[bytes, int, str]:
    """A profile photo: centre-cropped square, at most ``AVATAR_EDGE`` px.

    Returns ``(jpeg_bytes, edge, dhash)``; same refusals as ``sanitize_image``.
    """
    image = _open_photo(image_bytes)
    edge = min(AVATAR_EDGE, *image.size)
    square = ImageOps.fit(image, (edge, edge), Image.Resampling.LANCZOS)
    body, clean = _encode_clean(square)
    return body, edge, _dhash(clean)


def scan_ref_for(token: str) -> str:
    return hashlib.sha256(f"forum-scan-v1|{token}".encode()).hexdigest()


def _scan_claim(conn: sqlite3.Connection, token: str, actor: str, now: int) -> sqlite3.Row | tuple:
    """Load a current analysis capability this actor may attach to a post."""
    row = conn.execute(
        "SELECT expires_at, disease, confidence, species, image_binding, claimed_actor_id "
        "FROM community_analysis_tokens WHERE token = ?",
        (token,),
    ).fetchone()
    if row is None or int(row[0]) <= now or row[1] is None or row[4] is None:
        raise ForumError("scan_unknown")
    if row[5] is not None and str(row[5]) != actor:
        raise ForumError("scan_taken", 409)
    return row


def store_media(
    *,
    actor_id: str,
    image_bytes: bytes,
    scan_token: str | None = None,
    now: int | None = None,
) -> dict:
    """Store one photo for a later post or answer. Returns its public handle.

    With ``scan_token`` the raw bytes must be exactly the photo the server
    analysed for that token, which is what lets a post carry the AI verdict.
    """
    init_db()
    actor = _actor(actor_id)
    timestamp = _now(now)
    scan_ref: str | None = None
    token = (scan_token or "").strip()
    if token:
        if len(token) > 128:
            raise ForumError("scan_unknown")
        binding = hashlib.sha256(
            token.encode("utf-8") + b"\0" + hashlib.sha256(image_bytes or b"").digest()
        ).hexdigest()
        with community._connect() as conn:
            claim = _scan_claim(conn, token, actor, timestamp)
        if binding != str(claim[4]).lower():
            raise ForumError("scan_image_mismatch")
        scan_ref = scan_ref_for(token)
    body, width, height, phash = sanitize_image(image_bytes)
    public_id = _new_public_id(_MEDIA_PREFIX)
    filename = f"{public_id}.jpg"
    folder = media_dir()
    target = folder / filename
    temporary = folder / f".{filename}.tmp"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(body)
        temporary.chmod(0o600)
        os.replace(temporary, target)
    except OSError:
        temporary.unlink(missing_ok=True)
        logger.warning("Failed to write forum photo")
        raise ForumError("media_write_failed", 500) from None
    try:
        with community._write_lock, community._connect() as conn:
            _require_live_actor(conn, actor)
            _guard(conn, actor, "write")
            for guard in list(media_guards):
                guard(conn, actor, phash)
            conn.execute(
                "INSERT INTO forum_media (public_id, owner_actor_id, filename, width, height, "
                "phash, scan_ref, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (public_id, actor, filename, width, height, phash, scan_ref, timestamp),
            )
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return {"media_id": public_id, "width": width, "height": height}


def media_file(
    media_id: str,
    *,
    viewer_actor_id: str | None = None,
    moderator: bool = False,
) -> Path | None:
    """Path of a photo the viewer may see, or ``None``.

    Attached photos follow their post's visibility; an unattached upload is
    visible only to its owner (the composer preview).
    """
    token = _valid_public_id(media_id, _MEDIA_PREFIX)
    if token is None:
        return None
    init_db()
    viewer = community._normalise_actor_id(viewer_actor_id)
    with community._connect() as conn:
        row = conn.execute(
            "SELECT m.filename, m.owner_actor_id, m.post_id, m.answer_id, "
            "p.state, a.state, p.author_actor_id "
            "FROM forum_media m "
            "LEFT JOIN forum_answers a ON a.id = m.answer_id "
            "LEFT JOIN forum_posts p ON p.id = COALESCE(m.post_id, a.post_id) "
            "WHERE m.public_id = ? "
            "AND m.owner_actor_id NOT IN (SELECT actor_id FROM community_deleted_actors)",
            (token,),
        ).fetchone()
    if row is None:
        return None
    filename, owner, post_id, answer_id, post_state, answer_state, post_author = row
    if moderator and post_state != "deleted" and answer_state != "deleted":
        path = media_dir() / str(filename)
        return path if path.is_file() else None
    if post_id is None and answer_id is None:
        if viewer != owner:
            return None
    else:
        allowed_post = post_state == "visible" or (
            post_state == "hidden" and viewer is not None and viewer == post_author
        )
        if not allowed_post:
            return None
        if answer_id is not None and answer_state != "visible":
            return None
    path = media_dir() / str(filename)
    return path if path.is_file() else None


def _attach_media(
    conn: sqlite3.Connection,
    *,
    actor: str,
    media_ids: list[str],
    limit: int,
    post_id: int | None = None,
    answer_id: int | None = None,
) -> list[sqlite3.Row | tuple]:
    ids = list(dict.fromkeys(media_ids or []))
    if len(ids) > limit:
        raise ForumError("too_many_photos")
    rows = []
    for position, media_id in enumerate(ids):
        token = _valid_public_id(media_id, _MEDIA_PREFIX)
        if token is None:
            raise ForumError("media_not_found")
        row = conn.execute(
            "SELECT id, owner_actor_id, post_id, answer_id, scan_ref FROM forum_media "
            "WHERE public_id = ?",
            (token,),
        ).fetchone()
        if row is None or row[1] != actor or row[2] is not None or row[3] is not None:
            raise ForumError("media_not_found")
        conn.execute(
            "UPDATE forum_media SET post_id = ?, answer_id = ?, position = ? WHERE id = ?",
            (post_id, answer_id, position, int(row[0])),
        )
        rows.append(row)
    return rows


def _media_for(conn: sqlite3.Connection, *, post_id: int | None = None, answer_ids: list[int] | None = None):
    if post_id is not None:
        rows = conn.execute(
            "SELECT public_id, width, height FROM forum_media WHERE post_id = ? "
            "ORDER BY position",
            (post_id,),
        ).fetchall()
        return [{"media_id": r[0], "width": int(r[1]), "height": int(r[2])} for r in rows]
    result: dict[int, list[dict]] = {}
    if not answer_ids:
        return result
    placeholders = ",".join("?" * len(answer_ids))
    for answer_id, public_id, width, height in conn.execute(
        f"SELECT answer_id, public_id, width, height FROM forum_media "  # nosec B608
        f"WHERE answer_id IN ({placeholders}) ORDER BY answer_id, position",
        tuple(answer_ids),
    ).fetchall():
        result.setdefault(int(answer_id), []).append(
            {"media_id": public_id, "width": int(width), "height": int(height)}
        )
    return result


def _delete_media_rows(conn: sqlite3.Connection, where: str, params: tuple) -> list[str]:
    filenames = [
        str(row[0])
        for row in conn.execute(
            f"SELECT filename FROM forum_media WHERE {where}", params  # nosec B608
        ).fetchall()
    ]
    conn.execute(f"DELETE FROM forum_media WHERE {where}", params)  # nosec B608
    return filenames


def _unlink_media(filenames: Iterable[str]) -> bool:
    """Best-effort file removal; returns False if any file could not be removed."""
    ok = True
    folder = media_dir()
    for name in filenames:
        try:
            (folder / name).unlink(missing_ok=True)
        except OSError:
            ok = False
    return ok


# ── profile photos ──────────────────────────────────────────────────────


def _avatar_filename() -> str:
    return f"av{secrets.token_hex(15)}.jpg"


def _avatar_versions(conn: sqlite3.Connection, actors: Iterable[str | None]) -> dict[str, int]:
    """``avatar_version`` per actor that has a profile photo."""
    wanted = sorted({a for a in actors if a})
    if not wanted:
        return {}
    placeholders = ",".join("?" * len(wanted))
    try:
        rows = conn.execute(
            f"SELECT actor_id, version FROM forum_avatars "  # nosec B608
            f"WHERE actor_id IN ({placeholders})",
            tuple(wanted),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {str(r[0]): int(r[1]) for r in rows}


def avatar_version(actor_id: str | None) -> int | None:
    """The current profile-photo version of one actor, or ``None``."""
    actor = community._normalise_actor_id(actor_id)
    if actor is None:
        return None
    init_db()
    with community._connect() as conn:
        return _avatar_versions(conn, [actor]).get(actor)


def set_avatar(*, actor_id: str, image_bytes: bytes, now: int | None = None) -> dict:
    """Publish (or replace) the member's profile photo.

    The photo is re-encoded like any forum photo, cropped square, and refused
    when it matches a banned image or the member may not write.
    """
    init_db()
    actor = _actor(actor_id)
    timestamp = _now(now)
    body, edge, phash = sanitize_avatar(image_bytes)
    filename = _avatar_filename()
    folder = media_dir()
    target = folder / filename
    temporary = folder / f".{filename}.tmp"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(body)
        temporary.chmod(0o600)
        os.replace(temporary, target)
    except OSError:
        temporary.unlink(missing_ok=True)
        logger.warning("Failed to write a profile photo")
        raise ForumError("media_write_failed", 500) from None
    token = _author_token(actor)
    try:
        with community._write_lock, community._connect() as conn:
            _require_live_actor(conn, actor)
            _guard(conn, actor, "write")
            for guard in list(media_guards):
                guard(conn, actor, phash)
            previous = conn.execute(
                "SELECT filename, version FROM forum_avatars WHERE actor_id = ?", (actor,)
            ).fetchone()
            # Milliseconds, strictly increasing per actor, so a client URL
            # carrying the version never serves a replaced photo from cache.
            version = max(time.time_ns() // 1_000_000, int(previous[1]) + 1 if previous else 0)
            conn.execute(
                "INSERT INTO forum_avatars (actor_id, author_token, filename, phash, version, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(actor_id) DO UPDATE SET "
                "filename = excluded.filename, phash = excluded.phash, "
                "version = excluded.version, updated_at = excluded.updated_at",
                (actor, token, filename, phash, version, timestamp),
            )
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    if previous is not None:
        _unlink_media([str(previous[0])])
    return {"author_token": token, "avatar_version": version, "size": edge}


def remove_avatar_row(conn: sqlite3.Connection, actor: str) -> tuple[str, str | None] | None:
    """Delete the row inside the caller's transaction; returns ``(filename, phash)``.

    The caller unlinks the file (``unlink_avatar``) after the commit.
    """
    row = conn.execute(
        "SELECT filename, phash FROM forum_avatars WHERE actor_id = ?", (actor,)
    ).fetchone()
    if row is None:
        return None
    conn.execute("DELETE FROM forum_avatars WHERE actor_id = ?", (actor,))
    return str(row[0]), (str(row[1]) if row[1] else None)


def unlink_avatar(filename: str) -> None:
    _unlink_media([filename])


def delete_avatar(*, actor_id: str) -> bool:
    """Withdraw the member's profile photo. Returns whether one existed."""
    init_db()
    actor = _actor(actor_id)
    with community._write_lock, community._connect() as conn:
        removed = remove_avatar_row(conn, actor)
    if removed is None:
        return False
    unlink_avatar(removed[0])
    return True


def avatar_file(author_token: str) -> tuple[Path, int] | None:
    """``(path, version)`` of a member's public profile photo, or ``None``."""
    token = (author_token or "").strip()
    if not _AUTHOR_TOKEN.fullmatch(token):
        return None
    init_db()
    with community._connect() as conn:
        row = conn.execute(
            "SELECT filename, version FROM forum_avatars WHERE author_token = ? "
            "AND actor_id NOT IN (SELECT actor_id FROM community_deleted_actors)",
            (token,),
        ).fetchone()
    if row is None:
        return None
    path = media_dir() / str(row[0])
    return (path, int(row[1])) if path.is_file() else None


# ── search index ───────────────────────────────────────────────────────


def _label_words(label: str | None) -> str:
    if not label:
        return ""
    words = label.replace("_", " ")
    try:
        try:
            from .diseases_data import DISEASES_DATABASE  # type: ignore[import-not-found]
        except ImportError:
            from diseases_data import DISEASES_DATABASE
        entry = DISEASES_DATABASE.get(label) or {}
        words = " ".join(
            part for part in (words, entry.get("name_en"), entry.get("name_ru")) if part
        )
    except Exception:  # noqa: BLE001 — search text is best effort
        pass
    return words


def _reindex(conn: sqlite3.Connection, post_id: int) -> None:
    if not _fts_on():
        return
    conn.execute("DELETE FROM forum_search WHERE rowid = ?", (post_id,))
    row = conn.execute(
        "SELECT title, body, species, condition, ai_disease, ai_species, state "
        "FROM forum_posts WHERE id = ?",
        (post_id,),
    ).fetchone()
    if row is None or row[6] != "visible":
        return
    title, body, species, condition, ai_disease, ai_species, _state = row
    answers = conn.execute(
        "SELECT body, steps_json, diagnosis FROM forum_answers "
        "WHERE post_id = ? AND state = 'visible' AND is_ai = 0 ORDER BY id LIMIT 50",
        (post_id,),
    ).fetchall()
    answer_text = []
    for answer_body, steps_json, diagnosis in answers:
        answer_text.append(answer_body or "")
        if steps_json:
            with contextlib.suppress(ValueError, TypeError):
                answer_text.extend(json.loads(steps_json))
        answer_text.append(_label_words(diagnosis))
    body_text = " ".join(
        part
        for part in (
            body,
            species,
            ai_species,
            _label_words(condition),
            _label_words(ai_disease),
        )
        if part
    )
    conn.execute(
        "INSERT INTO forum_search (rowid, title, body, answers) VALUES (?, ?, ?, ?)",
        (post_id, title or "", body_text[:8_000], " ".join(answer_text)[:20_000]),
    )


def _match_stem(term: str) -> str:
    """Trim a word's ending so inflected forms match ("вентиляцию" ~ "вентиляция").

    Trigram FTS matches substrings, so a shorter stem finds every form that
    starts with it. Short words stay whole to keep precision.
    """
    return term[: len(term) - 2] if len(term) >= 6 else term


def search_terms(query: str | None) -> list[str]:
    """Words of a query, lower-cased and de-duplicated, as matched and highlighted."""
    words = [w.lower() for w in _SEARCH_TERM.findall(query or "")]
    return list(dict.fromkeys(w for w in words if len(w) >= 2))[:8]


# ── row shaping ─────────────────────────────────────────────────────────

_POST_COLUMNS = (
    "p.id, p.public_id, p.author_actor_id, p.author_token, p.nickname, p.kind, p.space, "
    "p.title, p.body, p.species, p.condition, p.urgent, p.ai_disease, p.ai_confidence, "
    "p.ai_species, p.parent_post_id, p.state, p.locked, p.pinned, p.like_count, "
    "p.answer_count, p.accepted_answer_id, p.created_at, p.edited_at, p.last_activity_at, "
    "p.training_consent"
)


def _post_card(
    conn: sqlite3.Connection,
    row: tuple,
    viewer: str | None,
    *,
    full: bool = False,
) -> dict:
    (
        post_id, public_id, author, author_token, nickname, kind, space, title, body,
        species, condition, urgent, ai_disease, ai_confidence, ai_species, parent_post_id,
        state, locked, pinned, like_count, answer_count, accepted_answer_id, created_at,
        edited_at, last_activity_at, training_consent,
    ) = row
    media = _media_for(conn, post_id=int(post_id))
    liked = False
    if viewer:
        liked = conn.execute(
            "SELECT 1 FROM forum_post_likes WHERE post_id = ? AND actor_id = ?",
            (post_id, viewer),
        ).fetchone() is not None
    accepted_public = None
    accepted_excerpt = None
    if accepted_answer_id is not None:
        accepted = conn.execute(
            "SELECT public_id, body, diagnosis, is_ai FROM forum_answers "
            "WHERE id = ? AND state = 'visible'",
            (accepted_answer_id,),
        ).fetchone()
        if accepted is not None:
            accepted_public = accepted[0]
            accepted_excerpt = (accepted[1] or "")[:EXCERPT_CHARS] or None
    parent_public = None
    if parent_post_id is not None:
        parent = conn.execute(
            "SELECT public_id FROM forum_posts WHERE id = ? AND state = 'visible'",
            (parent_post_id,),
        ).fetchone()
        parent_public = parent[0] if parent else None
    card = {
        "id": public_id,
        "kind": kind,
        "space": space,
        "title": title,
        "body": body if full else (body or "")[:EXCERPT_CHARS],
        "species": species,
        "condition": condition,
        "urgent": bool(urgent),
        "author": {
            "nickname": nickname or "",
            "author_token": author_token,
            "is_me": bool(viewer and viewer == author),
            "avatar_version": _avatar_versions(conn, [author]).get(author),
        },
        "ai": (
            {
                "disease": ai_disease,
                "confidence": float(ai_confidence) if ai_confidence is not None else None,
                "species": ai_species,
            }
            if ai_disease
            else None
        ),
        "media": media if full else media[:1],
        "media_count": len(media),
        "like_count": int(like_count),
        "answer_count": int(answer_count),
        "liked_by_me": liked,
        "solved": accepted_public is not None,
        "accepted_answer_id": accepted_public,
        "accepted_excerpt": accepted_excerpt,
        "parent_post_id": parent_public,
        "locked": bool(locked),
        "pinned": bool(pinned),
        "state": state,
        "created_at": int(created_at),
        "edited_at": int(edited_at) if edited_at is not None else None,
        "last_activity_at": int(last_activity_at),
    }
    if full:
        card["training_consent"] = bool(training_consent) if viewer == author else None
        updates = conn.execute(
            "SELECT public_id, title, created_at FROM forum_posts "
            "WHERE parent_post_id = ? AND state = 'visible' ORDER BY created_at",
            (post_id,),
        ).fetchall()
        card["recovery_updates"] = [
            {"id": u[0], "title": u[1], "created_at": int(u[2])} for u in updates
        ]
    return card


def _load_post(conn: sqlite3.Connection, public_id: str) -> tuple | None:
    token = _valid_public_id(public_id, _POST_PREFIX)
    if token is None:
        return None
    return conn.execute(
        f"SELECT {_POST_COLUMNS} FROM forum_posts p WHERE p.public_id = ?", (token,)  # nosec B608
    ).fetchone()


def _post_visible_to(conn: sqlite3.Connection, row: tuple, viewer: str | None) -> bool:
    author, state = row[2], row[16]
    if community._actor_is_deleted(conn, author):
        return False
    if state == "visible":
        return not _blocked(conn, viewer, author)
    return state == "hidden" and viewer is not None and viewer == author


def _require_post(conn: sqlite3.Connection, public_id: str, viewer: str | None) -> tuple:
    row = _load_post(conn, public_id)
    if row is None or not _post_visible_to(conn, row, viewer):
        raise ForumError("post_not_found", 404)
    return row


def _answer_row(conn: sqlite3.Connection, public_id: str) -> tuple | None:
    token = _valid_public_id(public_id, _ANSWER_PREFIX)
    if token is None:
        return None
    return conn.execute(
        "SELECT a.id, a.post_id, a.parent_id, a.depth, a.author_actor_id, a.is_ai, a.state, "
        "p.public_id, p.author_actor_id, p.state, p.locked, a.public_id "
        "FROM forum_answers a JOIN forum_posts p ON p.id = a.post_id WHERE a.public_id = ?",
        (token,),
    ).fetchone()


def _require_answer(conn: sqlite3.Connection, public_id: str, viewer: str | None) -> tuple:
    row = _answer_row(conn, public_id)
    if row is None or row[6] != "visible" or row[9] != "visible":
        raise ForumError("answer_not_found", 404)
    author, post_author = row[4], row[8]
    if (
        (author and community._actor_is_deleted(conn, author))
        or community._actor_is_deleted(conn, post_author)
        or _blocked(conn, viewer, author)
        or _blocked(conn, viewer, post_author)
    ):
        raise ForumError("answer_not_found", 404)
    return row


# ── spaces ──────────────────────────────────────────────────────────────


def list_spaces() -> list[dict]:
    init_db()
    with community._connect() as conn:
        rows = conn.execute(
            "SELECT s.slug, s.name_en, s.name_ru, s.icon, s.kind, "
            "(SELECT COUNT(*) FROM forum_posts p WHERE p.space = s.slug AND p.state = 'visible') "
            "FROM forum_spaces s WHERE s.archived = 0 ORDER BY s.sort_order, s.slug"
        ).fetchall()
    return [
        {
            "slug": r[0],
            "name_en": r[1],
            "name_ru": r[2],
            "icon": r[3],
            "kind": r[4],
            "post_count": int(r[5]),
        }
        for r in rows
    ]


def _require_space(conn: sqlite3.Connection, space: str | None) -> str:
    slug = (space or "general").strip().lower()
    if not _SLUG.fullmatch(slug):
        raise ForumError("invalid_space")
    row = conn.execute(
        "SELECT 1 FROM forum_spaces WHERE slug = ? AND archived = 0", (slug,)
    ).fetchone()
    if row is None:
        raise ForumError("invalid_space")
    return slug


# ── posts ───────────────────────────────────────────────────────────────


def create_post(
    *,
    actor_id: str,
    nickname: str | None,
    kind: str,
    title: str | None,
    body: str | None = None,
    space: str | None = None,
    species: str | None = None,
    condition: str | None = None,
    urgent: bool = False,
    media_ids: list[str] | None = None,
    scan_token: str | None = None,
    parent_post_id: str | None = None,
    training_consent: bool = False,
    locale: str | None = None,
    now: int | None = None,
) -> dict:
    """Publish a post and return it as ``get_post`` would."""
    init_db()
    actor = _actor(actor_id)
    timestamp = _now(now)
    kind_value = (kind or "").strip().lower()
    if kind_value not in POST_KINDS:
        raise ForumError("invalid_kind")
    title_value = clean_text(title, limit=TITLE_MAX, multiline=False)
    body_value = clean_text(body, limit=POST_BODY_MAX)
    if kind_value != "scan" and not title_value:
        raise ForumError("title_required")
    species_value = clean_text(species, limit=SPECIES_MAX, multiline=False) or None
    condition_value = _clean_label(condition)
    token = (scan_token or "").strip()
    if kind_value == "scan" and not token:
        raise ForumError("scan_required")
    if kind_value == "recovery" and not parent_post_id:
        raise ForumError("parent_required")
    public_id = _new_public_id(_POST_PREFIX)
    with community._write_lock, community._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _require_live_actor(conn, actor)
            _guard(conn, actor, "write", text=f"{title_value}\n{body_value}")
            space_value = _require_space(conn, space)
            parent_id: int | None = None
            plant_group = secrets.token_hex(16)
            if parent_post_id:
                parent = _load_post(conn, parent_post_id)
                if parent is None or parent[16] != "visible" or parent[2] != actor:
                    raise ForumError("parent_not_found", 404)
                parent_id = int(parent[0])
                plant_group = str(
                    conn.execute(
                        "SELECT plant_group FROM forum_posts WHERE id = ?", (parent_id,)
                    ).fetchone()[0]
                )
            ai: tuple | None = None
            scan_ref: str | None = None
            if token:
                claim = _scan_claim(conn, token, actor, timestamp)
                scan_ref = scan_ref_for(token)
                taken = conn.execute(
                    "SELECT 1 FROM forum_posts WHERE scan_ref = ?", (scan_ref,)
                ).fetchone()
                if taken is not None:
                    raise ForumError("scan_already_posted", 409)
                ai = (str(claim[1]), claim[2], claim[3])
            cur = conn.execute(
                "INSERT INTO forum_posts (public_id, author_actor_id, author_token, nickname, "
                "kind, space, title, body, species, species_key, condition, urgent, scan_ref, "
                "ai_disease, ai_confidence, ai_species, parent_post_id, plant_group, "
                "training_consent, locale, created_at, last_activity_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    public_id,
                    actor,
                    _author_token(actor),
                    clean_text(nickname, limit=48, multiline=False) or None,
                    kind_value,
                    space_value,
                    title_value,
                    body_value,
                    species_value or (ai[2] if ai else None),
                    (species_value or (ai[2] if ai else "") or "").lower() or None,
                    condition_value,
                    1 if urgent else 0,
                    scan_ref,
                    ai[0] if ai else None,
                    float(ai[1]) if ai and ai[1] is not None else None,
                    ai[2] if ai else None,
                    parent_id,
                    plant_group,
                    1 if training_consent else 0,
                    _clean_locale(locale),
                    timestamp,
                    timestamp,
                ),
            )
            post_id = int(cur.lastrowid or 0)
            attached = _attach_media(
                conn, actor=actor, media_ids=list(media_ids or []),
                limit=POST_MEDIA_MAX, post_id=post_id,
            )
            if scan_ref is not None:
                # The analysed photo must be one of the attached photos, so the
                # verdict shown on the post is about the photo people see.
                if not any(row[4] == scan_ref for row in attached):
                    raise ForumError("scan_photo_required")
                _insert_ai_answer(conn, post_id, ai[0], timestamp)
            if kind_value in {"question", "recovery"} and not attached and not body_value:
                raise ForumError("body_or_photo_required")
            _reindex(conn, post_id)
            conn.execute("COMMIT")
        except Exception:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise
    _emit("post_created", {"post_id": public_id, "actor_id": actor, "kind": kind_value})
    return get_post(public_id, viewer_actor_id=actor)


def _insert_ai_answer(conn: sqlite3.Connection, post_id: int, disease: str, timestamp: int) -> None:
    conn.execute(
        "INSERT INTO forum_answers (public_id, post_id, depth, is_ai, body, diagnosis, created_at) "
        "VALUES (?, ?, 0, 1, '', ?, ?)",
        (_new_public_id(_ANSWER_PREFIX), post_id, disease, timestamp),
    )


def get_post(public_id: str, *, viewer_actor_id: str | None = None) -> dict:
    """A post with its threaded answers, shaped for the viewer."""
    init_db()
    viewer = community._normalise_actor_id(viewer_actor_id)
    with community._connect() as conn:
        row = _require_post(conn, public_id, viewer)
        post = _post_card(conn, row, viewer, full=True)
        post["answers"] = _answers_for(conn, int(row[0]), row, viewer)
    return post


def _answers_for(conn: sqlite3.Connection, post_id: int, post_row: tuple, viewer: str | None) -> list[dict]:
    post_author = post_row[2]
    accepted_id = post_row[21]
    rows = conn.execute(
        "SELECT id, public_id, parent_id, depth, author_actor_id, author_token, nickname, is_ai, "
        "body, steps_json, diagnosis, state, like_count, not_helpful_count, created_at, edited_at, "
        "verified_at FROM forum_answers WHERE post_id = ? AND state IN ('visible', 'removed', 'deleted') "
        "ORDER BY id",
        (post_id,),
    ).fetchall()
    deleted_actors = {
        str(r[0]) for r in conn.execute("SELECT actor_id FROM community_deleted_actors").fetchall()
    }
    hidden_ids: set[int] = set()
    for r in rows:
        author = r[4]
        if author and (author in deleted_actors or _blocked(conn, viewer, author)):
            hidden_ids.add(int(r[0]))
    by_id = {int(r[0]): r for r in rows}
    children: dict[int | None, list[int]] = {}
    for r in rows:
        parent = r[2]
        if parent is not None and int(parent) not in by_id:
            parent = None  # orphaned reply after a purge: show it top level
        children.setdefault(parent if parent is None else int(parent), []).append(int(r[0]))

    def alive(answer_id: int) -> bool:
        r = by_id[answer_id]
        if answer_id in hidden_ids:
            return False
        if r[11] == "visible":
            return True
        return any(alive(child) for child in children.get(answer_id, []))

    reactions: dict[int, str] = {}
    if viewer:
        for answer_id, reaction in conn.execute(
            "SELECT r.answer_id, r.reaction FROM forum_answer_reactions r "
            "JOIN forum_answers a ON a.id = r.answer_id WHERE a.post_id = ? AND r.actor_id = ?",
            (post_id, viewer),
        ).fetchall():
            reactions[int(answer_id)] = str(reaction)
    media = _media_for(conn, answer_ids=[int(r[0]) for r in rows if r[11] == "visible"])
    avatars = _avatar_versions(conn, [r[4] for r in rows if r[11] == "visible" and not r[7]])

    def score(answer_id: int) -> tuple:
        r = by_id[answer_id]
        return (
            0 if answer_id == accepted_id else 1,
            0 if r[7] else 1,
            -(int(r[12]) - int(r[13])),
            int(r[14]),
        )

    ordered: list[dict] = []

    def walk(parent: int | None, depth: int) -> None:
        for answer_id in sorted(children.get(parent, []), key=score):
            if not alive(answer_id):
                continue
            r = by_id[answer_id]
            visible = r[11] == "visible"
            steps: list[str] = []
            if visible and r[9]:
                with contextlib.suppress(ValueError, TypeError):
                    steps = list(json.loads(r[9]))
            ordered.append(
                {
                    "id": r[1],
                    "parent_id": by_id[parent][1] if parent is not None else None,
                    "depth": min(depth, MAX_ANSWER_DEPTH),
                    "is_ai": bool(r[7]),
                    "author": (
                        None
                        if r[7] or not visible
                        else {
                            "nickname": r[6] or "",
                            "author_token": r[5],
                            "is_me": bool(viewer and viewer == r[4]),
                            "is_post_author": r[4] == post_author,
                            "avatar_version": avatars.get(r[4]) if r[4] else None,
                        }
                    ),
                    "body": r[8] if visible else "",
                    "steps": steps,
                    "diagnosis": r[10] if visible else None,
                    "state": "visible" if visible else "removed",
                    "like_count": int(r[12]),
                    "not_helpful_count": int(r[13]),
                    "my_reaction": reactions.get(answer_id),
                    "accepted": answer_id == accepted_id,
                    "verified": bool(visible and r[16] is not None),
                    "media": media.get(answer_id, []) if visible else [],
                    "created_at": int(r[14]),
                    "edited_at": int(r[15]) if r[15] is not None else None,
                }
            )
            walk(answer_id, depth + 1)

    walk(None, 0)
    return ordered


def edit_post(
    *,
    actor_id: str,
    post_id: str,
    title: str | None = None,
    body: str | None = None,
    space: str | None = None,
    species: str | None = None,
    condition: str | None = None,
    urgent: bool | None = None,
    training_consent: bool | None = None,
    now: int | None = None,
) -> dict:
    """Author-only edit of a post's text and tags."""
    init_db()
    actor = _actor(actor_id)
    timestamp = _now(now)
    with community._write_lock, community._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _require_live_actor(conn, actor)
            row = _require_post(conn, post_id, actor)
            if row[2] != actor:
                raise ForumError("not_author", 403)
            _guard(conn, actor, "write", text=f"{title or ''}\n{body or ''}")
            updates: dict[str, object] = {}
            if title is not None:
                value = clean_text(title, limit=TITLE_MAX, multiline=False)
                if not value and row[5] != "scan":
                    raise ForumError("title_required")
                updates["title"] = value
            if body is not None:
                updates["body"] = clean_text(body, limit=POST_BODY_MAX)
            if space is not None:
                updates["space"] = _require_space(conn, space)
            if species is not None:
                value = clean_text(species, limit=SPECIES_MAX, multiline=False) or None
                updates["species"] = value
                updates["species_key"] = value.lower() if value else None
            if condition is not None:
                updates["condition"] = _clean_label(condition)
            if urgent is not None:
                updates["urgent"] = 1 if urgent else 0
            if training_consent is not None:
                updates["training_consent"] = 1 if training_consent else 0
            if updates:
                updates["edited_at"] = timestamp
                assignments = ", ".join(f"{column} = ?" for column in updates)
                conn.execute(
                    f"UPDATE forum_posts SET {assignments} WHERE id = ?",  # nosec B608
                    (*updates.values(), int(row[0])),
                )
                _reindex(conn, int(row[0]))
            conn.execute("COMMIT")
        except Exception:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise
    return get_post(post_id, viewer_actor_id=actor)


def delete_post(*, actor_id: str, post_id: str, now: int | None = None) -> bool:
    """Author deletes their post: text and photos go now, the row stays as a tombstone."""
    init_db()
    actor = _actor(actor_id)
    timestamp = _now(now)
    with community._write_lock, community._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = _load_post(conn, post_id)
            if row is None or row[2] != actor or row[16] == "deleted":
                raise ForumError("post_not_found", 404)
            internal = int(row[0])
            conn.execute(
                "UPDATE forum_posts SET state = 'deleted', title = '', body = '', "
                "training_consent = 0, state_changed_at = ? WHERE id = ?",
                (timestamp, internal),
            )
            filenames = _delete_media_rows(conn, "post_id = ?", (internal,))
            _reindex(conn, internal)
            conn.execute("COMMIT")
        except Exception:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise
    _unlink_media(filenames)
    _emit("post_deleted", {"post_id": post_id, "actor_id": actor})
    return True


def set_post_like(*, actor_id: str, post_id: str, liked: bool, now: int | None = None) -> dict:
    init_db()
    actor = _actor(actor_id)
    timestamp = _now(now)
    with community._write_lock, community._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _require_live_actor(conn, actor)
            _guard(conn, actor, "react")
            row = _require_post(conn, post_id, actor)
            if row[16] != "visible":
                raise ForumError("post_not_found", 404)
            if row[2] == actor:
                raise ForumError("self_like", 409)
            internal = int(row[0])
            if liked:
                created = conn.execute(
                    "INSERT OR IGNORE INTO forum_post_likes (post_id, actor_id, created_at) "
                    "VALUES (?, ?, ?)",
                    (internal, actor, timestamp),
                ).rowcount
            else:
                created = 0
                conn.execute(
                    "DELETE FROM forum_post_likes WHERE post_id = ? AND actor_id = ?",
                    (internal, actor),
                )
            count = _recount_post_likes(conn, internal)
            conn.execute("COMMIT")
        except Exception:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise
    if created:
        _emit("post_liked", {"post_id": post_id, "actor_id": actor, "author_id": row[2]})
    return {"liked": bool(liked), "like_count": count}


def _recount_post_likes(conn: sqlite3.Connection, post_id: int) -> int:
    count = int(
        conn.execute(
            "SELECT COUNT(*) FROM forum_post_likes WHERE post_id = ?", (post_id,)
        ).fetchone()[0]
    )
    conn.execute("UPDATE forum_posts SET like_count = ? WHERE id = ?", (count, post_id))
    return count


def _recount_answers(conn: sqlite3.Connection, post_id: int) -> None:
    conn.execute(
        "UPDATE forum_posts SET answer_count = (SELECT COUNT(*) FROM forum_answers "
        "WHERE post_id = ? AND is_ai = 0 AND state = 'visible') WHERE id = ?",
        (post_id, post_id),
    )


def _recount_reactions(conn: sqlite3.Connection, answer_id: int) -> tuple[int, int]:
    likes, not_helpful = conn.execute(
        "SELECT COALESCE(SUM(reaction = 'like'), 0), COALESCE(SUM(reaction = 'not_helpful'), 0) "
        "FROM forum_answer_reactions WHERE answer_id = ?",
        (answer_id,),
    ).fetchone()
    conn.execute(
        "UPDATE forum_answers SET like_count = ?, not_helpful_count = ? WHERE id = ?",
        (int(likes), int(not_helpful), answer_id),
    )
    return int(likes), int(not_helpful)


# ── answers ─────────────────────────────────────────────────────────────


def create_answer(
    *,
    actor_id: str,
    nickname: str | None,
    post_id: str,
    body: str | None,
    parent_id: str | None = None,
    steps: list[str] | None = None,
    diagnosis: str | None = None,
    media_ids: list[str] | None = None,
    training_consent: bool = False,
    now: int | None = None,
) -> dict:
    """Answer a post (or reply to an answer) and return the new answer."""
    init_db()
    actor = _actor(actor_id)
    timestamp = _now(now)
    body_value = clean_text(body, limit=ANSWER_BODY_MAX)
    steps_value = _clean_steps(steps)
    diagnosis_value = _clean_label(diagnosis)
    public_id = _new_public_id(_ANSWER_PREFIX)
    with community._write_lock, community._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _require_live_actor(conn, actor)
            _guard(conn, actor, "write", text="\n".join([body_value, *steps_value]))
            post = _require_post(conn, post_id, actor)
            if post[16] != "visible":
                raise ForumError("post_not_found", 404)
            if post[17]:
                raise ForumError("post_locked", 423)
            if _blocked(conn, actor, post[2]):
                raise ForumError("blocked_interaction", 409)
            internal_post = int(post[0])
            parent_internal: int | None = None
            depth = 0
            if parent_id:
                parent = _require_answer(conn, parent_id, actor)
                if int(parent[1]) != internal_post:
                    raise ForumError("parent_not_found", 404)
                parent_internal = int(parent[0])
                depth = int(parent[3]) + 1
                if depth > MAX_ANSWER_DEPTH:
                    # Replying to the deepest level makes a sibling, not a 4th level.
                    parent_internal = int(parent[2]) if parent[2] is not None else None
                    depth = MAX_ANSWER_DEPTH
            if not body_value and not steps_value and not media_ids and not diagnosis_value:
                raise ForumError("body_required")
            cur = conn.execute(
                "INSERT INTO forum_answers (public_id, post_id, parent_id, depth, author_actor_id, "
                "author_token, nickname, body, steps_json, diagnosis, training_consent, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    public_id,
                    internal_post,
                    parent_internal,
                    depth,
                    actor,
                    _author_token(actor),
                    clean_text(nickname, limit=48, multiline=False) or None,
                    body_value,
                    json.dumps(steps_value, ensure_ascii=False) if steps_value else None,
                    diagnosis_value,
                    1 if training_consent else 0,
                    timestamp,
                ),
            )
            answer_internal = int(cur.lastrowid or 0)
            _attach_media(
                conn, actor=actor, media_ids=list(media_ids or []),
                limit=ANSWER_MEDIA_MAX, answer_id=answer_internal,
            )
            _recount_answers(conn, internal_post)
            conn.execute(
                "UPDATE forum_posts SET last_activity_at = ? WHERE id = ?",
                (timestamp, internal_post),
            )
            _reindex(conn, internal_post)
            conn.execute("COMMIT")
        except Exception:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise
    _emit(
        "answer_created",
        {
            "post_id": post_id,
            "answer_id": public_id,
            "actor_id": actor,
            "post_author_id": post[2],
        },
    )
    detail = get_post(post_id, viewer_actor_id=actor)
    return next(a for a in detail["answers"] if a["id"] == public_id)


def edit_answer(
    *,
    actor_id: str,
    answer_id: str,
    body: str | None = None,
    steps: list[str] | None = None,
    diagnosis: str | None = None,
    now: int | None = None,
) -> dict:
    init_db()
    actor = _actor(actor_id)
    timestamp = _now(now)
    with community._write_lock, community._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _require_live_actor(conn, actor)
            row = _require_answer(conn, answer_id, actor)
            if row[4] != actor:
                raise ForumError("not_author", 403)
            _guard(conn, actor, "write", text="\n".join([body or "", *(steps or [])]))
            updates: dict[str, object] = {}
            if body is not None:
                updates["body"] = clean_text(body, limit=ANSWER_BODY_MAX)
            if steps is not None:
                cleaned = _clean_steps(steps)
                updates["steps_json"] = json.dumps(cleaned, ensure_ascii=False) if cleaned else None
            if diagnosis is not None:
                updates["diagnosis"] = _clean_label(diagnosis)
            if updates:
                updates["edited_at"] = timestamp
                assignments = ", ".join(f"{column} = ?" for column in updates)
                conn.execute(
                    f"UPDATE forum_answers SET {assignments} WHERE id = ?",  # nosec B608
                    (*updates.values(), int(row[0])),
                )
                _reindex(conn, int(row[1]))
            conn.execute("COMMIT")
        except Exception:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise
    detail = get_post(row[7], viewer_actor_id=actor)
    return next(a for a in detail["answers"] if a["id"] == answer_id)


def delete_answer(*, actor_id: str, answer_id: str, now: int | None = None) -> bool:
    init_db()
    actor = _actor(actor_id)
    timestamp = _now(now)
    with community._write_lock, community._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = _answer_row(conn, answer_id)
            if row is None or row[4] != actor or row[6] == "deleted":
                raise ForumError("answer_not_found", 404)
            internal, post_internal = int(row[0]), int(row[1])
            conn.execute(
                "UPDATE forum_answers SET state = 'deleted', body = '', steps_json = NULL, "
                "training_consent = 0, state_changed_at = ? WHERE id = ?",
                (timestamp, internal),
            )
            conn.execute(
                "UPDATE forum_posts SET accepted_answer_id = NULL "
                "WHERE id = ? AND accepted_answer_id = ?",
                (post_internal, internal),
            )
            filenames = _delete_media_rows(conn, "answer_id = ?", (internal,))
            _recount_answers(conn, post_internal)
            _reindex(conn, post_internal)
            conn.execute("COMMIT")
        except Exception:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise
    _unlink_media(filenames)
    _emit("answer_deleted", {"answer_id": answer_id, "actor_id": actor})
    return True


def set_answer_reaction(
    *,
    actor_id: str,
    answer_id: str,
    reaction: str | None,
    now: int | None = None,
) -> dict:
    """Like, mark not helpful, or clear the viewer's reaction on an answer."""
    init_db()
    actor = _actor(actor_id)
    timestamp = _now(now)
    value = (reaction or "").strip().lower() or None
    if value is not None and value not in ANSWER_REACTIONS:
        raise ForumError("invalid_reaction")
    with community._write_lock, community._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _require_live_actor(conn, actor)
            _guard(conn, actor, "react")
            row = _require_answer(conn, answer_id, actor)
            if row[4] == actor:
                raise ForumError("self_reaction", 409)
            if value == "not_helpful" and can_mark_not_helpful is not None:
                if not can_mark_not_helpful(conn, actor):
                    raise ForumError("level_required", 403)
            internal = int(row[0])
            previous = conn.execute(
                "SELECT reaction FROM forum_answer_reactions WHERE answer_id = ? AND actor_id = ?",
                (internal, actor),
            ).fetchone()
            if value is None:
                conn.execute(
                    "DELETE FROM forum_answer_reactions WHERE answer_id = ? AND actor_id = ?",
                    (internal, actor),
                )
            else:
                conn.execute(
                    "INSERT INTO forum_answer_reactions (answer_id, actor_id, reaction, created_at) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT(answer_id, actor_id) DO UPDATE SET "
                    "reaction = excluded.reaction, created_at = excluded.created_at",
                    (internal, actor, value, timestamp),
                )
            likes, not_helpful = _recount_reactions(conn, internal)
            conn.execute("COMMIT")
        except Exception:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise
    if value == "like" and (previous is None or previous[0] != "like"):
        _emit(
            "answer_liked",
            {"answer_id": answer_id, "actor_id": actor, "author_id": row[4], "post_id": row[7]},
        )
    return {"reaction": value, "like_count": likes, "not_helpful_count": not_helpful}


def accept_answer(
    *,
    actor_id: str,
    post_id: str,
    answer_id: str | None,
    now: int | None = None,
) -> dict:
    """The post's author marks one answer as the one that solved it (or clears it)."""
    init_db()
    actor = _actor(actor_id)
    timestamp = _now(now)
    with community._write_lock, community._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _require_live_actor(conn, actor)
            _guard(conn, actor, "react")
            post = _require_post(conn, post_id, actor)
            if post[2] != actor:
                raise ForumError("not_author", 403)
            if post[16] != "visible":
                raise ForumError("post_not_found", 404)
            accepted_internal: int | None = None
            answer_author: str | None = None
            if answer_id:
                answer = _require_answer(conn, answer_id, actor)
                if int(answer[1]) != int(post[0]):
                    raise ForumError("answer_not_found", 404)
                if answer[4] == actor:
                    raise ForumError("self_accept", 409)
                accepted_internal = int(answer[0])
                answer_author = answer[4]
            conn.execute(
                "UPDATE forum_posts SET accepted_answer_id = ?, accepted_at = ?, "
                "last_activity_at = ? WHERE id = ?",
                (accepted_internal, timestamp if accepted_internal else None, timestamp, int(post[0])),
            )
            conn.execute("COMMIT")
        except Exception:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise
    if answer_id:
        _emit(
            "answer_accepted",
            {
                "post_id": post_id,
                "answer_id": answer_id,
                "actor_id": actor,
                "author_id": answer_author,
            },
        )
    return {"accepted_answer_id": answer_id or None}


# ── feed and search ─────────────────────────────────────────────────────


def feed(
    *,
    viewer_actor_id: str | None = None,
    tab: str = "all",
    sort: str = "hot",
    space: str | None = None,
    limit: int | None = 20,
    cursor: str | None = None,
    now: int | None = None,
) -> dict:
    """One page of post cards for a tab, sort and optional space."""
    init_db()
    viewer = community._normalise_actor_id(viewer_actor_id)
    timestamp = _now(now)
    tab_value = (tab or "all").strip().lower()
    sort_value = (sort or "hot").strip().lower()
    if tab_value not in FEED_TABS:
        raise ForumError("invalid_tab")
    if sort_value not in FEED_SORTS:
        raise ForumError("invalid_sort")
    if tab_value == "mine" and viewer is None:
        raise ForumError("auth_required", 401)
    page = _page_limit(limit)
    offset = _decode_cursor(cursor)
    where = []
    params: list = []
    if tab_value == "mine":
        where.append("p.author_actor_id = ? AND p.state IN ('visible', 'hidden')")
        params.append(viewer)
    else:
        where.append("p.state = 'visible'")
        clause, clause_params = _visible_author_clause("p.author_actor_id", viewer)
        where.append(clause)
        params.extend(clause_params)
    if space:
        slug = space.strip().lower()
        if not _SLUG.fullmatch(slug):
            raise ForumError("invalid_space")
        where.append("p.space = ?")
        params.append(slug)
    order_params: list = []
    if tab_value == "needs_help":
        where.append(
            "p.kind IN ('question', 'scan') AND p.answer_count = 0 "
            "AND p.accepted_answer_id IS NULL AND p.locked = 0 AND p.created_at >= ?"
        )
        params.append(timestamp - NEEDS_HELP_WINDOW_DAYS * 86_400)
        order = "p.urgent DESC, p.created_at ASC, p.id ASC"
    else:
        if tab_value == "solved":
            where.append("p.accepted_answer_id IS NOT NULL")
        if sort_value == "new":
            order = "p.created_at DESC, p.id DESC"
        elif sort_value == "top":
            where.append("p.created_at >= ?")
            params.append(timestamp - TOP_WINDOW_DAYS * 86_400)
            order = "p.like_count DESC, p.answer_count DESC, p.id DESC"
        else:
            # Gravity ranking: likes and answers lift a post, age pulls it down,
            # and an unanswered question gets a head start so it isn't buried.
            order = (
                "(? IS NOT NULL AND p.pinned = 1) DESC, "
                "((p.like_count + 2.0 * p.answer_count + 1.0"
                " + CASE WHEN p.answer_count = 0 AND p.kind IN ('question', 'scan') THEN 3.0 ELSE 0 END"
                " + CASE WHEN p.urgent = 1 THEN 2.0 ELSE 0 END)"
                " / (((? - p.created_at) / 3600.0 + 2.0) * ((? - p.created_at) / 3600.0 + 2.0))) DESC, "
                "p.id DESC"
            )
            order_params = [space, timestamp, timestamp]
    sql = (
        f"SELECT {_POST_COLUMNS} FROM forum_posts p WHERE {' AND '.join(where)} "  # nosec B608
        f"ORDER BY {order} LIMIT ? OFFSET ?"
    )
    with community._connect() as conn:
        rows = conn.execute(sql, (*params, *order_params, page + 1, offset)).fetchall()
        items = [_post_card(conn, row, viewer) for row in rows[:page]]
    next_offset = offset + page
    return {
        "items": items,
        "next_cursor": (
            _encode_cursor(next_offset)
            if len(rows) > page and next_offset <= OFFSET_MAX
            else None
        ),
    }


def search(
    *,
    query: str | None = None,
    viewer_actor_id: str | None = None,
    space: str | None = None,
    solved: bool | None = None,
    with_photos: bool | None = None,
    condition: str | None = None,
    species: str | None = None,
    limit: int | None = 20,
    cursor: str | None = None,
) -> dict:
    """Full-text search over posts and their answers, with filters.

    With no query but a ``condition``/``species`` this is "search with my scan":
    posts about the same plant and problem, solved ones first.
    """
    init_db()
    viewer = community._normalise_actor_id(viewer_actor_id)
    terms = search_terms(query)
    condition_value = _clean_label(condition)
    species_value = clean_text(species, limit=SPECIES_MAX, multiline=False).lower() or None
    if not terms and not condition_value and not species_value:
        raise ForumError("query_required")
    page = _page_limit(limit)
    offset = _decode_cursor(cursor)
    where = ["p.state = 'visible'"]
    params: list = []
    clause, clause_params = _visible_author_clause("p.author_actor_id", viewer)
    where.append(clause)
    params.extend(clause_params)
    if space:
        slug = space.strip().lower()
        if not _SLUG.fullmatch(slug):
            raise ForumError("invalid_space")
        where.append("p.space = ?")
        params.append(slug)
    if solved is True:
        where.append("p.accepted_answer_id IS NOT NULL")
    elif solved is False:
        where.append("p.accepted_answer_id IS NULL")
    if with_photos:
        where.append("EXISTS (SELECT 1 FROM forum_media m WHERE m.post_id = p.id)")
    if condition_value:
        where.append(
            "(p.condition = ? OR p.ai_disease = ? OR EXISTS (SELECT 1 FROM forum_answers a "
            "WHERE a.id = p.accepted_answer_id AND a.diagnosis = ?))"
        )
        params.extend([condition_value] * 3)
    if species_value:
        where.append("(p.species_key = ? OR LOWER(COALESCE(p.ai_species, '')) = ?)")
        params.extend([species_value, species_value])
    fts_terms = [_match_stem(t) for t in terms if len(t) >= 3]
    join = ""
    rank = "0"
    if terms:
        if _fts_on() and fts_terms and len(fts_terms) == len(terms):
            join = "JOIN forum_search s ON s.rowid = p.id"
            where.append("forum_search MATCH ?")
            params.append(" OR ".join('"' + t.replace('"', "") + '"' for t in fts_terms))
            rank = "bm25(forum_search, 4.0, 1.0, 1.0)"
        else:
            likes = []
            for term in terms:
                term = _match_stem(term)
                pattern = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                likes.append(
                    "(LOWER(p.title) LIKE ? ESCAPE '\\' OR LOWER(p.body) LIKE ? ESCAPE '\\')"
                )
                params.extend([pattern, pattern])
            where.append("(" + " OR ".join(likes) + ")")
    order = (
        f"({rank}) - CASE WHEN p.accepted_answer_id IS NOT NULL THEN 1.0 ELSE 0 END ASC, "
        "p.like_count DESC, p.created_at DESC, p.id DESC"
    )
    sql = (
        f"SELECT {_POST_COLUMNS} FROM forum_posts p {join} "  # nosec B608
        f"WHERE {' AND '.join(where)} ORDER BY {order} LIMIT ? OFFSET ?"
    )
    with community._connect() as conn:
        rows = conn.execute(sql, (*params, page + 1, offset)).fetchall()
        items = [_post_card(conn, row, viewer) for row in rows[:page]]
    next_offset = offset + page
    return {
        "items": items,
        "terms": terms,
        "next_cursor": (
            _encode_cursor(next_offset)
            if len(rows) > page and next_offset <= OFFSET_MAX
            else None
        ),
    }


def activity_for(actor_id: str, *, since: int = 0, limit: int = 50) -> dict:
    """New answers on the actor's posts and replies to the actor's answers.

    Drives the Community tab's unread count; it carries no other actor data.
    """
    init_db()
    actor = _actor(actor_id)
    bounded = max(1, min(int(limit), 100))
    with community._connect() as conn:
        clause, clause_params = _visible_author_clause("a.author_actor_id", actor)
        rows = conn.execute(
            "SELECT p.public_id, p.title, p.kind, a.public_id, a.nickname, a.created_at, "
            "CASE WHEN p.author_actor_id = ? THEN 'answer_on_post' ELSE 'reply_to_answer' END "
            "FROM forum_answers a JOIN forum_posts p ON p.id = a.post_id "
            "LEFT JOIN forum_answers parent ON parent.id = a.parent_id "
            "WHERE a.state = 'visible' AND a.is_ai = 0 AND p.state = 'visible' "
            "AND a.author_actor_id != ? AND a.created_at > ? "
            "AND (p.author_actor_id = ? OR parent.author_actor_id = ?) "
            f"AND {clause} ORDER BY a.created_at DESC LIMIT ?",  # nosec B608
            (actor, actor, int(since), actor, actor, *clause_params, bounded),
        ).fetchall()
        accepted = conn.execute(
            "SELECT p.public_id, p.title, a.public_id, p.last_activity_at FROM forum_posts p "
            "JOIN forum_answers a ON a.id = p.accepted_answer_id "
            "WHERE a.author_actor_id = ? AND p.state = 'visible' AND p.last_activity_at > ? "
            "ORDER BY p.last_activity_at DESC LIMIT ?",
            (actor, int(since), bounded),
        ).fetchall()
    items = [
        {
            "type": r[6],
            "post_id": r[0],
            "post_title": r[1],
            "post_kind": r[2],
            "answer_id": r[3],
            "nickname": r[4] or "",
            "created_at": int(r[5]),
        }
        for r in rows
    ]
    items.extend(
        {
            "type": "answer_accepted",
            "post_id": r[0],
            "post_title": r[1],
            "post_kind": None,
            "answer_id": r[2],
            "nickname": "",
            "created_at": int(r[3]),
        }
        for r in accepted
    )
    items.sort(key=lambda item: item["created_at"], reverse=True)
    return {"items": items[:bounded], "unread": len(items[:bounded])}


# ── reports and blocks ─────────────────────────────────────────────────


def report_content(
    *,
    reporter_actor_id: str,
    target_type: str,
    target_id: str,
    reason: str,
    now: int | None = None,
) -> bool:
    """Queue a report on a post or answer. Returns False for a repeat report."""
    init_db()
    reporter = _actor(reporter_actor_id)
    timestamp = _now(now)
    kind = (target_type or "").strip().lower()
    reason_value = (reason or "").strip().lower()
    if reason_value not in REPORT_REASONS:
        raise ForumError("invalid_reason")
    with community._write_lock, community._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _require_live_actor(conn, reporter)
            _guard(conn, reporter, "react")
            if kind == "post":
                post = _require_post(conn, target_id, reporter)
                post_internal, answer_internal, target_actor = int(post[0]), 0, post[2]
            elif kind == "answer":
                answer = _require_answer(conn, target_id, reporter)
                if answer[5]:
                    raise ForumError("target_not_reportable", 409)
                post_internal, answer_internal, target_actor = int(answer[1]), int(answer[0]), answer[4]
            else:
                raise ForumError("invalid_target_type")
            if target_actor == reporter:
                raise ForumError("self_target", 409)
            recent = int(
                conn.execute(
                    "SELECT COUNT(*) FROM forum_reports WHERE reporter_actor_id = ? "
                    "AND created_at >= ?",
                    (reporter, timestamp - 86_400),
                ).fetchone()[0]
            )
            if recent >= REPORTS_PER_DAY:
                raise ForumError("report_limit", 429)
            created = conn.execute(
                "INSERT OR IGNORE INTO forum_reports (reporter_actor_id, target_type, post_id, "
                "answer_id, target_actor_id, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (reporter, kind, post_internal, answer_internal, target_actor, reason_value, timestamp),
            ).rowcount
            conn.execute("COMMIT")
        except Exception:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise
    if created:
        _emit(
            "content_reported",
            {"target_type": kind, "post_internal": post_internal, "answer_internal": answer_internal},
        )
    return bool(created)


def _safety_target_post(conn: sqlite3.Connection, token: str):
    row = _load_post(conn, token)
    if row is None or row[16] not in {"visible", "hidden"}:
        return None
    if community._actor_is_deleted(conn, row[2]):
        return None
    return "", 0, row[2], row[4] or None


def _safety_target_answer(conn: sqlite3.Connection, token: str):
    row = _answer_row(conn, token)
    if row is None or row[6] != "visible" or not row[4]:
        return None
    if community._actor_is_deleted(conn, row[4]):
        return None
    nickname = conn.execute(
        "SELECT nickname FROM forum_answers WHERE id = ?", (int(row[0]),)
    ).fetchone()[0]
    return "", 0, row[4], nickname or None


# ── account deletion, nickname changes, maintenance ────────────────────


def _actor_media_files(conn: sqlite3.Connection, actor: str) -> list[Path]:
    """Every forum photo that disappears when this actor is purged."""
    try:
        rows = conn.execute(
            "SELECT filename FROM forum_media WHERE owner_actor_id = ? "
            "OR post_id IN (SELECT id FROM forum_posts WHERE author_actor_id = ?) "
            "OR answer_id IN (SELECT a.id FROM forum_answers a JOIN forum_posts p "
            "ON p.id = a.post_id WHERE p.author_actor_id = ?)",
            (actor, actor, actor),
        ).fetchall()
    except sqlite3.OperationalError:
        return []  # forum schema not created in this DB yet
    with contextlib.suppress(sqlite3.OperationalError):
        rows += conn.execute(
            "SELECT filename FROM forum_avatars WHERE actor_id = ?", (actor,)
        ).fetchall()
    folder = media_dir()
    return [folder / str(row[0]) for row in rows]


def _purge_actor(conn: sqlite3.Connection, actor: str) -> None:
    """Physically remove an actor's forum content inside the cleanup transaction."""
    try:
        conn.execute("SELECT 1 FROM forum_posts LIMIT 1")
    except sqlite3.OperationalError:
        return
    post_ids = [
        int(r[0])
        for r in conn.execute(
            "SELECT id FROM forum_posts WHERE author_actor_id = ?", (actor,)
        ).fetchall()
    ]
    affected_posts = {
        int(r[0])
        for r in conn.execute(
            "SELECT DISTINCT post_id FROM forum_answers WHERE author_actor_id = ?", (actor,)
        ).fetchall()
    }
    affected_posts |= {
        int(r[0])
        for r in conn.execute(
            "SELECT post_id FROM forum_post_likes WHERE actor_id = ?", (actor,)
        ).fetchall()
    }
    affected_answers = {
        int(r[0])
        for r in conn.execute(
            "SELECT answer_id FROM forum_answer_reactions WHERE actor_id = ?", (actor,)
        ).fetchall()
    }
    if post_ids:
        placeholders = ",".join("?" * len(post_ids))
        answer_ids = [
            int(r[0])
            for r in conn.execute(
                f"SELECT id FROM forum_answers WHERE post_id IN ({placeholders})",  # nosec B608
                tuple(post_ids),
            ).fetchall()
        ]
        if answer_ids:
            answer_placeholders = ",".join("?" * len(answer_ids))
            conn.execute(
                f"DELETE FROM forum_answer_reactions WHERE answer_id IN ({answer_placeholders})",  # nosec B608
                tuple(answer_ids),
            )
            conn.execute(
                f"DELETE FROM forum_media WHERE answer_id IN ({answer_placeholders})",  # nosec B608
                tuple(answer_ids),
            )
        for table, column in (
            ("forum_answers", "post_id"),
            ("forum_post_likes", "post_id"),
            ("forum_media", "post_id"),
            ("forum_reports", "post_id"),
        ):
            conn.execute(
                f"DELETE FROM {table} WHERE {column} IN ({placeholders})",  # nosec B608
                tuple(post_ids),
            )
        # Recovery updates by others never exist (parents must be own posts),
        # but unlink defensively so no row points at a purged parent.
        conn.execute(
            f"UPDATE forum_posts SET parent_post_id = NULL WHERE parent_post_id IN ({placeholders})",  # nosec B608
            tuple(post_ids),
        )
        conn.execute(
            f"DELETE FROM forum_posts WHERE id IN ({placeholders})",  # nosec B608
            tuple(post_ids),
        )
        if _fts_on():
            conn.execute(
                f"DELETE FROM forum_search WHERE rowid IN ({placeholders})",  # nosec B608
                tuple(post_ids),
            )
    own_answers = [
        int(r[0])
        for r in conn.execute(
            "SELECT id FROM forum_answers WHERE author_actor_id = ?", (actor,)
        ).fetchall()
    ]
    if own_answers:
        placeholders = ",".join("?" * len(own_answers))
        conn.execute(
            f"DELETE FROM forum_answer_reactions WHERE answer_id IN ({placeholders})",  # nosec B608
            tuple(own_answers),
        )
        conn.execute(
            f"UPDATE forum_posts SET accepted_answer_id = NULL "  # nosec B608
            f"WHERE accepted_answer_id IN ({placeholders})",
            tuple(own_answers),
        )
        conn.execute(
            f"DELETE FROM forum_reports WHERE answer_id IN ({placeholders})",  # nosec B608
            tuple(own_answers),
        )
    conn.execute("DELETE FROM forum_answers WHERE author_actor_id = ?", (actor,))
    conn.execute("DELETE FROM forum_media WHERE owner_actor_id = ?", (actor,))
    conn.execute("DELETE FROM forum_avatars WHERE actor_id = ?", (actor,))
    conn.execute("DELETE FROM forum_post_likes WHERE actor_id = ?", (actor,))
    conn.execute("DELETE FROM forum_answer_reactions WHERE actor_id = ?", (actor,))
    conn.execute(
        "DELETE FROM forum_reports WHERE reporter_actor_id = ? OR target_actor_id = ?",
        (actor, actor),
    )
    for post_id in affected_posts - set(post_ids):
        _recount_post_likes(conn, post_id)
        _recount_answers(conn, post_id)
        _reindex(conn, post_id)
    for answer_id in affected_answers - set(own_answers):
        _recount_reactions(conn, answer_id)


def _rename_actor(conn: sqlite3.Connection, actor: str, nickname: str) -> int:
    try:
        posts = conn.execute(
            "UPDATE forum_posts SET nickname = ? WHERE author_actor_id = ?", (nickname, actor)
        ).rowcount
        answers = conn.execute(
            "UPDATE forum_answers SET nickname = ? WHERE author_actor_id = ?", (nickname, actor)
        ).rowcount
    except sqlite3.OperationalError:
        return 0
    return int(posts or 0) + int(answers or 0)


def prune(*, now: int | None = None) -> dict[str, int]:
    """Drop abandoned uploads and purge content removed or deleted long ago."""
    init_db()
    timestamp = _now(now)
    purge_before = timestamp - REMOVED_CONTENT_PURGE_DAYS * 86_400
    with community._write_lock, community._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            orphan_files = _delete_media_rows(
                conn,
                "post_id IS NULL AND answer_id IS NULL AND created_at < ?",
                (timestamp - UNATTACHED_MEDIA_TTL_S,),
            )
            stale_posts = [
                int(r[0])
                for r in conn.execute(
                    "SELECT id FROM forum_posts WHERE state IN ('removed', 'deleted') "
                    "AND COALESCE(state_changed_at, created_at) < ?",
                    (purge_before,),
                ).fetchall()
            ]
            post_files: list[str] = []
            for post_id in stale_posts:
                post_files += _delete_media_rows(conn, "post_id = ?", (post_id,))
                post_files += _delete_media_rows(
                    conn,
                    "answer_id IN (SELECT id FROM forum_answers WHERE post_id = ?)",
                    (post_id,),
                )
                conn.execute(
                    "DELETE FROM forum_answer_reactions WHERE answer_id IN "
                    "(SELECT id FROM forum_answers WHERE post_id = ?)",
                    (post_id,),
                )
                conn.execute("DELETE FROM forum_answers WHERE post_id = ?", (post_id,))
                conn.execute("DELETE FROM forum_post_likes WHERE post_id = ?", (post_id,))
                conn.execute("DELETE FROM forum_reports WHERE post_id = ?", (post_id,))
                conn.execute(
                    "UPDATE forum_posts SET parent_post_id = NULL WHERE parent_post_id = ?",
                    (post_id,),
                )
                conn.execute("DELETE FROM forum_posts WHERE id = ?", (post_id,))
            stale_answers = [
                int(r[0])
                for r in conn.execute(
                    "SELECT id FROM forum_answers WHERE state IN ('removed', 'deleted') "
                    "AND COALESCE(state_changed_at, created_at) < ? "
                    "AND NOT EXISTS (SELECT 1 FROM forum_answers child "
                    "WHERE child.parent_id = forum_answers.id)",
                    (purge_before,),
                ).fetchall()
            ]
            for answer_id in stale_answers:
                post_files += _delete_media_rows(conn, "answer_id = ?", (answer_id,))
                conn.execute(
                    "DELETE FROM forum_answer_reactions WHERE answer_id = ?", (answer_id,)
                )
                conn.execute("DELETE FROM forum_reports WHERE answer_id = ?", (answer_id,))
                conn.execute("DELETE FROM forum_answers WHERE id = ?", (answer_id,))
            conn.execute("COMMIT")
        except Exception:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise
    _unlink_media(orphan_files + post_files)
    return {
        "orphan_media": len(orphan_files),
        "posts": len(stale_posts),
        "answers": len(stale_answers),
    }


def sync_legacy(*, limit: int = 200, now: int | None = None) -> dict[str, int]:
    """Carry second-opinion scans and opinions from older app versions into the forum.

    Only account-era scans that asked for, or received, an opinion become scan
    posts; plain shared scans stay on the Nearby map. Idempotent: legacy ids
    are unique on the forum rows.
    """
    init_db()
    timestamp = _now(now)
    bounded = max(1, min(int(limit), 1_000))
    window = timestamp - LEGACY_SYNC_WINDOW_DAYS * 86_400
    posts_created = 0
    answers_created = 0
    with community._connect() as conn:
        scans = conn.execute(
            "SELECT s.scan_id, s.account_actor_id, s.nickname, s.disease, s.confidence, "
            "s.species, s.thumb_path, s.locale, s.created_at FROM community_scans s "
            "WHERE s.account_actor_id IS NOT NULL "
            "AND s.account_actor_id NOT IN (SELECT actor_id FROM community_deleted_actors) "
            "AND s.created_at >= ? "
            "AND (s.wants_opinion = 1 OR EXISTS (SELECT 1 FROM community_opinions o "
            "WHERE o.scan_id = s.scan_id AND o.account_actor_id IS NOT NULL)) "
            "AND NOT EXISTS (SELECT 1 FROM forum_posts f WHERE f.legacy_scan_id = s.scan_id) "
            "ORDER BY s.created_at LIMIT ?",
            (window, bounded),
        ).fetchall()
    for scan_id, actor, nickname, disease, confidence, species, thumb, locale, created_at in scans:
        copied: tuple[str, str, int, int] | None = None
        if thumb:
            source = Path(community.COMMUNITY_THUMB_DIR) / str(thumb)
            if source.is_file():
                media_public = _new_public_id(_MEDIA_PREFIX)
                filename = f"{media_public}.jpg"
                try:
                    media_dir().mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, media_dir() / filename)
                    with Image.open(media_dir() / filename) as opened:
                        width, height = opened.size
                    copied = (media_public, filename, int(width), int(height))
                except (OSError, UnidentifiedImageError):
                    (media_dir() / filename).unlink(missing_ok=True)
                    copied = None
        try:
            with community._write_lock, community._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                if conn.execute(
                    "SELECT 1 FROM forum_posts WHERE legacy_scan_id = ?", (scan_id,)
                ).fetchone() or community._actor_is_deleted(conn, actor):
                    conn.execute("ROLLBACK")
                    if copied:
                        _unlink_media([copied[1]])
                    continue
                cur = conn.execute(
                    "INSERT INTO forum_posts (public_id, author_actor_id, author_token, nickname, "
                    "kind, space, title, body, species, species_key, ai_disease, ai_confidence, "
                    "ai_species, plant_group, legacy_scan_id, locale, created_at, last_activity_at) "
                    "VALUES (?, ?, ?, ?, 'scan', 'general', '', '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        _new_public_id(_POST_PREFIX),
                        actor,
                        _author_token(actor),
                        nickname,
                        species,
                        species.lower() if species else None,
                        disease,
                        confidence,
                        species,
                        secrets.token_hex(16),
                        scan_id,
                        _clean_locale(locale),
                        int(created_at),
                        int(created_at),
                    ),
                )
                post_internal = int(cur.lastrowid or 0)
                if copied:
                    conn.execute(
                        "INSERT INTO forum_media (public_id, owner_actor_id, post_id, position, "
                        "filename, width, height, created_at) VALUES (?, ?, ?, 0, ?, ?, ?, ?)",
                        (copied[0], actor, post_internal, copied[1], copied[2], copied[3], int(created_at)),
                    )
                _insert_ai_answer(conn, post_internal, str(disease), int(created_at))
                _reindex(conn, post_internal)
                conn.execute("COMMIT")
                posts_created += 1
        except Exception:  # noqa: BLE001 — one bad legacy row must not stop the pass
            if copied:
                _unlink_media([copied[1]])
            logger.warning("Legacy scan sync failed for one row")
    with community._write_lock, community._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            opinions = conn.execute(
                "SELECT o.id, o.account_actor_id, o.nickname, o.suggested_disease, o.note, "
                "o.created_at, f.id FROM community_opinions o "
                "JOIN forum_posts f ON f.legacy_scan_id = o.scan_id "
                "WHERE o.account_actor_id IS NOT NULL "
                "AND o.account_actor_id NOT IN (SELECT actor_id FROM community_deleted_actors) "
                "AND NOT EXISTS (SELECT 1 FROM forum_answers a WHERE a.legacy_opinion_id = o.id) "
                "ORDER BY o.id LIMIT ?",
                (bounded,),
            ).fetchall()
            touched: set[int] = set()
            for opinion_id, actor, nickname, disease, note, created_at, post_internal in opinions:
                conn.execute(
                    "INSERT INTO forum_answers (public_id, post_id, depth, author_actor_id, "
                    "author_token, nickname, body, diagnosis, legacy_opinion_id, created_at) "
                    "VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        _new_public_id(_ANSWER_PREFIX),
                        int(post_internal),
                        actor,
                        _author_token(actor),
                        nickname,
                        clean_text(note, limit=ANSWER_BODY_MAX),
                        disease,
                        int(opinion_id),
                        int(created_at),
                    ),
                )
                touched.add(int(post_internal))
                answers_created += 1
            legacy_likes = conn.execute(
                "SELECT f.id, l.account_actor_id, l.created_at FROM community_account_likes l "
                "JOIN forum_posts f ON f.legacy_scan_id = l.scan_id "
                "WHERE l.liked = 1 AND l.account_actor_id != f.author_actor_id "
                "AND l.account_actor_id NOT IN (SELECT actor_id FROM community_deleted_actors) "
                "AND NOT EXISTS (SELECT 1 FROM forum_post_likes fl "
                "WHERE fl.post_id = f.id AND fl.actor_id = l.account_actor_id) LIMIT ?",
                (bounded,),
            ).fetchall()
            for post_internal, actor, created_at in legacy_likes:
                conn.execute(
                    "INSERT OR IGNORE INTO forum_post_likes (post_id, actor_id, created_at) "
                    "VALUES (?, ?, ?)",
                    (int(post_internal), actor, int(created_at)),
                )
                touched.add(int(post_internal))
            for post_internal in touched:
                _recount_answers(conn, post_internal)
                _recount_post_likes(conn, post_internal)
                last = conn.execute(
                    "SELECT MAX(created_at) FROM forum_answers WHERE post_id = ?", (post_internal,)
                ).fetchone()[0]
                if last is not None:
                    conn.execute(
                        "UPDATE forum_posts SET last_activity_at = MAX(last_activity_at, ?) "
                        "WHERE id = ?",
                        (int(last), post_internal),
                    )
                _reindex(conn, post_internal)
            conn.execute("COMMIT")
        except Exception:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise
    return {"posts": posts_created, "answers": answers_created}


def maintenance(*, now: int | None = None) -> dict[str, int]:
    """One background pass: legacy sync, then pruning."""
    synced = sync_legacy(now=now)
    pruned = prune(now=now)
    return {**{f"synced_{k}": v for k, v in synced.items()}, **pruned}


def stats() -> dict[str, int]:
    init_db()
    with community._connect() as conn:
        posts, answers, solved = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(answer_count), 0), "
            "COALESCE(SUM(accepted_answer_id IS NOT NULL), 0) FROM forum_posts "
            "WHERE state = 'visible'"
        ).fetchone()
    return {"posts": int(posts), "answers": int(answers), "solved": int(solved)}


community.register_actor_hooks(
    media_files=_actor_media_files,
    purge=_purge_actor,
    rename=_rename_actor,
    safety_targets={"post": _safety_target_post, "answer": _safety_target_answer},
)
