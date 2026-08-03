# PlantVision — roadmap

Open work only — a checkbox to-do list (the code is the record). Companions:
`DATASET_LABELING_PLAN.md` (data loop), `../DEPLOYMENT.md` (deploy + Android
signing/FCM), `../server/models/MODEL_CARD.md` (per-run metrics — **currently
stale, see the model track**).

Every status below was verified against the tree, not carried over from a
previous edit.

Three tracks, independently releasable: the **app** (`versionName` 1.8 /
`versionCode` 18, already bumped in `app/build.gradle.kts`; the tag is not
cut), the **model** (bundle version, moves per training run), and the
**admin panel** (a local operator tool, not shipped to users).

---

## App 1.8 — one item left

Everything else in 1.8 has landed: the AI-doctor chat subtitle now resolves
through `localizedSpeciesName`, the shared `ConnectivityObserver` is wired
into every network-fed screen, `GET /api/species-profile` ships all three
fields, and the flowering chart, native-range map and coach-mark onboarding
are all in. The species-info refactor then consolidated every species surface
onto one screen behind one reusable card.

- [ ] **Cut the 1.8 tag and the RuStore build** — human-only. Version fields
      are already set.
- [ ] **First-launch intro video** — *externally blocked, slot in on arrival.*
      Server-hosted (ffmpeg-compressed to ~3 MB, background download,
      `/api/aqua`-style manifest), played above the consent page with the
      existing Lottie as the offline fallback so onboarding never blocks on
      the network. **Blocked on:** the user-generated Veo clip (vertical 9:16
      macro monstera, scan-light sweep, `#16A34A` accent, seamless ~8 s loop,
      no text). Nothing in the tree has started this.

### Loose ends left by 1.8

Small, real, and cheap — worth clearing before the tag or shortly after.

- [ ] **`ResultActivity.hasValidatedInternet()`** duplicates
      `ConnectivityObserver.hasValidatedInternet`. The shared-observer work
      converted `CameraFragment` but not this second ad-hoc site.
- [ ] **Wikipedia attribution on the species card.** The card renders a
      Wikipedia extract with no source line (the old result-screen card had
      one). CC BY-SA makes this a licensing question, not a design one. The
      species screen it links to does carry the article link. Decide: add a
      source line to the card, or accept the link one level down.
- [ ] **No test drives the real map hit-test.** The `1faddd8` rewrite moved
      hit-testing to `Region` + `Path.FillType.EVEN_ODD`; the tests that
      covered the old path were deleted rather than replaced, because
      `MapCanvasView` is private. Make it internal and test a dispatched
      `MotionEvent`.
- [ ] **CI does not cover `admin-panel/`.** The `paths-filter` has `android`
      and `server` only, so its 5 test files and its ruff config never run —
      on the one deliverable that holds production root SSH credentials.
      (`codex/**` now triggers CI; server CI is green — 510 tests.)

---

## Admin panel (`admin-panel/`) — shipped, undocumented

~4.7k lines added 2026-07-30, absent from every doc until now. A loopback-only
FastAPI operator console (binds `127.0.0.1:8787`, opened through a one-time
signed bootstrap URL) that administers the live VPS over SSH with an explicit
host-fingerprint check. It does anonymous-install data management, safe branch
switching and updating, service restarts, health checks, and an SSH console.
Credentials live only in the running process.

- [ ] **Give it a CI job** — see the loose end above. It has tests and a ruff
      config; neither runs.
- [ ] **Decide its support level** — it is an internal tool with production
      root credentials. Either treat it as a first-class deliverable (CI,
      docs, a security review per change) or mark it explicitly unsupported.

---

## Garden refactor — next major app work, its own release

> Today the Garden is a **flat list of saved plants**. The refactor turns it
> into a **care-management hub**: the screen a user opens daily to keep plants
> alive, where every scan feeds an ongoing care story instead of being a
> one-off verdict. This is the biggest remaining product lever. It builds on
> data the app already persists (Room: `plants`, `scans`, `journals`,
> `reminders`, `chat`) — mostly new UI + aggregation. Keep XML Views + Koin;
> no Compose.

**Verified not started:** no `room` column on `plants`, no checklist-step
storage, no health derivation, no aggregated queue. The DB is at `version = 5`
(`Database.kt:447`).

### 1. "Today" care queue — the daily hub

- Aggregates due **waterings**, **fertilising** and **open treatment steps**
  into one dated queue, most urgent first. Each row has a one-tap **Done**
  that advances the schedule and logs to the plant's journal.
- Promotes existing `PlantReminderWorker` data from background notifications
  to a first-class in-app list.
