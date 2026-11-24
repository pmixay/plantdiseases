"""Plain punctuation for text the app displays.

The app's design system uses no em dash, en dash or middle dot in user-facing
text: a plain hyphen (or a comma) takes their place. Static content in this
repo is written that way; this module normalises text that arrives from
elsewhere (LLM replies, Wikipedia extracts) before it is served.
"""

from __future__ import annotations

import re
from typing import Any

_EM = "\u2014"
_EN = "\u2013"
_DOT = "\u00b7"

# Ranges (2-3 cm, %1$d-%2$d) lose the spaces as well as the dash.
_RANGE = re.compile("(\\d)[ \u00a0]*[\u2013\u2014][ \u00a0]*(?=\\d)")
_SPACED_DASH = re.compile("[ \u00a0]+[\u2013\u2014][ \u00a0]+")
_DOT_SEPARATOR = re.compile("[ \u00a0]*\u00b7[ \u00a0]*")


def plain_punctuation(text: str) -> str:
    """Replace long dashes with ``-`` and middle-dot separators with ``, ``."""
    if not text or (_EM not in text and _EN not in text and _DOT not in text):
        return text
    text = _RANGE.sub(r"\1-", text)
    text = _SPACED_DASH.sub(" - ", text)
    text = text.replace(_EM, "-").replace(_EN, "-")
    return _DOT_SEPARATOR.sub(", ", text)


def plain_punctuation_deep(value: Any) -> Any:
    """Apply :func:`plain_punctuation` to every string in a JSON-like value.

    URLs are left untouched: a Wikipedia title can legitimately contain an
    en dash, and rewriting it would break the link.
    """
    if isinstance(value, str):
        if value.startswith(("http://", "https://")):
            return value
        return plain_punctuation(value)
    if isinstance(value, list):
        return [plain_punctuation_deep(v) for v in value]
    if isinstance(value, dict):
        return {k: plain_punctuation_deep(v) for k, v in value.items()}
    return value
