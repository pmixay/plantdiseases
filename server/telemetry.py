"""Minimized, consent-gated, errors-only technical telemetry store.

Rows contain only allowlisted enums, coarse buckets, an application version,
and a coarse UTC day. There is no message/exception text, image, location,
account/install/session identifier, request body, raw IP, or IP hash. Connection
addresses are used only by the HTTP layer's in-memory abuse limiter.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

try:
    from .config import get_str  # type: ignore[import-not-found]
except ImportError:
    from config import get_str


SCHEMA_VERSION = "2"
ALLOWED_STAGES = frozenset(
    {
        "image_prepare",
        "analyze",
        "aqua_inference",
        "crash",
        "community",
        "authentication",
        "startup",
    }
)
ALLOWED_KINDS = frozenset(
    {
        "decode_failed",
        "out_of_memory",
        "internal_error",
        "timeout",
        "no_network",
        "rate_limited",
        "server_starting",
        "unsupported_image",
        "uncaught_exception",
        "http_error",
        "model_unavailable",
        "invalid_envelope",
    }
)
ALLOWED_FAMILIES = frozenset({"aqua", "gamma", "frontier", "unknown"})
ALLOWED_BREADCRUMBS = frozenset(
    {
        "scanner_opened",
        "analyze_started_aqua",
        "analyze_started_gamma",
        "analyze_started_frontier",
        "retry_started",
        "result_opened",
        "community_opened",
    }
)
_APP_VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z._+-]{0,31}\Z")

_MODULE_DIR = Path(__file__).resolve().parent
TELEMETRY_DB_PATH = Path(
    get_str("TELEMETRY_DB_PATH", str(_MODULE_DIR / "telemetry.db"))
)

_EXPECTED_COLUMNS = (
    "id",
    "created_day",
    "stage",
    "kind",
    "fatal",
    "http_status_bucket",
    "family",
    "app_version",
    "android_api_bucket",
    "breadcrumbs_json",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS error_reports (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    created_day           TEXT NOT NULL,
    stage                 TEXT NOT NULL,
    kind                  TEXT NOT NULL,
    fatal                 INTEGER NOT NULL CHECK(fatal IN (0, 1)),
    http_status_bucket    TEXT,
    family                TEXT,
    app_version           TEXT,
    android_api_bucket    TEXT,
    breadcrumbs_json      TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS ix_telemetry_created_day
    ON error_reports(created_day);
CREATE INDEX IF NOT EXISTS ix_telemetry_dimensions
    ON error_reports(stage, kind, family);
"""

_write_lock = threading.RLock()
_init_lock = threading.Lock()
_initialised = False


class TelemetrySchemaError(RuntimeError):
    """The database contains a legacy identifier/free-text schema."""


class TelemetryValueError(ValueError):
    pass


@contextmanager
def _connect(*, read_only: bool = False) -> Iterator[sqlite3.Connection]:
    """Yield a short-lived connection and always close its file handles.

    ``sqlite3.Connection.__exit__`` commits or rolls back but does not close
    the connection, so closure is explicit (Windows file locks, WAL handles
    in long-running workers).
    """
    if read_only:
        connection = sqlite3.connect(
            f"file:{TELEMETRY_DB_PATH.resolve()}?mode=ro",
            uri=True,
            isolation_level=None,
        )
    else:
        connection = sqlite3.connect(
            str(TELEMETRY_DB_PATH), isolation_level=None, check_same_thread=False
        )
    try:
        if read_only:
            connection.execute("PRAGMA query_only=ON")
        else:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
        connection.row_factory = sqlite3.Row
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db() -> None:
    global _initialised
    with _init_lock:
        if _initialised:
            return
        TELEMETRY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        if TELEMETRY_DB_PATH.exists():
            with _connect(read_only=True) as connection:
                existing = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                    "AND name = 'error_reports'"
                ).fetchone()
                if existing is not None:
                    columns = tuple(
                        str(row["name"])
                        for row in connection.execute(
                            "PRAGMA table_info(error_reports)"
                        )
                    )
                    if columns != _EXPECTED_COLUMNS:
                        raise TelemetrySchemaError(
                            "legacy telemetry requires an explicit privacy audit"
                        )
        with _write_lock, _connect() as connection:
            connection.executescript(_SCHEMA)
        _initialised = True


def _day(value: datetime | None = None) -> str:
    return (value or datetime.now(UTC)).astimezone(UTC).date().isoformat()


def http_status_bucket(value: int | None) -> str | None:
    if value is None:
        return None
    status = int(value)
    if not 100 <= status <= 599:
        raise TelemetryValueError("invalid HTTP status")
    return f"{status // 100}xx"