- Empty state is a genuine reward ("all caught up 🌿") — the emotional core of
  a daily-return habit **without** a gamified streak.

### 2. Scan-driven care checklists

- When a scan diagnoses a disease, materialize the care plan's treatment steps
  as a **trackable checklist** attached to that plant, with per-step
  completion + follow-up dates feeding the Today queue.
- `generate_care_plan → treatment` already produces the steps; this persists
  and tracks them across the recovery window.
- Completion history becomes the signal for the health header, and later a
  real input to the retraining loop ("did the treatment work?").

### 3. Plant health header

- A single **healthy / recovering / declining** header per plant, derived from
  scan-history trend (verdict + confidence over time) and care adherence.
- Drives the Garden card accent so the list itself shows where attention is
  needed. Thresholds must live in one tunable place.

### 4. Rooms / locations

- Group plants by room. Organizational, but also **care-relevant**: light and
  humidity differ by room, so a room can seed default care and the light-meter
  baseline. Lightweight — one nullable column + a grouping header.

### 5. Progression / photo-compare — ✅ already shipped

`DetailTimelineController` + `PhotoCompareActivity`. Nothing to do; treat it
as a free win when scoping.

### Sequencing

| # | Task | Why here | Size |
|---|---|---|---|
| 1 | Design pass — schema + aggregation | Nothing about the storage is decided yet | M |
| 2 | Today queue **+** scan-driven checklists | The payoff; they share the schedule/journal plumbing — build together | XL |
| 3 | Plant health header | Needs the adherence signal from #2 | M |
| 4 | Rooms / locations | Independent polish; can slip | S |

**★ Human gate — schema approval, after #1.** Read the proposed migration
before implementation starts. Does the checklist storage fit the real care-plan
`treatment` shape? Is the Today queue derivable from existing tables? Note the
stakes are lower than they look: `Database.kt:443-446` declares the schema
ephemeral while the app is pre-release, so a wrong migration is recoverable —
but it is still the decision everything else builds on.

**★ Human gate — health heuristic, after #3.** The header tells users their
plant is *declining*; a wrong verdict is worse than no verdict. Feed it real
scan histories and sanity-check.

---

## Model track

`MODEL_VERSION` is **2.6.13** (notebook §1). `server/models/` holds only
`.gitkeep` + `MODEL_CARD.md` — **nothing past 2.6.8 is deployed.**

History, closed: **2.6.9** trained and gate-passed but lost a paired A/B
against 2.6.8 (keep 2.6.8). **2.6.10** recovered leaf_scorch via the boundary
fix but silently lost a 2.26k-image source mid-run, so it cannot anchor an
A/B. **2.6.11/v8** was superseded by the 2.6.12 general-plant pivot, whose
data-cleaning it folded in. Details in `DATASET_LABELING_PLAN.md`.

### 2.6.13 — the run that makes the pivot real

Six fixes have landed in the notebook: fail-fast on a truncated corpus
(`REQUIRED_SOURCE_FLOORS` + persisted `source_inventory.json`), re-sourced
ornamental data, a revived MaxLogit OOD floor scored over plant logits only,
an unpinned conformal grid, and — added 2026-08 — **`split_hint` routing** plus
**`subject_domain` passthrough + per-split domain reporting**.

The last two are the eval-integrity pair. `fetch_kaggle_supplement` used to read
only `hn_kind`, so every supplement row the offline pipeline had marked
`train_only` (sub-label-confidence positives — 1,226 of v8b's 3,125, minus the
~700 boundary negs already handled) still drained into the held-out splits at
~52%: they were grading the ship gate **and fitting the shipped temperature +
conformal q̂**. They now route to `supplement_train_only` — train-only, full 0.9
sampler weight, so aqua loses no volume. Separately, `subject_domain` now rides
into the sidecar and every split reports its domain mix, so the widening domain
(houseplants → garden ornamentals → kitchen-garden edibles) is measurable
instead of assumed. Neither the notebook nor the runtime gates on domain values:
a new bucket needs no code change. See `DATASET_LABELING_PLAN.md`.

- [ ] **Train 2.6.13 and paired-bootstrap vs 2.6.8** — expect `val_indomain` to
      recover toward 0.86. Treat `viral_disease` as known-starved (its 600
      ornamental train images are gone and unreplaced) — do **not** read a viral
      regression as a modelling failure. Expect the eval/cal slices to **shrink**
      and train to grow: that is the `split_hint` fix working.
      **The vs-2.6.8 A/B cannot be corpus-verified.** The instruction to "diff
      `source_inventory` first" is unrunnable for that pair — the inventory gate
      ships *in* 2.6.13, so 2.6.8 has none — and the corpora provably differ
      (dead `pappu54` → two replacements; supplement v6 → v8b). Worse, ~1,891
      v8b rows are `carried_from_v5`, i.e. inside 2.6.8's own training set, which
      biases the comparison **toward** 2.6.8. Read the paired number **only** on
      supplement rows tagged `carried_from_v7 | carried_from_v8 | new` (1,234
      rows, ~185 landing in `val_realworld`). The join is mechanical: on-disk
      stems are `supp_<class>_<original stem>`, so eval images map straight back
      to `manifest.csv`.

