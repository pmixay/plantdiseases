# PlantVision — roadmap

**Open work only.** The code is the record; finished work lives in git history,
not here. Companions: `DATASET_LABELING_PLAN.md` (the data loop and every
dataset finding), `../DEPLOYMENT.md` (deploy + Android signing/FCM),
`../server/models/MODEL_CARD.md` (**stale — the model track below is current**).

Two independently releasable tracks: the **app** (`versionName` 1.8 /
`versionCode` 18 set, tag not cut) and the **model** (bundle version, moves per
training run). Every status here was verified against the tree.

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

Where a finding was only partly addressed the remaining half is called out
inline rather than closed.

- [ ] **No inference admission control** (`main.py:2004`) — at `INFER_WORKERS=1`
      a client within the rate limit pins RAM and starves everyone. Wants a
      semaphore before decode → 503.
- [ ] **Cost caps** (`ai_services.py:1257`, `main.py:3865`) — caches key on
      client-chosen strings, so every distinct `name` is a guaranteed miss and a
      billable call. *OpenRouter has a monthly dollar cap, so bounded; stricter
      per-endpoint limits planned.* PlantNet has no server-side budget.
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
      the bundled privacy policy says this may be published as an open dataset.
      *Smallest change, largest privacy return.*
- [ ] **Pseudo-id in GET paths** (`ScanRepository.kt:775`) — a bearer capability
      landing in access logs.
- [ ] **Dependency constraints** — `starlette` unconstrained above 0.46.0
      (resolves clean today, range does not); `firebase-admin` pins
      `httpx==0.28.1`, colliding with `httpx>=0.28` once 0.29 ships; no lockfile.
      CI still catches a resolution break as a red job rather than at deploy
      time — but a lockfile is still the actual fix.
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

`MODEL_VERSION` is **2.7.3** (notebook §1). `server/models/` holds only
`.gitkeep` + `MODEL_CARD.md` — **nothing past 2.6.8 is deployed**, and 2.6.8
runs behind the NAP head veto (`pipeline.py`), a serving change needing no new
weights.

**Four releases have failed to beat 2.6.8.** 2.7.0, 2.7.1, 2.7.2 and 2.7.3 each
collapsed on the never-trained OOD benchmark's `leaf_scorch` arm while passing
in-domain. The instrument that finally caught it before shipping is the
**out-of-domain ship gate** in §6 (blocking since 2026-08-18): 2.7.3 raised
there and never became a loadable bundle.

| on the OOD benchmark (v2, 182 clusters) | hibiscus scorch (31) | healthy false-fire (151) |
|---|---|---|
| **2.6.8 — deployed** | **1.000** | 0.026 |
| 2.7.2 | 0.097 | 0.040 |
| 2.7.3 | 0.129 | 0.079 |

### The next move is data, and its target changed on 2026-08-22

Every data-free lever has now been tested and is falsified or bounded below the
gate's floor. The bound that settles it: a linear probe on 2.7.3's **own frozen
features**, trained corpus-scorch vs corpus-`leaf_spot`, puts **48.4%** of the
benchmark's scorch clusters on the scorch side where the shipped head puts
12.9%. So the head does discard real signal — 3.75× worth — but a *perfect*
linear readout of the current features still lands below 0.70.

**The failure is morphology, not framing, 6:1.** Of the 31 hibiscus clusters
under 2.7.3: **19 → `leaf_spot`**, 4 correct, 3 → `healthy`, 3 → `viral`,
2 → `powdery`. The corpus defines scorch as end-stage whole-leaf browning
(brown fraction p50 0.170) and the benchmark shows it early, dull and
edge-confined on a still-green blade (p50 0.008). "Green leaf + discrete dark
regions" *is* the model's `leaf_spot` prototype.

- [ ] **Collect early-stage marginal necrosis** — ~400 clusters, `brown_frac <
      0.05`, damage confined to the leaf edge, blade still mostly green, and
      **judged against `leaf_spot` exemplars**. The boundary being trained is
      scorch↔`leaf_spot`; scorch↔healthy already probes at 0.95. Full
      per-stratum spec in `DATASET_LABELING_PLAN.md` §6.