def android_api_bucket(value: int | None) -> str | None:
    if value is None:
        return None
    api = int(value)
    if not 1 <= api <= 100:
        raise TelemetryValueError("invalid Android API")
    if api < 21:
        return "below-21"
    if api <= 25:
        return "21-25"
    if api <= 28:
        return "26-28"
    if api <= 30:
        return "29-30"
    if api <= 32:
        return "31-32"
    if api <= 34:
        return "33-34"
    return "35-plus"


def _validate(
    *,
    stage: str,
    kind: str,
    family: str | None,
    app_version: str | None,
    breadcrumbs: list[str] | None,
) -> tuple[str | None, str | None, list[str]]:
    if stage not in ALLOWED_STAGES:
        raise TelemetryValueError("stage is not allowlisted")
    if kind not in ALLOWED_KINDS:
        raise TelemetryValueError("kind is not allowlisted")
    family_value = family or None
    if family_value is not None and family_value not in ALLOWED_FAMILIES:
        raise TelemetryValueError("family is not allowlisted")
    version = app_version or None
    if version is not None and _APP_VERSION.fullmatch(version) is None:
        raise TelemetryValueError("app version is invalid")
    trail = list(breadcrumbs or [])
    if len(trail) > 20 or any(item not in ALLOWED_BREADCRUMBS for item in trail):
        raise TelemetryValueError("breadcrumb is not allowlisted")
    return family_value, version, trail


def record_error(
    *,
    stage: str,
    kind: str,
    fatal: bool = False,
    http_status_bucket: str | None = None,
    family: str | None = None,
    app_version: str | None = None,
    android_api_bucket: str | None = None,
    breadcrumbs: list[str] | None = None,
    created_day: str | None = None,
) -> int:
    """Append one minimized report; validation failures never enter SQLite."""
    init_db()
    family_value, version, trail = _validate(
        stage=stage,
        kind=kind,
        family=family,
        app_version=app_version,
        breadcrumbs=breadcrumbs,
    )
    if http_status_bucket is not None and http_status_bucket not in {
        "1xx",
        "2xx",
        "3xx",
        "4xx",
        "5xx",
    }:
        raise TelemetryValueError("HTTP status bucket is invalid")
    if android_api_bucket is not None and android_api_bucket not in {
        "below-21",
        "21-25",
        "26-28",
        "29-30",
        "31-32",
        "33-34",
        "35-plus",
    }:
        raise TelemetryValueError("Android API bucket is invalid")
    day = created_day or _day()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) is None:
        raise TelemetryValueError("created day is invalid")
    with _write_lock, _connect() as connection:
        cursor = connection.execute(
            "INSERT INTO error_reports (created_day, stage, kind, fatal, "
            "http_status_bucket, family, app_version, android_api_bucket, "
            "breadcrumbs_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                day,
                stage,
                kind,
                int(bool(fatal)),
                http_status_bucket,
                family_value,
                version,
                android_api_bucket,
                json.dumps(trail, separators=(",", ":")),
            ),
        )
    return int(cursor.lastrowid or 0)


def error_counts(*, days: int = 7) -> list[dict]:
    init_db()
    cutoff = (datetime.now(UTC).date() - timedelta(days=max(0, int(days)))).isoformat()
    with _connect(read_only=True) as connection:
        rows = connection.execute(
            "SELECT stage, kind, fatal, family, http_status_bucket, "
            "android_api_bucket, COUNT(*) AS n FROM error_reports "
            "WHERE created_day >= ? GROUP BY stage, kind, fatal, family, "
            "http_status_bucket, android_api_bucket ORDER BY n DESC",
            (cutoff,),
        ).fetchall()
    return [dict(row) for row in rows]


def prune_older_than(days: int, *, now: datetime | None = None) -> int:
    init_db()
    reference = (now or datetime.now(UTC)).astimezone(UTC).date()
    cutoff = (reference - timedelta(days=max(1, int(days)))).isoformat()
    with _write_lock, _connect() as connection:
        cursor = connection.execute(
            "DELETE FROM error_reports WHERE created_day < ?", (cutoff,)
        )
    return int(cursor.rowcount or 0)


def schema_sql() -> str:
    return _SCHEMA


__all__ = [
    "ALLOWED_BREADCRUMBS",
    "ALLOWED_FAMILIES",
    "ALLOWED_KINDS",
    "ALLOWED_STAGES",
    "SCHEMA_VERSION",
    "TELEMETRY_DB_PATH",
    "TelemetrySchemaError",
    "TelemetryValueError",
    "android_api_bucket",
    "error_counts",
    "http_status_bucket",
    "init_db",
    "prune_older_than",
    "record_error",
    "schema_sql",
]
