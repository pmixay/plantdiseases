"""Community safety: blocks, content reports and scan removal.

Blocking an author, listing and lifting blocks, the pending report queue and the
moderator actions that delete a reported scan (``remove_scan``,
``moderate_content_report``). Other stores in ``community.db`` plug their own
report targets in through ``community.register_actor_hooks``; the pairwise
``community._actors_blocked`` predicate stays in ``community.py`` because the
forum and the feeds share it."""

from __future__ import annotations

import contextlib
import sqlite3
import time
import uuid

try:
    from . import community  # type: ignore[import-not-found]
except ImportError:
    import community


_PUBLIC_BLOCK_TOKEN_PREFIX = "cb"

COMMUNITY_BLOCK_LIMIT = 500


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
    resolver = community._extra_safety_targets.get(kind)
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
    community.init_db()
    blocker = community._normalise_actor_id(blocker_actor_id)
    if blocker is None:
        raise ValueError("invalid_actor")
    timestamp = int(time.time() if now is None else now)
    with community._write_lock, community._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if community._actor_is_deleted(conn, blocker):
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
    community.init_db()
    actor = community._normalise_actor_id(account_actor_id)
    if actor is None:
        return []
    bounded_limit = max(1, min(int(limit), COMMUNITY_BLOCK_LIMIT))
    with community._connect() as conn:
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
    community.init_db()
    actor = community._normalise_actor_id(account_actor_id)
    public_id = (block_id or "").strip()
    if (
        actor is None
        or not public_id.startswith(_PUBLIC_BLOCK_TOKEN_PREFIX)
        or len(public_id) != 32
    ):
        return False
    with community._write_lock, community._connect() as conn:
        deleted = conn.execute(
            "DELETE FROM community_actor_blocks "
            "WHERE blocker_actor_id = ? AND public_id = ?",
            (actor, public_id),
        ).rowcount
    return int(deleted or 0) > 0


def pending_content_reports(*, limit: int = 100) -> list[dict]:
    """Local-operator moderation queue; never wired to a public route."""
    community.init_db()
    bounded_limit = max(1, min(int(limit), 500))
    with community._connect() as conn:
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
    community.init_db()
    token = (public_id or "").strip()
    if len(token) != 32 or not token.startswith(community._PUBLIC_TOKEN_PREFIX):
        return False
    timestamp = int(time.time() if now is None else now)
    with community._write_lock, community._connect() as conn:
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
            (community.COMMUNITY_THUMB_DIR / thumb_path).unlink(missing_ok=True)
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
    community.init_db()
    resolution = (action or "").strip().lower()
    if resolution not in {"dismiss", "remove_content"}:
        raise ValueError("invalid_action")
    timestamp = int(time.time() if now is None else now)
    thumb_path: str | None = None
    with community._write_lock, community._connect() as conn:
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
            (community.COMMUNITY_THUMB_DIR / thumb_path).unlink(missing_ok=True)
    return True
