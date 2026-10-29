"""Durable deletion journal and backup-epoch barriers.

Why this exists
---------------
After an account deletion completes, the account database keeps no trace of the
account. Restoring an *older* backup would therefore silently bring the account
(and, if the community store is restored too, its content) back. This journal
lives on its own volume, outside every store that is backed up or restored, and
records each deletion *before* any irreversible step (the community tombstone
delivery and the hard delete of account rows). After a restore, the journaled
deletions recorded after the restored backup's epoch barrier are replayed.

Guarantees and limits
---------------------
* ``record_deletion`` returns only after the line is written and fsynced. If it
  raises, the caller must not proceed with the irreversible step.
* Records are idempotent per account; replay is idempotent downstream.
* A torn final line (crash in the middle of an append that was never
  acknowledged) is ignored and discarded on the next append. Any other
  unparsable line is corruption and fails closed.
* Entries older than ``JOURNAL_RETENTION`` are pruned. The retention must stay
  longer than the longest-lived restorable backup (``BACKUP_RETENTION_DAYS``).
* It does not protect against loss of the journal volume *and* the stores
  together; a full-host disaster restores to the last backup, which includes a
  journal snapshot taken at the same epoch.

The journal contains random internal identifiers and timestamps only: no
provider subject, nickname, email, token, or device data.
"""

from __future__ import annotations

import fcntl
import json
import os
import secrets
import stat
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

JOURNAL_FILENAME = "journal.jsonl"
LOCK_FILENAME = "journal.lock"
SCHEMA_VERSION = 1

# Matches the "30 days" disclosed in the privacy policy and enforced for the
# community deleted-actor marker. Backups are pruned after 27 days
# (deploy/account-beta-test/backup-test.sh, `-mtime +27`), so every restorable
# backup is younger than the oldest journal entry that could matter to it.
JOURNAL_RETENTION = timedelta(days=30)
BACKUP_RETENTION_DAYS = 27
assert JOURNAL_RETENTION > timedelta(days=BACKUP_RETENTION_DAYS)


class JournalError(RuntimeError):
    """The journal could not be read or written; treat as 'not recorded'."""


class UnknownEpochError(JournalError):
    """The requested epoch barrier is not in the retained journal."""


