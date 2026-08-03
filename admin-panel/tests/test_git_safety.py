from __future__ import annotations

from types import SimpleNamespace

import app.main as main_module
from app.main import classify_git_status, runtime_repo_paths, update_current_branch


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


def git_snapshot(commit: str = "local123") -> dict:
    return {
        "current_branch": "main",
        "commit": {"sha": commit * 4, "short_sha": commit, "subject": "test"},
        "dirty": False,
        "dirty_files": [],
        "preserved_runtime_files": [],
        "remote_branches": [{"name": "main", "sha": "remote", "short_sha": "remote"}],
        "local_branches": [],
    }


def configure_update_test(monkeypatch, ancestry_results):
    before = git_snapshot()
    after = git_snapshot("remote12")
    commands = []
    ancestry = iter(ancestry_results)

    monkeypatch.setattr(main_module, "connection_for", lambda session: production_connection())
    monkeypatch.setattr(
        main_module,
        "git_snapshot",
        lambda session, fetch=False: before if fetch else dict(after),
    )
    monkeypatch.setattr(main_module, "ensure_target_does_not_track_runtime", lambda session, branch: None)
    monkeypatch.setattr(main_module, "audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        main_module.ssh,
        "run",
        lambda session_id, command, timeout=30: {
            "exit_code": next(ancestry),
            "stdout": "",
            "stderr": "",
        },
    )
    monkeypatch.setattr(
        main_module,
        "run_checked",
        lambda session_id, command, label, timeout=90: commands.append(command) or {
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
        },
    )
    return SimpleNamespace(session_id="session"), commands


def test_fast_forward_path_keeps_normal_merge(monkeypatch) -> None:
    session, commands = configure_update_test(monkeypatch, [0])

    result = update_current_branch(session)

    assert len(commands) == 1
    assert "merge --ff-only origin/main" in commands[0]
    assert result["synchronization"] == {
        "mode": "fast_forward",
        "previous_relation": "fast_forward",
        "recovery_ref": None,
    }


def test_diverged_branch_is_recoverable_and_aligned_to_origin(monkeypatch) -> None:
    session, commands = configure_update_test(monkeypatch, [1, 1])

    result = update_current_branch(session)

    assert "update-ref refs/plantd-admin/backups/main/" in commands[0]
    assert "switch --detach origin/main" in commands[1]
    assert "branch --force main origin/main" in commands[2]
    assert "switch main" in commands[3]
    assert result["synchronization"]["mode"] == "aligned_to_origin"
    assert result["synchronization"]["previous_relation"] == "diverged"
    assert result["synchronization"]["recovery_ref"].startswith(
        "refs/plantd-admin/backups/main/",
    )
