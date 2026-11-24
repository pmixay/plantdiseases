"""Durable forum author identity (the ``community_authors`` table).

One author_id per device key, stored under a peppered digest of the client key
hash. Nothing in the server calls these today; they stay available for the
keyed-write path. Connection, write lock and the key digest stay in
``community.py``."""

from __future__ import annotations

import secrets
import sqlite3
import time

try:
    from . import community
except ImportError:
    import community


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
    stored_key = community._stored_author_key(kh)
    community.init_db()
    with community._write_lock, community._connect() as conn:
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
    stored_key = community._stored_author_key(kh)
    community.init_db()
    with community._connect() as conn:
        row = conn.execute(
            "SELECT author_id FROM community_authors WHERE key_hash = ?",
            (stored_key,),
        ).fetchone()
        return _author_row(conn, str(row[0])) if row else None
