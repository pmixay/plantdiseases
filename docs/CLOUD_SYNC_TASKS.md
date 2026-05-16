# Cloud sync — task list

Concrete steps to ship multi-device garden sync. Order is bottom-up:
the data shape and identity decisions gate everything else, so they
come first; UI and migration nice-to-haves come last.

Not in scope here: account recovery via email/SMS, social features
(shared gardens, plant-buddy follows), end-to-end encryption.

---

## Phase 0 — decide before any code

- [ ] Auth model: anonymous device-UUID only (uses the existing
      `TelemetryIds.userPseudoId`) vs full email auth. Anonymous
      means losing the garden if the user uninstalls without a
      "transfer to a new device" hand-off — pick a stance.
- [ ] Backend host: thin `/api/garden/sync` against per-user SQLite
      shards on the existing FastAPI server (cheapest, zero new
      ops) vs managed BaaS (Supabase / Firebase). Anonymous + thin
      backend is the recommended path.
- [ ] Photo storage: skip photos entirely (other devices show a
      placeholder), or stand up an S3-compatible bucket for them.
      Photos are most of the bytes — defer until adoption justifies
      the bill.
- [ ] Conflict policy: confirm last-write-wins per row is OK for
      every table (it is for plants / reminders; journal needs the
      timestamp-tolerant de-dup described in Phase 2).

## Phase 1 — schema preparation (destructive migration window)

- [ ] Migrate every garden table from autoincrement Long PK to
      server-issued UUID String PK. This is the only invasive
      change; doing it inside the existing
      `fallbackToDestructiveMigration` window costs nothing.
      After this commit, every garden schema change needs a real
      `Migration(from, to)` SQL.
- [ ] Add `updated_at: Long` (epoch ms) + `deleted: Boolean = false`
      (tombstone) + `device_id: String` (last writer's
      `userPseudoId`) to:
      - `PlantEntity`
      - `PlantJournalEntity`
      - `PlantReminderEntity`
      - `PlantReminderEventEntity`
      - optionally `ChatMessageEntity` (only when chat-sync ships)
- [ ] Update every repo method that writes to a garden table to
      stamp `updated_at = now` + `device_id = userPseudoId`. Audit
      the call sites: `PlantRepository.savePlant`,
      `addJournalEntry`, `addNote`, `addWatering`,
      `saveReminder`, `recordReminderFire`, `resolveReminderEvent`,
      `updateCoverPhoto`.
- [ ] Soft-delete instead of hard-delete: `deletePlant`,
      `deleteJournalEntry`, `deleteReminders` set `deleted=true`
      + `updated_at=now` instead of removing the row. Add filter
      `WHERE deleted = 0` to every query that today reads the
      table.

## Phase 2 — sync endpoint + protocol

- [ ] Server: per-user SQLite shard under
      `server/user_data/{pseudo_id}.db` with the same garden
      schema. Lazily-created on first sync.
- [ ] Server: `POST /api/garden/sync` endpoint.
      - Request:
        ```
        Headers:
          X-User-Pseudo-Id: <UUIDv4>
          X-Client-Version: <semver>
        Body: {
          "since": <epoch_ms>,   // 0 = full pull
          "plants":    [PlantRow, ...],
          "journal":   [JournalRow, ...],
          "reminders": [ReminderRow, ...],
          "events":    [ReminderEventRow, ...]
        }
        ```
      - Response: `{ "server_now", "plants", "journal", "reminders",
        "events" }` containing rows where `updated_at > since` that
        the requesting device doesn't already have.
- [ ] Server: LWW conflict resolution per row (compare
      `updated_at`; higher wins; ties broken by `device_id`
      string-compare for determinism).
- [ ] Server: journal de-dup on insert. The same logical event
      (e.g. "watered at 09:14:32") can be recorded on two devices
      ~simultaneously; collapse `(plant_id, type, timestamp ± 60 s)`
      pairs to one row.
- [ ] Server: rate-limit per `X-User-Pseudo-Id` so a runaway
      client can't DoS the shard backend.

## Phase 3 — Android sync worker

- [ ] `SyncWorker` (WorkManager) — runs every 30 min when on
      Wi-Fi + on app foreground. Calls `/api/garden/sync` with
      everything updated since the last successful `server_now`
      timestamp; applies the response in a single Room transaction.
- [ ] Persist `last_sync_at` per user in DataStore (or shared
      prefs). On a fresh install with sync ON, calls with
      `since=0` and pulls everything.
- [ ] UI states: "Syncing", "Synced 2 min ago", "Sync failed —
      tap to retry". Surface in a small status pill on the Profile
      tab.

## Phase 4 — user-facing opt-in

- [ ] Profile gains "Back up my garden to the cloud" toggle,
      parallel to the existing community-upload toggle. Default
      OFF.
- [ ] First-run consent sheet on toggle-on: explain what gets
      synced, what stays local (photos, chat unless separately
      opted in), and how to delete cloud data.
- [ ] "Delete my cloud data" button:
      `DELETE /api/garden/{user_pseudo_id}` + confirmation dialog.
      Mirror the existing community-delete flow.

## Phase 5 — photo sync (only when adoption justifies it)

- [ ] S3-compatible bucket + signed-URL read access.
- [ ] On sync, photo URIs in `PlantJournalEntity.photoUri` and
      `PlantEntity.coverPhotoUri` are rewritten:
      - When pushing: upload local `content://` / `file://` bytes,
        store the returned `https://photos.example/.../*.jpg`.
      - When pulling: download to local cache before applying
        the journal row.
- [ ] Storage quota per user (e.g. 500 MB free tier). Surface
      remaining quota on Profile.

## Phase 6 — chat sync (separate opt-in)

- [ ] Plant Doctor chat history is the most-sensitive content the
      app holds (free-form user text). Add a SECOND toggle on
      Profile specifically for chat sync; OFF by default; never
      flipped on by the main sync toggle.
- [ ] Encrypt chat rows at rest server-side (envelope encryption
      with a per-user key derived from `pseudo_id` is the minimum;
      ideally a user-supplied passphrase).

---

## When to start

**Trigger:** > 100 active installs OR > 5 multi-device requests,
whichever comes first. Until then the cost-benefit is upside-down —
2-3 weeks of work to support zero users today, plus an ongoing
ops cost we don't have a billing model for.

Identity decision (Phase 0 item 1) is the single biggest one and
it's easy to get wrong without user data; defer until there's
actual demand.
