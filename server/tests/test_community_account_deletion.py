from __future__ import annotations

import hashlib
import io
import shutil
import sqlite3
import time
import uuid
from contextlib import closing

import pytest
from PIL import Image


def _thumbnail_bytes() -> bytes:
    encoded = io.BytesIO()
    Image.new("RGB", (64, 64), color=(45, 135, 60)).save(encoded, format="PNG")
    return encoded.getvalue()


@pytest.fixture
def community(community_store):
    return community_store


def test_tombstone_hides_immediately_then_retryable_cleanup_removes_actor_data(
    community,
):
    owner = str(uuid.uuid4())
    responder = str(uuid.uuid4())
    legacy = "legacy-install"
    thumbnail_bytes = _thumbnail_bytes()
    community.record_analysis_token(
        "delete-account-scan",
        disease="leaf_spot",
        confidence=0.91,
        species="Synthetic fern",
        species_score=0.88,
        image_sha256=hashlib.sha256(thumbnail_bytes).hexdigest(),
    )
    community.record_analysis_token(
        "keep-unclaimed-token",
        disease="healthy",
        confidence=0.99,
        species=None,
        species_score=None,
        image_sha256=hashlib.sha256(b"unclaimed image").hexdigest(),
    )
    community.record_analysis_scan(
        scan_id="delete-account-scan",
        account_actor_id=owner,
        nickname="Private Fern",
        country="GB",
        region="London",
        image_bytes=thumbnail_bytes,
    )
    community.record_scan(
        scan_id="keep-legacy-scan",
        user_pseudo_id=legacy,
        country="GB",
        region="London",
        disease="healthy",
    )
    community.record_opinion(
        scan_id="delete-account-scan",
        account_actor_id=responder,
        nickname="Responder",
        suggested_disease="rust",
    )
    community.record_opinion(
        scan_id="keep-legacy-scan",
        account_actor_id=owner,
        nickname="Private Fern",
        suggested_disease="healthy",
    )
    community.record_view("keep-legacy-scan", account_actor_id=owner)
    assert community.view_count_for("keep-legacy-scan") == 1
    community.record_view(
        "delete-account-scan", account_actor_id=responder
    )
    with closing(sqlite3.connect(community.COMMUNITY_DB_PATH)) as connection, connection:
        thumbnail = connection.execute(
            "SELECT thumb_path FROM community_scans WHERE scan_id = 'delete-account-scan'"
        ).fetchone()[0]
    assert thumbnail and community.thumbnail_is_visible(thumbnail)

    assert community.request_actor_deletion(owner)

    visible = community.nearby_scans(country="GB", scope="country")
    assert [row["scan_id"] for row in visible] == ["keep-legacy-scan"]
    assert community.opinions_for("delete-account-scan")["opinions"] == []
    assert not community.thumbnail_is_visible(thumbnail)
    assert community.view_count_for("keep-legacy-scan") == 0
    with closing(sqlite3.connect(community.COMMUNITY_DB_PATH)) as connection, connection:
        # Logical hiding precedes the asynchronous physical pass.
        assert connection.execute(
            "SELECT COUNT(*) FROM community_scans WHERE account_actor_id = ?", (owner,)
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM community_analysis_tokens "
            "WHERE claimed_actor_id = ?", (owner,)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM community_analysis_tokens "
            "WHERE token = 'keep-unclaimed-token'"
        ).fetchone()[0] == 1

    result = community.process_cleanup_jobs()
    assert result == {"completed": 1, "retried": 0}
    assert not (community.COMMUNITY_THUMB_DIR / thumbnail).exists()
    with closing(sqlite3.connect(community.COMMUNITY_DB_PATH)) as connection, connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM community_scans WHERE account_actor_id = ?", (owner,)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM community_opinions WHERE account_actor_id = ?", (owner,)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM community_cleanup_jobs WHERE actor_id = ?", (owner,)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM community_scans WHERE user_pseudo_id = ?", (legacy,)
        ).fetchone()[0] == 1

    with pytest.raises(ValueError, match="deleted account actor"):
        community.record_scan(
            scan_id="replayed-after-delete",
            account_actor_id=owner,
            nickname="Private Fern",
            country="GB",
            region="London",
            disease="healthy",
        )


