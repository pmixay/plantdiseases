# Cloud sync — design note (infrastructure prep, no implementation yet)

**Status:** design only. No code, no schema migrations, no endpoints.
**Reader:** project owner; this is the doc to revisit when cloud sync
becomes a feature instead of a deferred ambition.

---

## 0. Why this doc

Phase A of the garden modernization (see `GARDEN_MODERNIZATION.md`)
intentionally avoided cloud sync. But three small decisions made
during Phase A already pin the shape of any future sync:

1. **Identity is already anonymous-per-install.** `TelemetryIds.userPseudoId`
   (`android/app/src/main/java/com/plantdiseases/app/util/TelemetryIds.kt`)
   stamps a UUIDv4 once per fresh install. Cloud sync should re-use it
   as the device identifier — no new identity layer needed.
2. **Schema is destructive while testing.**
   `Database.kt` ships `version = 1` with
   `fallbackToDestructiveMigration` — local data is expected to wipe on
   every schema change. Cloud sync becomes the first real reason to
   bump that to real `Migration(from, to)` SQL.
3. **No back-compat shims.** The Phase-A pass deleted every "pre-2.2"
   nullable / fallback path. The same discipline carries forward: sync
   should ship as a clean single-version contract, not as a
   migration-bridge with three optional shapes.

The recommendation for when to actually build it is at the bottom
([§ 6](#6-when-to-build-this)).

---

## 1. What gets synced

| Table | Sync? | Reason |
|---|---|---|
| `plants` | yes | The garden itself. Loss-on-uninstall is unacceptable for adopted users. |
| `plant_journal` (notes, waterings, photos) | yes (text), photos lazily | Text is small; photos can be tens of MB per plant. Upload photos with a backoff, fall back to local-only when offline. |
| `plant_reminders` | yes | Otherwise reminders only fire on the device that scheduled them. |
| `chat_messages` (AI Plant Doctor) | **opt-in** | Free-form text users typed; treat as sensitive. Off by default; surface a toggle in Profile. |
| `scans` (the disease-detection rows) | no (cloud), yes (the image + verdict via `/api/community/scan` if community toggle is on) | Scans are the public-good corpus; community upload already handles them. Cloud sync would duplicate. |
| `feedback` (per-scan corrections) | no | Already lives on the server. |

---

## 2. Conflict model

**Last-write-wins on a per-row `updated_at`.** Plants and journal
entries don't conflict in practice — two devices editing the same
plant name within the same minute is rare enough that LWW is fine.
Reminders behave the same.

The one exception is **watering / fertilising logs**. If two devices
both record a watering at roughly the same time, LWW silently drops
one. Mitigation: journal entries are append-only on the client (the
edit dialog only ever updates a single existing row by id), so the
server can de-dupe by `(plant_id, type, timestamp ± 60 s)` on the
write path.

---

## 3. Wire format

```
POST /api/garden/sync
Headers:
  X-User-Pseudo-Id: <UUIDv4>     # TelemetryIds.userPseudoId
  X-Client-Version: <semver>     # BuildConfig.VERSION_NAME
Body (JSON):
{
  "since": 1747396800000,         # epoch ms of last successful pull, 0 = full pull
  "plants":    [PlantRow, ...],   # rows the client has changed since `since`
  "journal":   [JournalRow, ...],
  "reminders": [ReminderRow, ...],
  "chat":      [ChatRow, ...]      # only when chat-sync toggle is on
}

Response 200:
{
  "server_now": 1747396802345,
  "plants":    [PlantRow, ...],   # rows the SERVER has that the client doesn't
  "journal":   [JournalRow, ...],
  "reminders": [ReminderRow, ...],
  "chat":      [ChatRow, ...],
  "conflicts": []                 # always empty under LWW
}
```

Each row carries: its primary key (server-issued UUID, not the
local autoincrement), `device_id` of the last writer, `updated_at`
(epoch ms), `deleted` (bool, tombstone), plus the table-specific fields.

The PK switch from autoincrement → UUID is the single biggest schema
change. Doing it during the destructive-migration window (Phase A,
v=1) costs nothing; deferring it until cloud sync ships means writing
the first real `Migration(1 → 2)` and shimming the autoincrement
columns through the sync layer.

---

## 4. Photo storage

Photo URIs in `PlantJournalEntity.photoUri` are `content://` (picked
from MediaStore) or `file://` (camera capture). Neither survives a
device transfer.

Two options:

A. **Upload-on-demand to our own object store** (S3-compatible).
   `photoUri` on synced rows is rewritten to `https://photos.plantdiseases.example/<plant>/<journal>.jpg`.
   Server-side: ~50 ms upload-and-link, signed URLs for read.
B. **Skip photo sync entirely.** Sync the journal row's text + type,
   leave the photo orphaned on the original device. Other devices show
   a "📷 — photo on another device" placeholder.

Recommend (A) but only when adoption justifies the storage bill.

---

## 5. Privacy & opt-out

- Off by default. Profile gets a new "Backup my garden to the cloud"
  toggle, parallel to the existing community-upload one
  (`switchCommunity`).
- "Delete my cloud data" mirrors the existing
  `deleteCommunityContributions()` flow:
  `DELETE /api/garden/{user_pseudo_id}` with a confirmation dialog.
- AI Plant Doctor chat history has a second, separate toggle —
  ON-sync should NOT also opt the chat history in.
- No third-party analytics in the sync request — `X-User-Pseudo-Id`
  is the only identifier and it's anonymous.

---

## 6. When to build this

**Trigger:** > 100 active installs OR > 5 user requests for
multi-device, whichever comes first. Until then the cost-benefit is
upside-down: 2–3 weeks of work to support zero users today.

When the trigger is met, the build order is:

1. Migrate primary keys to UUIDs. (One destructive migration. The
   last one — after this, real migrations only.)
2. Add `updated_at` + `deleted` + `device_id` columns to plants,
   journal, reminders.
3. Server: `/api/garden/sync` endpoint + per-user SQLite shard
   under `server/user_data/{pseudo_id}.db`. Anonymous, no auth.
4. Android: `SyncWorker` (WorkManager) — push on app foreground +
   pull every 30 min when online.
5. Android: profile toggle + first-run consent sheet.
6. Photo storage decision (§ 4).

**Out of scope for this push.** This document is the durable record
of "what should happen when we get there"; it shouldn't grow code
until it does.
