# Deletion journal and backup epoch

Purpose: restoring an old backup must never bring back an account, or its community
content, that was deleted after that backup was taken. This is the design that the
`production` channel stop in `.github/workflows/android-release.yml` refers to.

## The problem

After a deletion completes, the account database keeps no trace of the account (its rows are
hard-deleted). Restoring an older PostgreSQL backup therefore silently recreates the account,
and restoring an older community database recreates its posts. The community's
deleted-actor marker only protects the community store while that marker itself survives.

## Design

| Piece | What it is | Where |
|---|---|---|
| **Deletion journal** | Append-only JSON-lines file, one record per deletion (`account_id`, `actor_id`, request time). Random internal identifiers only: no provider subject, nickname, email, token or device data. | Own volume `…_deletion_journal` mounted read-write into the account API only. Outside every store that is backed up or restored. |
| **Write-ahead rule** | A deletion is journaled (written + `fsync`) *before* the community tombstone is delivered and *before* the account rows are hard-deleted. If the write fails the job retries (`journal_unavailable`); nothing irreversible has happened. | `AccountService.process_deletion_jobs` |
| **Backup epoch** | A barrier record with an id, appended to the journal immediately before a backup starts. Every snapshot of every store is labelled with that id. | `python -m app.deletion_replay begin-epoch` |
| **Replay** | After restoring snapshots taken at epoch `E`, re-apply every deletion journaled after barrier `E`: queue the deletion in the account database if the account came back, and re-deliver the community tombstone (the community store may have been restored independently). Idempotent. | `python -m app.deletion_replay replay --epoch E` |
| **Retention** | Journal entries and the community deleted-actor marker both live **30 days**, as disclosed in the privacy policy. Backups are pruned after 27 days (`backup-test.sh`, `-mtime +27`), so every restorable backup is younger than the oldest journal entry that could matter to it. A test pins these numbers together. | `JOURNAL_RETENTION`, `DELETED_ACTOR_MARKER_RETENTION_DAYS` |

A marker is erased only when its physical cleanup has finished; while a cleanup job is pending
the marker is still the replay-suppression record and is kept.

## Guarantees and limits

* Any restore of a backup that is not older than the retained journal is repaired by replay,
  provided the journal volume survived or its snapshot from the same epoch is restored first.
* A torn, never-acknowledged last journal line is ignored and discarded on the next append.
  Any other unparsable line is corruption: the journal refuses to continue (fail closed).
* An epoch that is no longer retained is refused (`exit 2`); the operator must then choose
  `--all-retained` deliberately.
* **Not covered:** deletions requested after the newest backup when the journal volume *and*
  all stores are lost together (total host loss). Those deletions cannot be known; keep an
  off-host copy of the journal if that matters.
* Existing restore caveat unchanged: restore into a fresh target, never in place.

## Operator procedure (this deployment)

All commands run from the release directory with the project's Compose wrapper (`compose`).
**These shell steps are a procedure; they have not been exercised on the live stack yet** (the
code and the tests below have). A live restore drill is the remaining acceptance step.

Backup (one epoch for all stores):

1. `EPOCH=$(compose exec -T beta-account-api python -m app.deletion_replay begin-epoch)`
2. Dump PostgreSQL (the encrypted dump + restore check in `backup-test.sh`).
3. Snapshot the community SQLite database with SQLite's online backup API (not a file copy):
   run `sqlite3.connect(path).backup(...)` inside the app container and stream the result out.
4. Copy `/run/deletion-journal/journal.jsonl` out of the account container.
5. Store all three artifacts under the name `…-$EPOCH-…` (encrypted) and record `$EPOCH`.

Restore (fresh target, same epoch for every store):

1. Restore PostgreSQL and the community database from the artifacts of one `$EPOCH`.
   If the live journal volume survived, keep it; otherwise restore the journal snapshot.
2. Start the account and application services.
3. `compose exec -T beta-account-api python -m app.deletion_replay replay --epoch "$EPOCH"`
4. Require `tombstones_failed: 0` and `deletion_jobs_waiting_retry: 0` (exit code 0). Exit 3 means
   incomplete: rerun after the cause (usually the app being down) is fixed.
5. Only then reopen the service.

## What is tested

* Journal file behaviour: privacy of file/directory, idempotence, durability across instances,
  epoch selection, unknown-epoch refusal, torn-tail handling, corruption refusal, pruning.
* Service: journaled *before* the tombstone and hard delete; a journal outage blocks both and
  the job retries to completion; maintenance prunes the journal.
* **Restore simulation (SQLite and real PostgreSQL):** snapshot at an epoch, delete an account,
  restore the older snapshot (the account is back), replay, verify it is gone again while another
  account is untouched, and that a second replay changes nothing.
* **Community restore:** SQLite online-backup snapshot, delete, restore the older database (content
  is back), replay the tombstone, content gone and a re-publish by that actor refused.
* Marker expiry: older than 30 days *and* cleanup finished; younger or still pending is kept.
* Settings: production requires `ACCOUNT_DELETION_JOURNAL_DIR`.

## Status of the production stop

The workflow stop is **still in place**. Lift it in the commit that follows (a) the owner
accepting this design and (b) a passing live restore drill on the deployed stack.
