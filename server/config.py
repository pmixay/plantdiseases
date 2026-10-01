"""
Server configuration loader.

Replaces the previous ``os.getenv(...)`` scatter-shot with a single
``config.json`` file (located alongside this module) that the server
parses at import time. Environment variables are still consulted as a
fallback so existing CI / systemd deployments continue to work, but the
primary, documented configuration channel is the file.

The config file is JSON so we don't take a YAML dependency. A
``config.example.json`` is committed; the operator copies it to
``config.json`` for non-secret settings. Direct secrets are accepted only in
an explicitly declared test/development environment; production-like
deployments use owner-only secret files.

Lookup order for any key:
  1. ``config.json`` (if present and contains the key)
  2. environment variable with the same name
  3. hard-coded default in this module
"""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).resolve().parent
_CONFIG_PATH = Path(os.getenv("PLANTDISEASES_CONFIG", _CONFIG_DIR / "config.json"))


def _load_file() -> dict[str, Any]:
    if not _CONFIG_PATH.exists():
        logger.info("No server config file; using environment and safe defaults")
        return {}
    try:
        with _CONFIG_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            logger.error("Server config must be a JSON object; using safe defaults")
            return {}
        logger.info("Loaded server config")
        return _flatten(data)
    except Exception:  # noqa: BLE001 - config contents and paths stay out of logs
        logger.error("Failed to parse server config; using safe defaults")
        return {}


def _flatten(data: dict[str, Any]) -> dict[str, Any]:
    """Allow nested sections in JSON. ``server.port`` → ``PORT`` style.

    Top-level sections (``server``, ``ai``, ``plantnet``, ``chat``,
    ``security``) are unwrapped so the file can be human-grouped without
    forcing us to invent a new key namespace."""
    flat: dict[str, Any] = {}
    for k, v in data.items():
        if isinstance(v, dict) and k.lower() != "api_keys":
            flat.update(_flatten(v))
        else:
            flat[k.upper()] = v
    return flat


_FILE_DATA: dict[str, Any] = _load_file()


def get(key: str, default: Any = None, cast: type | None = None) -> Any:
    """Read a config key. File wins over env wins over default.

    Matches the lookup order in the module docstring and ``get_list``:
    config.json first, then the same-named environment variable, then
    the hard-coded default.
    """
    raw: Any = _FILE_DATA.get(key.upper())
    # A blank file value (e.g. the empty ``""`` secrets shipped in
    # config.example.json) counts as "unset" so a real value injected via
    # the environment still wins — otherwise the empty string would shadow
    # it and silently disable env-configured deployments.
    if isinstance(raw, str) and not raw.strip():
        raw = None
    if raw is None:
        raw = os.environ.get(key, None)
    if raw is None:
        return default
    if cast is None or isinstance(raw, cast):
        return raw
    try:
        if cast is bool:
            if isinstance(raw, str):
                normalised = raw.strip().lower()
                if normalised in ("1", "true", "yes", "on"):
                    return True
                if normalised in ("0", "false", "no", "off"):
                    return False
                raise ValueError(f"unrecognised boolean: {raw!r}")
            # JSON booleans were returned by the isinstance check above.  Be
            # deliberately strict for numeric values so a typo such as ``2``
            # cannot silently enable a security or inference switch.
            if raw in (0, 1):
                return bool(raw)
            raise ValueError(f"unrecognised boolean: {raw!r}")
        return cast(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid server configuration value; using safe default")
        return default


def get_str(key: str, default: str = "") -> str:
    val = get(key, default)
    return str(val) if val is not None else default


def get_int(key: str, default: int) -> int:
    return int(get(key, default, cast=int))


def get_float(key: str, default: float) -> float:
    return float(get(key, default, cast=float))


def get_bool(key: str, default: bool) -> bool:
    """Read a boolean. JSON ``true``/``false`` pass through; string values
    (env vars are always strings) are parsed as
    ``1/true/yes/on`` → True and ``0/false/no/off`` → False. Blank or
    unrecognised values fall back to ``default`` rather than silently
    becoming False.
    """
    return bool(get(key, default, cast=bool))


def get_list(key: str, default: list[str] | None = None, sep: str = ",") -> list[str]:
    """Parse a list. JSON arrays are returned as-is; strings are split on ``sep``."""
    raw = _FILE_DATA.get(key.upper())
    # Treat a blank file value as unset so an env-provided list still wins
    # (mirrors ``get``); empty JSON arrays already yield an empty list.
    if isinstance(raw, str) and not raw.strip():
        raw = None
    if raw is None:
        raw = os.environ.get(key, None)
    if raw is None:
        return list(default or [])
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [s.strip() for s in str(raw).split(sep) if s.strip()]


def onnx_intra_op_threads() -> int:
    """Intra-op thread count shared by every ONNX Runtime session.

    Returns ``ONNX_INTRA_OP_THREADS`` (config/env), defaulting to ``0``
    which tells ONNX Runtime to use its own default — the host's physical
    core count. The DINOv2 ViT backbone is dominated by intra-op
    parallelism, so giving a single inference all cores is the biggest
    CPU-latency lever; it is paired with ``INFER_WORKERS=1`` so concurrent
    scans do not oversubscribe. Operators on a shared host can pin it
    lower. Lives here rather than in ``classifier.py`` because both ORT
    sessions (classifier and detector) must agree on the key and default.
    """
    return get_int("ONNX_INTRA_OP_THREADS", 0)


def get_secret(key: str, *, max_bytes: int = 8192) -> str:
    """Load a bounded service secret without making production env-readable.

    ``${key}_FILE`` is the production channel. A direct value remains useful
    for hermetic tests and local development, but production rejects it. Any
    ambiguity or unsafe file shape fails closed without logging a value/path.
    """

    direct = get_str(key, "").strip()
    file_value = get_str(f"{key}_FILE", "").strip()
    environment = get_str("ENVIRONMENT", "").strip().lower()
    if direct and file_value:
        logger.error("Secret has both direct and file configuration; disabling integration")
        return ""
    if direct:
        if environment not in {"test", "development"}:
            logger.error("Direct secret configuration is forbidden in this environment")
            return ""
        if len(direct.encode("utf-8")) > max_bytes or any(
            character.isspace() for character in direct
        ):
            logger.error("Secret value has an invalid shape; disabling integration")
            return ""
        return direct
    if not file_value:
        return ""

    path = Path(file_value)
    descriptor = -1
    try:
        if not path.is_absolute():
            raise ValueError
        before_open = path.lstat()
        if (
            stat.S_ISLNK(before_open.st_mode)
            or not stat.S_ISREG(before_open.st_mode)
        ):
            raise ValueError
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        file_stat = os.fstat(descriptor)
        if (
            file_stat.st_dev != before_open.st_dev
            or file_stat.st_ino != before_open.st_ino
            or not stat.S_ISREG(file_stat.st_mode)
            or stat.S_IMODE(file_stat.st_mode) & 0o077
            or file_stat.st_size <= 0
            or file_stat.st_size > max_bytes
        ):
            raise ValueError
        with os.fdopen(descriptor, "rb", closefd=True) as secret_stream:
            descriptor = -1
            raw = secret_stream.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ValueError
        value = raw.decode("utf-8").strip()
        if not value or any(character.isspace() for character in value):
            raise ValueError
        return value
    except (OSError, UnicodeError, ValueError):
        logger.error("Secret file is unavailable or unsafe; disabling integration")
        return ""
    finally:
        if descriptor >= 0:
            os.close(descriptor)
