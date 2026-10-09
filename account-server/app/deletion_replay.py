"""Operator tool: backup epochs and deletion replay.

    python -m app.deletion_replay begin-epoch
    python -m app.deletion_replay status
    python -m app.deletion_replay replay --epoch <id> [--all-retained]

``begin-epoch`` appends a barrier to the deletion journal and prints its id. Run
it immediately BEFORE snapshotting any store, and label every snapshot (account
database dump, community database, journal copy) with that id.

``replay`` is run after restoring snapshots taken at that epoch. It re-applies
every deletion journaled after the barrier to the account database and
re-delivers the community tombstone, then finishes the normal deletion jobs. It
is idempotent and prints counts only (never identifiers).

Exit codes: 0 complete, 2 epoch not retained (use --all-retained to replay
everything retained), 3 incomplete (rerun later), 1 any other failure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from app.deletion_journal import DeletionJournal, JournalError, UnknownEpochError

_MAX_PROCESS_ROUNDS = 50


def _journal_from_env() -> DeletionJournal:
    directory = os.environ.get("ACCOUNT_DELETION_JOURNAL_DIR", "").strip()
    if not directory:
        raise SystemExit("ACCOUNT_DELETION_JOURNAL_DIR is not set")
    return DeletionJournal(Path(directory))


def _begin_epoch() -> int:
    print(_journal_from_env().begin_epoch())
    return 0


def _status() -> int:
    print(json.dumps(_journal_from_env().counts(), sort_keys=True))
    return 0


def _replay(epoch: str | None, all_retained: bool) -> int:
    # Imported lazily so begin-epoch/status need nothing beyond the journal.
    from app.config import Settings
    from app.db.database import Database
    from app.security.internal import load_internal_service_token
    from app.service_factory import build_account_service

    config = Settings.from_env()
    if config.deletion_journal_dir is None:
        raise SystemExit("ACCOUNT_DELETION_JOURNAL_DIR is not set")
    journal = DeletionJournal(config.deletion_journal_dir)
    try:
        entries = journal.deletions_since(None if all_retained else epoch)
    except UnknownEpochError:
        print(
            json.dumps({"error": "epoch_not_retained", "hint": "use --all-retained"}),
            file=sys.stderr,
        )
        return 2

    database = Database(config.database_url)
    try:
        service = build_account_service(
            config,
            database_handle=database,
            providers={},
            internal_service_token=load_internal_service_token(config),
        )
        queued = 0
        tombstones_ok = 0
        tombstones_failed = 0
        for entry in entries:
            if service.replay_journaled_deletion(entry):
                queued += 1
            if service.deliver_journaled_tombstone(entry):
                tombstones_ok += 1
            else:
                tombstones_failed += 1
        completed = 0
        retried = 0
        for _ in range(_MAX_PROCESS_ROUNDS):
            outcome = service.process_deletion_jobs(limit=100)
            completed += outcome["completed"]
            retried = outcome["retried"]
            if outcome["completed"] == 0:
                break
    finally:
        database.close()

    summary = {
        "journaled_deletions_replayed": len(entries),
        "accounts_queued_for_deletion": queued,
        "tombstones_delivered": tombstones_ok,
        "tombstones_failed": tombstones_failed,
        "deletion_jobs_completed": completed,
        "deletion_jobs_waiting_retry": retried,
    }
    print(json.dumps(summary, sort_keys=True))
    return 0 if tombstones_failed == 0 and retried == 0 else 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("begin-epoch", help="append a backup-epoch barrier and print its id")
    commands.add_parser("status", help="print journal counts (no identifiers)")
    replay = commands.add_parser("replay", help="replay journaled deletions after a restore")
    group = replay.add_mutually_exclusive_group(required=True)
    group.add_argument("--epoch", help="epoch id the restored snapshots were taken at")
    group.add_argument(
        "--all-retained",
        action="store_true",
        help="replay every retained deletion (use when the epoch is no longer retained)",
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "begin-epoch":
            return _begin_epoch()
        if args.command == "status":
            return _status()
        return _replay(args.epoch, args.all_retained)
    except JournalError as exc:
        print(json.dumps({"error": type(exc).__name__}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
