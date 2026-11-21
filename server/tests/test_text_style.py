"""Plain punctuation for app-facing text: no em/en dashes or middle dots."""

from __future__ import annotations

import json

try:
    from diseases_data import DISEASES_DATABASE
    from text_style import plain_punctuation, plain_punctuation_deep
except ImportError:  # pragma: no cover - package layout
    from server.diseases_data import DISEASES_DATABASE
    from server.text_style import plain_punctuation, plain_punctuation_deep

LONG_PUNCTUATION = ("—", "–", "·")


def test_dashes_and_dots_become_plain():
    assert plain_punctuation("Water when the top 2–3 cm is dry — not before") == (
        "Water when the top 2-3 cm is dry - not before"
    )
    assert plain_punctuation("18 – 24 °C") == "18-24 °C"
    assert plain_punctuation("Tip · Pests · 5 days ago") == "Tip, Pests, 5 days ago"
    assert plain_punctuation("Мучнистая — x") == (
        "Мучнистая - x"
    )
    assert plain_punctuation("unchanged - text") == "unchanged - text"


def test_deep_normalisation_keeps_urls():
    value = {"url": "https://en.wikipedia.org/wiki/A–B", "items": ["a — b", 3]}
    assert plain_punctuation_deep(value) == {
        "url": "https://en.wikipedia.org/wiki/A–B",
        "items": ["a - b", 3],
    }


def test_diagnosis_content_uses_plain_punctuation():
    text = json.dumps(DISEASES_DATABASE, ensure_ascii=False)
    for ch in LONG_PUNCTUATION:
        assert ch not in text, f"diseases_data contains {ch!r}; use '-' or ',' instead"
