"""``init_db`` retires the old scan second-opinion and scan-like tables for good."""

from __future__ import annotations

import sqlite3
import time
import uuid
from contextlib import closing

_OLD_OPINION_SCHEMA = """
CREATE TABLE community_opinions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, scan_id TEXT NOT NULL,
    author_pseudo_id TEXT, account_actor_id TEXT, public_id TEXT,
    suggested_disease TEXT NOT NULL, note TEXT, nickname TEXT,
    client_ip_hash TEXT, created_at INTEGER NOT NULL, author_id TEXT);
CREATE INDEX ix_opinions_scan ON community_opinions(scan_id, created_at);
CREATE UNIQUE INDEX ix_opinions_public_id ON community_opinions(public_id)
    WHERE public_id IS NOT NULL;
CREATE TABLE community_opinion_requests (
    scan_id TEXT PRIMARY KEY, user_pseudo_id TEXT, account_actor_id TEXT,
    created_at INTEGER NOT NULL);
CREATE INDEX ix_opinion_requests_actor ON community_opinion_requests(account_actor_id);
ALTER TABLE community_scans ADD COLUMN wants_opinion INTEGER NOT NULL DEFAULT 0;
CREATE INDEX ix_scans_wants_opinion ON community_scans(wants_opinion);
DROP TABLE community_content_reports;
CREATE TABLE community_content_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT, reporter_actor_id TEXT NOT NULL,
    target_type TEXT NOT NULL CHECK(target_type IN ('scan', 'opinion')),
    target_scan_id TEXT NOT NULL, target_opinion_id INTEGER NOT NULL DEFAULT 0,
    target_actor_id TEXT, reason TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending', created_at INTEGER NOT NULL,
    resolved_at INTEGER,
    UNIQUE(reporter_actor_id, target_type, target_scan_id, target_opinion_id));
ALTER TABLE forum_posts ADD COLUMN legacy_scan_id TEXT;
ALTER TABLE forum_answers ADD COLUMN legacy_opinion_id INTEGER;
"""


def _names(conn: sqlite3.Connection, kind: str) -> set[str]:
    return {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = ?", (kind,))
    }


def test_init_db_drops_the_old_opinion_system_and_keeps_everything_else(community_store):
    import community_forum as forum

    community = community_store
    forum.init_db()
    owner = str(uuid.uuid4())
    viewer = str(uuid.uuid4())
    community.record_scan(
        scan_id="keeper-scan", account_actor_id=owner, nickname="Fern",
        country="GB", region="London", disease="healthy",
    )
    community.record_views(["keeper-scan"], account_actor_id=viewer)
    post = forum.create_post(
        actor_id=owner, nickname="Fern", kind="tip", title="Mist less", body="Ferns like humidity",
    )

    now = int(time.time())
    with closing(sqlite3.connect(community.COMMUNITY_DB_PATH)) as conn, conn:
        conn.executescript(_OLD_OPINION_SCHEMA)
        conn.execute("UPDATE community_scans SET wants_opinion = 1")
        conn.execute(
            "INSERT INTO community_opinions (scan_id, author_pseudo_id, public_id, "
            "suggested_disease, created_at) VALUES ('keeper-scan', 'p', 'co1', 'rust', ?)",
            (now,),
        )
        conn.execute(
            "INSERT INTO community_opinion_requests (scan_id, user_pseudo_id, created_at) "
            "VALUES ('keeper-scan', 'p', ?)", (now,),
        )
        for target_type in ("scan", "opinion"):
            conn.execute(
                "INSERT INTO community_content_reports (reporter_actor_id, target_type, "
                "target_scan_id, target_actor_id, reason, created_at) "
                "VALUES (?, ?, 'keeper-scan', ?, 'other_rules_violation', ?)",
                (viewer, target_type, owner, now),
            )
            conn.execute(
                "INSERT INTO community_reports (reporter_pseudo_id, target_type, target_id, "
                "reason, created_at) VALUES ('p', ?, 'x', 'other_rules_violation', ?)",
                (target_type, now),
            )
        conn.execute("UPDATE forum_posts SET legacy_scan_id = 'keeper-scan'")

    community._initialised = False
    forum._schema_ready_for = None
    forum.init_db()
    # A second pass over the already cleaned database is a no-op.
    community._initialised = False
    forum._schema_ready_for = None
    forum.init_db()

    with closing(sqlite3.connect(community.COMMUNITY_DB_PATH)) as conn, conn:
        assert {"community_opinions", "community_opinion_requests"}.isdisjoint(
            _names(conn, "table")
        )
        assert not {n for n in _names(conn, "index") if "opinion" in n}
        assert "wants_opinion" not in {
            row[1] for row in conn.execute("PRAGMA table_info(community_scans)")
        }
        assert [
            row[0] for row in conn.execute("SELECT target_type FROM community_content_reports")
        ] == ["scan"]
        assert [
            row[0] for row in conn.execute("SELECT target_type FROM community_reports")
        ] == ["scan"]
        assert conn.execute(
            "SELECT legacy_scan_id FROM forum_posts"
        ).fetchone() == (None,)

    assert community.scan_exists("keeper-scan")
    assert community.view_counts(["keeper-scan"]) == {"keeper-scan": 1}
    assert forum.get_post(post["id"])["title"] == "Mist less"