@dataclass(frozen=True, slots=True)
class JournaledDeletion:
    account_id: uuid.UUID
    actor_id: uuid.UUID
    requested_at: datetime
    recorded_at: datetime


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:  # SQLite returns naive datetimes; the service stores UTC
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_time(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


class DeletionJournal:
    def __init__(self, directory: Path, *, clock=None):
        self.directory = Path(directory)
        self.path = self.directory / JOURNAL_FILENAME
        self._lock_path = self.directory / LOCK_FILENAME
        self._clock = clock or (lambda: datetime.now(UTC))
        self._ensure_directory()

    # -- setup ---------------------------------------------------------------

    def _ensure_directory(self) -> None:
        try:
            self.directory.mkdir(mode=0o700, exist_ok=True)
            info = self.directory.lstat()
        except OSError as exc:
            raise JournalError("deletion journal directory is unavailable") from exc
        if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
            raise JournalError("deletion journal directory must be a private directory")

    @contextmanager
    def _locked(self) -> Iterator[None]:
        try:
            descriptor = os.open(
                self._lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
            )
        except OSError as exc:
            raise JournalError("deletion journal lock is unavailable") from exc
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    # -- reading -------------------------------------------------------------

    def _read(self) -> list[dict]:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise JournalError("deletion journal cannot be read") from exc
        lines = raw.split(b"\n")
        # A well-formed file ends with "\n", so the last split element is empty.
        # A non-empty last element is a torn, never-acknowledged append.
        complete, tail = lines[:-1], lines[-1]
        del tail
        records: list[dict] = []
        for line in complete:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                if record.get("v") != SCHEMA_VERSION or record.get("type") not in {
                    "deletion",
                    "epoch",
                }:
                    raise ValueError
                _parse_time(record["recorded_at"])
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                raise JournalError("deletion journal is corrupt; refusing to continue") from exc
            records.append(record)
        return records

    # -- writing -------------------------------------------------------------

    def _append(self, record: dict) -> None:
        line = (json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n").encode()
        try:
            created = not self.path.exists()
            descriptor = os.open(
                self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600
            )
            try:
                os.fchmod(descriptor, 0o600)
                if not created and os.fstat(descriptor).st_size:
                    existing = self.path.read_bytes()
                    if not existing.endswith(b"\n"):
                        # A torn tail was never acknowledged (its caller saw an
                        # error), so drop it instead of letting it become a
                        # corrupt middle line.
                        os.ftruncate(descriptor, existing.rfind(b"\n") + 1)
                os.write(descriptor, line)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            if created:
                self._fsync_directory()
        except OSError as exc:
            raise JournalError("deletion journal write failed") from exc

    def _fsync_directory(self) -> None:
        descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    # -- public API ----------------------------------------------------------

    def record_deletion(
        self,
        *,
        account_id: uuid.UUID,
        actor_id: uuid.UUID,
        requested_at: datetime,
    ) -> bool:
        """Durably record one account deletion. Idempotent per account.

        Returns True when a new line was written, False when the account was
        already journaled. Raises JournalError if it could not be recorded.
        """
        with self._locked():
            wanted = str(account_id)
            for record in self._read():
                if record["type"] == "deletion" and record["account_id"] == wanted:
                    return False
            self._append(
                {
                    "v": SCHEMA_VERSION,
                    "type": "deletion",
                    "account_id": wanted,
                    "actor_id": str(actor_id),
                    "requested_at": _utc_iso(requested_at),
                    "recorded_at": _utc_iso(self._clock()),
                }
            )
            return True

    def begin_epoch(self) -> str:
        """Append a barrier and return its id; call before snapshotting stores.

        Every deletion journaled *after* this barrier may be missing from the
        snapshots taken after it, so restoring that snapshot set must replay
        the deletions that follow the barrier.
        """
        now = self._clock()
        epoch = f"e{now.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}"
        with self._locked():
            self._append(
                {
                    "v": SCHEMA_VERSION,
                    "type": "epoch",
                    "epoch": epoch,
                    "recorded_at": _utc_iso(now),
                }
            )
        return epoch

    def deletions_since(self, epoch: str | None) -> list[JournaledDeletion]:
        """Deletions recorded after the barrier ``epoch`` (all retained if None).

        Raises UnknownEpochError when the barrier is not retained, because the
        caller then cannot know which deletions the snapshot already contains.
        """
        with self._locked():
            records = self._read()
        start = 0
        if epoch is not None:
            positions = [
                index
                for index, record in enumerate(records)
                if record["type"] == "epoch" and record["epoch"] == epoch
            ]
            if not positions:
                raise UnknownEpochError("epoch barrier is not in the retained journal")
            start = positions[-1] + 1
        return [
            JournaledDeletion(
                account_id=uuid.UUID(record["account_id"]),
                actor_id=uuid.UUID(record["actor_id"]),
                requested_at=_parse_time(record["requested_at"]),
                recorded_at=_parse_time(record["recorded_at"]),
            )
            for record in records[start:]
            if record["type"] == "deletion"
        ]

    def has_epoch(self, epoch: str) -> bool:
        with self._locked():
            return any(
                record["type"] == "epoch" and record["epoch"] == epoch
                for record in self._read()
            )

    def counts(self) -> dict[str, int]:
        with self._locked():
            records = self._read()
        return {
            "deletions": sum(1 for record in records if record["type"] == "deletion"),
            "epochs": sum(1 for record in records if record["type"] == "epoch"),
        }

    def prune(self, *, now: datetime | None = None) -> int:
        """Drop entries older than JOURNAL_RETENTION; returns how many."""
        cutoff = (now or self._clock()) - JOURNAL_RETENTION
        with self._locked():
            records = self._read()
            kept = [
                record for record in records if _parse_time(record["recorded_at"]) >= cutoff
            ]
            removed = len(records) - len(kept)
            if removed == 0:
                return 0
            temporary = self.directory / f".{JOURNAL_FILENAME}.{secrets.token_hex(4)}.tmp"
            try:
                descriptor = os.open(
                    temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
                )
                try:
                    for record in kept:
                        os.write(
                            descriptor,
                            (json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n").encode(),
                        )
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                os.replace(temporary, self.path)
                self._fsync_directory()
            except OSError as exc:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
                raise JournalError("deletion journal prune failed") from exc
            return removed
