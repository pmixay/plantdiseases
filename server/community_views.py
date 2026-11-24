"""Community view counts: recording distinct views and reading the counts.

A view counts once per viewer and scan and never for the scan's owner, so a
refresh does not inflate it. The recommendation feed in ``community_feeds``
ranks on the same table. Connection, write lock and identity helpers stay in
``community.py``."""

from __future__ import annotations

import time

try:
    from . import community  # type: ignore[import-not-found]
except ImportError:
    import community


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
        legacy_id, actor_id = community._identity(viewer_pseudo_id, account_actor_id)
    except ValueError:
        return False
    community.init_db()
    with community._write_lock, community._connect() as conn:
        if community._actor_is_deleted(conn, actor_id):
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
        legacy_id, actor_id = community._identity(viewer_pseudo_id, account_actor_id)
    except ValueError:
        return 0
    community.init_db()
    now = int(time.time())
    placeholders = ",".join("?" * len(ids))
    with community._write_lock, community._connect() as conn:
        if community._actor_is_deleted(conn, actor_id):
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
                and community._actors_blocked(conn, actor_id, str(row[2]))
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
    community.init_db()
    placeholders = ",".join("?" * len(ids))
    with community._connect() as conn:
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
    community.init_db()
    with community._connect() as conn:
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
