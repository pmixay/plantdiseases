from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from app import remote_agent

USER_ONE = "11111111-1111-4111-8111-111111111111"
USER_TWO = "22222222-2222-4222-8222-222222222222"


def execute(db: Path, script: str, rows: tuple[tuple, ...] = ()) -> None:
    with sqlite3.connect(db) as connection:
        connection.executescript(script)
        if rows:
            raise AssertionError("rows must be inserted explicitly")


def scalar(db: Path, sql: str, params: tuple = ()) -> int:
    with sqlite3.connect(db) as connection:
        return int(connection.execute(sql, params).fetchone()[0])


def build_stores(root: Path) -> dict[str, Path]:
    now = int(time.time())
    paths = {name: root / f"{name}.db" for name in ("feedback", "community", "push", "labeling", "presence")}
    execute(paths["feedback"], """
        CREATE TABLE feedback (
            id INTEGER PRIMARY KEY, scan_id TEXT, user_pseudo_id TEXT, created_at INTEGER,
            app_version TEXT, locale TEXT, platform TEXT, model_version TEXT,
            image_path TEXT, client_ip_hash TEXT, note TEXT
        );
        CREATE TABLE issued_scan_ids (scan_id TEXT PRIMARY KEY, created_at INTEGER);
    """)
    with sqlite3.connect(paths["feedback"]) as connection:
        connection.execute(
            "INSERT INTO feedback VALUES (1,?,?,?,?,?,?,?,?,?,?)",
            ("feedback-scan", USER_ONE, now - 50, "2.7.0", "en", "android", "gamma-3", "feedback-one.jpg", "do-not-return-ip", "looks wrong"),
        )
        connection.execute(
            "INSERT INTO feedback VALUES (2,?,?,?,?,?,?,?,?,?,?)",
            ("shared-file-scan", USER_TWO, now - 2000, "2.6.0", "ru", "android", "gamma-2", "shared.jpg", "another-secret-ip", "shared file"),
        )
        connection.executemany(
            "INSERT INTO issued_scan_ids VALUES (?,?)",
            (("feedback-scan", now), ("community-only-scan", now), ("shared-file-scan", now)),
        )

    execute(paths["community"], """
        CREATE TABLE community_scans (
            id INTEGER PRIMARY KEY, scan_id TEXT, user_pseudo_id TEXT, country TEXT,
            region TEXT, city TEXT, nickname TEXT, locale TEXT, thumb_path TEXT,
            client_ip_hash TEXT, created_at INTEGER
        );
        CREATE TABLE community_opinions (
            id INTEGER PRIMARY KEY, scan_id TEXT, author_pseudo_id TEXT, nickname TEXT,
            note TEXT, client_ip_hash TEXT, created_at INTEGER
        );
        CREATE TABLE community_scan_views (
            scan_id TEXT, viewer_pseudo_id TEXT, created_at INTEGER,
            PRIMARY KEY (scan_id, viewer_pseudo_id)
        );
        CREATE TABLE community_opinion_requests (
            scan_id TEXT PRIMARY KEY, user_pseudo_id TEXT, created_at INTEGER
        );
    """)
    with sqlite3.connect(paths["community"]) as connection:
        connection.execute(
            "INSERT INTO community_scans VALUES (1,?,?,?,?,?,?,?,?,?,?)",
            ("community-only-scan", USER_ONE, "PL", "Warsaw", "Warsaw", "FernFox", "en", "community-one.jpg", "hidden-ip", now - 30),
        )
        connection.execute(
            "INSERT INTO community_scans VALUES (2,?,?,?,?,?,?,?,?,?,?)",
            ("other-scan", USER_TWO, "DE", "Berlin", "Berlin", "MossBee", "de", None, "hidden-ip-2", now - 5000),
        )
        connection.execute(
            "INSERT INTO community_opinions VALUES (1,?,?,?,?,?,?)",
            ("other-scan", USER_ONE, "FernFox", "try watering", "opinion-ip", now - 20),
        )
        connection.execute(
            "INSERT INTO community_scan_views VALUES (?,?,?)", ("other-scan", USER_ONE, now - 10),
        )
        connection.execute(
            "INSERT INTO community_opinion_requests VALUES (?,?,?)", ("community-only-scan", USER_ONE, now - 25),
        )

    execute(paths["push"], """
        CREATE TABLE push_tokens (
            token TEXT PRIMARY KEY, user_pseudo_id TEXT, platform TEXT, updated_at INTEGER
        );
    """)
    with sqlite3.connect(paths["push"]) as connection:
        connection.executemany(
            "INSERT INTO push_tokens VALUES (?,?,?,?)",
            (("raw-fcm-token-one", USER_ONE, "android", now - 40), ("raw-fcm-token-two", USER_TWO, "android", now - 4000)),
        )

    execute(paths["labeling"], """
        CREATE TABLE candidates (
            id INTEGER PRIMARY KEY, scan_id TEXT, image_path TEXT, class TEXT, created_at INTEGER
        );
    """)
    with sqlite3.connect(paths["labeling"]) as connection:
        connection.executemany(
            "INSERT INTO candidates VALUES (?,?,?,?,?)",
            (
                (1, "feedback-scan", "feedback-label.jpg", "leaf_spot", now),
                (2, "community-only-scan", "community-label.jpg", "healthy", now),
                (3, "other-scan", "other-label.jpg", "healthy", now),
            ),
        )

    execute(paths["presence"], """
        CREATE TABLE user_presence (
            user_pseudo_id TEXT PRIMARY KEY, last_seen_at INTEGER, device_model TEXT,
            manufacturer TEXT, android_api INTEGER, app_version TEXT, auth_token TEXT
        );
    """)
    with sqlite3.connect(paths["presence"]) as connection:
        connection.execute(
            "INSERT INTO user_presence VALUES (?,?,?,?,?,?,?)",
            (USER_ONE, now - 5, "Pixel 9", "Google", 36, "2.7.0", "presence-secret"),
        )

    for directory in ("feedback_images", "community_thumbs", "labeling_images"):
        (root / directory).mkdir()
    for filename in ("feedback-one.jpg", "shared.jpg"):
        (root / "feedback_images" / filename).write_bytes(filename.encode())
    (root / "community_thumbs" / "community-one.jpg").write_bytes(b"thumb")
    for filename in ("feedback-label.jpg", "community-label.jpg", "other-label.jpg"):
        (root / "labeling_images" / filename).write_bytes(filename.encode())
    return paths