### Settled — do not retry

Each was run as a single knob and measured. Re-proposing one costs a run and
returns nothing.

| lever | verdict | evidence |
|---|---|---|
| `CLASS_LOGIT_BIAS` | **dead** | on 2.7.3, best within the 0.12 false-fire ceiling is bias 1.44 → hibiscus 0.323. Reaching the floor needs FF 0.417 — a third of never-trained healthy plants misdiagnosed |
| regime-aware sampling (2.7.3) | **falsified** | stratum draw share 25% → 50%, hibiscus 3/31 → 4/31, Fisher p=1.000 |
| regime-stratified conformal | **falsified** | marginal coverage 0.967; low-chroma 0.972 vs rest 0.965 — the dull stratum was already better covered |
| margin-preserving preprocessing | **not supported** | probe delta +0.013 against a +0.03 bar set in advance; letterboxing at inference made 2.7.2 *worse*, 3/31 → 1/31 |
| sampler weights generally | **falsified** | 2.7.1's `kaggle_ornamental` ablation (1.0 → 0.35) moved ornamental scorch by nothing and cost healthy (recall 0.947 → 0.861, McNemar p=0.0074) |
| temperature refitting | **arithmetically impossible** | `argmax(z/T) == argmax(z)` for every `T > 0`. It moves confidence and set sizes, never a prediction |

**Run-to-run noise is ~2pp.** Two runs of identical config at `SEED=0` gave
`val_indomain` 0.884 and 0.864. That band swallows most single-knob deltas —
which is why 2.6.8→2.7.1 (+1.2pp) and 2.7.2-vs-2.6.8 (−1.2pp) mean nothing on
their own, and why the OOD arm is the instrument that matters.

### Why it broke — the archaeology, kept because it constrains any fix

A longitudinal sweep scored every archived bundle (2.3.1 → 2.7.1, 14 models) on
one benchmark. **The collapse is a single cliff, not a drift:** 2.6.9 → 2.7.0
lost 31 of 31 hibiscus clusters, gained 0, McNemar **p=9.3e-10**, while healthy
recall stayed flat (0.974 → 0.967). No other transition loses more than 15.

The mechanism is a **content swap, not a volume change**: `pappu54` was deleted
upstream and replaced by `itselif/ornamental-plant-diseases`, which supplies
healthy / `leaf_spot` / `powdery_mildew` and **zero** scorch. The same source
entering at 2.3.1→2.4.0 was the series' single largest *gain* (+39/−0,
p=3.6e-12, scorch 0.129 → 0.758). Any fix has to explain a step change inside
the 2026-07-03 → 2026-08-08 window.

**Stage 1 is a real secondary contributor.** The detector returns no box on
44.9% of `pests` and 43.5% of `gray_mold`, versus 13.3% for healthy — it trains
on PlantDoc *leaf* boxes and that imagery is not leaf-shaped. On the benchmark's
scorch arm the no-box rate is 8.5× the healthy arm's (Fisher p=3.2e-05): a
shrivelled, curled leaf is not leaf-shaped either. New scorch data should carry
boxes.

### Open experiments that are not data

Ordered by evidence. None is expected to clear the floor alone; all are
compatible with the collection round.

- [ ] **Capacity — `frontier` (ViT-B/14) or DINOv3.** Previously ruled out on
      "features are fine", which was read off the scorch-vs-*healthy* probe
      (0.95). The axis that fails is scorch-vs-`leaf_spot`, where the same
      features sit at 48.4% — telling a scorched margin from discrete spots is a
      fine-grained dense-feature problem, which is what DINOv3 claims to
      improve. **Verify DINOv3's licence before committing**; it is not
      DINOv2's Apache-2.0. `frontier` is a plain mode, so it costs one run.
- [ ] **`USE_REGISTERS`** — `dinov2_vits14_reg` has cleaner features; off today
      only because the validated 0.91 probe used non-register weights. One flag,
      one run, one paired bootstrap.
- [ ] **Domain-robust training objective** — an intervention strong enough to
      match the probe that motivated 2.7.3, rather than a sampling nudge.
