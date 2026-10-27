"""Regenerate the checked-in app API contract deterministically."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

os.environ.setdefault("RATE_LIMIT_RPS", "1000")

from main import app  # noqa: E402


def main() -> None:
    target = SERVER_DIR / "openapi.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


if __name__ == "__main__":
    main()
