#!/usr/bin/env python3
"""Small, dependency-free database agent for the Plant Diseases admin panel.

The desktop panel sends this file to the configured remote Python on stdin. It prepends a
single ``REQUEST_JSON = '...'`` assignment, so this module deliberately has no
``__future__`` imports (a future import would no longer be at the beginning of
the generated input).  Importing the module is side-effect free; script mode
prints exactly one compact JSON document.

Supported actions are ``list_users``, ``user_detail``, ``delete_user`` and
``backup_all``.  All database work uses Python's standard-library sqlite3
driver.  Read connections are opened in normal read-only mode (not immutable),
which means committed records in a live database's WAL are visible.  Backups
use SQLite's online backup API rather than copying database files.
"""

import contextlib
import datetime as _datetime
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path


if "REQUEST_JSON" not in globals():
    REQUEST_JSON = ""


DEFAULT_ACTIVE_WINDOW_SECONDS = 15 * 60
MAX_ACTIVE_WINDOW_SECONDS = 366 * 24 * 60 * 60
DEFAULT_DETAIL_LIMIT = 50
MAX_DETAIL_LIMIT = 250
SQLITE_BUSY_TIMEOUT_MS = 8_000
SQLITE_CHUNK_SIZE = 400

_USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_USER_COLUMNS = (
    "user_pseudo_id",
    "user_id",
    "pseudo_id",
    "install_id",
)
_TIME_COLUMNS = (
    "last_seen_at",
    "last_active_at",
    "heartbeat_at",
    "active_at",
    "seen_at",
    "event_at",
    "updated_at",
    "created_at",
    "timestamp",
    "last_seen",
)

_DB_FILENAMES = {
    "feedback": "feedback.db",
    "community": "community.db",
    "push": "push.db",
    "presence": "presence.db",
    "labeling": "labeling.db",
}
_DB_CONFIG_KEYS = {
    "feedback": "FEEDBACK_DB_PATH",
    "community": "COMMUNITY_DB_PATH",
    "push": "PUSH_DB_PATH",
    "presence": "PRESENCE_DB_PATH",
    "labeling": "LABELING_DB_PATH",
}
_MEDIA_DEFAULTS = {
    "feedback": "feedback_images",
    "community": "community_thumbs",
    "labeling": "labeling_images",
}
_MEDIA_CONFIG_KEYS = {
    "feedback": "FEEDBACK_IMG_DIR",
    "community": "COMMUNITY_THUMB_DIR",
    "labeling": "LABELING_IMG_DIR",
}

_ACTIVITY_PRIORITY = {
    "presence": 100,
    "community_opinion": 70,
    "community_view": 60,
    "community_opinion_request": 55,
    "community_scan": 50,
    "feedback": 40,
    "push_registration": 20,
}

_PROFILE_ALIASES = {
    "phone_model": "device_model",
    "model": "device_model",
    "device": "device_model",
    "manufacturer": "device_manufacturer",
    "brand": "device_brand",
    "api_level": "android_api",
    "sdk_int": "android_api",
    "os_release": "android_version",
    "os_version": "android_version",
    "application_version": "app_version",
}


class AgentError(Exception):
    """A request error that can safely be returned to the desktop client."""

    def __init__(self, code, message, details=None):
        Exception.__init__(self, message)
        self.code = str(code)
        self.message = str(message)
        self.details = details


def _error_result(action, exc):
    error = {"code": exc.code, "message": exc.message}
    if exc.details is not None:
        error["details"] = _json_safe(exc.details)
    return {"ok": False, "action": action, "error": error}


def _quote_identifier(value):
    return '"' + str(value).replace('"', '""') + '"'


