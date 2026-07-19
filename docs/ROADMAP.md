# PlantVision — roadmap

Open work only — a checkbox to-do list (the code is the record). Companions:
`DATASET_LABELING_PLAN.md` (data loop), `../DEPLOYMENT.md` (deploy + Android
signing/FCM), `server/models/MODEL_CARD.md` (per-run metrics).

Two independent tracks: the **app release** (`versionName`, currently 1.7 →
next 1.8) and the **model** (bundle version, moves per training run). A 1.8
app tag does not require a new model; a new model can ship under any app tag.

---

## App 1.8 (in progress)

**Theme: plant-knowledge cards + first-run polish.** The community /
second-opinion rework, the two-safety-gate NAP/feedback flow, the guide
handbook + profile slimming, the animated launch, and the server reliability
work (dedicated network executor, LLM truncation/dosage fixes, endpoint
pruning) already landed on `claude/community-garden-refactor-m9zwyb`. What's
left before the 1.8 tag:

### Species knowledge cards — build first

One new best-effort endpoint feeds all three cards. LLM-generated, strict
JSON, `reasoning_effort=minimal`, in-memory + disk cache with a long TTL, and
a graceful `available:false` fallback — the same discipline as
`species_common_diseases`. Cards render in the **species detail card** and,
collapsed (tap to expand), in the **scan result** screen.

- [ ] `GET /api/species-profile?name=&locale=` — returns
      `flowering` (12-month intensity `0|1|2` + an indoor note),
      `flowering_conditions` (short "how to make it bloom" tips), and
      `native_range` (per-country `zone` = grows-more/less + a summary).
      One OpenRouter call per (species, locale); never raises.
- [ ] **Flowering-period graphic** — a `FloweringChartView` (BarChartView
      idiom): 12 intensity bars, month initials, current-month marker, and a
      "blooms now / next bloom in <month>" line derived from today's date.
      "Rarely flowers indoors" is a first-class answer, not a faked curve.
- [ ] **Flowering-conditions card** — the bloom-trigger tips beside the chart.
- [ ] **Native-range mini map** — a self-contained `WorldMapView` (bundled
      simplified Natural Earth outlines, ~100–150 KB; no new deps, works
      offline once cached). Two-tone fill for grows-more vs grows-less zones,
      auto-fit to the range, tap a country for a name + zone chip, with
      country chips listed below for accessibility / tiny island ranges.

### First-run