_OLD_LIKE_SCHEMA = """
CREATE TABLE community_likes (
    scan_id TEXT NOT NULL, user_pseudo_id TEXT NOT NULL, author_id TEXT,
    client_ip_hash TEXT, liked INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL,
    PRIMARY KEY (scan_id, user_pseudo_id)
);
CREATE INDEX ix_likes_scan ON community_likes(scan_id);
CREATE INDEX ix_likes_user_pseudo ON community_likes(user_pseudo_id);
CREATE TABLE community_account_likes (
    scan_id TEXT NOT NULL, account_actor_id TEXT NOT NULL,
    liked INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL,
    PRIMARY KEY (scan_id, account_actor_id)
);
CREATE INDEX ix_account_likes_scan ON community_account_likes(scan_id);
"""


def test_init_db_drops_the_old_scan_likes_and_keeps_the_scans(community_store):
    community = community_store
    community.init_db()
    owner = str(uuid.uuid4())
    viewer = str(uuid.uuid4())
    community.record_scan(
        scan_id="liked-scan", account_actor_id=owner, nickname="Fern",
        country="GB", region="London", disease="healthy",
    )
    community.record_views(["liked-scan"], account_actor_id=viewer)
    now = int(time.time())
    with closing(sqlite3.connect(community.COMMUNITY_DB_PATH)) as conn, conn:
        conn.executescript(_OLD_LIKE_SCHEMA)
        conn.execute(
            "INSERT INTO community_likes (scan_id, user_pseudo_id, created_at) "
            "VALUES ('liked-scan', 'p', ?)", (now,),
        )
        conn.execute(
            "INSERT INTO community_account_likes (scan_id, account_actor_id, created_at) "
            "VALUES ('liked-scan', ?, ?)", (viewer, now),
        )

    # Twice: the second pass runs over the already cleaned database.
    for _ in range(2):
        community._initialised = False
        community.init_db()

    with closing(sqlite3.connect(community.COMMUNITY_DB_PATH)) as conn, conn:
        assert not {n for n in _names(conn, "table") if "like" in n}
        assert not {n for n in _names(conn, "index") if "like" in n}
    assert community.scan_exists("liked-scan")
    assert community.view_counts(["liked-scan"]) == {"liked-scan": 1}
    (item,) = community.nearby_scans(country="GB", scope="country")
    assert not {"nickname", "author_token", "like_count", "liked_by_me"} & item.keys()


def test_a_fresh_database_never_creates_the_old_tables(community_store):
    import community_forum as forum

    forum.init_db()
    with closing(sqlite3.connect(community_store.COMMUNITY_DB_PATH)) as conn, conn:
        assert not {n for n in _names(conn, "table") if "opinion" in n}
        assert not {n for n in _names(conn, "table") if n.startswith("community_") and "like" in n}
        columns = {row[1] for row in conn.execute("PRAGMA table_info(community_scans)")}
        assert "wants_opinion" not in columns
