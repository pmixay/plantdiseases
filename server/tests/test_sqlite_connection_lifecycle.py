"""Windows regression checks for the short-lived SQLite stores."""

from __future__ import annotations

import pytest

import community
import feedback
import labeling
import push
import telemetry


@pytest.mark.parametrize(
    ("module", "path_attr"),
    [
        (community, "COMMUNITY_DB_PATH"),
        (feedback, "FEEDBACK_DB_PATH"),
        (labeling, "LABELING_DB_PATH"),
        (push, "PUSH_DB_PATH"),
        (telemetry, "TELEMETRY_DB_PATH"),
    ],
)
def test_connection_context_releases_database_file(module, path_attr, tmp_path, monkeypatch):
    """Context exit must close, not merely commit, the SQLite connection."""
    path = tmp_path / f"{module.__name__}.db"
    monkeypatch.setattr(module, path_attr, path)
    with module._connect() as conn:
        conn.execute("CREATE TABLE lifecycle_probe (id INTEGER)")
    path.unlink()
    assert not path.exists()
