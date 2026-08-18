from __future__ import annotations

import hashlib
import importlib
import io
import sqlite3
import time
import uuid

import pytest
from PIL import Image


def _thumbnail_bytes() -> bytes:
    encoded = io.BytesIO()
    Image.new("RGB", (64, 64), color=(45, 135, 60)).save(encoded, format="PNG")
    return encoded.getvalue()


@pytest.fixture
def community(tmp_path, monkeypatch):
    monkeypatch.setenv("COMMUNITY_DB_PATH", str(tmp_path / "community.db"))
    monkeypatch.setenv("COMMUNITY_THUMB_DIR", str(tmp_path / "thumbs"))
    monkeypatch.setenv("FEEDBACK_IP_PEPPER", "synthetic-pepper")
    import community as module

    importlib.reload(module)
    return module


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
    with sqlite3.connect(community.COMMUNITY_DB_PATH) as connection:
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
    with sqlite3.connect(community.COMMUNITY_DB_PATH) as connection:
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
    with sqlite3.connect(community.COMMUNITY_DB_PATH) as connection:
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
    with sqlite3.connect(community.COMMUNITY_DB_PATH) as connection:
        state, attempts, next_attempt, code = connection.execute(
            "SELECT state, attempt_count, next_attempt_at, last_error_code "
            "FROM community_cleanup_jobs WHERE actor_id = ?",
            (owner,),
        ).fetchone()
    assert (state, attempts, next_attempt, code) == ("retry", 1, 1_060, "thumbnail_io")


def test_legacy_not_null_schema_migrates_without_linking_rows_to_accounts(
    tmp_path, monkeypatch,
):
    database = tmp_path / "legacy.db"
    monkeypatch.setenv("COMMUNITY_DB_PATH", str(database))
    monkeypatch.setenv("COMMUNITY_THUMB_DIR", str(tmp_path / "thumbs"))
    import community as module

    importlib.reload(module)
    with sqlite3.connect(database) as connection:
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
    with sqlite3.connect(database) as connection:
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