def test_thumbnail_failure_keeps_rows_hidden_and_schedules_bounded_retry(
    community, monkeypatch,
):
    owner = str(uuid.uuid4())
    community.record_scan(
        scan_id="retry-delete-scan",
        account_actor_id=owner,
        nickname="Retry Fern",
        country="GB",
        region="London",
        disease="healthy",
        image_bytes=_thumbnail_bytes(),
    )
    community.request_actor_deletion(owner, now=1_000)

    def fail_unlink(*_args, **_kwargs):
        raise OSError("synthetic filesystem failure")

    monkeypatch.setattr(type(community.COMMUNITY_THUMB_DIR), "unlink", fail_unlink)
    result = community.process_cleanup_jobs(now=1_000)
    assert result == {"completed": 0, "retried": 1}
    assert community.nearby_scans(country="GB", scope="country") == []
    with closing(sqlite3.connect(community.COMMUNITY_DB_PATH)) as connection, connection:
        state, attempts, next_attempt, code = connection.execute(
            "SELECT state, attempt_count, next_attempt_at, last_error_code "
            "FROM community_cleanup_jobs WHERE actor_id = ?",
            (owner,),
        ).fetchone()
    assert (state, attempts, next_attempt, code) == ("retry", 1, 1_060, "thumbnail_io")


def test_legacy_not_null_schema_migrates_without_linking_rows_to_accounts(
    community_store, tmp_path, monkeypatch,
):
    database = tmp_path / "legacy.db"
    module = community_store
    monkeypatch.setattr(module, "COMMUNITY_DB_PATH", database)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.executescript(
            "CREATE TABLE community_scans ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT, scan_id TEXT NOT NULL,"
            " user_pseudo_id TEXT NOT NULL, country TEXT, region TEXT NOT NULL,"
            " disease TEXT NOT NULL, confidence REAL, species TEXT,"
            " species_lower TEXT, species_score REAL, thumb_path TEXT,"
            " locale TEXT, client_ip_hash TEXT,"
            " wants_opinion INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL);"
            "CREATE TABLE community_opinions ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT, scan_id TEXT NOT NULL,"
            " author_pseudo_id TEXT NOT NULL, suggested_disease TEXT NOT NULL,"
            " note TEXT, nickname TEXT, client_ip_hash TEXT, created_at INTEGER NOT NULL);"
            "CREATE TABLE community_opinion_requests ("
            " scan_id TEXT PRIMARY KEY, user_pseudo_id TEXT NOT NULL,"
            " created_at INTEGER NOT NULL);"
            "CREATE TABLE community_scan_views ("
            " scan_id TEXT NOT NULL, viewer_pseudo_id TEXT NOT NULL,"
            " created_at INTEGER NOT NULL, PRIMARY KEY (scan_id, viewer_pseudo_id));"
        )
        connection.execute(
            "INSERT INTO community_scans "
            "(scan_id, user_pseudo_id, country, region, disease, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("legacy-schema-row", "legacy-only", "GB", "London", "healthy", int(time.time())),
        )
        timestamp = int(time.time())
        connection.execute(
            "INSERT INTO community_opinions "
            "(scan_id, author_pseudo_id, suggested_disease, created_at) "
            "VALUES ('legacy-schema-row', 'legacy-viewer', 'healthy', ?)",
            (timestamp,),
        )
        connection.execute(
            "INSERT INTO community_opinion_requests "
            "(scan_id, user_pseudo_id, created_at) "
            "VALUES ('legacy-schema-row', 'legacy-only', ?)",
            (timestamp,),
        )
        connection.execute(
            "INSERT INTO community_scan_views "
            "(scan_id, viewer_pseudo_id, created_at) "
            "VALUES ('legacy-schema-row', 'legacy-viewer', ?)",
            (timestamp,),
        )

    module.init_db()
    actor = str(uuid.uuid4())
    module.record_scan(
        scan_id="new-account-row",
        account_actor_id=actor,
        nickname="Account Fern",
        country="GB",
        region="London",
        disease="healthy",
    )
    with closing(sqlite3.connect(database)) as connection, connection:
        columns = {
            row[1]: row[3]
            for row in connection.execute("PRAGMA table_info(community_scans)")
        }
        legacy_row = connection.execute(
            "SELECT user_pseudo_id, account_actor_id FROM community_scans "
            "WHERE scan_id = 'legacy-schema-row'"
        ).fetchone()
        account_row = connection.execute(
            "SELECT user_pseudo_id, account_actor_id FROM community_scans "
            "WHERE scan_id = 'new-account-row'"
        ).fetchone()
        migrated_identities = {
            table: connection.execute(
                f"SELECT {legacy_column}, account_actor_id FROM {table} LIMIT 1"
            ).fetchone()
            for table, legacy_column in {
                "community_opinions": "author_pseudo_id",
                "community_opinion_requests": "user_pseudo_id",
                "community_scan_views": "viewer_pseudo_id",
            }.items()
        }
        nullable_identities = {
            table: {
                row[1]: row[3]
                for row in connection.execute(f"PRAGMA table_info({table})")
            }[legacy_column]
            for table, legacy_column in {
                "community_opinions": "author_pseudo_id",
                "community_opinion_requests": "user_pseudo_id",
                "community_scan_views": "viewer_pseudo_id",
            }.items()
        }
    assert columns["user_pseudo_id"] == 0
    assert legacy_row == ("legacy-only", None)
    assert account_row == (None, actor)
    assert migrated_identities == {
        "community_opinions": ("legacy-viewer", None),
        "community_opinion_requests": ("legacy-only", None),
        "community_scan_views": ("legacy-viewer", None),
    }
    assert nullable_identities == {
        "community_opinions": 0,
        "community_opinion_requests": 0,
        "community_scan_views": 0,
    }


