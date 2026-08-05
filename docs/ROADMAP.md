# PlantVision — roadmap

Open work only — a checkbox to-do list (the code is the record). Companions:
`PLAN.md` (execution plan — parallel waves, model assignments, ready-to-run
prompts, human review gates), `DATASET_LABELING_PLAN.md` (data loop),
`../DEPLOYMENT.md` (deploy + Android signing/FCM), `server/models/MODEL_CARD.md`
(per-run metrics).

Two independent tracks: the **app release** (`versionName`, currently **1.8**
/ `versionCode` 18 — the fields are already bumped in `app/build.gradle.kts`;
the tag itself is not cut) and the **model** (bundle version, moves per
training run). A 1.8 app tag does not require a new model; a new model can
ship under any app tag.

---

## App 1.8 (in progress)

**Theme: plant-knowledge cards + first-run polish.** The community /
second-opinion rework, the two-safety-gate NAP/feedback flow, the guide
handbook + profile slimming, the animated launch, the server reliability work
(dedicated network executor, LLM truncation/dosage fixes, endpoint pruning),
the toolbar → page-content title move, and RuStore in-app updates (+ minSdk
29) have all landed on `main`.

### Build order

Smallest-first: the two quick items below are hours of work each, and the
offline signal is infrastructure the new card screens want anyway — cheaper
now than retrofitted after.

| # | Task | Size |
|---|---|---|
| 1 | AI-doctor chat subtitle — RU localization | XS |
| 2 | App-wide connectivity signal | S |
| 3 | `GET /api/species-profile` (server; gates 4 and 5) | M |
| 4 | Flowering chart + flowering-conditions card | M |
| 5 | Native-range mini map | L |
| 6 | Interactive onboarding | M |
| — | First-launch intro video — *externally blocked, slot in on arrival* | M |
| — | Tag 1.8 + cut the RuStore build | — |

### Species knowledge cards

One new best-effort endpoint feeds all three cards. LLM-generated, strict
JSON, `reasoning_effort=minimal`, in-memory + disk cache with a long TTL, and
a graceful `available:false` fallback — the same discipline as
`species_common_diseases`. Cards render in the **species detail card**
(`SpeciesDetailActivity`) and, collapsed (tap to expand), in the **scan
result** screen.

**Status: none of this exists yet** — no endpoint server-side, no
`FloweringChartView`, no `WorldMapView`. The endpoint gates both views, so it
goes first; the map is the largest single item in 1.8 (bundled outlines +
hit-testing) and should go last.

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
      landed as the interim step. `OnboardingActivity` is still the
      `ViewPager2` tour — this is a rework, not a tweak.
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

- [ ] **App-wide connectivity signal** — connectivity is checked ad-hoc in
      exactly two places (`CameraFragment`, `ResultActivity`); every other
      screen discovers offline state reactively and silently renders
      blank/empty. A shared observer would drive one consistent "offline —
      showing cached content" affordance instead of blank placeholders. This is
      the ceiling for offline work — surface the state, don't rebuild features
      offline. **Do this before the knowledge cards**: those screens are
      network-fed and will need the same affordance, so building it first
      avoids a retrofit.

### Release

- [ ] **Localize the AI-doctor chat subtitle** — the plant/species name under
      the header still shows the English common name in RU locale
      (`DoctorChatSheet` passes `scientificName ?: species` straight through).
      Smallest item on the board; do it first.
- [x] Bump `versionName` 1.7 → 1.8 (+ `versionCode` 18) — done in
      `app/build.gradle.kts`.
- [ ] Cut the 1.8 tag and the RuStore build.

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

### 5. Progression / photo-compare strip — ✅ already shipped

Landed ahead of the rest of the refactor: `DetailTimelineController` renders
the horizontal scan-photo timeline and `PhotoCompareActivity` gives the
before/after drag-divider compare. Pure visualization over the existing Room
rows + local image files (`filesDir/plant_images`) — fully offline, no new
storage. **Nothing left to do here**; treat it as a free win when scoping the
release.

