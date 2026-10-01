"""Configuration parsing regression tests."""

from __future__ import annotations

import pytest

import config


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (True, True),
        (False, False),
        ("1", True),
        ("true", True),
        ("YES", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("NO", False),
        ("off", False),
        (1, True),
        (0, False),
    ],
)
def test_get_bool_accepts_only_documented_boolean_values(
    monkeypatch, raw, expected,
):
    monkeypatch.setattr(config, "_FILE_DATA", {"FLAG": raw})
    monkeypatch.delenv("FLAG", raising=False)
    assert config.get_bool("FLAG", not expected) is expected


@pytest.mark.parametrize("raw", ["truthy", "disabled", 2, [], {}])
def test_get_bool_invalid_value_falls_back_to_default(monkeypatch, raw):
    monkeypatch.setattr(config, "_FILE_DATA", {"FLAG": raw})
    monkeypatch.delenv("FLAG", raising=False)
    assert config.get_bool("FLAG", True) is True
    assert config.get_bool("FLAG", False) is False


def test_get_bool_blank_env_falls_back_to_default(monkeypatch):
    """Empty env strings are malformed booleans, not a silent False."""
    monkeypatch.setattr(config, "_FILE_DATA", {})
    monkeypatch.setenv("FLAG", "")
    assert config.get_bool("FLAG", True) is True
    assert config.get_bool("FLAG", False) is False


def test_generic_get_bool_cast_uses_same_fail_safe_parser(monkeypatch):
    monkeypatch.setattr(config, "_FILE_DATA", {"FLAG": "not-a-boolean"})
    monkeypatch.delenv("FLAG", raising=False)
    assert config.get("FLAG", True, cast=bool) is True
