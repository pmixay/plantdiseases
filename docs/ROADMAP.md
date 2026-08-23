# PlantVision — roadmap

**Open work only.** The code is the record; finished work lives in git history,
not here. Companions: `DATASET_LABELING_PLAN.md` (the data loop and every
dataset finding), `../DEPLOYMENT.md` (deploy + Android signing/FCM),
`../server/models/MODEL_CARD.md` (deliberately version-agnostic — per-run
numbers ship as `model_card.json` inside each bundle).

Two independently releasable tracks: the **app** (`versionName` 1.9 /
`versionCode` 19 at `android/app/build.gradle.kts:31-32`, tag not cut) and the
**model** (bundle version, moves per training run). Every status here was
verified against the tree.

---

## App 1.9

What 1.9 carries beyond 1.8: the Garden rooms / care-calendar / treatment-plan
release (`c3560c4`), the Option-A rebuild of the community opinion sheet, and
`minSdk` 31. Details are in git history.

- [ ] **Cut the 1.9 tag and the RuStore build** — human-only.
- [ ] **First-launch intro video** — *externally blocked.* Server-hosted
      (~3 MB, background download, `/api/aqua`-style manifest), above the
      consent page, existing Lottie as offline fallback. Blocked on the
      user-generated Veo clip (vertical 9:16 macro monstera, scan-light sweep,
      `#16A34A`, ~8 s seamless loop, no text). Not started in the tree — no
      video view, no clip in `res/raw/`.

### Loose ends

- [ ] **Wikipedia attribution on the species card** — `SpeciesInfoCardView`
      renders a Wikipedia extract with no source line; CC BY-SA makes this
      licensing, not design. Decide: source line on the card, or accept the
      link one level down.
- [ ] **No test drives the real map hit-test.** `1faddd8` moved hit-testing to
      `Region` + `Path.FillType.EVEN_ODD` and the old tests were deleted, not
      replaced, because `MapCanvasView` is private (`ui/widget/WorldMapView.kt:228`).
      Make it internal and test a dispatched `MotionEvent`. `WorldMapViewTest`
      covers the bundled asset and `WorldMapGeometryTest` the normalizer; the
      hit test itself is untested.

---

## Security — open findings

August 2026 audit: 49 raised, 38 verified, 22 confirmed. Fixed items are in git
history. Theme is **cost and availability, not compromise** — an unauthenticated
caller is throttled only by a 2 req/s bucket, and staying under it is free.

Where a finding was only partly addressed the remaining half is called out
inline rather than closed.

- [ ] **No inference admission control** (`main.py:2049`, `INFER_WORKERS` at
      `main.py:877`) — at `INFER_WORKERS=1` a client within the rate limit pins
      RAM and starves everyone. Wants a semaphore before decode → 503; there is
      no `Semaphore` anywhere in `main.py` today.
- [ ] **Cost caps** (`ai_services.py:1533`, endpoint `main.py:4513`) — the
      species-diseases cache keys on client-chosen strings, so every distinct
      `name` is a guaranteed miss and a billable call. *OpenRouter has a monthly
      dollar cap, so bounded; stricter per-endpoint limits planned.* PlantNet
      has no server-side budget.
- [ ] **Prompt-cache poisoning** (`ai_services.py:1533` key vs `:1545` prompt) —
      `common_name` reaches the prompt through `_prompt_subject` but not the
      `v2:{name}:{lang}` cache key, poisoning the shared answer.
- [ ] **Unbounded list on `/api/progression`** (`ProgressionRequest.history` at
      `main.py:2697`, endpoint `main.py:3148`), handed straight to
      `compare_progression_history` on the event loop; every comparable field is
      capped.
- [ ] **`/api/analyze` replays another user's `scan_id`** (`main.py:2177`) — the
      dedup key is image sha256 + PlantNet lang + family, with no caller
      binding, and `scan_id` authorises mutations.
- [ ] **`/api/chat` caps decorative** (`main.py:2432`, endpoint `main.py:3034`) —
      client-chosen `session_id` resets turns and tokens.
- [ ] **Forgeable second opinions — half fixed** (`community.py:958`, tally at
      `community.py:1187-1197`). The durable `X-Community-Author-Key` identity
      landed (`POST /api/community/authors/adopt`, `main.py:3785`) and the
      consensus tally dedupes on `COALESCE(author_id, author_pseudo_id)`.
      Remaining: the header is optional on the opinion endpoint
      (`main.py:3680`), so a client that simply omits it still votes under a
      self-chosen `author_pseudo_id`.
