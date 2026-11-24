"""Community retention, account deletion and cleanup jobs.

Age-based pruning of scans, reports and deleted-actor markers, the account
deletion request that tombstones an actor's public content, nickname
propagation, and the durable cleanup-job worker that erases the rest (media
files and the hooks other stores in ``community.db`` registered through
``community.register_actor_hooks``).

Connection, write lock, paths and the actor hooks stay in ``community.py`` and
are read as ``community.<name>`` at call time."""

from __future__ import annotations

import time
import uuid
from pathlib import Path

try:
    from . import community
except ImportError:
    import community


COMMUNITY_REPORT_RETENTION_DAYS = 180

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
    community.init_db()
    timestamp = int(time.time() if now is None else now)
    cutoff = timestamp - DELETED_ACTOR_MARKER_RETENTION_DAYS * 86_400
    with community._write_lock, community._connect() as conn:
        return int(conn.execute(_EXPIRE_DELETED_ACTOR_MARKERS_SQL, (cutoff,)).rowcount or 0)


def prune_older_than(days: int) -> int:
    """Delete rows + thumbnails older than ``days`` days.

    Covers every table in this store: the scans themselves, their orphaned
    companion rows (views) and unreferenced
    ``community_authors`` rows. Returns the number of scans deleted."""
    community.init_db()
    if days <= 0:
        return 0
    cutoff = int(time.time()) - days * 86_400
    with community._write_lock, community._connect() as conn:
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
                    (community.COMMUNITY_THUMB_DIR / str(thumb_path)).unlink(missing_ok=True)
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


def request_actor_deletion(account_actor_id: str, *, now: int | None = None) -> bool:
    """Tombstone an actor immediately and enqueue idempotent physical cleanup."""
    community.init_db()
    try:
        actor = str(uuid.UUID(account_actor_id))
    except (TypeError, ValueError, AttributeError):
        return False
    timestamp = int(time.time() if now is None else now)
    with community._write_lock, community._connect() as conn:
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
    community.init_db()
    try:
        actor = str(uuid.UUID(account_actor_id))
    except (TypeError, ValueError, AttributeError):
        return 0
    cleaned = (nickname or "").strip()[:48]
    if not cleaned:
        return 0
    with community._write_lock, community._connect() as conn:
        if community._actor_is_deleted(conn, actor):
            return 0
        scans = conn.execute(
            "UPDATE community_scans SET nickname = ? WHERE account_actor_id = ?",
            (cleaned, actor),
        ).rowcount
        extra = sum(
            int(hook["rename"](conn, actor, cleaned) or 0)
            for hook in community._actor_hooks
            if hook.get("rename") is not None
        )
    return int(scans or 0) + extra


def process_cleanup_jobs(*, now: int | None = None, limit: int = 20) -> dict[str, int]:
    """Remove tombstoned actor content, retrying transient filesystem failures."""
    community.init_db()
    timestamp = int(time.time() if now is None else now)
    bounded_limit = max(1, min(int(limit), 100))
    with community._connect() as conn:
        jobs = conn.execute(
            "SELECT actor_id, attempt_count FROM community_cleanup_jobs "
            "WHERE state IN ('pending', 'retry') AND next_attempt_at <= ? "
            "ORDER BY next_attempt_at, created_at LIMIT ?",
            (timestamp, bounded_limit),
        ).fetchall()
    completed = 0
    retried = 0
    for actor, attempt_count in jobs:
        with community._connect() as conn:
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
                for hook in community._actor_hooks
                if hook.get("media_files") is not None
                for path in hook["media_files"](conn, actor)
            ]
        filesystem_failed = False
        for thumb in thumbs:
            try:
                (community.COMMUNITY_THUMB_DIR / thumb).unlink(missing_ok=True)
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
            with community._write_lock, community._connect() as conn:
                conn.execute(
                    "UPDATE community_cleanup_jobs SET state = 'retry', "
                    "attempt_count = ?, next_attempt_at = ?, last_error_code = 'thumbnail_io', "
                    "updated_at = ? WHERE actor_id = ?",
                    (attempts, timestamp + delay, timestamp, actor),
                )
            retried += 1
            continue

        with community._write_lock, community._connect() as conn:
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
                for hook in community._actor_hooks:
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
