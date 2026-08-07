# PlantVision — roadmap

Open work only; the code is the record. Companions: `DATASET_LABELING_PLAN.md`
(data loop), `../DEPLOYMENT.md` (deploy + Android signing/FCM),
`../server/models/MODEL_CARD.md` (**stale — see the model track**).

Every status here was verified against the tree. Two independently releasable
tracks: the **app** (`versionName` 1.8 / `versionCode` 18 already set, tag not
cut) and the **model** (bundle version, moves per training run).

---

## App 1.8

Everything else has landed: AI-doctor chat subtitle via `localizedSpeciesName`,
shared `ConnectivityObserver`, `GET /api/species-profile` returning all three
fields, flowering chart, native-range map, coach-mark onboarding, and the
species-info consolidation onto one screen behind one reusable card.

- [ ] **Cut the 1.8 tag and the RuStore build** — human-only.
- [ ] **First-launch intro video** — *externally blocked.* Server-hosted
      (~3 MB, background download, `/api/aqua`-style manifest), above the
      consent page, existing Lottie as offline fallback. Blocked on the
      user-generated Veo clip (vertical 9:16 macro monstera, scan-light sweep,
      `#16A34A`, ~8 s seamless loop, no text). Not started in the tree.

### Loose ends

- [ ] `ResultActivity.hasValidatedInternet()` duplicates
      `ConnectivityObserver.hasValidatedInternet` — the shared-observer work
      converted `CameraFragment` but not this second site.
- [ ] **Wikipedia attribution on the species card** — the card renders an
      extract with no source line; CC BY-SA makes this licensing, not design.
      Decide: source line on the card, or accept the link one level down.
- [ ] **No test drives the real map hit-test.** `1faddd8` moved hit-testing to
      `Region` + `Path.FillType.EVEN_ODD` and the old tests were deleted, not
      replaced, because `MapCanvasView` is private. Make it internal and test a
      dispatched `MotionEvent`.

---

## Security — open findings

August 2026 audit: 49 raised, 38 verified, 22 confirmed. Fixed items are in git
history. Theme is **cost and availability, not compromise** — an unauthenticated
caller is throttled only by a 2 req/s bucket, and staying under it is free.

- [ ] **`/api/feedback/image` unbounded** (`main.py:3043,3068`) — quota reads a
      counter this endpoint never increments so it can never fire; no
      magic-byte/decode validation; files never deleted. Disk fill **and**
      arbitrary bytes into the retraining corpus. *Highest value here.*
- [ ] **No inference admission control** (`main.py:2004`) — at `INFER_WORKERS=1`
      a client within the rate limit pins RAM and starves everyone. Wants a
      semaphore before decode → 503.
- [ ] **Cost caps** (`ai_services.py:1257`, `main.py:3865`) — caches key on
      client-chosen strings, so every distinct `name` is a guaranteed miss and a
      billable call. *OpenRouter has a monthly dollar cap, so bounded; stricter
      per-endpoint limits planned.* PlantNet has no server-side budget.
- [ ] **`/api/metrics`** (`main.py:1711`) — rate-limit exempt, full-table SQLite
      aggregates on the event loop.
- [ ] **`/api/plant-photo` amplifier** (`main.py:4041`, `plant_photos.py:184`) —
      unbounded WebP variants; LRU caps file *count*, not bytes, so any client
      can evict every warm photo.
- [ ] **Unbounded list on `/api/progression`** (`main.py:2491`), on the event
      loop; every comparable field is capped.
- [ ] **Prompt-cache poisoning** (`ai_services.py:1257`) — `common` reaches the
      prompt but not the cache key, poisoning the shared answer.
- [ ] **`/api/analyze` replays another user's `scan_id`** (`main.py:1972`) —
      keyed on upload-byte hash only, and `scan_id` authorises mutations.
- [ ] **`/api/chat` caps decorative** (`main.py:2226`) — client-chosen
      `session_id` resets turns and tokens.