def _marker_ids(community) -> set[str]:
    with closing(sqlite3.connect(community.COMMUNITY_DB_PATH)) as connection, connection:
        return {
            row[0] for row in connection.execute("SELECT actor_id FROM community_deleted_actors")
        }


def test_deleted_actor_marker_expires_after_thirty_days_only_when_cleanup_finished(community):
    now = 2_000_000_000
    day = 86_400
    expired, recent, pending = (str(uuid.uuid4()) for _ in range(3))
    assert community.DELETED_ACTOR_MARKER_RETENTION_DAYS == 30  # disclosed in the policy
    for actor, age_days in ((expired, 31), (recent, 29), (pending, 40)):
        community.request_actor_deletion(actor, now=now - age_days * day)
    with closing(sqlite3.connect(community.COMMUNITY_DB_PATH)) as connection, connection:  # hold one job back
        connection.execute(
            "UPDATE community_cleanup_jobs SET next_attempt_at = ? WHERE actor_id = ?",
            (now + 10 * day, pending),
        )
    assert community.process_cleanup_jobs(now=now) == {"completed": 2, "retried": 0}

    assert community.prune_expired_deleted_actor_markers(now=now) == 1
    # Too young stays; an unfinished cleanup keeps its marker even past 30 days.
    assert _marker_ids(community) == {recent, pending}

    with closing(sqlite3.connect(community.COMMUNITY_DB_PATH)) as connection, connection:
        connection.execute(
            "UPDATE community_cleanup_jobs SET next_attempt_at = ? WHERE actor_id = ?",
            (now, pending),
        )
    assert community.process_cleanup_jobs(now=now) == {"completed": 1, "retried": 0}
    assert community.prune_expired_deleted_actor_markers(now=now) == 1
    assert _marker_ids(community) == {recent}
    assert community.prune_expired_deleted_actor_markers(now=now + 2 * day) == 1
    assert _marker_ids(community) == set()


def test_daily_prune_also_expires_old_markers(community):
    actor = str(uuid.uuid4())
    community.request_actor_deletion(actor, now=int(time.time()) - 31 * 86_400)
    assert community.process_cleanup_jobs() == {"completed": 1, "retried": 0}
    assert _marker_ids(community) == {actor}

    community.prune_older_than(365)

    assert _marker_ids(community) == set()


def test_replaying_the_deletion_repairs_a_community_store_restored_from_an_older_backup(
    community, tmp_path,
):
    owner = str(uuid.uuid4())
    community.record_scan(
        scan_id="restore-scan",
        account_actor_id=owner,
        nickname="Restore Fern",
        country="GB",
        region="London",
        disease="healthy",
        image_bytes=_thumbnail_bytes(),
    )
    # Backup exactly as the backup procedure does: SQLite's online backup API.
    backup = tmp_path / "community-backup.db"
    with closing(sqlite3.connect(community.COMMUNITY_DB_PATH)) as source, closing(
        sqlite3.connect(backup)
    ) as target:
        source.backup(target)

    # The account is deleted after the backup was taken.
    assert community.request_actor_deletion(owner)
    assert community.process_cleanup_jobs()["completed"] == 1
    assert community.nearby_scans(country="GB", scope="country") == []

    # Disaster recovery restores the older community database.
    for suffix in ("-wal", "-shm"):
        community.COMMUNITY_DB_PATH.with_name(
            community.COMMUNITY_DB_PATH.name + suffix
        ).unlink(missing_ok=True)
    shutil.copyfile(backup, community.COMMUNITY_DB_PATH)
    with closing(sqlite3.connect(community.COMMUNITY_DB_PATH)) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM community_scans WHERE account_actor_id = ?", (owner,)
        ).fetchone()[0] == 1  # the deleted account's content is back
    assert _marker_ids(community) == set()  # and nothing suppresses it

    # The account service re-delivers the tombstone.
    assert community.request_actor_deletion(owner)
    assert community.process_cleanup_jobs()["completed"] == 1

    assert community.nearby_scans(country="GB", scope="country") == []
    with closing(sqlite3.connect(community.COMMUNITY_DB_PATH)) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM community_scans WHERE account_actor_id = ?", (owner,)
        ).fetchone()[0] == 0
    with pytest.raises(ValueError, match="deleted account actor"):
        community.record_scan(
            scan_id="after-replay",
            account_actor_id=owner,
            nickname="Restore Fern",
            country="GB",
            region="London",
            disease="healthy",
        )
