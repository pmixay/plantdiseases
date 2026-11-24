"""Shared SQLite plumbing for the server's on-disk stores.

``community``, ``training_store`` and ``telemetry`` each keep their own
database file, schema and migrations; what they share is how a connection is
opened and closed and how a column is added to an older table. Each store's
``_connect`` passes its *current* path at call time, so tests that redirect a
store's path keep working.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def connect(
    path: Path,
    *,
    read_only: bool = False,
    query_only: bool = False,
    foreign_keys: bool = False,
    busy_timeout_ms: int | None = None,
    row_factory: type | None = None,
    setup: Callable[[sqlite3.Connection], None] | None = None,
) -> Iterator[sqlite3.Connection]:
    """Yield a short-lived autocommit connection and always close it.

    Writable connections use WAL with ``synchronous=NORMAL``. The block's work
    is committed on success and rolled back on any exception, and the file
    handle is closed explicitly: ``sqlite3.Connection.__exit__`` commits or
    rolls back but does not close, which left databases locked on Windows and
    kept WAL handles open in long-running workers.
    """
    if read_only:
        connection = sqlite3.connect(
            f"file:{path.resolve()}?mode=ro",
            uri=True,
            check_same_thread=False,
            isolation_level=None,
        )
    else:
        connection = sqlite3.connect(
            str(path), check_same_thread=False, isolation_level=None
        )
    try:
        if not read_only:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
        if query_only:
            connection.execute("PRAGMA query_only=ON")
        if foreign_keys:
            connection.execute("PRAGMA foreign_keys=ON")
        if busy_timeout_ms is not None:
            connection.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        if row_factory is not None:
            connection.row_factory = row_factory
        if setup is not None:
            setup(connection)
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def add_column_if_missing(
    conn: sqlite3.Connection, table: str, column: str, decl: str
) -> None:
    """Idempotent ``ALTER TABLE ... ADD COLUMN`` for upgrading older files."""
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


__all__ = ["add_column_if_missing", "connect"]