- [ ] **Report the production-faithful number.** Every headline metric scores
      the classifier on a whole val image while production classifies a
      **detector crop**. §6's detector-crop eval defaults ON for
      `gamma`/`frontier`; publish both in `MODEL_CARD.md` and treat the gap as a
      finding, not a footnote.
- [ ] **Build the comparison set** from the exportable production scans (2,593
      and growing) — the only never-trained, real-distribution eval that scales.

### 2.8 — the leap

> From "a good leaf classifier" to a **holistic, trustworthy plant doctor**.
> Items 1–3 change what the model *is*.

1. **Real-distribution data at scale.** Every source today is scraped or
   curated; production scans are what users actually shoot. The evidence is
   already in the corpus — DoctorP (real app photos) is the source that supplies
   the starved classes. Nothing else moves reliability as much per unit effort.
2. **Multi-label instead of single-label.** A softmax over 9 classes cannot say
   "underwatered **and** nutrient-deficient", the most common real
   co-occurrence, and plausibly part of why `nutrient` over-fires. Sigmoid head
   + per-class thresholds + per-class conformal. Do it *after* the boundary is
   stable, never during.
3. **Segmentation, not a box.** Stage 1 → lesion **area**, so severity is
   measured rather than heuristic, and one diseased leaf stops being diluted in
   a whole frame. Also the honest fix for the crop-vs-whole gap above.

### Closed releases

- **2.6.8** — shipped, still deployed. The incumbent on every axis.
- **2.6.9** — gate-passed, lost a paired A/B vs 2.6.8. Not shipped. On the OOD
  scorch arm it is marginally the best model on record.
- **2.6.10** — recovered scorch but lost a 2.26k-image source mid-run, so it
  anchors no A/B.
- **2.6.11 / v8** — superseded by the 2.6.12 general-plant pivot, whose data
  cleaning it folded in. 2.6.12/2.6.13 never trained; the work became 2.7.0.
- **2.7.0** — data-integrity release. Collapsed the scorch arm (the cliff above).
- **2.7.1** — single-knob ablation of `kaggle_ornamental`. Falsified it, and cost
  healthy. Reverted.
- **2.7.2** — archaeology + the NAP head veto. The veto shipped as a serving
  change; the model did not.
- **2.7.3** — regime-aware sampling + regime conformal. Both falsified. First
  release stopped by the out-of-domain gate before producing a bundle.

---

## Documentation debt

None of it blocks a release.

- [ ] **Privacy policy — third-party processors undisclosed. Highest-priority
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
- **Stay free + honest** — calibrated, OOD-aware, on-device offline,
  privacy-first. The current build has no account, login, email or password.
  Community V2 creates a persistent anonymous per-device handle only after
  Community opt-in or an explicit one-scan share; it deduplicates authors,
  likes and views and enforces moderation without identifying the person.
  Account Beta and any account linkage remain deferred and are not part of
  this branch. The handle and same-device linkability are disclosed in
  `privacy_policy.html`.
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
**server** jobs only when their trees change. Android: `ktlintCheck` +
`detekt` + `testDebugUnitTest` + R8 `assembleRelease`. Server: `ruff` +
`pytest`, on Python 3.14. Dependency updates are made by hand.

No `pull_request` trigger, deliberately: every branch that reaches a PR was
pushed here first, so a PR trigger only duplicates runs (they land under a
different `github.ref`, so `concurrency` does not cancel them).

Two failure modes worth remembering, both of which produced a *green-looking*
repo with nothing actually running:

1. **A branch that matches no glob gets no CI, and its PR shows an empty
   status rather than a red one.** That is how #158-#167 merged unbuilt —
   they came from automated branches — and five mutually-incompatible
   dependency bumps broke the Android build at Gradle configuration time on
   `main`. The push-only stance is safe *because* dependency updates are
   manual now. Reintroducing any automation means adding its branch prefix
   to the trigger list in the same commit.
2. **`paths-filter` could not `git fetch` its diff base** under
   `persist-credentials: false` with the default depth-1 clone, so the
   `changes` job died on every push — and when `changes` dies, `needs:`
   silently skips android and server. Fixed with `fetch-depth: 0`, which
   makes the base commit already local so no fetch (and no credential) is
   needed.

---