- [ ] **Interactive onboarding** — shrink to a single consent page + in-app
      coach marks (scan-tab pulse on first home visit, a one-time "understand
      your result" hint on the first result). Drop the swipe tour; keep the
      blocking data-consent disclosure. Icon-circle page indicators already
      landed as the interim step.
- [ ] **First-launch intro video** — server-hosted (ffmpeg-compressed to
      ~3 MB, background download, `/api/aqua`-style manifest), played above the
      consent page with the existing Lottie as the offline fallback so
      onboarding never blocks on the network. **Blocked on:** the
      user-generated Veo clip (vertical 9:16 macro monstera, scan-light sweep,
      #16A34A accent, seamless ~8 s loop, no text).

### Offline polish (kept intentionally minimal)

Offline is **best-effort, not a second app**. The core that must work offline
already does: the on-device Aqua scan, the bundled guide text + (now
prefetched) disease photos, and the Room-backed garden/gallery/history.
Online-only features — community, popular carousels, symptom checker, AI doctor
— simply show a graceful "needs connection" state; we deliberately do **not**
replicate them offline (no offline carousel, no offline symptom triage).

Already landed this session: guide/disease example photos are **prefetched**
into Glide's disk cache on a connected launch (they're server-hosted, not
bundled, so they used to show a placeholder offline unless viewed online
first). The one remaining gap worth closing:

- [x] **App-wide connectivity signal** — landed: `ConnectivityMonitor`
      (one process-wide default-network callback, exposed as a `StateFlow`)
      plus a self-driving `OfflineBannerView`. The main shell shows one
      "offline — showing saved content" banner across all tabs (suppressed on
      Scan, which keeps its on-preview overlay, now driven by the same
      signal). This was the ceiling for offline work — the state is surfaced,
      features are not rebuilt offline.

### Release

- [x] **Localize the AI-doctor chat subtitle** — landed: the header name now
      goes through the shared `localizedSpeciesName` rule (RU users get the
      Latin binomial instead of an English-only common name).
- [ ] Bump `versionName` 1.7 → 1.8 (+ `versionCode`) and cut the RuStore build.

---

## Garden refactor (next major app work — its own release, post-1.8)

> Today the Garden is a **flat list of saved plants** — a gallery of what you
> own. The refactor turns it into a **care-management hub**: the screen a user
> opens daily to keep plants alive, where every scan feeds an ongoing care
> story instead of being a one-off verdict. This is the app's biggest remaining
> product lever after the knowledge cards, and large enough to be its own
> release. All of it builds on data the app already persists (Room: `plants`,
> `scans`, `journals`, `reminders`, `chat`) — mostly new UI + aggregation, with
> little new storage. Keep XML Views + Koin; no Compose.

### 1. "Today" care queue — the daily hub

The landing surface of the Garden becomes a prioritized **"what needs doing
today"** list across all plants, not an alphabetical grid:

- Aggregates due **waterings**, **fertilising**, and **open treatment steps**
  (from active scan-driven checklists, below) into one dated queue, most urgent
  first. Each row has a one-tap **Done** that advances the schedule and logs to
  the plant's journal.
- Promotes the existing `PlantReminderWorker` data from background
  notifications to a first-class in-app list — the reminders already exist;
  this makes them actionable in one place.
- Empty state is a genuine reward ("all caught up 🌿"), not a blank screen — the
  emotional core of a daily-return habit **without** a gamified streak
  (per the earlier calm-over-Duolingo decision).

### 2. Scan-driven care checklists

Close the loop between **diagnosis and action**:

- When a scan diagnoses a disease, materialize its care-plan treatment steps
  ("isolate the plant", "remove leaves with >25% spotting", "apply a
  copper-based spray weekly ×3") as a **trackable checklist** attached to that
  plant, with per-step completion + follow-up dates that feed the Today queue.
- The care plan already produces these steps (`generate_care_plan` →
  `treatment`); this persists and tracks them over the recovery window instead
  of showing them once on the result screen.
- Completion history becomes the signal for the health header (below) and,
  later, a real input to the retraining feedback loop ("did the treatment
  work?").

### 3. Plant health header

Each plant's detail **leads with a status**, not a photo grid:

- A single **healthy / recovering / declining** header derived from the plant's
  scan-history trend (confidence + verdict over time) and care adherence, so a
  user sees state at a glance instead of mentally diffing individual scans.
- Drives the Garden card accent (a subtly red-tinted card for a declining
  plant) so the list itself communicates where attention is needed.

### 4. Rooms / locations

Group plants by **room** (living room, balcony, office, …):

- Organizational for large collections, but also **care-relevant**: light and
  humidity differ by room, so a room can seed a new plant's default care plan
  and the Light-meter baseline, and a future "your balcony is entering winter"
  seasonal nudge has a scope to attach to.
- Lightweight: one nullable `room` column on `plants` + a grouping header in the
  list; no new screens required for v1.

### 5. Progression / photo-compare strip

Make **recovery visible**:

- A horizontal timeline of a plant's scan photos with a **before/after
  compare** (slider or side-by-side), so disease progression or recovery reads
  at a glance across weeks.
- Pure visualization — scans are already Room rows pointing at local image
  files (`filesDir/plant_images`), so this works fully offline and adds no
  storage.

### 6. Architecture

- Rebuild the Garden list + detail (already split into modular controllers) for
  the richer cards; reuse the existing Room schema and repositories. The only
  new persistence is the per-plant `room` and the checklist step state
  (a small table or a JSON column on the scan). Ship behind the same
  single-module XML-Views + Koin structure.

**Sequencing when this release starts:** (1) Today queue + care checklists
together (they're the payoff and share the schedule/journal plumbing), then
(2) health header (needs the checklist adherence signal), then (3) rooms +
photo-compare as independent polish. Rooms and the compare strip could slip to
a follow-up without blocking the core hub.

---

## Model track

### 2.6.9 — next train (dataset v7, not yet trained)

`MODEL_VERSION` is bumped to 2.6.9 in the notebook (§1); **2.6.8 stays the
shipped bundle until 2.6.9 trains and clears the gate.** The aqua-collapse fix
already landed in the notebook (§5 ships the near-lossless **float16** TFLite,
~2.9 MB, not the dynamic-range INT8 that collapsed MobileNetV3 to ≈0.01
macro-F1; §6 degeneracy guard) — it deploys on-device once 2.6.9 trains and
the guard is confirmed silent.

- [ ] **Train 2.6.9 on dataset v7** — keep collecting confusion-pair negatives
      **and** weak-class positives (recall floor); re-test the LA-prior trim
      for nutrient/scorch (paired vs 2.6.8). Plan + per-class targets in
      `DATASET_LABELING_PLAN.md` (scorch/nutrient boundary still binding —
      data/precision-bound, not backbone-bound; §3 frozen probe ~0.91).

### Unveiling 2.7 — the leap

> From "a good leaf classifier" to a **holistic, trustworthy plant doctor** —
> the revolution is in **scope + real-world reliability + data scale**, not a
> bigger backbone alone (the probe says features aren't the ceiling).

**Monster ingredients (priority order):**

1. **Real-distribution data flywheel at scale** — fold *thousands* of
   offline-judged production scans (`tools/export_data.py` → vision-agent
   judge) into the next dataset. Bigger reliability lever than any backbone.
2. **Scope expansion** — activate `edema` + `sunburn` (content cards + deferred
   feedback labels already exist) and add **root rot via image + symptom-triage
   fusion** (the #1 houseplant problem, not separable on a leaf photo alone).
   Diagnose *conditions*, not leaf classes.
3. **Multimodal holistic diagnosis** — fuse the vision verdict with species ID
   (PlantNet / BioCLIP) and the symptom Q&A, so species + context condition the
   disease priors.
4. **Capacity jump — `frontier` (DINOv2 ViT-B/14, GPU)** — already scaffolded;
   A/B vs gamma **only if** the §3 probe shows real headroom on the new data,
   then distill back to gamma/aqua so the win ships cheaply on CPU + device.
5. **Better localization** — evolve Stage 1 toward lesion **segmentation**
   (per-leaf, multi-disease, severity from lesion area) instead of a single box.
6. **Safety at scale** — per-class conformal, an explicit **abstain →
   expert/community escalation** path, calibrated severity that never over-treats.

**Prep now (parallel tracks):**

- [ ] Wire `frontier` end-to-end in the notebook + a probe-gate (cheap go/no-go).
- [ ] Stand up the production-scan export→judge loop at volume (the flywheel).
- [ ] Spike multimodal fusion (vision + species + symptom) behind a flag.
- [ ] Design the segmentation-detector path (data + head) — research spike.

---

## Later / maybe

- [ ] **Spider-mite split** from `pests` (deferred — `pests` P already 0.94).
- [ ] **More UI languages** (server content already bilingual).
- [ ] **Expert-human consultation** (community-powered) — ties into 2.7 safety
      escalation and builds on the shipped second-opinion loop.

## Decisions

- **Reliability & safety first** — optimize against a confident wrong diagnosis
  (precision + honest uncertainty + OOD/blur refusal) over headline recall/F1.
- **Data > architecture** — the ceiling is data/precision; spend on the flywheel
  + scope before backbone size.
- **New knowledge cards are best-effort, never blocking** — LLM + cache with a
  graceful `available:false`; the app always renders without them.
- **No new disease classes until the scorch/nutrient boundary is stable** —
  scope expansion (edema / sunburn / root rot) waits for **2.7**.
- **Target store: RuStore** (RU audience; app already bilingual). No Google Play.
- **No Compose rewrite** — keep XML Views, single-module; modernize the guts
  (Koin, Ktor).
- **Stay free + honest** — calibrated, OOD-aware, on-device offline, no account,
  privacy-first.
- **Data loop is local** — collect/judge offline → Kaggle supplement → notebook
  pulls it.
