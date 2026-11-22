"""Regression checks for the short-lived SQLite stores.

The bug was Windows-only in its symptom (an open handle blocks deleting the
file), but the cause — a context manager that commits without closing — is
checked directly, so the test bites on the POSIX CI runner too, where
unlinking an open database file succeeds regardless."""

from __future__ import annotations

import sqlite3

import pytest

import community
import telemetry
import training_store


@pytest.mark.parametrize(
    ("module", "path_attr"),
    [
        (community, "COMMUNITY_DB_PATH"),
        (telemetry, "TELEMETRY_DB_PATH"),
        (training_store, "TRAINING_DB_PATH"),
    ],
)
def test_connection_context_releases_database_file(module, path_attr, tmp_path, monkeypatch):
    """Context exit must close, not merely commit, the SQLite connection."""
    path = tmp_path / f"{module.__name__}.db"
    monkeypatch.setattr(module, path_attr, path)
    with module._connect() as conn:
        conn.execute("CREATE TABLE lifecycle_probe (id INTEGER)")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        conn.execute("SELECT 1")
    path.unlink()
    assert not path.exists()