def test_list_users_unions_stores_and_redacts_sensitive_values(tmp_path: Path) -> None:
    build_stores(tmp_path)
    result = remote_agent.list_users(tmp_path, active_window_seconds=900)

    assert result["ok"] is True
    assert result["discovered_total"] == 2
    users = {row["id"]: row for row in result["users"]}
    first = users[USER_ONE]
    assert first["active"] is True
    assert first["device_model"] == "Pixel 9"
    assert first["device_manufacturer"] == "Google"
    assert first["source_counts"]["feedback"] == 1
    assert first["source_counts"]["community_scans"] == 1
    assert first["source_counts"]["push_registrations"] == 1
    assert first["first_seen_at"] <= first["last_seen_at"]

    serialized = json.dumps(result)
    assert "raw-fcm-token" not in serialized
    assert "presence-secret" not in serialized
    assert "hidden-ip" not in serialized
    assert "do-not-return-ip" not in serialized


def test_user_detail_returns_records_without_secrets(tmp_path: Path) -> None:
    build_stores(tmp_path)
    result = remote_agent.user_detail(tmp_path, USER_ONE)

    assert result["ok"] is True
    assert result["owned_scan_count"] == 2
    assert set(result["owned_scan_ids"]) == {"feedback-scan", "community-only-scan"}
    serialized = json.dumps(result)
    assert "raw-fcm-token-one" not in serialized
    assert "hidden-ip" not in serialized
    assert "presence-secret" not in serialized
    assert "looks wrong" in serialized


def test_delete_user_backups_and_cascades_community_only_scan(tmp_path: Path) -> None:
    paths = build_stores(tmp_path)
    result = remote_agent.delete_user(tmp_path, USER_ONE, create_backup=True)

    assert result["ok"] is True
    assert result["deleted_total"] > 0
    backup_dir = Path(result["backup_dir"])
    assert backup_dir.is_dir()
    assert len(list(backup_dir.rglob("*.db"))) == 5

    assert scalar(paths["feedback"], "SELECT COUNT(*) FROM feedback WHERE user_pseudo_id=?", (USER_ONE,)) == 0
    assert scalar(paths["community"], "SELECT COUNT(*) FROM community_scans WHERE user_pseudo_id=?", (USER_ONE,)) == 0
    assert scalar(paths["community"], "SELECT COUNT(*) FROM community_opinions WHERE author_pseudo_id=?", (USER_ONE,)) == 0
    assert scalar(paths["community"], "SELECT COUNT(*) FROM community_scan_views WHERE viewer_pseudo_id=?", (USER_ONE,)) == 0
    assert scalar(paths["push"], "SELECT COUNT(*) FROM push_tokens WHERE user_pseudo_id=?", (USER_ONE,)) == 0
    assert scalar(paths["presence"], "SELECT COUNT(*) FROM user_presence WHERE user_pseudo_id=?", (USER_ONE,)) == 0
    assert scalar(paths["labeling"], "SELECT COUNT(*) FROM candidates WHERE scan_id IN (?,?)", ("feedback-scan", "community-only-scan")) == 0
    assert scalar(paths["labeling"], "SELECT COUNT(*) FROM candidates WHERE scan_id='other-scan'") == 1
    assert scalar(paths["feedback"], "SELECT COUNT(*) FROM issued_scan_ids WHERE scan_id IN (?,?)", ("feedback-scan", "community-only-scan")) == 0

    assert not (tmp_path / "feedback_images" / "feedback-one.jpg").exists()
    assert not (tmp_path / "community_thumbs" / "community-one.jpg").exists()
    assert not (tmp_path / "labeling_images" / "feedback-label.jpg").exists()
    assert not (tmp_path / "labeling_images" / "community-label.jpg").exists()
    assert (tmp_path / "labeling_images" / "other-label.jpg").exists()
    assert (tmp_path / "feedback_images" / "shared.jpg").exists()


def test_backup_all_uses_online_sqlite_snapshots(tmp_path: Path) -> None:
    paths = build_stores(tmp_path)
    result = remote_agent.backup_all(tmp_path)

    assert result["ok"] is True
    assert result["count"] == len(paths)
    for backup in result["files"]:
        with sqlite3.connect(backup) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_execute_request_returns_one_error_envelope() -> None:
    result = remote_agent.execute_request({"action": "not-real"})
    assert result["ok"] is False
    assert result["error"]["code"] == "unknown_action"