- [ ] **EXIF GPS still leaves the device on two secondary paths.**
      `/api/analyze` is fixed — `AnalysisActivity.kt:187` runs
      `ImageUtils.prepareImageForUpload`, which decodes to a Bitmap and
      re-encodes, so no EXIF survives and the PlantNet fan-out is stripped too.
      Not converted: the negative-feedback image upload
      (`FeedbackController.kt:198` → `ScanRepository.kt:617`) and species
      identify (`SpeciesIdentifyActivity.kt:126` → `ScanRepository.kt:253`)
      still post the raw camera JPEG. The bundled privacy policy states that
      photo metadata including GPS is stripped before storage.
      *Smallest change, largest privacy return.*
- [ ] **Pseudo-id in GET paths** (`ScanRepository.kt:817` →
      `GET /api/community/my-scan-opinions/{id}`, `main.py:3931`) — a bearer
      capability landing in access logs.
- [ ] **No lockfile.** Runtime pins are a mix of exact (`fastapi==0.141.1`,
      `Pillow==12.3.0`) and floors (`numpy>=2.4.6,<3.0`, `onnxruntime>=1.28.0`,
      `firebase-admin>=7.5.0`), and the transitive deps that actually serve
      requests (starlette, httpx) are unconstrained by us. CI catches a
      resolution break as a red job rather than at deploy time — a lockfile is
      still the actual fix.
