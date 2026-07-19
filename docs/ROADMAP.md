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

### Release

- [ ] Bump `versionName` 1.7 → 1.8 (+ `versionCode`) and cut the RuStore build.

---

## Garden refactor (next major app work — its own release, post-1.8)

> Turn the Garden from a flat list of saved plants into a **care-management
> hub** — the screen a user opens daily, where every scan feeds an ongoing
> care story instead of being a one-off verdict. The foundations already
> ship: the **"Today" care queue** (due actions across all plants atop the
> Garden tab, one-tap Done, calm "all caught up 🌿" state) and the
> **before/after photo compare** (`PhotoCompareActivity`, long-press in the
> detail photo grid, plus the scan timeline). What's left builds on data
> already in Room (`plants`, `scans`, `journals`, `reminders`); the only new
> persistence is checklist step state and a re-added per-plant `room`. Keep
> XML Views + Koin; no Compose.

- [ ] **1. Scan-driven care checklists** — materialize the care plan's
      treatment steps (`generate_care_plan` → `treatment`) as a per-plant
      trackable checklist: a small Room table (bilingual step text, done
      flag, follow-up date, source scan), with per-step completion feeding
      the Today queue as treatment rows. Completion history later powers
      the health header and a "did the treatment work?" retraining signal.
- [ ] **2. Plant health header** — a healthy / recovering / declining status
      per plant from scan-history trend + care adherence; leads the detail
      screen and tints the Garden card so the list shows where attention is
      needed. Needs #1's adherence signal.
- [ ] **3. Rooms / locations** — nullable `room` column + grouping headers
      in the Garden list; care-relevant later (per-room light/humidity
      defaults, seasonal nudges). NB a free-text `room` column was
      deliberately dropped in `MIGRATION_4_5` — re-adding it is a schema
      v5→v6 migration, this time with the grouped UI that justifies it.

**Sequencing:** #1 next (the payoff; plugs into the shipped queue's
schedule/journal plumbing), then #2 (needs #1). #3 is independent polish
that can slip without blocking the hub.

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
- **Offline is best-effort, not a second app** — the core works offline (Aqua
  scan, guide + prefetched photos, Room garden/history); everything else shows
  the shared `ConnectivityMonitor` offline affordance instead of being rebuilt
  offline. That ceiling is intentional.
- **No Compose rewrite** — keep XML Views, single-module; modernize the guts
  (Koin, Ktor).
- **Stay free + honest** — calibrated, OOD-aware, on-device offline, no account,
  privacy-first.
- **Data loop is local** — collect/judge offline → Kaggle supplement → notebook
  pulls it.