def _json_safe(value):
    """Coerce SQLite/extension values into JSON-safe standard types."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        # Binary database fields are never useful in this administrative view.
        return "<binary:{} bytes>".format(len(value))
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if _is_sensitive_column(key):
                continue
            result[str(key)] = _json_safe(item)
        return result
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _is_sensitive_column(name):
    """Return True for fields whose values must never leave the remote host."""
    lower = str(name or "").strip().lower()
    if not lower:
        return True
    if "token" in lower or "password" in lower or "passwd" in lower:
        return True
    if "secret" in lower or "credential" in lower or "cookie" in lower:
        return True
    if lower in ("authorization", "auth", "public_id") or lower.startswith("auth_"):
        return True
    if "ip_hash" in lower or lower in ("ip", "client_ip", "remote_ip", "ip_address"):
        return True
    if lower.endswith("_ip") or lower.startswith("ip_"):
        return True
    # Coarse community coordinates are not necessary for account management;
    # country/city/region remain available without returning map coordinates.
    if lower in ("lat", "lon", "latitude", "longitude"):
        return True
    return False


def _sanitize_value(value):
    """Sanitize JSON-looking metadata as well as ordinary scalar values."""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped and stripped[0] in "[{" and stripped[-1:] in "]}":
            try:
                return _json_safe(json.loads(stripped))
            except (TypeError, ValueError):
                pass
    return _json_safe(value)


def _normalise_timestamp(value):
    """Convert Unix seconds/milliseconds or an ISO timestamp to epoch seconds."""
    if value is None or isinstance(value, bool):
        return None
    number = None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            try:
                parsed = _datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=_datetime.timezone.utc)
                return int(parsed.timestamp())
            except (TypeError, ValueError, OverflowError):
                return None
    if number is None:
        return None
    absolute = abs(number)
    if absolute >= 1e17:       # nanoseconds
        number /= 1_000_000_000.0
    elif absolute >= 1e14:     # microseconds
        number /= 1_000_000.0
    elif absolute >= 1e11:     # milliseconds
        number /= 1_000.0
    try:
        return int(number)
    except (ValueError, OverflowError):
        return None


def _iso_timestamp(epoch):
    if epoch is None:
        return None
    try:
        return _datetime.datetime.fromtimestamp(
            int(epoch), tz=_datetime.timezone.utc
        ).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError, OSError):
        return None


def _flatten_config(data):
    flat = {}
    if not isinstance(data, dict):
        return flat
    for key, value in data.items():
        if isinstance(value, dict) and str(key).lower() not in ("api_keys", "paths"):
            flat.update(_flatten_config(value))
        else:
            flat[str(key).upper()] = value
    return flat


def _path_value(request, containers, aliases):
    for alias in aliases:
        if alias in request and request.get(alias) not in (None, ""):
            return request.get(alias)
    for container_name in containers:
        container = request.get(container_name)
        if not isinstance(container, dict):
            continue
        for alias in aliases:
            if alias in container and container.get(alias) not in (None, ""):
                return container.get(alias)
    return None


def _absolute_path(raw, base):
    path = Path(os.path.expandvars(os.path.expanduser(str(raw))))
    if not path.is_absolute():
        path = base / path
    return Path(os.path.abspath(str(path)))


def resolve_layout(request=None, data_dir=None):
    """Resolve DB/media paths from a request, config.json, env, then defaults."""
    request = dict(request or {})
    raw_base = data_dir
    if raw_base is None:
        raw_base = _path_value(
            request,
            ("paths",),
            ("data_dir", "database_dir", "db_dir", "server_dir", "database_path"),
        )
    if raw_base in (None, ""):
        raw_base = os.getcwd()
    supplied_path = _absolute_path(raw_base, Path(os.getcwd()))

    explicit_file = supplied_path if supplied_path.suffix.lower() == ".db" else None
    base = supplied_path.parent if explicit_file is not None else supplied_path
    # It is convenient to point the panel at a repository checkout instead of
    # its server subdirectory.  Prefer server/ only when it actually looks like
    # the data/config directory and the given root does not.
    server_child = base / "server"
    root_has_data = any((base / name).exists() for name in _DB_FILENAMES.values())
    child_has_data = any((server_child / name).exists() for name in _DB_FILENAMES.values())
    if server_child.is_dir() and not root_has_data and (
        child_has_data or (server_child / "config.json").is_file()
    ):
        base = server_child

    config_raw = _path_value(request, ("paths",), ("config_path", "config_file"))
    config_path = _absolute_path(config_raw, base) if config_raw else base / "config.json"
    config = {}
    if config_path.is_file():
        try:
            with config_path.open("r", encoding="utf-8") as handle:
                config = _flatten_config(json.load(handle))
        except (OSError, ValueError):
            # The server itself falls back when config.json is malformed.  The
            # agent mirrors that behaviour and still permits explicit paths.
            config = {}

    db_paths = {}
    for role, filename in _DB_FILENAMES.items():
        aliases = (
            role + "_db_path",
            role + "_db",
            _DB_CONFIG_KEYS[role],
            _DB_CONFIG_KEYS[role].lower(),
        )
        raw = _path_value(request, ("paths", "db_paths", "database_paths"), aliases)
        if raw in (None, ""):
            raw = config.get(_DB_CONFIG_KEYS[role])
        if raw in (None, ""):
            raw = os.environ.get(_DB_CONFIG_KEYS[role])
        if explicit_file is not None and explicit_file.name == filename:
            raw = str(explicit_file)
        db_paths[role] = _absolute_path(raw or filename, base)

    media_dirs = {}
    for role, dirname in _MEDIA_DEFAULTS.items():
        aliases = (
            role + "_media_dir",
            role + "_image_dir",
            role + "_img_dir",
            _MEDIA_CONFIG_KEYS[role],
            _MEDIA_CONFIG_KEYS[role].lower(),
        )
        raw = _path_value(request, ("paths", "media_dirs"), aliases)
        if raw in (None, ""):
            raw = config.get(_MEDIA_CONFIG_KEYS[role])
        if raw in (None, ""):
            raw = os.environ.get(_MEDIA_CONFIG_KEYS[role])
        media_dirs[role] = _absolute_path(raw or dirname, base)

    return {
        "data_dir": base,
        "config_path": config_path,
        "db": db_paths,
        "media": media_dirs,
        "backup_root": base / "admin_backups",
    }


def _open_database(path, write=False):
    path = Path(path)
    if not path.is_file():
        return None
    try:
        if write:
            connection = sqlite3.connect(
                str(path), timeout=SQLITE_BUSY_TIMEOUT_MS / 1000.0,
                isolation_level=None,
            )
        else:
            uri = path.resolve().as_uri() + "?mode=ro"
            connection = sqlite3.connect(
                uri, uri=True, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000.0,
                isolation_level=None,
            )
            connection.execute("PRAGMA query_only=ON")
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout={}".format(SQLITE_BUSY_TIMEOUT_MS))
        return connection
    except sqlite3.Error as exc:
        raise AgentError(
            "database_open_failed",
            "Could not open {} database".format(path.name),
            str(exc),
        )


def _table_names(connection):
    rows = connection.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [str(row[0]) for row in rows]


def _columns(connection, table):
    try:
        return [str(row[1]) for row in connection.execute(
            "PRAGMA table_info({})".format(_quote_identifier(table))
        ).fetchall()]
    except sqlite3.Error:
        return []


def _pick_column(columns, candidates):
    lookup = {str(column).lower(): str(column) for column in columns}
    for candidate in candidates:
        if candidate.lower() in lookup:
            return lookup[candidate.lower()]
    return None


def _valid_user_id(user_id):
    return bool(_USER_ID_RE.fullmatch(str(user_id or "")))


def _require_user_id(user_id):
    value = str(user_id or "").strip()
    if not _valid_user_id(value):
        raise AgentError(
            "invalid_user_id",
            "User id must contain only A-Z, a-z, 0-9, '-' or '_' (1-128 characters)",
        )
    return value


def _bounded_int(value, default, minimum, maximum, field):
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise AgentError("invalid_request", "{} must be an integer".format(field))
    if parsed < minimum or parsed > maximum:
        raise AgentError(
            "invalid_request",
            "{} must be between {} and {}".format(field, minimum, maximum),
        )
    return parsed


def _new_user(user_id):
    return {
        "id": user_id,
        "_first_activity": None,
        "_last_activity": None,
        "_activity_source": None,
        "_activity_priority": -1,
        "_sources": set(),
        "_counts": {},
        "_profile": {},
        "_profile_versions": {},
        "_metadata": {},
    }


def _user_entry(users, raw_user_id):
    if raw_user_id is None:
        return None
    user_id = str(raw_user_id).strip()
    if not user_id:
        return None
    if user_id not in users:
        users[user_id] = _new_user(user_id)
    return users[user_id]


def _activity_priority(source):
    base = str(source).split(":", 1)[0]
    return _ACTIVITY_PRIORITY.get(base, 0)


def _record_activity(entry, raw_timestamp, source):
    entry["_sources"].add(source)
    timestamp = _normalise_timestamp(raw_timestamp)
    if timestamp is None:
        return
    if entry["_first_activity"] is None or timestamp < entry["_first_activity"]:
        entry["_first_activity"] = timestamp
    priority = _activity_priority(source)
    old = entry["_last_activity"]
    if old is None or timestamp > old or (
        timestamp == old and priority > entry["_activity_priority"]
    ):
        entry["_last_activity"] = timestamp
        entry["_activity_source"] = source
        entry["_activity_priority"] = priority


def _record_metadata(entry, name, value, raw_timestamp, source):
    if value in (None, "") or _is_sensitive_column(name):
        return
    timestamp = _normalise_timestamp(raw_timestamp)
    version = timestamp if timestamp is not None else -1
    canonical = _PROFILE_ALIASES.get(str(name).lower(), str(name).lower())
    clean = _sanitize_value(value)
    known_profile = {
        "device_model", "device_manufacturer", "device_brand",
        "android_version", "android_api", "platform", "app_version",
        "locale", "country", "region", "city", "nickname",
        "network_type", "connection_type",
    }
    target = entry["_profile"] if canonical in known_profile else entry["_metadata"]
    key = canonical
    previous = entry["_profile_versions"].get(key)
    if previous is None or version >= previous[0]:
        target[key] = clean
        entry["_profile_versions"][key] = (version, source)


def _latest_metadata_rows(connection, table, user_col, time_col, metadata_columns):
    if not metadata_columns:
        return []
    selected = [user_col]
    if time_col:
        selected.append(time_col)
    selected.extend(metadata_columns)
    select_sql = ", ".join(_quote_identifier(column) for column in selected)
    user_q = _quote_identifier(user_col)
    table_q = _quote_identifier(table)
    if time_col:
        time_q = _quote_identifier(time_col)
        sql = (
            "SELECT {} FROM {} WHERE {} IS NOT NULL AND {} != '' "
            "ORDER BY {} DESC"
        ).format(select_sql, table_q, user_q, user_q, time_q)
    else:
        sql = (
            "SELECT {} FROM {} WHERE {} IS NOT NULL AND {} != ''"
        ).format(select_sql, table_q, user_q, user_q)
    # Iterate newest-first and retain one metadata-bearing record per user.  A
    # presence store is normally one row/install; the cap prevents a malformed
    # event log from returning an unbounded result to Python.
    rows = connection.execute(sql).fetchmany(100_000)
    seen = set()
    result = []
    for row in rows:
        user_id = str(row[user_col]).strip() if row[user_col] is not None else ""
        if not user_id or user_id in seen:
            continue
        seen.add(user_id)
        result.append(row)
    return result


def _aggregate_table(
    connection,
    users,
    table,
    user_candidates,
    time_candidates,
    source,
    count_key,
    metadata_candidates=(),
):
    columns = _columns(connection, table)
    if not columns:
        return False
    user_col = _pick_column(columns, user_candidates)
    if not user_col:
        return False
    time_col = _pick_column(columns, time_candidates)
    user_q = _quote_identifier(user_col)
    table_q = _quote_identifier(table)
    if time_col:
        time_q = _quote_identifier(time_col)
        sql = (
            "SELECT {u} AS uid, MAX({t}) AS latest, COUNT(*) AS records "
            "FROM {table} WHERE {u} IS NOT NULL AND {u} != '' GROUP BY {u}"
        ).format(u=user_q, t=time_q, table=table_q)
    else:
        sql = (
            "SELECT {u} AS uid, NULL AS latest, COUNT(*) AS records "
            "FROM {table} WHERE {u} IS NOT NULL AND {u} != '' GROUP BY {u}"
        ).format(u=user_q, table=table_q)
    for row in connection.execute(sql):
        entry = _user_entry(users, row["uid"])
        if entry is None:
            continue
        entry["_counts"][count_key] = entry["_counts"].get(count_key, 0) + int(
            row["records"] or 0
        )
        _record_activity(entry, row["latest"], source)

    lookup = {column.lower(): column for column in columns}
    metadata_columns = []
    for name in metadata_candidates:
        actual = lookup.get(str(name).lower())
        if actual and actual not in (user_col, time_col) and not _is_sensitive_column(actual):
            metadata_columns.append(actual)
    for row in _latest_metadata_rows(
        connection, table, user_col, time_col, metadata_columns
    ):
        entry = _user_entry(users, row[user_col])
        if entry is None:
            continue
        timestamp = row[time_col] if time_col else None
        for column in metadata_columns:
            _record_metadata(entry, column, row[column], timestamp, source)
    return True


def _aggregate_presence(connection, users):
    found = False
    for table in _table_names(connection):
        columns = _columns(connection, table)
        user_col = _pick_column(columns, _USER_COLUMNS)
        if not user_col:
            continue
        time_col = _pick_column(columns, _TIME_COLUMNS)
        metadata = [
            column for column in columns
            if column not in (user_col, time_col)
            and str(column).lower() not in ("id", "rowid")
            and not _is_sensitive_column(column)
        ]
        source = "presence:{}".format(table)
        if _aggregate_table(
            connection,
            users,
            table,
            (user_col,),
            (time_col,) if time_col else (),
            source,
            "presence",
            metadata,
        ):
            found = True
    return found


def _schema_summary(layout):
    summary = {}
    for role, path in layout["db"].items():
        item = {
            "available": False,
            "path": str(path),
            "table_names": [],
            "tables": [],
        }
        connection = _open_database(path, write=False)
        if connection is None:
            summary[role] = item
            continue
        try:
            names = _table_names(connection)
            item["available"] = True
            item["table_names"] = names
            item["tables"] = [
                {"name": table, "column_count": len(_columns(connection, table))}
                for table in names
            ]
        except sqlite3.Error as exc:
            item["error"] = str(exc)
        finally:
            connection.close()
        summary[role] = item
    return summary


def _collect_users(layout):
    users = {}
    warnings = []

    specifications = {
        "feedback": (
            (
                "feedback", ("user_pseudo_id",), ("created_at",), "feedback",
                "feedback", ("platform", "app_version", "locale"),
            ),
        ),
        "community": (
            (
                "community_scans", ("user_pseudo_id",), ("created_at",),
                "community_scan", "community_scans",
                ("platform", "app_version", "locale", "country", "region", "city", "nickname"),
            ),
            (
                "community_opinions", ("author_pseudo_id",), ("created_at",),
                "community_opinion", "community_opinions", ("nickname",),
            ),
            (
                "community_scan_views", ("viewer_pseudo_id",), ("created_at",),
                "community_view", "community_views", (),
            ),
            (
                "community_opinion_requests", ("user_pseudo_id",), ("created_at",),
                "community_opinion_request", "opinion_requests", (),
            ),
        ),
        "push": (
            (
                "push_tokens", ("user_pseudo_id",), ("updated_at",),
                "push_registration", "push_registrations", ("platform",),
            ),
        ),
    }

    for role in ("feedback", "community", "push"):
        path = layout["db"][role]
        connection = _open_database(path, write=False)
        if connection is None:
            continue
        try:
            for spec in specifications[role]:
                _aggregate_table(connection, users, *spec)
        except sqlite3.Error as exc:
            warnings.append("{} database could not be fully read: {}".format(role, exc))
        finally:
            connection.close()

    presence = _open_database(layout["db"]["presence"], write=False)
    if presence is not None:
        try:
            _aggregate_presence(presence, users)
        except sqlite3.Error as exc:
            warnings.append("presence database could not be fully read: {}".format(exc))
        finally:
            presence.close()
    return users, warnings


def _public_user(entry, now, active_window_seconds):
    last_activity = entry["_last_activity"]
    first_activity = entry["_first_activity"]
    active = last_activity is not None and last_activity >= now - active_window_seconds
    counts = dict(sorted(entry["_counts"].items()))
    profile = dict(sorted(entry["_profile"].items()))
    metadata = dict(sorted(entry["_metadata"].items()))
    result = {
        "id": entry["id"],
        "user_id": entry["id"],
        "user_pseudo_id": entry["id"],
        "active": bool(active),
        "first_activity_at": first_activity,
        "first_activity_at_iso": _iso_timestamp(first_activity),
        "first_seen_at": first_activity,
        "last_activity_at": last_activity,
        "last_activity_at_iso": _iso_timestamp(last_activity),
        "last_seen_at": last_activity,
        "activity_source": entry["_activity_source"],
        "activity_sources": sorted(entry["_sources"]),
        "source_counts": counts,
        "profile": profile,
    }
    # Frequently displayed fields are duplicated at the top level to keep the
    # GUI simple while profile remains the canonical structured representation.
    for key in (
        "device_model", "device_manufacturer", "device_brand", "android_version",
        "android_api", "platform", "app_version", "locale", "country", "region",
        "city", "nickname", "network_type", "connection_type",
    ):
        result[key] = profile.get(key)
    if metadata:
        result["metadata"] = metadata
    return result


def list_users(
    data_dir=None,
    active_window_seconds=DEFAULT_ACTIVE_WINDOW_SECONDS,
    active_only=False,
    request=None,
):
    """Aggregate pseudonymous installs across all available user stores."""
    if isinstance(data_dir, dict) and request is None:
        request = dict(data_dir)
        data_dir = None
    request = dict(request or {})
    if data_dir is None:
        data_dir = _path_value(
            request, ("paths",),
            ("data_dir", "database_dir", "db_dir", "server_dir", "database_path"),
        )
    if "active_window_seconds" in request:
        active_window_seconds = request.get("active_window_seconds")
    active_window_seconds = _bounded_int(
        active_window_seconds,
        DEFAULT_ACTIVE_WINDOW_SECONDS,
        1,
        MAX_ACTIVE_WINDOW_SECONDS,
        "active_window_seconds",
    )
    if "active_only" in request:
        active_only = bool(request.get("active_only"))
    layout = resolve_layout(request, data_dir)
    users, warnings = _collect_users(layout)
    now = int(time.time())
    all_rows = [_public_user(entry, now, active_window_seconds) for entry in users.values()]
    all_rows.sort(
        key=lambda row: (
            row["last_activity_at"] is not None,
            row["last_activity_at"] if row["last_activity_at"] is not None else -1,
            row["id"],
        ),
        reverse=True,
    )
    active_count = sum(1 for row in all_rows if row["active"])
    rows = [row for row in all_rows if row["active"]] if active_only else all_rows
    return {
        "ok": True,
        "action": "list_users",
        "generated_at": now,
        "active_window_seconds": active_window_seconds,
        "activity_definition": (
            "active means the latest persisted feedback, community, push or "
            "presence event is within active_window_seconds"
        ),
        "users": rows,
        "total": len(rows),
        "discovered_total": len(all_rows),
        "active_count": active_count,
        "schema_summary": _schema_summary(layout),
        "warnings": warnings,
    }


def _scan_ids_from_connection(connection, table, user_col, user_id):
    columns = _columns(connection, table)
    if "scan_id" not in columns or user_col not in columns:
        return set()
    sql = "SELECT DISTINCT scan_id FROM {} WHERE {} = ? AND scan_id IS NOT NULL".format(
        _quote_identifier(table), _quote_identifier(user_col)
    )
    return {
        str(row[0]) for row in connection.execute(sql, (user_id,)).fetchall()
        if row[0] not in (None, "")
    }


def _owned_scan_ids(layout, user_id, connections=None):
    result = set()
    owned_here = connections or {}
    close_after = []
    for role, table in (("feedback", "feedback"), ("community", "community_scans")):
        connection = owned_here.get(role)
        if connection is None:
            connection = _open_database(layout["db"][role], write=False)
            if connection is not None:
                close_after.append(connection)
        if connection is None:
            continue
        try:
            result.update(_scan_ids_from_connection(
                connection, table, "user_pseudo_id", user_id
            ))
        except sqlite3.Error:
            if connections is not None:
                raise
    for connection in close_after:
        connection.close()
    return result


def _safe_columns_for_table(connection, table):
    return [column for column in _columns(connection, table) if not _is_sensitive_column(column)]


def _safe_row(row, columns):
    output = {}
    for column in columns:
        if _is_sensitive_column(column):
            continue
        output[column] = _sanitize_value(row[column])
    return output


def _recent_matching_rows(
    connection,
    table,
    user_id,
    user_col=None,
    scan_ids=None,
    scan_col="scan_id",
    limit=DEFAULT_DETAIL_LIMIT,
):
    columns = _safe_columns_for_table(connection, table)
    all_columns = _columns(connection, table)
    if not columns:
        return []
    time_col = _pick_column(all_columns, _TIME_COLUMNS)
    selected = ", ".join(_quote_identifier(column) for column in columns)
    table_q = _quote_identifier(table)
    order = ""
    if time_col:
        order = " ORDER BY {} DESC".format(_quote_identifier(time_col))
    elif "id" in all_columns:
        order = " ORDER BY {} DESC".format(_quote_identifier("id"))

    collected = {}

    def run(where_sql, params):
        sql = "SELECT {} FROM {} WHERE {}{} LIMIT ?".format(
            selected, table_q, where_sql, order
        )
        for row in connection.execute(sql, tuple(params) + (limit,)).fetchall():
            clean = _safe_row(row, columns)
            key = json.dumps(clean, sort_keys=True, ensure_ascii=False, default=str)
            collected[key] = clean

    if user_col and user_col in all_columns:
        run("{} = ?".format(_quote_identifier(user_col)), (user_id,))
    ids = sorted(set(scan_ids or ()))
    if scan_col in all_columns:
        for chunk in _chunks(ids):
            placeholders = ",".join("?" for _ in chunk)
            run(
                "{} IN ({})".format(_quote_identifier(scan_col), placeholders),
                tuple(chunk),
            )

    rows = list(collected.values())
    if time_col:
        rows.sort(
            key=lambda row: _normalise_timestamp(row.get(time_col)) or -1,
            reverse=True,
        )
    return rows[:limit]


def _detail_records(layout, user_id, scan_ids, limit):
    result = {
        "feedback": [],
        "community_scans": [],
        "community_opinions": [],
        "community_views": [],
        "opinion_requests": [],
        "push_registrations": [],
        "presence": [],
    }
    specs = {
        "feedback": (
            ("feedback", "feedback", "user_pseudo_id", True),
        ),
        "community": (
            ("community_scans", "community_scans", "user_pseudo_id", True),
            ("community_opinions", "community_opinions", "author_pseudo_id", True),
            ("community_scan_views", "community_views", "viewer_pseudo_id", True),
            ("community_opinion_requests", "opinion_requests", "user_pseudo_id", True),
        ),
        "push": (
            ("push_tokens", "push_registrations", "user_pseudo_id", False),
        ),
    }
    for role, role_specs in specs.items():
        connection = _open_database(layout["db"][role], write=False)
        if connection is None:
            continue
        try:
            available = set(_table_names(connection))
            for table, output_key, user_col, scan_linked in role_specs:
                if table not in available:
                    continue
                result[output_key] = _recent_matching_rows(
                    connection,
                    table,
                    user_id,
                    user_col=user_col,
                    scan_ids=scan_ids if scan_linked else (),
                    limit=limit,
                )
        finally:
            connection.close()

    presence = _open_database(layout["db"]["presence"], write=False)
    if presence is not None:
        try:
            rows = []
            for table in _table_names(presence):
                columns = _columns(presence, table)
                user_col = _pick_column(columns, _USER_COLUMNS)
                if not user_col:
                    continue
                for row in _recent_matching_rows(
                    presence, table, user_id, user_col=user_col, limit=limit
                ):
                    row = dict(row)
                    row["source_table"] = table
                    rows.append(row)
            rows.sort(
                key=lambda row: max(
                    [_normalise_timestamp(row.get(name)) or -1 for name in _TIME_COLUMNS]
                ),
                reverse=True,
            )
            result["presence"] = rows[:limit]
        finally:
            presence.close()
    return result


def user_detail(
    data_dir,
    user_id,
    active_window_seconds=DEFAULT_ACTIVE_WINDOW_SECONDS,
    detail_limit=DEFAULT_DETAIL_LIMIT,
    request=None,
):
    """Return safe stored metadata and recent records for one pseudo-id."""
    request = dict(request or {})
    user_id = _require_user_id(user_id)
    active_window_seconds = _bounded_int(
        request.get("active_window_seconds", active_window_seconds),
        DEFAULT_ACTIVE_WINDOW_SECONDS,
        1,
        MAX_ACTIVE_WINDOW_SECONDS,
        "active_window_seconds",
    )
    detail_limit = _bounded_int(
        request.get("detail_limit", request.get("limit", detail_limit)),
        DEFAULT_DETAIL_LIMIT,
        1,
        MAX_DETAIL_LIMIT,
        "detail_limit",
    )
    layout = resolve_layout(request, data_dir)
    users, warnings = _collect_users(layout)
    if user_id not in users:
        raise AgentError("user_not_found", "No persisted data was found for this user id")
    now = int(time.time())
    public = _public_user(users[user_id], now, active_window_seconds)
    scan_ids = _owned_scan_ids(layout, user_id)
    records = _detail_records(layout, user_id, scan_ids, detail_limit)
    return {
        "ok": True,
        "action": "user_detail",
        "generated_at": now,
        "active_window_seconds": active_window_seconds,
        "user": public,
        "owned_scan_ids": sorted(scan_ids),
        "owned_scan_count": len(scan_ids),
        "records": records,
        "warnings": warnings,
    }


def _chunks(values, size=SQLITE_CHUNK_SIZE):
    values = list(values)
    for index in range(0, len(values), size):
        yield values[index:index + size]


def _matching_count(
    connection, table, user_id=None, user_col=None, scan_ids=None, scan_col="scan_id"
):
    columns = _columns(connection, table)
    if not columns:
        return 0
    has_user = bool(user_col and user_col in columns and user_id is not None)
    has_scan = bool(scan_col in columns and scan_ids)
    if not has_user and not has_scan:
        return 0
    table_q = _quote_identifier(table)
    total = 0
    if has_user:
        total += int(connection.execute(
            "SELECT COUNT(*) FROM {} WHERE {} = ?".format(
                table_q, _quote_identifier(user_col)
            ),
            (user_id,),
        ).fetchone()[0])
    if has_scan:
        for chunk in _chunks(sorted(set(scan_ids))):
            placeholders = ",".join("?" for _ in chunk)
            where = "{} IN ({})".format(_quote_identifier(scan_col), placeholders)
            params = list(chunk)
            if has_user:
                user_q = _quote_identifier(user_col)
                where += " AND ({} IS NULL OR {} <> ?)".format(user_q, user_q)
                params.append(user_id)
            total += int(connection.execute(
                "SELECT COUNT(*) FROM {} WHERE {}".format(table_q, where), params
            ).fetchone()[0])
    return total


def _matching_values(
    connection,
    table,
    value_col,
    user_id=None,
    user_col=None,
    scan_ids=None,
    scan_col="scan_id",
):
    columns = _columns(connection, table)
    if value_col not in columns:
        return []
    has_user = bool(user_col and user_col in columns and user_id is not None)
    has_scan = bool(scan_col in columns and scan_ids)
    if not has_user and not has_scan:
        return []
    table_q = _quote_identifier(table)
    value_q = _quote_identifier(value_col)
    values = []
    if has_user:
        rows = connection.execute(
            "SELECT {} FROM {} WHERE {} = ?".format(
                value_q, table_q, _quote_identifier(user_col)
            ),
            (user_id,),
        ).fetchall()
        values.extend(row[0] for row in rows if row[0] not in (None, ""))
    if has_scan:
        for chunk in _chunks(sorted(set(scan_ids))):
            placeholders = ",".join("?" for _ in chunk)
            where = "{} IN ({})".format(_quote_identifier(scan_col), placeholders)
            params = list(chunk)
            if has_user:
                user_q = _quote_identifier(user_col)
                where += " AND ({} IS NULL OR {} <> ?)".format(user_q, user_q)
                params.append(user_id)
            rows = connection.execute(
                "SELECT {} FROM {} WHERE {}".format(value_q, table_q, where), params
            ).fetchall()
            values.extend(row[0] for row in rows if row[0] not in (None, ""))
    return values


def _delete_matching(
    connection, table, user_id=None, user_col=None, scan_ids=None, scan_col="scan_id"
):
    columns = _columns(connection, table)
    if not columns:
        return 0
    has_user = bool(user_col and user_col in columns and user_id is not None)
    has_scan = bool(scan_col in columns and scan_ids)
    if not has_user and not has_scan:
        return 0
    table_q = _quote_identifier(table)
    deleted = 0
    if has_user:
        cursor = connection.execute(
            "DELETE FROM {} WHERE {} = ?".format(table_q, _quote_identifier(user_col)),
            (user_id,),
        )
        deleted += int(cursor.rowcount or 0)
    if has_scan:
        for chunk in _chunks(sorted(set(scan_ids))):
            placeholders = ",".join("?" for _ in chunk)
            cursor = connection.execute(
                "DELETE FROM {} WHERE {} IN ({})".format(
                    table_q, _quote_identifier(scan_col), placeholders
                ),
                tuple(chunk),
            )
            deleted += int(cursor.rowcount or 0)
    return deleted


def _known_delete_specs():
    return {
        "feedback": (
            ("feedback", "feedback", "user_pseudo_id", True, "image_path", "feedback"),
            ("issued_scan_ids", "issued_scan_ids", None, True, None, None),
        ),
        "community": (
            (
                "community_scans", "community_scans", "user_pseudo_id", True,
                "thumb_path", "community",
            ),
            (
                "community_opinions", "community_opinions", "author_pseudo_id", True,
                None, None,
            ),
            (
                "community_scan_views", "community_views", "viewer_pseudo_id", True,
                None, None,
            ),
            (
                "community_opinion_requests", "opinion_requests", "user_pseudo_id", True,
                None, None,
            ),
        ),
        "push": (
            ("push_tokens", "push_registrations", "user_pseudo_id", False, None, None),
        ),
        "labeling": (
            ("candidates", "labeling_candidates", None, True, "image_path", "labeling"),
        ),
    }


def _group_store_counts(flat_counts):
    groups = {
        "feedback": ("feedback", "issued_scan_ids"),
        "community": (
            "community_scans", "community_opinions", "community_views", "opinion_requests"
        ),
        "push": ("push_registrations",),
        "presence": tuple(key for key in flat_counts if key.startswith("presence:")),
        "labeling": ("labeling_candidates",),
    }
    result = {}
    for store, keys in groups.items():
        records = {key: int(flat_counts.get(key, 0)) for key in keys}
        result[store] = {"records": records, "total": sum(records.values())}
    return result


def _preview_with_connections(layout, connections, user_id, scan_ids):
    counts = {}
    media = []
    for role, specifications in _known_delete_specs().items():
        connection = connections.get(role)
        if connection is None:
            for _table, key, _user_col, _linked, _path_col, _media_role in specifications:
                counts[key] = 0
            continue
        for table, key, user_col, scan_linked, path_col, media_role in specifications:
            ids = scan_ids if scan_linked else ()
            counts[key] = _matching_count(
                connection, table, user_id, user_col, ids
            )
            if path_col:
                for raw_path in _matching_values(
                    connection, table, path_col, user_id, user_col, ids
                ):
                    media.append({
                        "role": role,
                        "table": table,
                        "column": path_col,
                        "media_role": media_role,
                        "value": str(raw_path),
                    })

    presence = connections.get("presence")
    if presence is not None:
        for table in _table_names(presence):
            columns = _columns(presence, table)
            user_col = _pick_column(columns, _USER_COLUMNS)
            if not user_col:
                continue
            counts["presence:{}".format(table)] = _matching_count(
                presence, table, user_id, user_col, ()
            )
    per_store = _group_store_counts(counts)
    unique_media = {(item["media_role"], item["value"]) for item in media}
    return {
        "user_id": user_id,
        "owned_scan_count": len(scan_ids),
        "per_store": per_store,
        "total_rows": sum(group["total"] for group in per_store.values()),
        "media_file_references": len(unique_media),
    }, media


def _path_is_within(path, root):
    try:
        return os.path.commonpath([str(path), str(root)]) == str(root)
    except (ValueError, OSError):
        return False


def _safe_media_path(root, raw_value):
    root = Path(os.path.abspath(str(root)))
    raw = Path(str(raw_value))
    candidate = raw if raw.is_absolute() else root / raw
    candidate = Path(os.path.abspath(str(candidate)))
    if candidate == root or not _path_is_within(candidate, root):
        return None
    try:
        resolved_root = root.resolve()
        resolved_candidate = candidate.resolve()
    except OSError:
        return None
    if resolved_candidate == resolved_root or not _path_is_within(
        resolved_candidate, resolved_root
    ):
        # This also rejects a symlink under the media directory that targets a
        # file elsewhere on the host.
        return None
    return candidate


def _media_still_referenced(connections, reference):
    connection = connections.get(reference["role"])
    if connection is None:
        return False
    columns = _columns(connection, reference["table"])
    if reference["column"] not in columns:
        return False
    row = connection.execute(
        "SELECT 1 FROM {} WHERE {} = ? LIMIT 1".format(
            _quote_identifier(reference["table"]),
            _quote_identifier(reference["column"]),
        ),
        (reference["value"],),
    ).fetchone()
    return row is not None


def _remove_media(layout, connections, references):
    grouped = {}
    unsafe = 0
    for reference in references:
        media_root = layout["media"].get(reference["media_role"])
        candidate = _safe_media_path(media_root, reference["value"])
        if candidate is None:
            unsafe += 1
            continue
        grouped.setdefault(str(candidate), []).append(reference)
    deleted = 0
    missing = 0
    retained = 0
    errors = 0
    for path_text, refs in grouped.items():
        if any(_media_still_referenced(connections, ref) for ref in refs):
            retained += 1
            continue
        path = Path(path_text)
        try:
            if path.is_dir() and not path.is_symlink():
                unsafe += 1
                continue
            path.unlink()
            deleted += 1
        except FileNotFoundError:
            missing += 1
        except OSError:
            errors += 1
    return {
        "deleted": deleted,
        "missing": missing,
        "retained_referenced": retained,
        "rejected_unsafe": unsafe,
        "errors": errors,
    }


def _discover_databases(layout, include_all=False):
    discovered = []
    for path in layout["db"].values():
        if Path(path).is_file():
            discovered.append(Path(path))
    if include_all:
        base = Path(layout["data_dir"])
        backup_root = Path(layout["backup_root"])
        if base.is_dir():
            for current_root, directories, files in os.walk(str(base), followlinks=False):
                current = Path(current_root)
                directories[:] = [
                    name for name in directories
                    if not _path_is_within(
                        Path(os.path.abspath(str(current / name))),
                        Path(os.path.abspath(str(backup_root))),
                    )
                ]
                for filename in files:
                    if filename.lower().endswith(".db"):
                        path = current / filename
                        if path.is_file():
                            discovered.append(path)
    unique = {}
    for path in discovered:
        try:
            key = str(path.resolve())
        except OSError:
            key = os.path.abspath(str(path))
        unique[key] = Path(key)
    return [unique[key] for key in sorted(unique)]


def _new_backup_directory(layout, label):
    root = Path(layout["backup_root"])
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AgentError("backup_failed", "Could not create admin_backups", str(exc))
    stamp = _datetime.datetime.now(tz=_datetime.timezone.utc).strftime(
        "%Y%m%dT%H%M%S.%fZ"
    )
    safe_label = re.sub(r"[^A-Za-z0-9_-]+", "-", str(label or "snapshot"))[:48]
    candidate = root / "{}-{}".format(stamp, safe_label)
    suffix = 1
    while candidate.exists():
        candidate = root / "{}-{}-{}".format(stamp, safe_label, suffix)
        suffix += 1
    try:
        candidate.mkdir(mode=0o700)
    except OSError as exc:
        raise AgentError("backup_failed", "Could not create backup snapshot directory", str(exc))
    return candidate


def _backup_destination(source, snapshot_dir, data_dir, used):
    source = Path(source)
    base = Path(data_dir)
    try:
        relative = source.resolve().relative_to(base.resolve())
        parts = [part for part in relative.parts if part not in ("", ".", "..")]
        destination = snapshot_dir.joinpath(*parts)
    except (ValueError, OSError):
        digest = hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:10]
        destination = snapshot_dir / "external" / "{}-{}".format(digest, source.name)
    original = destination
    suffix = 1
    while str(destination) in used or destination.exists():
        destination = original.with_name(
            "{}-{}{}".format(original.stem, suffix, original.suffix)
        )
        suffix += 1
    used.add(str(destination))
    return destination


def _backup_paths(layout, paths, label):
    snapshot_dir = _new_backup_directory(layout, label)
    files = []
    details = []
    used = set()
    for source in paths:
        destination = _backup_destination(
            source, snapshot_dir, layout["data_dir"], used
        )
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            source_connection = _open_database(source, write=False)
            if source_connection is None:
                continue
            destination_connection = sqlite3.connect(str(destination))
            try:
                source_connection.backup(destination_connection)
                destination_connection.commit()
            finally:
                destination_connection.close()
                source_connection.close()
            size = destination.stat().st_size
        except (OSError, sqlite3.Error, AgentError) as exc:
            with contextlib.suppress(OSError):
                destination.unlink()
            raise AgentError(
                "backup_failed",
                "SQLite backup failed for {}".format(Path(source).name),
                str(exc),
            )
        files.append(str(destination))
        details.append({
            "source": str(source),
            "backup": str(destination),
            "size_bytes": int(size),
        })
    return {
        "created": True,
        "backup_dir": str(snapshot_dir),
        "files": files,
        "databases": details,
        "count": len(files),
    }


def backup_all(data_dir=None, request=None):
    """Online-back up every ``*.db`` discovered beneath the selected data dir."""
    if isinstance(data_dir, dict) and request is None:
        request = dict(data_dir)
        data_dir = None
    request = dict(request or {})
    layout = resolve_layout(request, data_dir)
    paths = _discover_databases(layout, include_all=True)
    backup = _backup_paths(layout, paths, request.get("label") or "all")
    return {
        "ok": True,
        "action": "backup_all",
        "backup_dir": backup["backup_dir"],
        "files": backup["files"],
        "count": backup["count"],
        "backup": backup,
    }


def _open_write_connections(layout):
    unique = {}
    by_role = {}
    for role, path in layout["db"].items():
        if not Path(path).is_file():
            by_role[role] = None
            continue
        key = str(Path(path).resolve())
        connection = unique.get(key)
        if connection is None:
            connection = _open_database(path, write=True)
            unique[key] = connection
        by_role[role] = connection
    return unique, by_role


def _begin_all(unique_connections):
    begun = []
    try:
        for key in sorted(unique_connections):
            connection = unique_connections[key]
            connection.execute("BEGIN IMMEDIATE")
            begun.append(connection)
    except sqlite3.Error:
        for connection in begun:
            with contextlib.suppress(sqlite3.Error):
                connection.rollback()
        raise


def _rollback_all(unique_connections):
    for connection in unique_connections.values():
        with contextlib.suppress(sqlite3.Error):
            if connection.in_transaction:
                connection.rollback()


def _commit_all(unique_connections):
    for key in sorted(unique_connections):
        connection = unique_connections[key]
        if connection.in_transaction:
            connection.commit()


def _close_all(unique_connections):
    for connection in unique_connections.values():
        with contextlib.suppress(sqlite3.Error):
            connection.close()


def delete_user(data_dir, user_id, create_backup=True, request=None):
    """Erase one pseudonymous install and every scan-linked data artefact."""
    request = dict(request or {})
    user_id = _require_user_id(user_id)
    if "create_backup" in request:
        create_backup = bool(request.get("create_backup"))
    elif "backup" in request:
        create_backup = bool(request.get("backup"))
    layout = resolve_layout(request, data_dir)

    backup = {
        "created": False,
        "backup_dir": None,
        "files": [],
        "databases": [],
        "count": 0,
    }
    if create_backup:
        backup = _backup_paths(
            layout,
            _discover_databases(layout, include_all=False),
            "delete-{}".format(user_id[:24]),
        )

    unique_connections, connections = _open_write_connections(layout)
    preview = None
    media_references = []
    deleted_counts = {}
    try:
        _begin_all(unique_connections)
        scan_ids = _owned_scan_ids(layout, user_id, connections=connections)
        preview, media_references = _preview_with_connections(
            layout, connections, user_id, scan_ids
        )

        for role, specifications in _known_delete_specs().items():
            connection = connections.get(role)
            if connection is None:
                for _table, key, _user_col, _linked, _path_col, _media_role in specifications:
                    deleted_counts[key] = 0
                continue
            for table, key, user_col, scan_linked, _path_col, _media_role in specifications:
                deleted_counts[key] = _delete_matching(
                    connection,
                    table,
                    user_id,
                    user_col,
                    scan_ids if scan_linked else (),
                )

        presence = connections.get("presence")
        if presence is not None:
            for table in _table_names(presence):
                columns = _columns(presence, table)
                user_col = _pick_column(columns, _USER_COLUMNS)
                if not user_col:
                    continue
                key = "presence:{}".format(table)
                deleted_counts[key] = _delete_matching(
                    presence, table, user_id, user_col, ()
                )

        _commit_all(unique_connections)
        deleted_per_store = _group_store_counts(deleted_counts)
        media_result = _remove_media(layout, connections, media_references)
    except sqlite3.Error as exc:
        _rollback_all(unique_connections)
        raise AgentError("delete_failed", "SQLite deletion transaction failed", str(exc))
    finally:
        _close_all(unique_connections)

    deleted_total = sum(group["total"] for group in deleted_per_store.values())
    return {
        "ok": True,
        "action": "delete_user",
        "user_id": user_id,
        "preview": preview,
        "deleted": {
            "per_store": deleted_per_store,
            "total_rows": deleted_total,
            "media": media_result,
        },
        "deleted_total": deleted_total,
        "backup": backup,
        "backup_dir": backup.get("backup_dir"),
    }


def execute_request(request):
    """Dispatch one decoded request and always return a JSON-compatible dict."""
    if isinstance(request, str):
        try:
            request = json.loads(request)
        except (TypeError, ValueError) as exc:
            return _error_result(None, AgentError("invalid_json", "REQUEST_JSON is invalid", str(exc)))
    if not isinstance(request, dict):
        return _error_result(None, AgentError("invalid_request", "Request must be a JSON object"))
    action = str(request.get("action") or "").strip()
    try:
        if action == "list_users":
            return list_users(request=request)
        if action == "user_detail":
            return user_detail(
                _path_value(
                    request, ("paths",),
                    ("data_dir", "database_dir", "db_dir", "server_dir", "database_path"),
                ),
                request.get("user_id", request.get("user_pseudo_id")),
                request=request,
            )
        if action == "delete_user":
            return delete_user(
                _path_value(
                    request, ("paths",),
                    ("data_dir", "database_dir", "db_dir", "server_dir", "database_path"),
                ),
                request.get("user_id", request.get("user_pseudo_id")),
                request=request,
            )
        if action == "backup_all":
            return backup_all(request=request)
        raise AgentError(
            "unknown_action",
            "action must be one of: list_users, user_detail, delete_user, backup_all",
        )
    except AgentError as exc:
        return _error_result(action, exc)
    except (OSError, sqlite3.Error) as exc:
        return _error_result(
            action,
            AgentError("operation_failed", "The remote database operation failed", str(exc)),
        )
    except Exception as exc:  # Last-resort one-result guarantee for pipe mode.
        return _error_result(
            action,
            AgentError("internal_error", "The remote agent could not complete the request", str(exc)),
        )


def _script_main():
    result = execute_request(REQUEST_JSON)
    sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    sys.stdout.write("\n")


if __name__ == "__main__":
    _script_main()
