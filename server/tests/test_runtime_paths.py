from __future__ import annotations

import pytest

from runtime_paths import normalize_root_path


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", ""),
        (" /plantdisease-test/ ", "/plantdisease-test"),
        ("plantdisease-test/id", "/plantdisease-test/id"),
    ],
)
def test_root_path_is_normalized(raw: str, expected: str):
    assert normalize_root_path(raw) == expected


@pytest.mark.parametrize(
    "unsafe",
    [
        "https://example.invalid/path",
        "/path?token=value",
        "/path#fragment",
        "/path//child",
        "/path/../child",
        "/path with space",
        "\\windows\\path",
    ],
)
def test_root_path_refuses_url_or_ambiguous_paths(unsafe: str):
    with pytest.raises(RuntimeError, match="APP_ROOT_PATH"):
        normalize_root_path(unsafe)