**What to check in the run log** — each line proves one fix landed:

| Look for | Expect | Means |
|---|---|---|
| `source floors OK: …` | present | The §2 gate ran. If it *raised*, a source is down — read it, don't bypass. |
| `Kaggle ornamental per-class:` | `healthy 600, leaf_spot 600, powdery_mildew 133` | Replacement sources loaded. A missing class is the known gap, not a bug. |
| `MaxLogit OOD floor calibrated [plant_only]` | `sep=` **positive** | The fallback is alive for the first time since v2.3. Still negative ⇒ tell me. |
| `conformal: selected alpha=… lambda_reg=…` | **not** the last grid value | Search converged inside the grid. |
| `head early stop @ epN (best ep=M)` | `M` well above 3 | Best-epoch 3 was the corpus-loss symptom. |
| `[final val_indomain] disease_macro_f1` | toward **0.86** | The headline regression is undone. |
| `Kaggle supplement routing:` | all three of `supplement` / `supplement_train_only` / `supplement_boundary` non-zero | `split_hint` is being honoured. Only two keys ⇒ the column is gone and the run raised (or you flipped `STRICT_SUPPLEMENT_SPLIT_HINT`). |
| `Kaggle supplement subject_domain:` | the buckets you collected | Domain rides into the sidecars. All `unspecified` ⇒ the manifest column is missing. |
| `per-split subject_domain:` → `val_realworld` | a non-trivial non-`unspecified` share | **The ship gate can finally see the broadened domain.** If ornamental/edible rows are ~0 here, no number in this repo says whether the pivot worked. |

### Still open from the 2.6.12 pivot

- [ ] **Loosen the supplement's upstream `field_crop`/`orchard_fruit` gate** to
      admit ornamentals, and collect garden-ornamental scans (weak classes
      first). The `kaggle_ornamental` up-weight already landed and the dead
      source was re-pointed in 2.6.13.
- [ ] **Broaden the eval seed** — **half-closed.** v8b's domain re-judge put 190
      garden ornamentals into `source=supplement`, which *is* eval-eligible, so
      the gate now carries some; and the per-split domain report (2.6.13) finally
      says how many. Still open: every *dedicated* ornamental source
      (`kaggle_ornamental`) remains train-only, and kitchen-garden edibles have
      no eval-eligible supply at all. Collect the new domain into the
      **supplement**, not into a train-only slug — that routing is why the
      2.26k-image loss was invisible.
- [ ] **Refresh `MODEL_CARD.md`** — it still says "v2.6.9 (in training — NOT
      YET TRAINED)" and contradicts this file on aqua export precision. Do not
      import its numbers until it is rewritten.

### Unveiling 2.7 — the leap

