#!/usr/bin/env python3
"""Local-only moderation of shared community scans.

This tool deliberately exposes no HTTP/admin endpoint.  Run it on the server
with the same ``COMMUNITY_DB_PATH`` configuration as the API.  The app has no
Report control on a scan, so a scan flagged to the moderation contact is taken
down with ``remove-scan``; ``list`` and ``resolve`` drain the reports filed by
older app versions.  Content removal requires an additional explicit flag so
an operator cannot delete a scan through a mistyped command.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from .. import community
except ImportError:  # pragma: no cover - direct script execution
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import community


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    queue = commands.add_parser("list", help="list oldest pending reports")
    queue.add_argument("--limit", type=int, default=100)

    resolve = commands.add_parser("resolve", help="resolve one pending report")
    resolve.add_argument("report_id", type=int)
    resolve.add_argument(
        "--action", choices=("dismiss", "remove_content"), required=True
    )
    resolve.add_argument(
        "--confirm-remove",
        action="store_true",
        help="required when --action=remove_content",
    )

    remove = commands.add_parser(
        "remove-scan", help="take one shared scan down by its public id"
    )
    remove.add_argument(
        "public_id",
        help="the scan's public id (cs..., the start of its thumbnail filename)",
    )
    remove.add_argument(
        "--confirm-remove", action="store_true", help="required to delete the scan"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "list":
        reports = community.pending_content_reports(limit=args.limit)
        print(json.dumps({"reports": reports}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "remove-scan":
        if not args.confirm_remove:
            raise SystemExit("--confirm-remove is required to remove community content")
        removed = community.remove_scan(args.public_id)
        print(json.dumps({"removed": removed, "public_id": args.public_id}))
        return 0 if removed else 2

    if args.action == "remove_content" and not args.confirm_remove:
        raise SystemExit("--confirm-remove is required to remove community content")
    resolved = community.moderate_content_report(args.report_id, args.action)
    print(json.dumps({"resolved": resolved, "report_id": args.report_id}))
    return 0 if resolved else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
