from __future__ import annotations

from types import SimpleNamespace

from app.main import classify_git_status, runtime_repo_paths


def production_connection(**overrides):
    values = {
        "repo_path": "/home/plantdiseases/repo",
        "data_dir": "/home/plantdiseases/repo/server",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_runtime_paths_are_resolved_relative_to_repository() -> None:
    files, directories = runtime_repo_paths(production_connection())
    assert "server/feedback.db" in files
    assert "server/community.db-wal" in files
    assert "server/push.db-shm" in files
    assert "server/feedback_images" in directories
    assert "server/admin_backups" in directories


def test_known_untracked_runtime_files_do_not_mark_source_dirty() -> None:
    dirty, runtime = classify_git_status(
        [
            "?? server/push.db",
            "?? server/feedback_images/upload.jpg",
            "?? server/admin_backups/20260730/feedback.db",
        ],
        production_connection(),
    )
    assert dirty == []
    assert len(runtime) == 3


def test_source_unknown_and_tracked_runtime_changes_still_block() -> None:
    dirty, runtime = classify_git_status(
        [
            " M server/main.py",
            "?? server/unexpected.py",
            " M server/feedback.db",
            '?? "server/feedback_images/unusual\\nname.jpg"',
        ],
        production_connection(),
    )
    assert runtime == []
    assert len(dirty) == 4


def test_external_data_directory_is_not_hidden_from_git_status() -> None:
    connection = production_connection(data_dir="/var/lib/plantdiseases")
    assert runtime_repo_paths(connection) == (set(), set())
    dirty, runtime = classify_git_status(["?? server/push.db"], connection)
    assert dirty == ["?? server/push.db"]
    assert runtime == []
