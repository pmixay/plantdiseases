"""Write the deterministic account API schema used by clients and handoff."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    # Import only after the caller supplies an explicit validated environment.
    from app.main import app  # noqa: PLC0415

    rendered = json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    args.output.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