> From "a good leaf classifier" to a **holistic, trustworthy plant doctor** —
> the revolution is in **scope + real-world reliability + data scale**, not a
> bigger backbone (the probe says features aren't the ceiling).

1. **Real-distribution data flywheel at scale** — fold thousands of
   offline-judged production scans into the next dataset. Bigger reliability
   lever than any backbone.
2. **Scope expansion** — activate `edema` + `sunburn` (content cards and
   deferred feedback labels already exist) and add **root rot via image +
   symptom-triage fusion**. Diagnose *conditions*, not leaf classes.
3. **Multimodal holistic diagnosis** — fuse the vision verdict with species ID
   and the symptom Q&A so species + context condition the disease priors.
4. **Capacity jump — `frontier` (DINOv2 ViT-B/14, GPU)** — A/B vs gamma **only
   if** the §3 probe shows real headroom, then distill back so the win ships
   cheaply on CPU + device.
5. **Better localization** — evolve Stage 1 toward lesion **segmentation**
   (severity from lesion area) instead of a single box.
6. **Safety at scale** — per-class conformal, an explicit **abstain →
   expert/community escalation** path, calibrated severity that never
   over-treats.

**Prep now (parallel tracks):**

- [ ] Wire `frontier` end-to-end in the notebook + a probe-gate (cheap go/no-go).
- [ ] Stand up the production-scan export→judge loop at volume (the flywheel).
- [ ] Spike multimodal fusion (vision + species + symptom) behind a flag.
- [ ] Design the segmentation-detector path (data + head) — research spike.

---

## Documentation debt

Found by a full doc-vs-code sweep; none of it blocks a release.

- [ ] **`CLAUDE.md`** — says "two independent deliverables" and omits
      `admin-panel/` entirely; its server module map misses `telemetry.py`,
      `plant_photos.py`, `popular_plants.py`, `countries.py`; CI trigger
      branches are wrong (`master` is not one, `work/**` is undocumented).
- [ ] **`README.ru.md` has diverged from `README.md`** — missing the admin
      panel section, 12 of 24 API rows, the project-tree line and two
      Documentation bullets. It also carries a project-website block the
      English README never gained.
- [ ] **`DEPLOYMENT.md`** — tells operators to set the server URL "through
      Profile → Server URL" (no such setting; it is compile-time only), and
      lists an onboarding entry point that the coach-mark rework removed. Its
      config table is missing the three `TELEMETRY_*` keys, which are also
      absent from `config.example.json`.
- [ ] **`THIRD_PARTY.md`** — lists `android-image-cropper`, which is not a
      dependency (cropping is first-party); misses osmdroid, the RuStore
      SDK and paramiko. `NOTICE` still lists Retrofit, removed some time ago.
- [ ] **Both READMEs link to `server/models/classes.json`** as the canonical
      class list. That path is gitignored and absent, so the link 404s.
- [ ] **`privacy.html` — third-party processors are still undisclosed.**
      `/api/identify` sends photos to PlantNet, and `/api/chat`,
      `/api/care-plan`, `/api/species-diseases` and `/api/species-profile`
      send text to OpenRouter. The policy names neither. It also does not
      mention the stored FCM push token or that error reports carry a hashed
      client IP. **Highest-priority doc item** — it is the canonical
      disclosure surface. (The deletion section is now accurate: the app's
      delete-my-data control was removed and the policy says so plainly.)
- [ ] **Consolidate repetition** — the "model artefacts are not committed /
      fail-fast" paragraph appears in five places and the optional-integration
      setup in four. `DEPLOYMENT.md` should own both; the rest should link.

---

## Later / maybe

- [ ] **Spider-mite split** from `pests` (deferred — `pests` P already 0.94).
- [ ] **More UI languages** (server content already bilingual).
- [ ] **Expert-human consultation** (community-powered) — ties into 2.7 safety
      escalation and builds on the shipped second-opinion loop.

## Decisions

- **Reliability & safety first** — optimize against a confident wrong
  diagnosis (precision + honest uncertainty + OOD/blur refusal) over headline
  recall/F1.
- **Data > architecture** — the ceiling is data/precision; spend on the
  flywheel + scope before backbone size.
- **One species surface** — species information lives on the species screen
  alone; every other screen links to it through `SpeciesInfoCardView`.
- **New knowledge cards are best-effort, never blocking** — LLM + cache with a
  graceful `available:false`; the app always renders without them.
- **No new disease classes until the scorch/nutrient boundary is stable** —
  scope expansion (edema / sunburn / root rot) waits for **2.7**.
- **Target store: RuStore** (RU audience; app already bilingual). No Google Play.
- **No Compose rewrite** — keep XML Views, single-module; modernize the guts
  (Koin, Ktor).
- **Stay free + honest** — calibrated, OOD-aware, on-device offline, no
  account, privacy-first.
- **Data loop is local** — collect/judge offline → Kaggle supplement →
  notebook pulls it.

---

## Working agreements

Carried over from the execution plan, which is now folded into this file.

**Model choice for delegated work.** *Is there already a pattern in this repo
to follow?* Yes → **Sonnet 5** (well-scoped, copy the pattern; it follows
instructions literally, so spell out scope). New architecture across many
files → **Opus 5** (`effort: xhigh`, generous `max_tokens`; it verifies its own
work, so "double-check your answer" makes output *worse*, and it expands scope
readily, so state the boundary). Nobody has decided what it should be →
**Fable 5**, once, to decide it (omit `thinking` entirely; it degrades when you
enumerate steps — give intent and constraints, then get out of the way).

**RU string review is a standing gate.** Machine-written Russian UI copy reads
wrong in ways tests never catch. Review the `values-ru/strings.xml` diff on
every merge.

**CI** (`.github/workflows/ci.yml`) is push-only on `main`, `claude/**`,
`work/**`, with a `paths-filter` running the **android** and **server** jobs
only when their trees change. Android: `ktlintCheck` + `detekt` +
`testDebugUnitTest` + R8 `assembleRelease`. Server: `ruff` + `pytest`. Two
gaps are open work above: `admin-panel/` has no job, and `codex/**` branches
do not trigger CI at all.
