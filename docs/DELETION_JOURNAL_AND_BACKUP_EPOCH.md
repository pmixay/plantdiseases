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

## Operator procedure (production on the VDS)

All commands use the production Compose wrapper `/opt/pd-account-prod/deploy/compose.sh` (below: `compose`) and the
service `prod-account-api`. The steps were exercised end to end by the restore drill below, on a disposable stack.

Backup (automated daily by `deploy/production-vds/backup.sh` and its systemd timer):

1. `EPOCH=$(compose exec -T prod-account-api python -m app.deletion_replay begin-epoch)`
2. Dump PostgreSQL with the read-only `account_backup` role.
3. Snapshot the community SQLite database with SQLite's online backup API (not a file copy), written to the
   application data volume and streamed out.
4. Copy `/run/deletion-journal/journal.jsonl` out of the account container.
5. Encrypt all three artifacts, name them `…-$EPOCH-…`, restore-check the dump in a temporary database.

**Retention applies to every copy.** The guarantee holds only while no restorable backup is older than the
journal (30 days). Backups are pruned after 27 days on the host; any off-host copy must be deleted on the same
schedule. A copy older than that must not be restored, because deletions recorded before its journal window would
come back.

Restore (fresh target, same epoch for every store, **service closed until replay succeeds**):

1. Close public access first: point the `/` and `/id/` locations at the maintenance response (or roll nginx back),
   so no restored session can be used before the replay has run.
2. Restore PostgreSQL and the community database from the artifacts of one `$EPOCH`.
   If the live journal volume survived, keep it; otherwise restore the journal snapshot.
3. Start the account and application services (reachable only on loopback while closed).
4. `compose exec -T prod-account-api python -m app.deletion_replay replay --epoch "$EPOCH"`
5. Require `tombstones_failed: 0` and `deletion_jobs_waiting_retry: 0` (exit code 0). Exit 3 means
   incomplete: rerun after the cause (usually the app being down) is fixed.
6. Only then reopen public access.

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

## Restore drill result

Run 2026-10-09 on a disposable mock-provider stack built from branch commit `dc8f1c6`
(real PostgreSQL `pg_dump`/`pg_restore`, SQLite online backup, the journal volume, and the replay
command inside the containers; the live beta and `main` were not touched). **15 of 15 checks passed:**

1. two accounts with public scans; epoch barrier; PostgreSQL dump + community SQLite backup + journal copy;
2. account A deleted after the backup (gone, scan hidden, journaled);
3. the older stores restored: **the deleted account, its public scan and its old session came back**;
4. `replay --epoch` reported 1 deletion queued, 1 tombstone delivered, 0 failures, 0 waiting;
5. A gone again with its old session rejected, B and B's scan untouched;
6. a second replay changed nothing; an unknown epoch was refused with exit code 2.

Not covered by the drill: community media files (thumbnails) are not part of the snapshot, the age
encryption wrapper (exercised separately by `backup-test.sh`), and the real-provider beta stack
(it cannot create test accounts). The drill harness lives with the operator tooling, not in this repository.

## Status of the production stop

Lifted on 2026-10-09 after the owner approved this design. The review asked for the closed-service restore step,
the retention rule for off-host copies, alerting on `journal_unavailable` and failed backups (tracked in plan step 10),
and a backup snapshot path that is not limited by the container's small `/tmp`; all but alerting are done in the
same change.
