# My Garden — modernization report

**Status:** design proposal, no code changes yet
**Scope:** the "Garden" tab and the per-plant detail screen
**Reader:** project owner; this doc takes ~10 min to read end-to-end

---

## 0. Corrections to the verbal report

The first verbal draft of this audit got three things wrong. I'm calling
them out at the top so the rest of this document is trustworthy.

| Claim in first draft | Reality | File |
|---|---|---|
| "Plant ID light meter doesn't exist; we should build one (Phase C1)" | **It already exists** — 660 LOC, uses the device's `Sensor.TYPE_LIGHT` ambient light sensor (not camera EV estimation), has a log-scale gauge, species presets (succulent / tropical / fern / general / vegetable) with min/max lux ranges, and is reachable from a `cardLightMeter` tile on the Profile tab. The opportunity is **wiring it into the garden flow**, not building one. | `ui/camera/LightMeterActivity.kt` (660 LOC), `ui/profile/ProfileFragment.kt:61-64` |
| "AI Plant Doctor is a local stub (subtitle says 'ready for server AI later')" | **It's fully wired to `/api/chat` on OpenRouter.** The subtitle is just stale text from before the integration shipped. `sendMessage` in `DoctorChatSheet` hits the backend; `_call_openrouter` in `ai_services.py` runs an OpenRouter chat completion with bounded retry. | `ui/garden/DoctorChatSheet.kt:257-300`, `server/ai_services.py:269-338`, `server/main.py:1533` (`@app.post("/api/chat")`) |
| "Community share is out of scope; doesn't exist" | **A whole community pipeline ships today:** `server/community.py` + `community.db`, `/api/community/scan` (upload), `/api/community/thumb/{file}` (similar-scans thumbnails), `CommunityUploader` repository on Android, profile toggle (`switchCommunity`), and "Delete my contributions" GDPR-style action. | `server/community.py`, `data/repository/CommunityUploader.kt`, `ui/profile/ProfileFragment.kt:84-118`, `util/CommunityPrefs.kt` |

What's actually missing is much more modest than the first report
implied. The audit below reflects the corrected picture.

---

## 1. What currently exists (verified)

### 1.1 Garden tab (`ui/garden/GardenFragment.kt`, 539 LOC)
- List of plant cards, FAB "Add plant", inline name+species dialog, edit + delete context menu, empty state ("Your garden is ready").
- Cards show: a generic green `ic_garden` icon (NOT a photo of the plant), name, species, scan count (pluralized), last-scan relative date ("Today" / "3 days ago").
- All UI programmatic (no XML layout).

### 1.2 Plant detail (`ui/garden/PlantDetailActivity.kt`, 1029 LOC)
Hero block (name + species), 3 stat pills (Scans / Journal / Waterings),
4-button action row (Note / Photo / Water / Reminders), Disease
Progression card with `SparklineView`, Care Plan card with 4 pills,
AI Plant Doctor entry, Timeline / Journal tab toggle. The plant
detail screen is the most polished single surface in the garden.

### 1.3 Care Plan (`ui/common/CarePlanCardView.kt`, 439 LOC)
Server-generated via `/api/careplan`. Returns ~14 fields (placement,
acclimation_days, soil_mix, npk ratio, light_lux range, humidity_band,
temperature_c, treatment_checklist, red_flags, checkpoints, …) but the
UI **only renders 4 pills + a one-line note + a follow-up day count**.
The ~10 v2 fields are deserialised, then dropped.

### 1.4 Journal (`PlantJournalEntity` + `PlantRepository`)
Three entry types: `NOTE`, `PHOTO`, `WATERING`. Each entry: text,
photo_uri, timestamp. Listed reverse-chronologically with long-press
to edit / delete. Empty state: "No reminders or notes yet".

### 1.5 Reminders (`ReminderScheduleSheet` + `ReminderManager` +
`PlantReminderWorker`)
Three types: `watering` / `check` / `fertilize`. `interval_days` +
`hour` + `minute` per type, scheduled with `WorkManager.PeriodicWorkRequest`.
Notifications fire at the chosen HH:MM via a `CoroutineWorker`.
POST_NOTIFICATIONS permission requested on Android 13+.
**Notifications are passive:** no action buttons, no log, no "mark done".