- [ ] **Host named publicly** — host IP + SSH user `root` in git history
      (`cd4bda2`, removed with the admin panel in #149). **No credentials
      leaked** — no key or password was ever committed. Action is ordinary
      hardening: `PermitRootLogin prohibit-password`, `PasswordAuthentication
      no`, SSH rate-limiting.

---

## Garden refactor

**Shipped in `c3560c4`.** The Garden tab is now **a single surface — the
do-today queue over room cards, each holding a plant collection, with no pane
switcher**. The care month grid is its own screen
(`ui/garden/care/CareCalendarActivity`, opened from the button at the top of the
tab); the lunar calendar is per-plant and lives on the plant detail screen only.
The four planned pieces all exist: the Today queue (`util/CareQueue.kt` +
`CareRepository.queue()`), treatment checklists (`TreatmentPlanEntity` /
`TreatmentStepEntity` + `util/TreatmentPlanFactory.kt`), the health header
(`util/PlantHealth.kt`, consumed by `DetailHeaderController` and
`RoomDetailActivity`), and rooms (`RoomEntity`, `RoomRepository`,
`RoomDetailActivity`, `RoomDialogs`).

Room DB is at `version = 9` (`data/local/Database.kt:881`), 11 entities, with
`MIGRATION_1_2`…`MIGRATION_8_9` registered (`:1187-1196`).

Still open:

- [ ] **★ Human gate — health heuristic.** The header tells users a plant is
      *declining*; a wrong verdict is worse than none. Sanity-check
      `util/PlantHealth.kt`'s thresholds against real scan histories. The
      thresholds are deliberately in one tunable place so this is a review, not
      a refactor.
- [ ] **Room-level care defaults.** A room implies light and humidity, so it
      could seed default care and the light-meter baseline
      (`Database.kt:595` notes the intent). Rooms group and filter today;
      nothing derives care from them.

---

## Model track

`MODEL_VERSION` is **2.7.3** (notebook §1). `server/models/` holds only
`.gitkeep` + `MODEL_CARD.md` — **nothing past 2.6.8 is deployed**, and 2.6.8
runs behind the NAP head veto, a serving change needing no new weights.

**The OOD AND-gate has been three-of-three since 2.7.2.** It fires
`not_a_plant` only when the detector confidence is below
`NAP_DETECTOR_CONF_FLOOR`, the classifier-side OOD signal fires (embedding k-NN
distance over the shipped bank, or the MaxLogit floor when no bank ships),
**and** the classifier's own `not_a_plant` head is not vetoing
(`NAP_HEAD_MASS_FLOOR`, default 0.05, `pipeline.py:150-188`). The veto
is on for every bundle including ones exported before the key existed; a bundle
restores the historical two-of-two gate with `ood.nap_head_mass_floor: null`
(`pipeline.py:315-336`).

**Four releases have failed to beat 2.6.8.** 2.7.0, 2.7.1, 2.7.2 and 2.7.3 each
collapsed on the never-trained OOD benchmark's `leaf_scorch` arm while passing
in-domain: the deployed 2.6.8 scores 1.000 on the 31 hibiscus clusters, the two
measured rejects 0.097 and 0.129, against a floor of 0.70. The instrument that
finally caught it before shipping is the **out-of-domain ship gate** in §6
(`OOD_ARM_FLOORS = {'hibiscus': 0.70}`, `REQUIRE_OOD_BENCHMARK = True`, blocking
since 2026-08-18): 2.7.3 raised there and never became a loadable bundle. Full
per-release numbers, the benchmark's construction and the served-vs-argmax
caveat are in `DATASET_LABELING_PLAN.md` §6b.

### The next move is data, and its target changed on 2026-08-22

Every data-free lever has been tested and is falsified or bounded below the
gate's floor. The bound that settles it: a linear probe on 2.7.3's **own frozen
features**, trained corpus-scorch vs corpus-`leaf_spot`, puts 48.4% of the
benchmark's scorch clusters on the scorch side where the shipped head puts
12.9% — the head discards 3.75× real signal, and a *perfect* linear readout
still lands below 0.70.

**The failure is morphology, not framing, 6:1** — of the 31 hibiscus clusters
under 2.7.3, 19 are called `leaf_spot` against 3 called `healthy`. The corpus
defines scorch as end-stage whole-leaf browning; the benchmark shows it early,
dull and edge-confined on a still-green blade, which is the model's `leaf_spot`
prototype. Mechanism, decile statistics and the ablations are in
`DATASET_LABELING_PLAN.md` §1-§4.

- [ ] **Collect early-stage marginal necrosis** — ~400 clusters, `brown_frac <
      0.05`, damage confined to the leaf edge, blade still mostly green, and
      **judged against `leaf_spot` exemplars**. The boundary being trained is
      scorch↔`leaf_spot`; scorch↔healthy already probes at 0.95. New scorch data
      should ship with bounding boxes — Stage 1 misses the benchmark's scorch
      clusters at 8.5× the healthy rate. Full per-stratum spec in
      `DATASET_LABELING_PLAN.md` §6.

### Settled — do not retry

Each was run as a single knob and measured. Re-proposing one costs a run and
returns nothing. The notebook's §1 falsification record points here for the
numbers.

| lever | verdict | evidence |
|---|---|---|
| `CLASS_LOGIT_BIAS` | **dead** | on 2.7.3, best within the 0.12 false-fire ceiling is bias 1.44 → hibiscus 0.323. Reaching the floor needs FF 0.417 — a third of never-trained healthy plants misdiagnosed |
| regime-aware sampling (2.7.3) | **falsified** | stratum draw share 25% → 50%, hibiscus 3/31 → 4/31, Fisher p=1.000. Code removed |
| regime-stratified conformal | **falsified** | marginal coverage 0.967; low-chroma 0.972 vs rest 0.965 — the dull stratum was already better covered. Code removed |
| margin-preserving preprocessing | **not supported** | probe delta +0.013 against a +0.03 bar set in advance; letterboxing at inference made 2.7.2 *worse*, 3/31 → 1/31 |
| sampler weights generally | **falsified** | 2.7.1's `kaggle_ornamental` ablation (1.0 → 0.35) moved ornamental scorch by nothing and cost healthy (recall 0.947 → 0.861, McNemar p=0.0074) |
| temperature refitting | **arithmetically impossible** | `argmax(z/T) == argmax(z)` for every `T > 0`. It moves confidence and set sizes, never a prediction |

**Run-to-run noise is ~2pp.** Two runs of identical config at `SEED=0` gave
`val_indomain` 0.884 and 0.864. That band swallows most single-knob deltas —
which is why 2.6.8→2.7.1 (+1.2pp) and 2.7.2-vs-2.6.8 (−1.2pp) mean nothing on
their own, and why the OOD arm is the instrument that matters.

### Why it broke — the constraint any fix must satisfy

A longitudinal sweep over 14 archived bundles (2.3.1 → 2.7.1) found the collapse
is **a single cliff, not a drift**: 2.6.9 → 2.7.0 lost 31 of 31 hibiscus
clusters, gained 0, McNemar p=9.3e-10, while healthy recall stayed flat. The
cause is a **content swap, not a volume change** — the only ornamental source
was deleted upstream and replaced by one supplying zero scorch. Any fix has to
explain a step change inside the 2026-07-03 → 2026-08-08 window. The source-loss
record and the per-class fallout are in `DATASET_LABELING_PLAN.md`
("Source loss").

**Stage 1 is a real secondary contributor.** The detector trains on PlantDoc
*leaf* boxes and returns no box on 44.9% of `pests` and 43.5% of `gray_mold`
against 13.3% for healthy; a shrivelled, curled leaf is not leaf-shaped either.
Boxes on new data are the cheap mitigation.

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
      `gamma`/`frontier`; publish both and treat the gap as a finding, not a
      footnote.
- [ ] **Build the comparison set** from the exportable production scans (2,593
      and growing) — the only never-trained, real-distribution eval that scales.
- [ ] **A second OOD arm (8–12 species).** The gate rests on one species and one
      photographic session, so a pass reads as "did not regress on the one arm
      we can measure", not "scorch works".

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
  change and made the OOD gate three-of-three; the model did not ship.
- **2.7.3** — regime-aware sampling + regime conformal. Both falsified, both
  removed from the notebook. First release stopped by the out-of-domain gate
  before producing a bundle.

---

## Documentation debt

None of it blocks a release.

- [ ] **README license section under-lists runtime deps.** `NOTICE` and
      `THIRD_PARTY.md` were deleted in `73b6ef0` on the grounds that attribution
      is now summarised inline in the README, but that section names DINOv2,
      YOLOv8 and the datasets and then covers everything else with "runtime
      libraries remain under their own licenses". osmdroid and the RuStore
      appupdate/review SDKs are the two worth naming.
- [ ] **`AquaClassifier`'s own docs describe a bundled asset that does not
      exist.** Its KDoc (`util/AquaClassifier.kt:19-23`) and
      `android/app/build.gradle.kts:384-386` both say the model loads from
      `assets/models/aqua_classifier.tflite`; the code path is filesDir-only —
      `AquaModelManager` stores the downloaded `.tflite` under
      `filesDir/aqua/`, and `app/src/main/assets/` contains no model.
      `build.gradle.kts:78-79` already states it correctly.

---

## Architecture — reviewed 2026-08, follow-ups

A full review of both trees ran during the 2026-08 cleanup. The verdict: the
server's domain layer and the app's layering are genuinely sound; the debt is
concentrated in a handful of god-modules and in HTTP-layer idiom. What was safe
to do without a dedicated PR is done — the event-loop fix, the shared feed
serialiser, the global exception handler, test-store isolation, and the detekt
complexity gates. What is left needs its own PR each, and must not be folded
into another change.

**Server**

- [ ] **Split `main.py` (5k lines) into routers.** It holds the app, five
      middlewares, the limiter/ban/quota state, the metrics counters, two thread
      pools, image validation, 16 Pydantic models, 39 handlers and the lifespan.
      Order: extract `deps.py` (the nine hand-copied quota preambles become one
      `Depends`) → switch the ~400-line duplicated alias-import block to module
      imports → then one router group per PR, smallest first, community last.
      Gated by one hard constraint: 24 test sites `monkeypatch.setattr` names on
      `main`, and 11 modules `importlib.reload`, so a router doing
      `from main import pipeline` binds `None` forever. `openapi.json` plus
      `test_openapi_contract.py` catch a dropped route, which is what makes the
      split safer than it looks.
- [ ] **`analyze_image` is 376 lines**, of which ~150 are pure response
      assembly. Extracting `build_analyze_payload()` makes the entropy guard,
      the dual-care split and the `not_a_plant` species suppression unit-testable
      without a TestClient. Best value-for-effort item on this list.
- [ ] **Group the 16 inline Pydantic models** into `schemas.py` with shared
      `Annotated` field types. The same `scan_id` is `8..64` in two models and
      `1..128` in four others, and the charset check is written longhand four
      times. Standardise on the looser bound; tightening is its own decision,
      because the Android client mints these.
- [ ] **`ai_services.py` (3.4k lines) is three modules** — transport, five
      features, and ~700 lines of bilingual product content that belongs with
      `diseases_data.py`. `community.py` (2.9k lines, 64 functions) is nine
      responsibilities behind one namespace and wants to become a package.
- [ ] **Five hand-rolled SQLite stores** repeat the same connect/init/prune
      boilerplate, and six near-identical `daily_*_count_for_ip_hash` functions
      have silently diverged (rolling 24h vs calendar day). Schema evolution
      replays every migration on every boot with no `PRAGMA user_version`.
- [ ] **No `response_model` on any route**, so `openapi.json` describes request
      bodies only and the contract test compares paths and methods alone. The
      30-key `/api/analyze` payload can change without failing CI while a frozen
      APK parses it. Cheapest first step: assert the response keys in the
      contract test.
- [ ] **Nothing type-checks the annotations.** Coverage is high (92-100% outside
      `main.py`) but neither mypy nor pyright is configured or in CI.

**Android**

- [ ] **`AnalysisActivity` loses a scan on rotation.** The server round-trip
      runs in `lifecycleScope` and the Room write is a suspend call in the same
      scope; there is no `configChanges` and no ViewModel. Rotating mid-analysis
      burns a second paid inference, and rotating between the response and
      `saveScan` loses the scan after the server has already issued its
      `scan_id`. The only outright correctness bug found.
- [ ] **`ScanRepository` is a 1654-line god object** spanning seven bounded
      contexts, injected wholesale into six consumers. Split by context.
- [ ] **XML vs imperative-Kotlin view construction.**
      `DetailExtraCardsController` and `CarePlanCardView` build whole hierarchies
      in code with hardcoded dp and colors — 113 of the ~166 programmatic View
      constructions app-wide, and the entire reason for their size. A shared kit
      already exists (`DetailUiKit.baseCard()`, "single source of truth for every
      detail-screen panel") with two call sites, and the two styles disagree on
      screen today.
- [ ] **Gson is still a live dependency** for Room field JSON, `ResponseCache`
      and intent payloads, so all 76 DTOs carry 401 hand-mirrored
      `@SerializedName` annotations beside their `@SerialName`, plus a
      `KSerializer<JsonObject>` bridge and six ProGuard rules. Retiring it is
      M/L but deletes all of that.
- [ ] **`GuideDataProvider` (1638 lines) is a second localization system** —
      78 EN/RU pairs as Kotlin strings, outside the resource framework, driving
      41 hand-written `LocaleHelper.isRussian(...)` branches. Nothing enforces
      RU parity there the way `strings.xml` gets it for free.
- [ ] **Navigation Component manages a two-destination graph** while 22
      Activities navigate by explicit Intent, and the integration has been
      unwound by hand (no `setupWithNavController`, a re-entrancy flag, tabs
      living in a ViewPager2 instead of as destinations). Its cost now exceeds
      its contribution.
- [ ] **No DAO returns a `Flow`**, so every screen re-reads on resume and
      cross-screen edits are invisible until a foreground transition. That is
      the root cause of the ~20 `lifecycleScope.launch(start = UNDISPATCHED) {
      withContext(NonCancellable) { … } }` sites. Migrate one screen per PR,
      additively — and only after the `AnalysisActivity` and `ScanRepository`
      items land, which give it seams to attach to.
- [ ] **Ratchet the detekt thresholds down.** They are set just above today's
      worst case (LongMethod 210 vs a measured 195; LargeClass 1300 vs 1165);
      lower each by ~20% as the splits above land. `GuideDataProvider.kt` is
      excluded from the two size rules until it stops being a content blob.
- [ ] **Single `:app` module is still right** — do not modularize. The one edge
      that blocked a clean `:core:data` extraction (util → ui) is now fixed.

---

## Later / maybe

- [ ] **Spider-mite split** from `pests` (deferred — `pests` P already 0.94).
- [ ] **More UI languages** (server content already bilingual).
- [ ] **Expert-human consultation** — ties into 2.8 safety escalation, builds
      on the shipped second-opinion loop.

---

## Decisions

- **Reliability & safety first** — optimize against a confident wrong diagnosis
  (precision + honest uncertainty + OOD/blur refusal) over headline recall/F1.
- **Data > architecture** — the ceiling is data/precision; spend on the flywheel
  and scope before backbone size.
- **One species surface** — species info lives on `SpeciesDetailActivity` alone;
  every other screen references a species through the reusable
  `ui/common/SpeciesInfoCardView` (Wikipedia photo + name + description + "more
  about this species"), embedded on the scan result, species identify and the
  community opinion sheet. It was dropped from that sheet in Community v2 and
  restored in the Option-A redesign — knowing what the plant *is* changes what
  you would call the problem. Never build a second species card.
- **New knowledge cards are best-effort, never blocking** — LLM + cache with a
  graceful `available:false`.
- **No new disease classes until the scorch/nutrient boundary is stable** —
  scope expansion waits for **2.8**.
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

**Training runs on Colab, never locally.** The target runtime is a Colab **A100
(40 GB VRAM, 80 GB RAM, 100 GB disk)** driving `server/train_notebook.ipynb`;
the notebook's own header sizes a full run at ≈20–35 min on a single
A100-40GB including data prep.
Everything — training, distillation, calibration, ONNX/TFLite export — happens
there. For ML work, edit the notebook or the pipeline code and hand the
maintainer the cells to run.

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
**server** jobs only when their trees change. Android is split in two so the
halves run concurrently: `android-check` (`ktlintCheck` + `detekt` +
`testDebugUnitTest`) and `android-release` (R8 `assembleRelease` + APK signing
verification). Server: `ruff` + `pytest`, on Python 3.14. Dependency updates
are made by hand.

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

Note the filters gate only on `android/**` and `server/**` (plus the workflow
itself): a change confined to `docs/`, `README.md`, `DEPLOYMENT.md` or
`CLAUDE.md` runs neither job.

---
