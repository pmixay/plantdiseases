"""Operator tool for community roles: the only way to create an admin.

Run inside the account-api container, with its normal environment:

    python manage_roles.py grant  --nickname Fern --role admin
    python manage_roles.py grant  --nickname Moss --role moderator --scope aroids
    python manage_roles.py revoke --nickname Moss --role moderator --scope aroids
    python manage_roles.py list

Admins then grant and revoke expert and moderator roles from the app.
Output names accounts by nickname only; no identity data is printed.
"""

from __future__ import annotations

import argparse
import json
import sys


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("grant", "revoke"):
        command = commands.add_parser(name)
        command.add_argument("--nickname", required=True)
        command.add_argument("--role", choices=("expert", "moderator", "admin"), required=True)
        command.add_argument("--scope", default="*")
    commands.add_parser("list")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    # Import only after argument parsing so --help works without the environment.
    from app.config import Settings  # noqa: PLC0415
    from app.db.database import Database  # noqa: PLC0415
    from app.db.repository import AccountRepository  # noqa: PLC0415
    from app.services.community_roles import (  # noqa: PLC0415
        CommunityRoleError,
        CommunityRoleService,
    )

    settings = Settings.from_env()
    database = Database(settings.database_url)
    service = CommunityRoleService(repository=AccountRepository(database.session_factory))
    try:
        if args.command == "grant":
            result = service.grant_by_operator(
                nickname=args.nickname, role=args.role, scope=args.scope
            )
            result.pop("actor_id", None)
        elif args.command == "revoke":
            result = {
                "revoked": service.revoke_by_operator(
                    nickname=args.nickname, role=args.role, scope=args.scope
                )
            }
        else:
            result = {
                "items": [
                    {key: value for key, value in item.items() if key != "actor_id"}
                    for item in service.holders()
                ]
            }
    except CommunityRoleError as exc:
        print(json.dumps({"error": exc.code}), file=sys.stderr)
        return 1
    finally:
        database.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