### 1.6 Disease Progression (`ui/garden/SparklineView.kt`, 105 LOC)
Fetches `/api/progression` with the plant's scan history (latest 12,
oldest-first), renders a 5-point severity sparkline + trend chip
("improving" / "worsening" / "stable" / "baseline" — color-coded
green / red / blue / grey). Empty state when scan count ≤ 1.

### 1.7 AI Plant Doctor (`ui/garden/DoctorChatSheet.kt`, 513 LOC)
Bottom sheet chat UI, per-plant history persisted in `ChatMessageEntity`.
Server-wired (`/api/chat`) with structured context (species + latest
scan + recent journal). **Not a stub.**

### 1.8 Light Meter (`ui/camera/LightMeterActivity.kt`, 660 LOC)
Ambient-light-sensor-driven lux meter. Adaptive log gauge, smoothing
(α=0.1), species presets, indoor-range tick marks. Reachable from
Profile tab only.

### 1.9 Community (server + Android)
- Server: `/api/community/scan` accepts uploads, `/api/community/thumb/{file}`
  serves thumbnails, `community.db` is the storage.
- Android: `CommunityUploader` posts opportunistically post-scan;
  Profile toggle opts in/out; "Delete my contributions" action.
- Similar-scans appear on the result screen via `SimilarScansAdapter`.

### 1.10 Data model summary

```
PlantEntity            id, name, species, icon_uri (unused),
                       created_at, scientific_name, common_name
PlantJournalEntity     id, plant_id, type (note|photo|watering),
                       text, photo_uri, timestamp
PlantReminderEntity    id, plant_id, type (watering|check|fertilize),
                       interval_days, hour, minute, enabled
ChatMessageEntity      per-plant doctor-chat history (local)
ScanEntity.plant_id    optional FK so scans roll up to a plant
```

Storage is **purely local**. No sync, no auth, no cloud backup.

### 1.11 Navigation
- Bottom nav: Scan / Gallery / **Garden** / Guide / Profile.
- Garden tab → list → tap card → `PlantDetailActivity`.
- Result screen → "Add to garden" → `CreatePlantSheet` (with PlantNet
  species suggestion) → assigns `ScanEntity.plant_id`.

---

## 2. Genuine gaps vs. competitor benchmarks

Verified against Planta, Greg, PictureThis, Plant Parent (2026 store
listings + recent feature reviews). ★ = best-in-class, ✓ = present,
△ = partial, ✗ = absent.

| Capability | Us | Planta | Greg | PictureThis | Notes |
|---|:---:|:---:|:---:|:---:|---|
| Disease ID | ★ | △ | △ | ✓ | We lead. v2.2 widened the head 7→9. |
| Plant identification | ✓ | ✓ | ✓ | ★ | PlantNet integration via `_species_identify_best_effort`. |
| Light meter | ✓ (sensor) | ★ (camera) | ✗ | ✗ | We have it; Planta's is camera-EV, ours is direct sensor (arguably more accurate). |
| AI Plant Doctor chat | ✓ | △ | ✗ | ✗ | Per-plant chat with scan context wired. |
| Community / similar scans | ✓ | ✓ | △ | ✗ | Similar-scan tile on result screen ships today. |
| Watering reminders (fixed) | ✓ | ✓ | ✓ | △ | Present. |
| **Watering reminders (adaptive)** | ✗ | △ | ★ | ✗ | **Gap.** Greg learns from user's actual logs. |
| **Photo growth timeline** | △ | ✓ | ✓ | △ | Journal lists photos chronologically but no grid view. |
| **Before/after photo compare** | ✗ | ✓ | ✓ | ✗ | **Gap.** Big visual feature. |
| **Notification actions (mark done / snooze)** | ✗ | ✓ | ✓ | ✓ | **Gap.** Notifications are passive today. |
| **Plant card thumbnail** | ✗ | ✓ | ✓ | ✓ | **Easy win.** Card has generic icon today. |
| **Per-room organisation** | ✗ | ✓ | ✓ | △ | **Gap.** No `PlantEntity.room`. |
| **Care-plan depth (placement, NPK, soil)** | ✗ | ✓ | △ | △ | **Gap.** Server returns it, UI drops it. |
| **Cloud sync / multi-device** | ✗ | ✓ | ✓ | ✓ | Bigger work. |
| Onboarding tour | ✓ | ✓ | ✓ | ✓ | `OnboardingActivity` ships, 200 LOC. |
| Bilingual (EN/RU) | ✓ | △ | ✗ | △ | We lead on RU. |