### 6. Architecture

- Rebuild the Garden list + detail (already split into modular controllers) for
  the richer cards; reuse the existing Room schema and repositories. The only
  new persistence is the per-plant `room` and the checklist step state
  (a small table or a JSON column on the scan). Ship behind the same
  single-module XML-Views + Koin structure.

**Sequencing when this release starts:**

| # | Task | Why here | Size |
|---|---|---|---|
| 1 | Today queue **+** scan-driven care checklists | The payoff, and they share the schedule/journal plumbing — build together, not in sequence | XL |
| 2 | Plant health header | Needs the checklist-adherence signal from #1 | M |
| 3 | Rooms / locations | Independent polish; one nullable column + a grouping header | S |
| — | Progression / photo-compare | Already shipped — see §5 | — |

Rooms could slip to a follow-up without blocking the core hub.

**Status check before starting:** none of §1–§4 exists yet — no `room` column
on `plants`, no checklist-step storage, no health-status derivation, no
aggregated queue. §5 is the only part already built.

---

## Model track

### 2.6.9 — trained (dataset v7, 2026-07-03) + evaluated → NOT shipped

Trained and gate-passed, **but a paired A/B against the shipped 2.6.8 says keep
2.6.8.** The aqua-collapse fix landed as **float32** TFLite (~6.1 MB) — float16
won't *prepare* on LiteRT (`CONV_2D`), dynamic-range INT8 collapsed MobileNetV3
to ≈0.01; float32 is the only build that runs *and* matches the torch student
(aqua realworld 0.76). But on the v4 & v7 supplement eval slices
(`tools/eval_bootstrap.py`, 2026-07-16) **2.6.8 beats 2.6.9 on both** (supported
disease macro-F1 **+0.046** on v4, **+0.021** on v7 — CIs exclude 0, and 2.6.8
wins even on 2.6.9's *own* v7 era) at lower false-fire; 2.6.8→2.6.9 regressed
leaf_scorch (recall 0.89→0.77) + nutrient, gaining only powdery recall. The v7
iteration net-hurt gamma. Numbers in `DATASET_LABELING_PLAN.md`.

- [x] **Train + evaluate 2.6.9 on dataset v7** — done; **do not deploy** (regressed
      vs 2.6.8). The aqua float32 fix carries forward.

### 2.6.10 — trained (dataset v7 + boundary fix, 2026-07-20) → recovered, but CONFOUNDED

The RCA fix (§4, `SOURCE_WEIGHTS['supplement_boundary']` 0.9→0.35) landed and was
validated on v7: boundary draw 5.8%→2.8%, **leaf_scorch recall 0.61→0.75**, disease
macro-F1 **0.834→0.845**, ship gate passed, aqua healthy (float32). The 2.6.9 *recall*
regression is undone and 2.6.10 ≈ 2.6.8 on gamma. Weak-class **precision** (nutrient
P 0.67) is still open → v8.

> **⚠ Corrected 2026-07-24 — this run also silently lost a 2.26k-image source.**
> `pappu54/indoor-plant-disease-dataset-23-classes` (= `kaggle_ornamental`) began
> returning HTTP 403 mid-run; `_kaggle_download` is fail-soft, so it printed one
> `skipping` line and trained on a corpus **2,264 TRAIN images smaller** than
> 2.6.9's (`train_feats` 14,315 → 12,025). The source is **train-only**, so
> `val_realworld` — the ship gate — could not see the loss and passed. It shows up
> on `val_indomain`, which nobody read at the time:
>
> | val_indomain | 2.6.9 (07-03) | 2.6.10 (07-20) |
> |---|---|---|
> | disease macro-F1 | 0.865 | **0.809** |
> | leaf_spot recall | 0.852 | **0.695** |
> | viral_disease recall | 0.952 | **0.792** |
> | head best epoch | 5 | **3** (of a 30-epoch budget) |
>
> Exactly the classes `kaggle_ornamental` supplied 600 images of each. **The
> leaf_scorch recovery is still credible** (that source contributed only 67 scorch
> images, so the boundary fix owns that delta), but the run is **not a clean
> validation of anything else** and must not anchor a paired A/B: 2.6.10 and 2.6.9
> were not trained on the same dataset. See `DATASET_LABELING_PLAN.md` → "Source
> loss".

- [x] **Apply + validate the boundary fix (2.6.10 on v7)** — done; leaf_scorch
      recovery holds, the rest of the run is confounded (above).

### 2.6.11 — next train (dataset v8)

`MODEL_VERSION` bumped to 2.6.11 in the notebook (§1); the boundary fix carries forward.
v8 is a data-cleaning pass (the v7 eval is 71% recycled from v4, ~30% of physiological
positives are sub-0.80 noise, and gray_mold ~39% / nutrient ~31% of disease positives are
agricultural-domain leak) — the path to the still-open nutrient/scorch **precision** and a
trustworthy benchmark.

- [ ] **Build dataset v8 then train 2.6.11** — purge the gray_mold/nutrient ag-leak,
      refresh recycled weak-class positives, floor the sub-0.80 positives to train-only,
      thicken powdery. Paired-bootstrap vs 2.6.8 and 2.6.10. Full plan in
      `DATASET_LABELING_PLAN.md`.

### 2.6.12 — next train (general-plant pivot) — supersedes 2.6.11/v8

**Domain broadening:** extend from houseplants-only to **houseplants + outdoor/garden
ornamentals** (potted, garden, landscape); field/agricultural crops stay excluded. This
supersedes the houseplant-only 2.6.11/v8 *direction* but **folds in its data-cleaning**
(ag-leak purge → now field-crop leak, sub-0.80 noise floor, weak-class refresh). Motivated by
the stalled houseplant-only collection (weak-class targets unmet); ornamentals unlock more
weak-class supply. `MODEL_VERSION` is 2.6.12 (§1); runtime gate is **bundle-derived** (0 server
gating-logic changes).

> **⚠ The pivot as written was a no-op.** Its only model-side change was up-weighting
> `kaggle_ornamental` 0.8→1.0 (and its prior 0.6→0.8) — a source that had already
> returned **zero images** on the previous run and is **permanently gone** (the owner
> deleted it; `datasets/list?user=pappu54` is empty). Re-sourced in 2.6.13 below.

- [ ] **Broaden the data** — ~~up-weight `kaggle_ornamental`~~ (done, §4, but the source
      was dead — re-pointed in 2.6.13); loosen the supplement's **upstream**
      `field_crop`/`orchard_fruit` gate to admit ornamentals; collect garden-ornamental
      scans (weak classes first).
- [ ] **Retrain + refit** — new classifier + `classes.json`, rebuilt `ood_embedding.npz`
      (ornamentals in, field crops out), detector on outdoor leaves, refit calibration.
      Paired-bootstrap vs the last houseplant bundle (2.6.8/2.6.10 — noting 2.6.10's
      corpus caveat above).
- [ ] **Broaden the eval seed** — add an ornamental source to `val_realworld` (cap augmented
      share) so the gate measures the new domain. Details in `DATASET_LABELING_PLAN.md` →
      "Domain (v2.6.12)". **This is now load-bearing, not polish:** the whole ornamental
      leg is train-only today, which is precisely why its disappearance was invisible.

### 2.6.13 — next train (regression fixes, 2026-07-24) — supersedes 2.6.12

Fixes the three defects the 07-20 run exposed. **No new modelling ideas — this is the
run that makes 2.6.12's pivot real and the next A/B trustworthy.**

- [x] **Fail-fast on a truncated corpus** (§2) — `REQUIRED_SOURCE_FLOORS` generalises the
      existing `REQUIRE_SUPPLEMENT` gate to every load-bearing source and raises before
      training. The per-source inventory is persisted (`data_v2/source_inventory.json`)
      and shipped in `model_card.json → source_inventory`, so a future paired A/B can
      **prove** two runs shared a corpus instead of assuming it.
- [x] **Re-source the ornamental leg** (§2) — `KAGGLE_ORNAMENTAL_SLUGS` now pools two
      live, permissively-licensed sets (`itselif/ornamental-plant-diseases-master-dataset`
      CC0, rose + aloe vera; `warcoder/rose-leaves-disease-detection` CC BY 4.0, FlowerNet
      rose). Verified mapping at the shipped `per_class_cap=600`: **healthy 600,
      leaf_spot 600, powdery_mildew 133** ≈ 1,333 of the 2,544 lost.
      **Honest gap:** `viral_disease` **−600** and `powdery_mildew` **−467** are *not*
      replaced — no live ornamental mosaic-virus set was found. Expect viral to stay
      supply-starved until collection or a new source closes it; don't read a viral
      regression vs 2.6.9 as a modelling failure.
- [x] **Revive the MaxLogit OOD fallback** (§6 + `pipeline.py`/`classifier.py`) — score the
      floor over the **plant logits only**. `not_a_plant` is a *trained* class, so the
      all-class max is high on NAP photos by construction; separation was negative in
      **all four** runs on record (−0.939 / −0.403 / −0.199 / −0.104) and the floor
      auto-disabled every time, leaving the AND-gate on the embedding bank alone. Shipped
      as `classes.json → ood.max_logit_scope`; bundles without the key keep
      `all_classes`, so old artefacts are unaffected.
- [x] **Unpin the conformal grid** (§6) — every run selected the grid's **last** alpha
      *and* last lambda (0.20 / 0.50), i.e. the optimum was outside the search. Extended
      to alpha ≤ 0.35, lambda ≤ 1.0; the `coverage ≥ 0.90` guard is unchanged, so sets can
      only shrink where they're allowed to. Should cut mean set size below today's ~1.90/9.
- [ ] **Train 2.6.13 and paired-bootstrap vs 2.6.8** — diff `source_inventory` first.
      Expect `val_indomain` to recover toward 0.86 as the ornamental images return; treat
      `viral_disease` as the known-starved class.

**What to check in the 2.6.13 run log** (each line proves one fix landed — if a line is
missing or reads the old way, the fix did *not* take effect):

| Look for | Expect | Means |
|---|---|---|
| `source floors OK: … all present and above floor` | present | The §2 gate ran. If it *raised* instead, a source is down — read the message, don't bypass it. |
| `Kaggle ornamental per-class:` | `healthy 600, leaf_spot 600, powdery_mildew 133` | Replacement sources loaded. A missing class here is the known unreplaced gap, not a bug. |
| `per-source image counts:` → `kaggle_ornamental` | ~1,333 | Compare against 2,264 (07-03) and 0 (07-20). |
| `MaxLogit OOD floor calibrated [plant_only]` | `sep=` **positive**, floor a number | The OOD fallback is alive for the first time since v2.3. Still negative ⇒ tell me, that would be genuinely surprising. |
| `conformal: selected alpha=… lambda_reg=…` | **not** the last grid value (0.35 / 1.0) | Search converged inside the grid. If it picks the rail again, widen again. |
| `head early stop @ epN (best ep=M)` | `M` well above 3 | Best-epoch 3 was the corpus-loss symptom; recovery toward ~15–19 indicates the training set is whole again. |
| `[final val_indomain] disease_macro_f1` | toward **0.86** (from 0.809) | The headline regression is undone. |

Do **not** treat `viral_disease` recall as a regression signal in this run — its 600
ornamental train images are gone and unreplaced (see `DATASET_LABELING_PLAN.md` item 9).

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