- [ ] **Forgeable second opinions** (`community.py:636`) — client-supplied
      `author_pseudo_id`, so dedup does not bind.
- [ ] **EXIF GPS leaves the device** (`ScanRepository.kt:576,238`) — raw camera
      JPEG uploaded verbatim and forwarded to PlantNet, though
      `ImageUtils.prepareImageForUpload` exists and would strip it.
      `privacy.html:87` says this may be published as an open dataset.
      *Smallest change, largest privacy return.*
- [ ] **Pseudo-id in GET paths** (`ScanRepository.kt:775`) — a bearer capability
      landing in access logs.
- [ ] **Dependency constraints** — `starlette` unconstrained above 0.46.0
      (resolves clean today, range does not); `firebase-admin` pins
      `httpx==0.28.1`, colliding with `httpx>=0.28` once 0.29 ships; no lockfile.
- [ ] **Host named publicly** — host IP + SSH user `root` in git history
      (`cd4bda2`, removed with the admin panel in #149). **No credentials
      leaked** — no key or password was ever committed. Action is ordinary
      hardening: `PermitRootLogin prohibit-password`, `PasswordAuthentication
      no`, SSH rate-limiting.

---

## Garden refactor — next major app work, its own release

> Today the Garden is a **flat list of saved plants**. The refactor turns it
> into a **care-management hub** — the screen a user opens daily, where every
> scan feeds an ongoing care story instead of a one-off verdict. Biggest
> remaining product lever, mostly new UI + aggregation over data Room already
> persists (`plants`, `scans`, `journals`, `reminders`, `chat`). Keep XML Views
> + Hilt; no Compose.

**Status:** DB is at `version = 6` (`Database.kt:452`). The `room` column on
`plants` **exists** (`Database.kt:29`, re-added by `MIGRATION_5_6`), so item 4
is partly done at the schema level. Still absent: checklist-step storage,
health derivation, aggregated queue.

1. **"Today" care queue** — aggregates due waterings, fertilising and open
   treatment steps into one dated queue, most urgent first; one-tap **Done**
   advances the schedule and logs to the journal. Promotes existing
   `PlantReminderWorker` data from notifications to a first-class list. Empty
   state is a genuine reward ("all caught up 🌿") — habit without a streak.
2. **Scan-driven care checklists** — materialize the care plan's treatment
   steps as a trackable checklist on the plant, with per-step completion and
   follow-up dates feeding the Today queue. `generate_care_plan → treatment`
   already produces the steps. Completion history feeds the health header and
   later the retraining loop ("did the treatment work?").
3. **Plant health header** — one healthy / recovering / declining verdict per
   plant from scan-history trend + care adherence; drives the Garden card
   accent. Thresholds in one tunable place.
4. **Rooms / locations** — group by room; care-relevant because light and
   humidity differ, so a room can seed default care and the light-meter
   baseline. Column exists; UI + grouping do not.
5. ~~Progression / photo-compare~~ — shipped (`DetailTimelineController`,
   `PhotoCompareActivity`).

| # | Task | Why here | Size |
|---|---|---|---|
| 1 | Design pass — schema + aggregation | Storage undecided | M |
| 2 | Today queue **+** checklists | The payoff; shared schedule/journal plumbing | XL |
| 3 | Health header | Needs adherence signal from #2 | M |
| 4 | Rooms UI | Independent polish; can slip | S |

**★ Human gate — schema approval, after #1.** Does checklist storage fit the
real care-plan `treatment` shape? Is the Today queue derivable from existing
tables? Stakes are lower than they look — `Database.kt` declares the schema
ephemeral pre-release — but everything builds on it.

**★ Human gate — health heuristic, after #3.** The header tells users a plant
is *declining*; a wrong verdict is worse than none. Sanity-check on real scan
histories.

---

## Model track

`MODEL_VERSION` is **2.6.13** (notebook §1). `server/models/` holds only
`.gitkeep` + `MODEL_CARD.md` — **nothing past 2.6.8 is deployed.**

Closed history: **2.6.9** gate-passed but lost a paired A/B vs 2.6.8 (kept
2.6.8). **2.6.10** recovered leaf_scorch but silently lost a 2.26k-image source
mid-run, so it cannot anchor an A/B. **2.6.11/v8** superseded by the 2.6.12
general-plant pivot, whose data-cleaning it folded in. Details in
`DATASET_LABELING_PLAN.md`.

### 2.6.13 — makes the pivot real

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

- [ ] **★ BLOCKER — publish the v8b supplement to Kaggle first.** The dataset the
      notebook downloads is **version 7 = a v7-era build**: 2,539 rows vs v8b's
      3,125, **no `leaf_spot` at all**, no `split_hint`, no provenance column, and
      `subject_domain` limited to `houseplant`/`not_a_plant` — **zero garden
      ornamentals**. The v8 and v8b rounds never reached the publish step, so none
      of that work has ever been in a training run, and the domain pivot has never
      been in a corpus. §2 now fails loud on this (`SUPPLEMENT_MIN_ROWS`). See
      `DATASET_LABELING_PLAN.md`.
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

Run-log checks, each proving one fix landed:

| Look for | Expect | Means |
|---|---|---|
| `source floors OK: …` | present | §2 gate ran. If it *raised*, a source is down — read it, don't bypass. |
| `Kaggle ornamental per-class:` | `healthy 600, leaf_spot 600, powdery_mildew 133` | Replacement sources loaded; a missing class is the known gap. |
| `MaxLogit OOD floor calibrated [plant_only]` | `sep=` **positive** | Fallback alive for the first time since v2.3. Still negative ⇒ flag it. |
| `conformal: selected alpha=… lambda_reg=…` | **not** the last grid value | Search converged inside the grid. |
| `head early stop @ epN (best ep=M)` | `M` well above 3 | Best-epoch 3 was the corpus-loss symptom. |
| `[final val_indomain] disease_macro_f1` | toward **0.86** | The headline regression is undone. |
| `Kaggle supplement manifest:` | row count matching the build you published | **Read this line first.** A stale upload otherwise trains and grades silently. |
| `Kaggle supplement: eval/train routing basis =` | `split_hint`, else `confidence>=0.8` | `none` means neither column shipped and every row is grading the model. |
| `Kaggle supplement routing:` | all three of `supplement` / `supplement_train_only` / `supplement_boundary` non-zero | The verdict is being honoured. Only two keys ⇒ no routing basis (or you flipped `STRICT_SUPPLEMENT_SPLIT_HINT`). |
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

### 2.7 — the leap

> From "a good leaf classifier" to a **holistic, trustworthy plant doctor**.
> Scope + real-world reliability + data scale, not a bigger backbone — the probe
> says features aren't the ceiling.

1. **Data flywheel** — fold thousands of offline-judged production scans into
   the next dataset. Bigger lever than any backbone.
2. **Scope expansion** — activate `edema` + `sunburn` (cards and deferred
   feedback labels exist); add root rot via image + symptom fusion. Diagnose
   *conditions*, not leaf classes.
3. **Multimodal diagnosis** — fuse vision verdict with species ID and symptom
   Q&A so species + context condition the disease priors.
4. **`frontier` (DINOv2 ViT-B/14, GPU)** — A/B vs gamma **only if** the probe
   shows headroom, then distill back to ship cheaply.
5. **Segmentation** — Stage 1 toward lesion area for severity, not one box.
6. **Safety at scale** — per-class conformal, explicit abstain →
   expert/community escalation, calibrated severity that never over-treats.

Prep now, in parallel:

- [ ] Wire `frontier` end-to-end in the notebook + a probe-gate (cheap go/no-go).
- [ ] Stand up the production-scan export→judge loop at volume.
- [ ] Spike multimodal fusion behind a flag.
- [ ] Design the segmentation-detector path — research spike.

---

## Documentation debt

None of it blocks a release.

- [ ] **`privacy.html` — third-party processors undisclosed. Highest-priority
      doc item**, as the canonical disclosure surface. `/api/identify` sends
      photos to PlantNet; `/api/chat`, `/api/care-plan`, `/api/species-diseases`
      and `/api/species-profile` send text to OpenRouter. Neither is named. It
      also omits the stored FCM token and that error reports carry a hashed
      client IP.
- [ ] **`README.ru.md` has diverged from `README.md`** — missing 12 of 24 API
      rows, the project-tree line and two Documentation bullets; carries a
      project-website block the English one never gained.
- [ ] **`DEPLOYMENT.md`** — tells operators to set the server URL "through
      Profile → Server URL" (compile-time only, no such setting); lists an
      onboarding entry point the coach-mark rework removed; config table misses
      the three `TELEMETRY_*` keys.
- [ ] **`THIRD_PARTY.md`** — lists `android-image-cropper` (not a dependency;
      cropping is first-party) and **Koin** (replaced by Hilt); misses osmdroid
      and the RuStore SDK. `NOTICE` still lists Retrofit, removed some time ago.
- [ ] **Both READMEs link to `server/models/classes.json`** as the canonical
      class list — gitignored and absent, so the link 404s.
- [ ] **Consolidate repetition** — the "model artefacts are not committed /
      fail-fast" paragraph appears in five places and optional-integration setup
      in four. `DEPLOYMENT.md` should own both; the rest link.

---

## Later / maybe

- [ ] **Spider-mite split** from `pests` (deferred — `pests` P already 0.94).
- [ ] **More UI languages** (server content already bilingual).
- [ ] **Expert-human consultation** — ties into 2.7 safety escalation, builds
      on the shipped second-opinion loop.

---

## Decisions

- **Reliability & safety first** — optimize against a confident wrong diagnosis
  (precision + honest uncertainty + OOD/blur refusal) over headline recall/F1.
- **Data > architecture** — the ceiling is data/precision; spend on the flywheel
  and scope before backbone size.
- **One species surface** — species info lives on the species screen alone;
  every other screen links via `SpeciesInfoCardView`.
- **New knowledge cards are best-effort, never blocking** — LLM + cache with a
  graceful `available:false`.
- **No new disease classes until the scorch/nutrient boundary is stable** —
  scope expansion waits for 2.7.
- **Target store: RuStore** (RU audience; app already bilingual). No Google Play.
- **No Compose rewrite** — keep XML Views, single-module; modernize the guts
  (Hilt, Ktor).
- **Stay free + honest** — calibrated, OOD-aware, on-device offline, no account,
  privacy-first.
- **Data loop is local** — collect/judge offline → Kaggle supplement → notebook
  pulls it.

---

## Working agreements

**Model choice for delegated work.** *Is there already a pattern in this repo to
follow?* Yes → **Sonnet 5** (follows instructions literally, so spell out
scope). New architecture across many files → **Opus 5** (`effort: xhigh`,
generous `max_tokens`; it verifies its own work, so "double-check your answer"
makes output *worse*, and it expands scope readily, so state the boundary).
Nobody has decided what it should be → **Fable 5**, once, to decide it (omit
`thinking` entirely; it degrades when you enumerate steps — give intent and
constraints, then get out of the way).

**RU string review is a standing gate.** Machine-written Russian UI copy reads
wrong in ways tests never catch. Review the `values-ru/strings.xml` diff on
every merge.

**CI** (`.github/workflows/ci.yml`) is push-only, on `main`, `claude/**`,
`work/**` and `codex/**`, with a `paths-filter` running the **android** and
**server** jobs only when their trees change. Deliberately no `pull_request`
trigger: every branch that reaches a PR was pushed here first, so a PR trigger
only duplicates runs (they land under a different `github.ref`, so `concurrency`
does not cancel them). Android:
`ktlintCheck` + `detekt` + `testDebugUnitTest` + R8 `assembleRelease`. Server:
`ruff` + `pytest`. Dependency updates arrive via `.github/dependabot.yml`.