**The seven real gaps**, sized by impact-per-week-of-work:

1. Plant card thumbnail (1 day) — biggest visual win for the lowest effort.
2. Care-plan v2 fields (2–3 days) — server already returns them.
3. Journal types beyond watering (2 days) — fertilising, repotting, pruning, misting.
4. Notification actions + reminder log (1 week) — Room migration + worker rework.
5. Before/after photo compare (1 week) — new view, drag-divider gesture.
6. Per-room organisation (4 days) — Room migration + sticky-header list.
7. Adaptive watering (Greg-style) (1 week) — new event table + recompute logic.

---

## 3. Proposed modernization — three phases

### Phase A — "Make the screens feel alive" (≈ 5 days, Android-only, no migrations)

Goal: in five days of work the user opens the app and the difference
is obvious. No new tables, no server work, no permission requests.

#### A1. Plant card thumbnail
Replace the generic `ic_garden` icon in `GardenFragment.buildPlantCard`
(`ui/garden/GardenFragment.kt:280-288`) with the most-recent scan
image, loaded via Glide with `CircleCrop()` or `RoundedCorners`.
Fallback to the icon when no scan exists yet. **One file, ~30 LOC.**

#### A2. Health pill on the plant card
Pull the latest-scan verdict (`scan.diseaseName` + `scan.confidence` +
`scan.isHealthy`) and render a coloured chip ("Healthy 86 %" /
"Disease — low" / "Needs attention"). Re-uses the colour palette
already in `result_screen` (`R.color.healthy_green`,
`R.color.disease_red`, `R.color.neutral_blue`).

#### A3. "Next action" on the plant card
Compute the soonest of (`watering` / `fertilising` / `check`) reminders
from `PlantReminderEntity.lastFiredAt + interval_days`. No new column
needed if we infer "lastFiredAt" from the most recent matching journal
entry (`WATERING` for the watering reminder, etc.). Surface as
"💧 in 2 days" or "💧 overdue".

#### A4. Disease-progression empty-state CTA
Today the card renders "No history yet" + scan count when scans ≤ 1
and stops there. Replace with: "📸 Take a follow-up scan in 7 days to
compare" + a one-tap "Remind me" button that calls
`ReminderManager.schedule(type=check, interval_days=7)`. Wires the
empty state to the existing reminders pipeline.

