"""Normalize reverse-proxy prefixes without accepting URL-like input."""

from __future__ import annotations


def normalize_root_path(value: str) -> str:
    raw = value.strip()
    if not raw:
        return ""
    if any(character.isspace() or ord(character) < 0x20 for character in raw):
        raise RuntimeError("APP_ROOT_PATH contains invalid characters")
    if "://" in raw or "?" in raw or "#" in raw or "\\" in raw:
        raise RuntimeError("APP_ROOT_PATH must be a URL path")
    segments = raw.strip("/").split("/")
    if not segments or any(segment in {"", ".", ".."} for segment in segments):
        raise RuntimeError("APP_ROOT_PATH must be a normalized URL path")
    return "/" + "/".join(segments)
