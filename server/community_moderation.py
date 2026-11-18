"""Forum moderation: audit log, removals, sanctions, appeals and auto-hiding.

Roles are checked by the HTTP layer against the account service before any
function here runs; this module trusts its ``moderator_actor_id`` argument
and records it in an append-only audit log (``forum_mod_actions``).

* Removal is reversible: content becomes "removed by a moderator" and can be
  restored until the forum's purge job deletes it (30 days).
* Content reported by ``AUTO_HIDE_REPORTS`` distinct trusted accounts is hidden
  until a moderator reviews it.
* Sanctions escalate warn -> mute (24 h / 7 days) -> ban. A muted actor can
  still like and report; a banned actor cannot write at all. Diagnosis and
  reading are never affected.
* Each action against a user can be appealed once, and a different
  moderator decides the appeal.
"""

from __future__ import annotations

import contextlib
import re
import secrets
import sqlite3
import time

try:
    from . import community, community_forum  # type: ignore[import-not-found]
    from .config import get_str  # type: ignore[import-not-found]
except ImportError:
    import community
    import community_forum
    from config import get_str

ForumError = community_forum.ForumError

REASON_CODES = frozenset(
    {
        "spam",
        "harassment",
        "dangerous_advice",
        "off_topic",
        "inappropriate_image",
        "privacy",
        "duplicate",
        "other_rules",
    }
)
POST_ACTIONS = frozenset({"remove", "restore", "lock", "unlock", "pin", "unpin", "move"})
ANSWER_ACTIONS = frozenset({"remove", "restore"})
SANCTION_KINDS = frozenset({"warn", "mute", "ban", "unban", "unmute"})
MUTE_HOURS = (24, 168)
AUTO_HIDE_REPORTS = 3
APPEAL_TEXT_MAX = 500
APPEAL_WINDOW_DAYS = 30
NEW_ACCOUNT_LINK_DAYS = 7
BANNED_IMAGE_DISTANCE = 6
_ACTION_PREFIX = "ma"
_APPEAL_PREFIX = "ap"
_LINK = re.compile(
    r"(https?://|www\.|\b[\w-]+\.(ru|com|net|org|io|shop|store|site|online|xyz|рф)\b)",
    re.IGNORECASE,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS forum_mod_actions (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id          TEXT    NOT NULL UNIQUE,
    moderator_actor_id TEXT,
    action             TEXT    NOT NULL,
    target_type        TEXT    NOT NULL,
    post_id            INTEGER,
    answer_id          INTEGER,
    target_actor_id    TEXT,
    reason_code        TEXT,
    note               TEXT,
    prev_state         TEXT,
    created_at         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_forum_mod_actions_created ON forum_mod_actions(created_at);
CREATE INDEX IF NOT EXISTS ix_forum_mod_actions_target  ON forum_mod_actions(target_actor_id, created_at);

CREATE TABLE IF NOT EXISTS forum_sanctions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id    TEXT    NOT NULL,
    kind        TEXT    NOT NULL CHECK(kind IN ('warn', 'mute', 'ban')),
    until       INTEGER,
    reason_code TEXT    NOT NULL,
    action_id   INTEGER NOT NULL,
    created_at  INTEGER NOT NULL,
    revoked_at  INTEGER
);
CREATE INDEX IF NOT EXISTS ix_forum_sanctions_actor ON forum_sanctions(actor_id, revoked_at);

CREATE TABLE IF NOT EXISTS forum_appeals (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id          TEXT    NOT NULL UNIQUE,
    action_id          INTEGER NOT NULL UNIQUE,
    actor_id           TEXT    NOT NULL,
    text               TEXT    NOT NULL DEFAULT '',
    state              TEXT    NOT NULL DEFAULT 'pending'
                       CHECK(state IN ('pending', 'upheld', 'overturned')),
    reviewer_actor_id  TEXT,
    created_at         INTEGER NOT NULL,
    resolved_at        INTEGER
);
CREATE INDEX IF NOT EXISTS ix_forum_appeals_state ON forum_appeals(state, created_at);

CREATE TABLE IF NOT EXISTS forum_banned_images (
    dhash      TEXT    PRIMARY KEY,
    action_id  INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);
"""

_schema_ready_for: str | None = None

# Who counts toward auto-hide. Defaults to every live account; the
# achievements module narrows it to level 2 and above.
reporter_counts = None  # Callable[[sqlite3.Connection, str], bool] | None


def init_db() -> None:
    global _schema_ready_for
    community_forum.init_db()
    key = str(community.COMMUNITY_DB_PATH)
    if _schema_ready_for == key:
        return
    with community._write_lock, community._connect() as conn:
        conn.executescript(_SCHEMA)
    _schema_ready_for = key


def _now(now: int | None) -> int:
    return int(time.time() if now is None else now)


def _reason(code: str | None) -> str:
    value = (code or "").strip().lower()
    if value not in REASON_CODES:
        raise ForumError("invalid_reason")
    return value


def _note(note: str | None) -> str | None:
    return community_forum.clean_text(note, limit=500) or None


def _log(
    conn: sqlite3.Connection,
    *,
    moderator: str | None,
    action: str,
    target_type: str,
    post_id: int | None = None,
    answer_id: int | None = None,
    target_actor: str | None = None,
    reason_code: str | None = None,
    note: str | None = None,
    prev_state: str | None = None,
    now: int,
) -> tuple[int, str]:
    public_id = _ACTION_PREFIX + secrets.token_hex(15)
    cur = conn.execute(
        "INSERT INTO forum_mod_actions (public_id, moderator_actor_id, action, target_type, "
        "post_id, answer_id, target_actor_id, reason_code, note, prev_state, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            public_id, moderator, action, target_type, post_id, answer_id, target_actor,
            reason_code, note, prev_state, now,
        ),
    )
    return int(cur.lastrowid or 0), public_id


def _tx():
    """``BEGIN IMMEDIATE`` transaction under the community write lock."""

    @contextlib.contextmanager
    def scope():
        with community._write_lock, community._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except Exception:
                with contextlib.suppress(sqlite3.Error):
                    conn.execute("ROLLBACK")
                raise

    return scope()


# ── targets ─────────────────────────────────────────────────────────────


def _post_row(conn: sqlite3.Connection, public_id: str) -> tuple:
    token = community_forum._valid_public_id(public_id, "fp")
    row = None
    if token:
        row = conn.execute(
            "SELECT id, author_actor_id, state, locked, pinned, space FROM forum_posts "
            "WHERE public_id = ?",
            (token,),
        ).fetchone()
    if row is None or row[2] == "deleted":
        raise ForumError("post_not_found", 404)
    return row


def _answer_row(conn: sqlite3.Connection, public_id: str) -> tuple:
    token = community_forum._valid_public_id(public_id, "fa")
    row = None
    if token:
        row = conn.execute(
            "SELECT a.id, a.author_actor_id, a.state, a.post_id, p.space, a.is_ai "
            "FROM forum_answers a JOIN forum_posts p ON p.id = a.post_id WHERE a.public_id = ?",
            (token,),
        ).fetchone()
    if row is None or row[2] == "deleted":
        raise ForumError("answer_not_found", 404)
    return row


def space_of(target_type: str, public_id: str) -> str:
    """The space a post or answer lives in, for scoped moderator checks."""
    init_db()
    with community._connect() as conn:
        if target_type == "post":
            return str(_post_row(conn, public_id)[5])
        if target_type == "answer":
            return str(_answer_row(conn, public_id)[4])
    raise ForumError("invalid_target_type")


def author_token_for(actor_id: str) -> str | None:
    return community.public_account_author_token(actor_id)


def actor_for_token(author_token: str) -> str:
    """Resolve a public author token to the internal actor id (server-side only)."""
    init_db()
    token = (author_token or "").strip()
    if not (token.startswith("ca") and len(token) == 32):
        raise ForumError("actor_not_found", 404)
    with community._connect() as conn:
        row = conn.execute(
            "SELECT author_actor_id FROM forum_posts WHERE author_token = ? "
            "UNION SELECT author_actor_id FROM forum_answers WHERE author_token = ? LIMIT 1",
            (token, token),
        ).fetchone()
    if row is not None and row[0]:
        return str(row[0])
    actor = community.resolve_public_account_author_token(token)
    if actor is None:
        raise ForumError("actor_not_found", 404)
    return actor


# ── content actions ─────────────────────────────────────────────────────


def moderate_post(
    *,
    moderator_actor_id: str,
    post_id: str,
    action: str,
    reason_code: str | None = None,
    note: str | None = None,
    space: str | None = None,
    now: int | None = None,
) -> dict:
    init_db()
    timestamp = _now(now)
    if action not in POST_ACTIONS:
        raise ForumError("invalid_action")
    reason = _reason(reason_code) if action in {"remove", "move"} else (
        _reason(reason_code) if reason_code else None
    )
    with _tx() as conn:
        internal, author, state, locked, pinned, current_space = _post_row(conn, post_id)
        prev = state
        if action == "remove":
            if state == "removed":
                raise ForumError("already_removed", 409)
            conn.execute(
                "UPDATE forum_posts SET state = 'removed', state_changed_at = ? WHERE id = ?",
                (timestamp, internal),
            )
            conn.execute(
                "UPDATE forum_reports SET state = 'actioned', resolved_at = ? "
                "WHERE post_id = ? AND answer_id = 0 AND state = 'pending'",
                (timestamp, internal),
            )
        elif action == "restore":
            if state not in {"removed", "hidden"}:
                raise ForumError("not_removed", 409)
            conn.execute(
                "UPDATE forum_posts SET state = 'visible', state_changed_at = ? WHERE id = ?",
                (timestamp, internal),
            )
            conn.execute(
                "UPDATE forum_reports SET state = 'dismissed', resolved_at = ? "
                "WHERE post_id = ? AND answer_id = 0 AND state = 'pending'",
                (timestamp, internal),
            )
        elif action in {"lock", "unlock"}:
            prev = "locked" if locked else "unlocked"
            conn.execute(
                "UPDATE forum_posts SET locked = ? WHERE id = ?",
                (1 if action == "lock" else 0, internal),
            )
        elif action in {"pin", "unpin"}:
            prev = "pinned" if pinned else "unpinned"
            conn.execute(
                "UPDATE forum_posts SET pinned = ? WHERE id = ?",
                (1 if action == "pin" else 0, internal),
            )
        else:  # move
            prev = current_space
            new_space = community_forum._require_space(conn, space)
            conn.execute("UPDATE forum_posts SET space = ? WHERE id = ?", (new_space, internal))
        community_forum._reindex(conn, internal)
        action_id, public_id = _log(
            conn,
            moderator=moderator_actor_id,
            action=action,
            target_type="post",
            post_id=internal,
            target_actor=author,
            reason_code=reason,
            note=_note(note),
            prev_state=prev,
            now=timestamp,
        )
    community_forum._emit(
        "post_moderated", {"post_id": post_id, "action": action, "author_id": author}
    )
    return {"action_id": public_id, "action": action}


def moderate_answer(
    *,
    moderator_actor_id: str,
    answer_id: str,
    action: str,
    reason_code: str | None = None,
    note: str | None = None,
    now: int | None = None,
) -> dict:
    init_db()
    timestamp = _now(now)
    if action not in ANSWER_ACTIONS:
        raise ForumError("invalid_action")
    reason = _reason(reason_code) if action == "remove" else None
    with _tx() as conn:
        internal, author, state, post_internal, _space, is_ai = _answer_row(conn, answer_id)
        if is_ai:
            raise ForumError("target_not_moderatable", 409)
        if action == "remove":
            if state == "removed":
                raise ForumError("already_removed", 409)
            conn.execute(
                "UPDATE forum_answers SET state = 'removed', state_changed_at = ? WHERE id = ?",
                (timestamp, internal),
            )
            conn.execute(
                "UPDATE forum_posts SET accepted_answer_id = NULL "
                "WHERE id = ? AND accepted_answer_id = ?",
                (post_internal, internal),
            )
            conn.execute(
                "UPDATE forum_reports SET state = 'actioned', resolved_at = ? "
                "WHERE answer_id = ? AND state = 'pending'",
                (timestamp, internal),
            )
        else:
            if state not in {"removed", "hidden"}:
                raise ForumError("not_removed", 409)
            conn.execute(
                "UPDATE forum_answers SET state = 'visible', state_changed_at = ? WHERE id = ?",
                (timestamp, internal),
            )
            conn.execute(
                "UPDATE forum_reports SET state = 'dismissed', resolved_at = ? "
                "WHERE answer_id = ? AND state = 'pending'",
                (timestamp, internal),
            )
        community_forum._recount_answers(conn, post_internal)
        community_forum._reindex(conn, post_internal)
        _action_id, public_id = _log(
            conn,
            moderator=moderator_actor_id,
            action=action,
            target_type="answer",
            post_id=post_internal,
            answer_id=internal,
            target_actor=author,
            reason_code=reason,
            note=_note(note),
            prev_state=state,
            now=timestamp,
        )
    community_forum._emit(
        "answer_moderated", {"answer_id": answer_id, "action": action, "author_id": author}
    )
    return {"action_id": public_id, "action": action}


def dismiss_reports(
    *, moderator_actor_id: str, target_type: str, target_id: str, now: int | None = None
) -> dict:
    """Keep reported content: close its reports and un-hide it if auto-hidden."""
    init_db()
    timestamp = _now(now)
    with _tx() as conn:
        if target_type == "post":
            internal, author, state, *_rest = _post_row(conn, target_id)
            post_internal, answer_internal = internal, 0
            if state == "hidden":
                conn.execute(
                    "UPDATE forum_posts SET state = 'visible', state_changed_at = ? WHERE id = ?",
                    (timestamp, internal),
                )
        elif target_type == "answer":
            internal, author, state, post_internal, _space, _is_ai = _answer_row(conn, target_id)
            answer_internal = internal
            if state == "hidden":
                conn.execute(
                    "UPDATE forum_answers SET state = 'visible', state_changed_at = ? WHERE id = ?",
                    (timestamp, internal),
                )
                community_forum._recount_answers(conn, post_internal)
        else:
            raise ForumError("invalid_target_type")
        closed = conn.execute(
            "UPDATE forum_reports SET state = 'dismissed', resolved_at = ? "
            "WHERE post_id = ? AND answer_id = ? AND state = 'pending'",
            (timestamp, post_internal, answer_internal),
        ).rowcount
        community_forum._reindex(conn, post_internal)
        _action_id, public_id = _log(
            conn,
            moderator=moderator_actor_id,
            action="dismiss_reports",
            target_type=target_type,
            post_id=post_internal,
            answer_id=answer_internal or None,
            target_actor=author,
            prev_state=state,
            now=timestamp,
        )
    return {"action_id": public_id, "dismissed": int(closed or 0)}


def verify_answer(
    *, expert_actor_id: str, answer_id: str, verified: bool, now: int | None = None
) -> dict:
    """An expert marks a diagnosis as verified (counts as agreement on its own)."""
    init_db()
    timestamp = _now(now)
    with _tx() as conn:
        internal, author, state, post_internal, _space, _is_ai = _answer_row(conn, answer_id)
        if state != "visible":
            raise ForumError("answer_not_found", 404)
        if author == expert_actor_id:
            raise ForumError("self_target", 409)
        conn.execute(
            "UPDATE forum_answers SET verified_by_actor_id = ?, verified_at = ? WHERE id = ?",
            (expert_actor_id if verified else None, timestamp if verified else None, internal),
        )
        _log(
            conn,
            moderator=expert_actor_id,
            action="verify" if verified else "unverify",
            target_type="answer",
            post_id=post_internal,
            answer_id=internal,
            target_actor=author,
            now=timestamp,
        )
    community_forum._emit(
        "answer_verified",
        {"answer_id": answer_id, "verified": verified, "author_id": author},
    )
    return {"verified": verified}


def exclude_from_training(
    *, admin_actor_id: str, post_id: str, now: int | None = None
) -> dict:
    init_db()
    timestamp = _now(now)
    with _tx() as conn:
        internal, author, *_rest = _post_row(conn, post_id)
        conn.execute("UPDATE forum_posts SET training_excluded = 1 WHERE id = ?", (internal,))
        _log(
            conn,
            moderator=admin_actor_id,
            action="exclude_training",
            target_type="post",
            post_id=internal,
            target_actor=author,
            now=timestamp,
        )
    return {"excluded": True}


def ban_image(*, admin_actor_id: str, media_id: str, now: int | None = None) -> dict:
    """Refuse future uploads that look like this photo."""
    init_db()
    timestamp = _now(now)
    token = community_forum._valid_public_id(media_id, "fm")
    with _tx() as conn:
        row = conn.execute(
            "SELECT phash, owner_actor_id FROM forum_media WHERE public_id = ?", (token,)
        ).fetchone() if token else None
        if row is None or not row[0]:
            raise ForumError("media_not_found", 404)
        action_id, public_id = _log(
            conn,
            moderator=admin_actor_id,
            action="ban_image",
            target_type="image",
            target_actor=row[1],
            now=timestamp,
        )
        conn.execute(
            "INSERT OR IGNORE INTO forum_banned_images (dhash, action_id, created_at) "
            "VALUES (?, ?, ?)",
            (row[0], action_id, timestamp),
        )
    return {"action_id": public_id}


def record_role_change(
    *,
    admin_actor_id: str,
    actor_id: str,
    role: str,
    scope: str,
    granted: bool,
    now: int | None = None,
) -> None:
    """Audit an expert/moderator grant or revocation made through the app."""
    init_db()
    with _tx() as conn:
        _log(
            conn,
            moderator=admin_actor_id,
            action="grant_role" if granted else "revoke_role",
            target_type="role",
            target_actor=actor_id,
            note=f"{role}:{scope}",
            now=_now(now),
        )


# ── sanctions ───────────────────────────────────────────────────────────


def active_sanction(conn: sqlite3.Connection, actor: str, now: int | None = None) -> tuple | None:
    """The strongest active mute or ban as ``(kind, until)``; ``until`` None = permanent."""
    timestamp = _now(now)
    try:
        rows = conn.execute(
            "SELECT kind, until FROM forum_sanctions WHERE actor_id = ? AND revoked_at IS NULL "
            "AND kind IN ('mute', 'ban') AND (until IS NULL OR until > ?)",
            (actor, timestamp),
        ).fetchall()
    except sqlite3.OperationalError:
        return None
    bans = [r for r in rows if r[0] == "ban"]
    if bans:
        return "ban", None if any(r[1] is None for r in bans) else max(int(r[1]) for r in bans)
    if rows:
        return "mute", max(int(r[1]) for r in rows if r[1] is not None)
    return None


def sanction(
    *,
    moderator_actor_id: str,
    actor_id: str,
    kind: str,
    reason_code: str | None = None,
    hours: int | None = None,
    note: str | None = None,
    now: int | None = None,
) -> dict:
    init_db()
    timestamp = _now(now)
    if kind not in SANCTION_KINDS:
        raise ForumError("invalid_action")
    if actor_id == moderator_actor_id:
        raise ForumError("self_target", 409)
    with _tx() as conn:
        if kind in {"unban", "unmute"}:
            revoked_kind = "ban" if kind == "unban" else "mute"
            changed = conn.execute(
                "UPDATE forum_sanctions SET revoked_at = ? WHERE actor_id = ? AND kind = ? "
                "AND revoked_at IS NULL",
                (timestamp, actor_id, revoked_kind),
            ).rowcount
            if not changed:
                raise ForumError("no_active_sanction", 409)
            _action_id, public_id = _log(
                conn, moderator=moderator_actor_id, action=kind, target_type="actor",
                target_actor=actor_id, note=_note(note), now=timestamp,
            )
            return {"action_id": public_id, "kind": kind, "until": None}
        reason = _reason(reason_code)
        until: int | None = None
        if kind == "mute":
            if hours not in MUTE_HOURS:
                raise ForumError("invalid_duration")
            until = timestamp + int(hours) * 3_600
        elif kind == "ban" and hours is not None:
            if not 1 <= int(hours) <= 24 * 365:
                raise ForumError("invalid_duration")
            until = timestamp + int(hours) * 3_600
        action_id, public_id = _log(
            conn, moderator=moderator_actor_id, action=kind, target_type="actor",
            target_actor=actor_id, reason_code=reason, note=_note(note), now=timestamp,
        )
        conn.execute(
            "INSERT INTO forum_sanctions (actor_id, kind, until, reason_code, action_id, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (actor_id, kind, until, reason, action_id, timestamp),
        )
    return {"action_id": public_id, "kind": kind, "until": until}


def actor_summary(actor_id: str) -> dict:
    """Moderator view of one member: nickname, content counts, sanction history."""
    init_db()
    with community._connect() as conn:
        nickname = conn.execute(
            "SELECT nickname FROM forum_posts WHERE author_actor_id = ? "
            "UNION ALL SELECT nickname FROM forum_answers WHERE author_actor_id = ? LIMIT 1",
            (actor_id, actor_id),
        ).fetchone()
        posts = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(state = 'removed'), 0) FROM forum_posts "
            "WHERE author_actor_id = ?",
            (actor_id,),
        ).fetchone()
        answers = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(state = 'removed'), 0) FROM forum_answers "
            "WHERE author_actor_id = ?",
            (actor_id,),
        ).fetchone()
        history = conn.execute(
            "SELECT s.kind, s.until, s.reason_code, s.created_at, s.revoked_at "
            "FROM forum_sanctions s WHERE s.actor_id = ? ORDER BY s.created_at DESC LIMIT 50",
            (actor_id,),
        ).fetchall()
        active = active_sanction(conn, actor_id)
    return {
        "nickname": (nickname[0] if nickname else None) or "",
        "author_token": community.public_account_author_token(actor_id),
        "posts": int(posts[0]),
        "posts_removed": int(posts[1]),
        "answers": int(answers[0]),
        "answers_removed": int(answers[1]),
        "active_sanction": (
            {"kind": active[0], "until": active[1]} if active else None
        ),
        "sanctions": [
            {
                "kind": r[0],
                "until": int(r[1]) if r[1] is not None else None,
                "reason_code": r[2],
                "created_at": int(r[3]),
                "revoked": r[4] is not None,
            }
            for r in history
        ],
    }


# ── queue, audit, notices, appeals ─────────────────────────────────────


def _space_filter(column: str, scopes: set[str] | None) -> tuple[str, tuple]:
    if scopes is None:
        return "1 = 1", ()
    if not scopes:
        return "1 = 0", ()
    placeholders = ",".join("?" * len(scopes))
    return f"{column} IN ({placeholders})", tuple(sorted(scopes))


def queue(*, kind: str, scopes: set[str] | None = None, limit: int = 50) -> dict:
    """Work for moderators: ``reports``, ``hidden`` or ``appeals``.

    ``scopes`` limits results to those spaces (``None`` = all spaces).
    """
    init_db()
    bounded = max(1, min(int(limit), 100))
    space_sql, space_params = _space_filter("p.space", scopes)
    with community._connect() as conn:
        if kind == "reports":
            rows = conn.execute(
                "SELECT r.post_id, r.answer_id, r.target_type, COUNT(*), "
                "GROUP_CONCAT(DISTINCT r.reason), MIN(r.created_at), p.public_id, p.title, "
                "p.body, p.state, p.space, a.public_id, a.body, a.state, "
                "COALESCE(a.nickname, p.nickname), COALESCE(a.author_token, p.author_token) "
                "FROM forum_reports r JOIN forum_posts p ON p.id = r.post_id "
                "LEFT JOIN forum_answers a ON a.id = r.answer_id AND r.answer_id != 0 "
                f"WHERE r.state = 'pending' AND p.state != 'deleted' AND {space_sql} "  # nosec B608
                "GROUP BY r.post_id, r.answer_id ORDER BY COUNT(*) DESC, MIN(r.created_at) "
                "LIMIT ?",
                (*space_params, bounded),
            ).fetchall()
            items = [
                {
                    "target_type": r[2],
                    "target_id": r[11] if r[2] == "answer" else r[6],
                    "post_id": r[6],
                    "report_count": int(r[3]),
                    "reasons": sorted((r[4] or "").split(",")),
                    "first_reported_at": int(r[5]),
                    "excerpt": ((r[12] if r[2] == "answer" else f"{r[7]}\n{r[8]}") or "")[:300],
                    "state": r[13] if r[2] == "answer" else r[9],
                    "space": r[10],
                    "author": {"nickname": r[14] or "", "author_token": r[15]},
                }
                for r in rows
            ]
        elif kind == "hidden":
            rows = conn.execute(
                "SELECT 'post', p.public_id, p.public_id, p.title || char(10) || p.body, p.space, "
                "p.nickname, p.author_token, p.state_changed_at FROM forum_posts p "
                f"WHERE p.state = 'hidden' AND {space_sql} "  # nosec B608
                "UNION ALL SELECT 'answer', a.public_id, p.public_id, a.body, p.space, a.nickname, "
                "a.author_token, a.state_changed_at FROM forum_answers a "
                "JOIN forum_posts p ON p.id = a.post_id "
                f"WHERE a.state = 'hidden' AND {space_sql} "  # nosec B608
                "ORDER BY 8 LIMIT ?",
                (*space_params, *space_params, bounded),
            ).fetchall()
            items = [
                {
                    "target_type": r[0],
                    "target_id": r[1],
                    "post_id": r[2],
                    "excerpt": (r[3] or "")[:300],
                    "space": r[4],
                    "author": {"nickname": r[5] or "", "author_token": r[6]},
                    "hidden_at": int(r[7]) if r[7] is not None else None,
                }
                for r in rows
            ]
        elif kind == "appeals":
            rows = conn.execute(
                "SELECT ap.public_id, ap.text, ap.created_at, m.public_id, m.action, "
                "m.target_type, m.reason_code, m.moderator_actor_id, p.public_id, p.space "
                "FROM forum_appeals ap JOIN forum_mod_actions m ON m.id = ap.action_id "
                "LEFT JOIN forum_posts p ON p.id = m.post_id "
                "WHERE ap.state = 'pending' ORDER BY ap.created_at LIMIT ?",
                (bounded,),
            ).fetchall()
            items = [
                {
                    "appeal_id": r[0],
                    "text": r[1],
                    "created_at": int(r[2]),
                    "action_id": r[3],
                    "action": r[4],
                    "target_type": r[5],
                    "reason_code": r[6],
                    "post_id": r[8],
                    "space": r[9],
                }
                for r in rows
                if scopes is None or (r[9] is not None and r[9] in scopes)
            ]
        else:
            raise ForumError("invalid_queue")
    return {"kind": kind, "items": items}


def moderator_post_view(post_id: str) -> dict:
    """A post with every answer, including hidden and removed text, for review."""
    init_db()
    with community._connect() as conn:
        internal, author, state, locked, pinned, space = _post_row(conn, post_id)
        post = conn.execute(
            "SELECT title, body, nickname, author_token, kind, created_at, training_consent, "
            "training_excluded FROM forum_posts WHERE id = ?",
            (internal,),
        ).fetchone()
        answers = conn.execute(
            "SELECT public_id, body, nickname, author_token, state, is_ai, diagnosis, "
            "created_at, verified_at FROM forum_answers WHERE post_id = ? ORDER BY id",
            (internal,),
        ).fetchall()
        media = community_forum._media_for(conn, post_id=internal)
        reports = conn.execute(
            "SELECT answer_id, reason, COUNT(*) FROM forum_reports WHERE post_id = ? "
            "AND state = 'pending' GROUP BY answer_id, reason",
            (internal,),
        ).fetchall()
    return {
        "id": post_id,
        "state": state,
        "locked": bool(locked),
        "pinned": bool(pinned),
        "space": space,
        "title": post[0],
        "body": post[1],
        "author": {"nickname": post[2] or "", "author_token": post[3]},
        "kind": post[4],
        "created_at": int(post[5]),
        "training_consent": bool(post[6]),
        "training_excluded": bool(post[7]),
        "media": media,
        "pending_reports": [
            {"on_answer": bool(r[0]), "reason": r[1], "count": int(r[2])} for r in reports
        ],
        "answers": [
            {
                "id": a[0],
                "body": a[1],
                "author": None if a[5] else {"nickname": a[2] or "", "author_token": a[3]},
                "state": a[4],
                "is_ai": bool(a[5]),
                "diagnosis": a[6],
                "created_at": int(a[7]),
                "verified": a[8] is not None,
            }
            for a in answers
            if a[4] != "deleted"
        ],
    }


def audit(*, limit: int = 100, before: int | None = None) -> dict:
    init_db()
    bounded = max(1, min(int(limit), 200))
    with community._connect() as conn:
        rows = conn.execute(
            "SELECT m.id, m.public_id, m.action, m.target_type, m.reason_code, m.note, "
            "m.prev_state, m.created_at, m.moderator_actor_id, m.target_actor_id, p.public_id "
            "FROM forum_mod_actions m LEFT JOIN forum_posts p ON p.id = m.post_id "
            "WHERE (? IS NULL OR m.id < ?) ORDER BY m.id DESC LIMIT ?",
            (before, before, bounded),
        ).fetchall()
    return {
        "items": [
            {
                "action_id": r[1],
                "action": r[2],
                "target_type": r[3],
                "reason_code": r[4],
                "note": r[5],
                "prev_state": r[6],
                "created_at": int(r[7]),
                "moderator_token": community.public_account_author_token(r[8]) if r[8] else None,
                "automatic": r[8] is None,
                "target_token": community.public_account_author_token(r[9]) if r[9] else None,
                "post_id": r[10],
            }
            for r in rows
        ],
        "next_before": int(rows[-1][0]) if len(rows) == bounded else None,
    }


_NOTICE_ACTIONS = ("remove", "hide_auto", "warn", "mute", "ban", "lock", "move")


def notices_for(actor_id: str, *, limit: int = 50) -> dict:
    """Moderation decisions that affected this member, with appeal status."""
    init_db()
    placeholders = ",".join("?" * len(_NOTICE_ACTIONS))
    with community._connect() as conn:
        rows = conn.execute(
            "SELECT m.public_id, m.action, m.target_type, m.reason_code, m.created_at, "
            "p.public_id, p.title, ap.state, m.moderator_actor_id IS NULL "
            "FROM forum_mod_actions m LEFT JOIN forum_posts p ON p.id = m.post_id "
            "LEFT JOIN forum_appeals ap ON ap.action_id = m.id "
            f"WHERE m.target_actor_id = ? AND m.action IN ({placeholders}) "  # nosec B608
            "ORDER BY m.created_at DESC LIMIT ?",
            (actor_id, *_NOTICE_ACTIONS, max(1, min(int(limit), 100))),
        ).fetchall()
        active = active_sanction(conn, actor_id)
    now = int(time.time())
    return {
        "active_sanction": {"kind": active[0], "until": active[1]} if active else None,
        "items": [
            {
                "action_id": r[0],
                "action": r[1],
                "target_type": r[2],
                "reason_code": r[3],
                "created_at": int(r[4]),
                "post_id": r[5],
                "post_title": r[6],
                "automatic": bool(r[8]),
                "appeal_state": r[7],
                "can_appeal": r[7] is None and now - int(r[4]) <= APPEAL_WINDOW_DAYS * 86_400,
            }
            for r in rows
        ],
    }


def appeal(*, actor_id: str, action_id: str, text: str | None, now: int | None = None) -> dict:
    """One appeal per moderation action, by the member it affected."""
    init_db()
    timestamp = _now(now)
    with _tx() as conn:
        row = conn.execute(
            "SELECT id, target_actor_id, action, created_at FROM forum_mod_actions "
            "WHERE public_id = ?",
            ((action_id or "").strip(),),
        ).fetchone()
        if row is None or row[1] != actor_id or row[2] not in _NOTICE_ACTIONS:
            raise ForumError("action_not_found", 404)
        if timestamp - int(row[3]) > APPEAL_WINDOW_DAYS * 86_400:
            raise ForumError("appeal_window_closed", 409)
        if conn.execute("SELECT 1 FROM forum_appeals WHERE action_id = ?", (row[0],)).fetchone():
            raise ForumError("already_appealed", 409)
        public_id = _APPEAL_PREFIX + secrets.token_hex(15)
        conn.execute(
            "INSERT INTO forum_appeals (public_id, action_id, actor_id, text, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                public_id,
                row[0],
                actor_id,
                community_forum.clean_text(text, limit=APPEAL_TEXT_MAX),
                timestamp,
            ),
        )
    return {"appeal_id": public_id, "state": "pending"}


def resolve_appeal(
    *, reviewer_actor_id: str, appeal_id: str, decision: str, now: int | None = None
) -> dict:
    """Uphold or overturn; the reviewer must not be the moderator who acted."""
    init_db()
    timestamp = _now(now)
    if decision not in {"uphold", "overturn"}:
        raise ForumError("invalid_action")
    with _tx() as conn:
        row = conn.execute(
            "SELECT ap.id, ap.state, m.id, m.action, m.moderator_actor_id, m.post_id, "
            "m.answer_id, m.target_actor_id FROM forum_appeals ap "
            "JOIN forum_mod_actions m ON m.id = ap.action_id WHERE ap.public_id = ?",
            ((appeal_id or "").strip(),),
        ).fetchone()
        if row is None:
            raise ForumError("appeal_not_found", 404)
        appeal_internal, state, action_internal, action, moderator, post_internal, answer_internal, target = row
        if state != "pending":
            raise ForumError("appeal_resolved", 409)
        if moderator is not None and moderator == reviewer_actor_id:
            raise ForumError("same_moderator", 409)
        if reviewer_actor_id == target:
            raise ForumError("self_target", 409)
        if decision == "overturn":
            if action in {"remove", "hide_auto"}:
                if answer_internal:
                    conn.execute(
                        "UPDATE forum_answers SET state = 'visible', state_changed_at = ? "
                        "WHERE id = ? AND state IN ('removed', 'hidden')",
                        (timestamp, answer_internal),
                    )
                    community_forum._recount_answers(conn, int(post_internal))
                elif post_internal:
                    conn.execute(
                        "UPDATE forum_posts SET state = 'visible', state_changed_at = ? "
                        "WHERE id = ? AND state IN ('removed', 'hidden')",
                        (timestamp, post_internal),
                    )
                if post_internal:
                    community_forum._reindex(conn, int(post_internal))
            elif action in {"mute", "ban", "warn"}:
                conn.execute(
                    "UPDATE forum_sanctions SET revoked_at = ? WHERE action_id = ?",
                    (timestamp, action_internal),
                )
            elif action == "lock" and post_internal:
                conn.execute("UPDATE forum_posts SET locked = 0 WHERE id = ?", (post_internal,))
        conn.execute(
            "UPDATE forum_appeals SET state = ?, reviewer_actor_id = ?, resolved_at = ? "
            "WHERE id = ?",
            (
                "overturned" if decision == "overturn" else "upheld",
                reviewer_actor_id,
                timestamp,
                appeal_internal,
            ),
        )
        _log(
            conn,
            moderator=reviewer_actor_id,
            action=f"appeal_{decision}",
            target_type="appeal",
            post_id=post_internal,
            answer_id=answer_internal,
            target_actor=target,
            now=timestamp,
        )
    return {"appeal_id": appeal_id, "state": "overturned" if decision == "overturn" else "upheld"}


# ── guards and automatic moderation (registered into the forum) ────────


def _sanction_guard(conn: sqlite3.Connection, actor: str, operation: str) -> None:
    active = active_sanction(conn, actor)
    if active is None:
        return
    kind, _until = active
    if kind == "ban":
        raise ForumError("banned", 403)
    if operation == "write":
        raise ForumError("muted", 403)


def _blocked_terms() -> list[str]:
    raw = get_str("FORUM_BLOCKED_TERMS", "") or ""
    return [term.strip().lower() for term in raw.split(",") if term.strip()]


def _text_guard(conn: sqlite3.Connection, actor: str, text: str) -> None:
    lowered = text.lower()
    for term in _blocked_terms():
        if term in lowered:
            raise ForumError("blocked_terms", 422)
    if _LINK.search(text):
        first = conn.execute(
            "SELECT MIN(created_at) FROM (SELECT created_at FROM forum_posts "
            "WHERE author_actor_id = ? UNION ALL SELECT created_at FROM forum_answers "
            "WHERE author_actor_id = ?)",
            (actor, actor),
        ).fetchone()[0]
        if first is None or int(time.time()) - int(first) < NEW_ACCOUNT_LINK_DAYS * 86_400:
            raise ForumError("links_not_allowed_yet", 422)


def _hamming(first: str, second: str) -> int:
    try:
        return bin(int(first, 16) ^ int(second, 16)).count("1")
    except ValueError:
        return 64


def _media_guard(conn: sqlite3.Connection, actor: str, dhash: str) -> None:
    try:
        banned = conn.execute("SELECT dhash FROM forum_banned_images").fetchall()
    except sqlite3.OperationalError:
        return
    if any(_hamming(dhash, str(row[0])) <= BANNED_IMAGE_DISTANCE for row in banned):
        raise ForumError("image_not_allowed", 422)


def _on_report(event: str, payload: dict) -> None:
    if event != "content_reported":
        return
    post_internal = int(payload["post_internal"])
    answer_internal = int(payload["answer_internal"])
    init_db()
    timestamp = int(time.time())
    with _tx() as conn:
        reporters = [
            str(r[0])
            for r in conn.execute(
                "SELECT DISTINCT reporter_actor_id FROM forum_reports "
                "WHERE post_id = ? AND answer_id = ? AND state = 'pending'",
                (post_internal, answer_internal),
            ).fetchall()
        ]
        if reporter_counts is not None:
            reporters = [r for r in reporters if reporter_counts(conn, r)]
        if len(reporters) < AUTO_HIDE_REPORTS:
            return
        if answer_internal:
            row = conn.execute(
                "SELECT state, author_actor_id FROM forum_answers WHERE id = ?",
                (answer_internal,),
            ).fetchone()
            if row is None or row[0] != "visible":
                return
            conn.execute(
                "UPDATE forum_answers SET state = 'hidden', state_changed_at = ? WHERE id = ?",
                (timestamp, answer_internal),
            )
            community_forum._recount_answers(conn, post_internal)
        else:
            row = conn.execute(
                "SELECT state, author_actor_id FROM forum_posts WHERE id = ?", (post_internal,)
            ).fetchone()
            if row is None or row[0] != "visible":
                return
            conn.execute(
                "UPDATE forum_posts SET state = 'hidden', state_changed_at = ? WHERE id = ?",
                (timestamp, post_internal),
            )
        community_forum._reindex(conn, post_internal)
        _log(
            conn,
            moderator=None,
            action="hide_auto",
            target_type="answer" if answer_internal else "post",
            post_id=post_internal,
            answer_id=answer_internal or None,
            target_actor=row[1],
            prev_state="visible",
            now=timestamp,
        )


def _purge_actor(conn: sqlite3.Connection, actor: str) -> None:
    try:
        conn.execute("DELETE FROM forum_sanctions WHERE actor_id = ?", (actor,))
        conn.execute("DELETE FROM forum_appeals WHERE actor_id = ?", (actor,))
        # The audit trail keeps the decision but forgets who it concerned.
        conn.execute(
            "UPDATE forum_mod_actions SET target_actor_id = NULL, note = NULL "
            "WHERE target_actor_id = ?",
            (actor,),
        )
        # NULL means "automatic"; a deleted moderator becomes a fixed marker.
        conn.execute(
            "UPDATE forum_mod_actions SET moderator_actor_id = 'deleted' "
            "WHERE moderator_actor_id = ?",
            (actor,),
        )
    except sqlite3.OperationalError:
        return


community_forum.write_guards.append(_sanction_guard)
community_forum.text_guards.append(_text_guard)
community_forum.media_guards.append(_media_guard)
community_forum.event_listeners.append(_on_report)
community.register_actor_hooks(purge=_purge_actor)