#### A5. Journal entry types beyond watering
Today `JournalType` is `NOTE / PHOTO / WATERING` only. Add
`FERTILISING`, `REPOTTING`, `PRUNING`, `MISTING` as new enum values.
No migration needed (the column is a `String` already). Action row on
the plant detail grows from 4 buttons to a horizontally-scrolling chip
strip ("💧 Water · 🌱 Fertilise · 🌿 Prune · 🪴 Repot · 💨 Mist ·
📷 Photo · 📝 Note"). Repository gains `addFertilising`,
`addRepotting`, etc. — mirror `addWatering`.

#### A6. Render the rest of the care plan
`CarePlanCardView.bind` (`ui/common/CarePlanCardView.kt:287-351`)
currently uses 4 server fields (watering_days, fertilise_weeks,
light_tier, humidity_target) + key_action + follow_up_days. Add a
collapsed "Details" expander that shows: placement,
acclimation_days, soil_mix (with NPK if present), light_lux range,
humidity_band, temperature_c range, red_flags (bullets), checkpoints
(bullets), treatment_checklist (bullets). Each field guards against
null since the server may omit any of them.

#### A7. Tap-to-fullscreen photo viewer
Today a journal `PHOTO` entry renders inline but is not tappable.
Add `setOnClickListener` → open a full-screen `Activity` with the
photo loaded by Glide, pinch-to-zoom (`PhotoView` library or
`Matrix`-based custom). Swipe between sibling photo entries.

#### A8. AI Plant Doctor subtitle de-stub
Change "Saved chat, ready for server AI later" (R.string.doctor_stub_subtitle)
to "Ask about this plant" / "Спросить об этом растении". The chat is
live; the subtitle is stale.

#### A9. Light Meter entry from the plant detail
Add a small "Measure light" button to the Care Plan card. Launches
`LightMeterActivity` with the plant's species as a hint, so the lux
gauge shows the matching species band overlaid. On return, suggest:
"Reading 850 lx · tropical band is 500–3 000 lx — looks good".
**No new functionality — pure wiring** of an existing activity into
a new context.

**Phase A total:** roughly 5 days of Android work, no migrations,
no server changes, no new permissions.

### Phase B — "Functional depth" (≈ 2 weeks, Android + 2 Room migrations + tiny server change)

#### B1. Reminder event log + notification actions
New table `PlantReminderEventEntity (id, reminder_id, fired_at,
action: pending|done|snoozed|skipped, done_at)`. Migration v4→v5.

`PlantReminderWorker` writes a row on every fire. The notification
gains two action buttons:
- **"✓ Done"** — broadcast receiver flips the event to `done`,
  auto-creates the matching journal entry (`addWatering` /
  `addFertilising`), and reschedules the next worker.
- **"Snooze 1 day"** — broadcast receiver flips the event to
  `snoozed` and reschedules the worker for +1 day.

Plant detail "Reminders" button surfaces the event log: "Last watered
3 days ago · 5-day streak · 12 watered, 1 skipped this month".

#### B2. Before/after photo compare
New screen `PhotoCompareActivity`. Inputs: two photo URIs + their
timestamps. Renders both at full width with a vertical drag divider
(standard before/after slider — drag the line left/right to reveal
each side). Top bar shows "27 days ago" vs "Today" + the disease
verdict of each scan if applicable.

Entry point: the new "Photos" tab (see B3) — long-press any photo →
"Compare with…" picker.

#### B3. Photos tab (timeline grid)
New tab inside `PlantDetailActivity` between Timeline and Journal:
"Photos". Renders every scan image + every `PHOTO` journal entry as
a chronological grid (3 columns on phone). Each tile shows the
relative date overlay. Tap → A7 viewer. Long-press → compare picker
(B2).

This is the screenshot-of-app screen the competitors all show in
their App Store listings.

#### B4. Per-room organisation
New `PlantEntity.room` nullable column (free-text). Migration v5→v6.
`Add plant` dialog grows a room field with a chip strip of
"recently used" rooms. Garden list group-by-room with sticky headers
("Living room (3) · Bathroom (1) · Kitchen (2)"). Filter chip strip
at the top of the garden tab.

#### B5. Care-plan response → server change (small)
The care plan server emits a `humidity_target` string (e.g. "50–70 %")
but the UI needs the numeric `humidity_band` for the new details
expander (A6). Either:
- Parse the string client-side (regex, fragile), or
- Add a numeric `humidity_min` / `humidity_max` to the response
  (server change, ~10 LOC).

Recommend the server change; it's the right contract.

**Phase B total:** ≈ 2 weeks Android + 2 Room migrations + 1 small
server diff.

### Phase C — "Wow features" (≈ 4–6 weeks, mixed)

Discuss these one at a time; not all need to ship together.

#### C1. Light Meter → garden integration deepen
Once A9 lands, persist a `PlantEntity.measured_lux_recent` (Room
column, nullable, Migration v6→v7). When the user runs the meter
"with this plant in frame", the value is stored on the plant and
shows on the Care Plan card. The server's care plan can then be
re-requested with a measured `light_lux` parameter instead of the
hardcoded `light_tier="bright_indirect"`.

#### C2. Adaptive watering (Greg-style)
Cap the heuristic: after ≥5 reminder events (B1), compute the rolling
mean of (`actual_done_at − expected_fired_at`). If the user is
consistently early (mean < −1 day), suggest "Your plant looks
thirstier than the schedule — shorten interval to N days?". Same in
reverse. **Suggestion only**, never auto-edits — the user keeps
control. Behind a feature flag for the first release.

#### C3. Per-plant progression alerts
Server: extend `/api/progression` to emit `alert: true` when severity
worsens 2 scans in a row. Android: high-priority notification with
"Take a scan" CTA.

#### C4. Cloud sync (the biggest single bet)
Three sub-decisions before any code:
- **Auth model**: anonymous device-ID-only (Greg's approach, no
  sign-up friction) vs full email auth (Planta's approach, supports
  multi-device).
- **Backend**: thin `/api/garden/sync` (last-write-wins on
  `updated_at`) vs a managed BaaS (Supabase / Firebase).
- **Privacy**: which fields sync. Photos and AI Plant Doctor chat
  may carry sensitive content; at minimum the chat history should
  be opt-in and encrypted at rest.

This is the single largest scope item. Recommend deferring until
there's user demand — adoption today is anonymous-install only.

#### C5. Plant care guide deep-link
The app already has a Guide tab (`ui/guide/`, 200 LOC) with bilingual
plant care content. From the plant detail Care Plan card, deep-link
"Learn more about Hortensia care →" into the matching Guide article.
Re-uses existing content; just a new navigation edge.

#### C6. Share plant (photo + verdict) to OS share sheet
Long-press on a scan card → "Share". Render the scan image + verdict
+ confidence into a 1080×1080 PNG (or a vertical 1080×1920 for
Instagram Stories) using `Canvas.drawBitmap` + branded overlay,
hand to `Intent.ACTION_SEND`.

---

## 4. Risks & open decisions

- **Room migrations**: today's pattern is `fallbackToDestructiveMigration`
  (wipes local data on schema bump). Phase A is migration-free. Phase B
  adds 2 migrations (events log + room field). Phase C adds 2–3 more.
  **Each migration costs the user their garden until cloud sync (C4)
  lands.** Recommend writing real `Migration(from, to)` SQL from Phase
  B onward and dropping `fallbackToDestructiveMigration`.
- **Care plan v2 field coverage**: server may omit any field for some
  `disease × species` combos. The A6 expander needs null-guards on
  every field, with the row collapsing when the underlying value is
  null instead of rendering "—".
- **Adaptive watering (C2) drift**: can creep wrong on a chronically-late
  user. Mitigations: ≥5-event minimum, ±1 day per cycle cap, suggestion
  only (never auto-edit), opt-in feature flag for the first release.
- **Photo storage growth**: B3 (photos grid) makes journal photos a
  first-class feature; users will take more. Today photos live in
  `app filesDir/plant_images` with no eviction. After B3 ships, audit
  on-disk size and add a quota / oldest-first eviction policy.

---

## 5. Decisions I need from you before I write any code

1. **Phase prioritisation.** A→B→C in order, or skip ahead to a
   specific wow feature (B2 photo compare or B3 photos grid are the
   most-visible)?
2. **Migration policy.** Keep `fallbackToDestructiveMigration` and
   accept the data loss every release, or write real migrations from
   Phase B onward?
3. **A1 thumbnail source.** Latest-scan image, or let the user pick a
   "cover photo" from the journal? (Cover photo is the Planta /
   Greg pattern.)
4. **Cloud sync (C4) scope.** In this push, or defer until users
   actually exist? Recommend defer.
5. **Visual direction.** I can either keep the current dark-mode-only
   Material 3 palette or push it to a richer "cover photo + soft
   gradient + glass overlay" treatment on the plant detail hero.
   Pick one — they don't compose cleanly.

---

## 6. Smallest possible starting commit

If you want to see this go from report to working code immediately,
the shortest path with the highest visual delta is:

- **A1 (thumbnail) + A2 (health pill) + A8 (subtitle de-stub).**
  Three files, ≈ 60 LOC, no migrations, no server work. The garden
  tab goes from "demo" to "real" in one commit. I can ship it the
  same day you sign off.

Pick a starting point and I'll begin.
