# PlantVision — roadmap

**Open work only.** Finished work lives in git history; every model run, with
its numbers, lives in `MODEL_HISTORY.md`. Companions: `DATASET_LABELING_PLAN.md`
(the data loop, the v9 protocol, every dataset finding), `../DEPLOYMENT.md`
(deploy + Android signing/FCM), `../server/models/MODEL_CARD.md`
(version-agnostic; per-run numbers ship as `model_card.json` in each bundle).

Every item below was re-verified against `main` @ `e4b4a45` on 2026-10-09.
Line references are as of that commit.

---

## Where things stand

| track | state |
|---|---|
| **App** | `versionName` 1.9 / `versionCode` 19 (`android/app/build.gradle.kts:31-32`). Not tagged — the repo has no git tags at all. 1.9 carries the Garden rooms / care calendar / treatment plans, the Option-A community opinion sheet and `minSdk` 31. |
| **Model** | **2.6.8 is deployed.** **2.7.5** (trained 2026-10-05 on dataset v8) passed its ship gate and is the first bundle that boots the current strict server. No ship decision is recorded. |
| **Server** | 38 routes, fail-closed bundle contract, all optional integrations degrade to `available:false`. 2.6.8 is a legacy bundle with no manifest, and **a legacy bundle cannot boot the current server**. So production runs a server build from before the strict contract, and every server fix since then reaches users only once a manifest-bound bundle ships. |
| **Security** | 10 open findings from the August audit. They are about cost, availability and privacy, not compromise. One is a privacy-policy misstatement (EXIF). |
| **Data** | v8 is the training corpus. v9 collection, the locked test set T and OOD benchmark v3 are specified in `DATASET_LABELING_PLAN.md`. None of them has started. |

---

## Next up — in this order

Each line names its section below. ★ = a human decision or human-only task.

1. ★ **Decide on 2.7.5** (Model track → "The 2.7.5 decision"). The current
   server code only deploys with a manifest-bound bundle, so this decision also
   decides when the server fixes reach users.
2. **Fix the EXIF leak and the privacy-policy claim it contradicts**
   (Security). The bundled policy promises something the app does not do.
   Small change, and the best privacy return on the list.
3. **Inference admission control** (Security). A semaphore before decode on
   `/api/analyze` and `/api/identify`, returning 503.
4. **Cost-abuse bundle** (Security). Per-IP daily quotas on the LLM and
   PlantNet endpoints, `common_name` in the species cache keys, a cap on the
   `/api/progression` list. Each is small; together they close three findings.
5. ★ **Cut the 1.9 tag and the RuStore build.** If you ship 2.7.5, tag the
   server commit you deploy at the same time: DEPLOYMENT.md's checklist says
   "deployed from a tagged commit", and no tag exists yet.
6. **Re-assert determinism after §3** (Model track → code-only). A one-cell
   notebook fix. Land it before the next Colab run, or that run is
   non-deterministic again.
7. ★ **Start v9 collection**: early, dull scorch, 400 families with boxes. It
   takes calendar time, so start now. In parallel, with no collection needed:
   adjudicate the 80 export rows and carve T_v1.
8. **`AnalysisActivity` recreate bug** (Architecture → Android). The one
   outright Android correctness bug.
9. **`build_analyze_payload()` extraction** (Architecture → Server). The best
   value-for-effort refactor, and it unblocks testing the response shape.

---

## App

- [ ] ★ **Cut the 1.9 tag and the RuStore build.** Human-only.
- [ ] **First-launch intro video.** *Externally blocked* on the user-generated
      Veo clip (vertical 9:16 macro monstera, scan-light sweep, `#16A34A`,
      ~8 s seamless loop, no text). The plan is a server-hosted clip (~3 MB,
      background download, an `/api/aqua`-style manifest) above the consent
      page. Nothing exists in the tree yet. Onboarding is now a single consent
      page with no Lottie (`ui/onboarding/OnboardingActivity.kt`), so the
      old "existing Lottie as offline fallback" plan no longer applies. Pick
      a new fallback, such as a static frame.
- [ ] **Wikipedia attribution.** `SpeciesInfoCardView` renders a Wikipedia
      extract with no source line (`ui/common/SpeciesInfoCardView.kt:207-211`).
      `SpeciesDetailActivity` only has a "Read on Wikipedia" button. CC BY-SA
      makes this a licensing question, not a design one. Related: the app
      deserializes `SpeciesInfoResponse.source` and `.translated`
      (`data/model/Models.kt:856-867`) and never reads either, so a
      machine-translated RU extract is shown unmarked.
- [ ] **No test drives the real map hit-test.** `MapCanvasView` is still
      `private` (`ui/widget/WorldMapView.kt:228`), and the hit test
      (`onTouchEvent` → `hitRegion.contains`, `:368-392`, EVEN_ODD regions at
      `:420-433`) is untested. Make the class internal and dispatch a
      `MotionEvent`. Also fix `WorldMapGeometryTest.kt:10-11`, whose KDoc
      claims `WorldMapViewTest` drives the hit test end to end. It does not.

---

## Garden refactor — follow-ups

The Garden tab is one surface: the do-today queue over room cards, each room
holding a plant collection. The care month grid is its own screen
(`ui/garden/care/CareCalendarActivity`), and the lunar calendar is per-plant,
on the plant detail screen. Room DB `version = 9` (`data/local/Database.kt:876`),
`MIGRATION_1_2`…`MIGRATION_8_9` registered (`:1182-1191`).

- [ ] ★ **Human gate: the health heuristic.** The plant header and the Garden
      card accent read `util/PlantHealth.kt`. A wrong "needs attention"
      verdict is worse than none. Check the thresholds (`:41-48`, all in one
      block) against real scan histories. One defect to settle during the
      review: `score >= SCORE_WATCH_MIN` and `else` both return `WATCH`
      (`:83-84`), so `SCORE_WATCH_MIN` does nothing and a declining plant is
      indistinguishable from a borderline one at the status level. Either
      drop the constant or give the low band its own status.
- [ ] **Room-level care defaults.** `RoomEntity.baselineLux`
      (`Database.kt:591`) is reserved for this and read by nothing. Its KDoc
      says the Light Meter seeds it, but nothing writes it, and
      `LightMeterActivity` has no room link. There is no humidity field. Rooms
      group and filter today; nothing derives care from them.

---

## Security — open findings

From the August 2026 audit: 49 raised, 38 verified, 22 confirmed. The fixed
ones are in git history. Ordered by value.

- [ ] **EXIF GPS leaves the device, and the privacy policy says it cannot.**
      `privacy_policy.html:103-104` promises "the crop step re-encodes every
      photo before it is sent, so location tags never leave the device". That
      holds for the main camera → `CropActivity` flow. It does not hold for
      **plant-detail "New scan"** (system camera `PlantDetailActivity.kt:145-149`,
      `:291-295`; gallery import `:179-187` through a raw `copyUriToFile`).
      Those paths skip the cropper, and `AnalysisActivity.kt:367` stores the
      unprepared file as `scan.imagePath`. Two later uploads post that file
      raw: the negative-feedback upload (`FeedbackController.kt:200` →
      `ScanRepository.kt:649-682`) and identify-from-a-saved-scan
      (`ui/result/CreatePlantSheet.kt:157-167` → `ScanRepository.kt:291-321`).
      `SpeciesIdentifyActivity` goes through the camera and cropper, so it is
      clean. Server side, `/api/feedback/image` writes
      the bytes verbatim (`feedback.py:393-437`), although its docstring
      (`main.py:3492-3495`) says "EXIF-stripped". `/api/identify` forwards the
      raw bytes to PlantNet (`main.py:3212-3214`). Fix on both ends: run
      `ImageUtils.prepareImageForUpload` (only caller today:
      `AnalysisActivity.kt:197`) on every upload path, and re-encode on
      receipt before storing or forwarding.
- [ ] **No inference admission control.** `analyze_image`
      (`main.py:2186`) reads up to 10 MB, decodes (`:2275`) and queues on an
      unbounded-queue `ThreadPoolExecutor(INFER_WORKERS)` (`:886`, `:916`).
      `/api/identify` feeds the same executor (`:3162`, `:3189`). There is no
      `Semaphore` in the server. At `INFER_WORKERS=1`, one client inside the
      2 req/s bucket pins RAM and starves everyone else. `--limit-concurrency
      64` is set only by DEPLOYMENT.md, not by `start.sh`. Add a semaphore
      before decode that returns 503.
- [ ] **No cost caps on the paid endpoints.** Per-IP daily quotas exist only
      on writes (feedback, community, opinions, likes, safety, telemetry).
      `/analyze`, `/identify`, `/chat`, `/care-plan`, `/species-diseases`,
      `/species-profile` and `/progression` have none. The middleware quota
      applies to API-key callers only (`main.py:1205-1209`). In practice:
      - The species-diseases cache keys on the client-chosen `name`
        (`ai_services.py:1538`), so every distinct string is a billable miss.
        `/species-profile` has the same problem (`:2020`).
      - `/api/chat` caps turns and tokens per `session_id`
        (`ai_services.py:1239-1258`), and the client mints that id
        (`ChatRequest`, `main.py:2591`). A new id resets the caps. No per-IP
        cap exists.
      - PlantNet has no server-side budget. The "5/min soft client cap"
        comment at `ai_services.py:65-67` describes code that does not exist.

      OpenRouter's monthly dollar cap bounds the worst case. PlantNet has no
      equivalent.
- [ ] **Prompt-cache poisoning.** `common_name` reaches the prompt through
      `_prompt_subject` (`ai_services.py:1550`, `:670-690`) but is not part of
      the `v2:{name}:{lang}` key (`:1538`), so one caller's `common` poisons
      the shared answer. `species_profile` has the same bug (key `:2020` vs
      prompt `:2038`). Put it in the key, or drop it from the prompt.
- [ ] **Unbounded list on `/api/progression`.** `ProgressionRequest.history:
      list[dict]` (`main.py:2885-2889`) has no `max_length`, and the endpoint
      (`:3338-3345`) runs `compare_progression_history` on the event loop.
      Only the ~12 MB body limit bounds it.
- [ ] **`/api/analyze` replays another user's `scan_id`.** The dedup key is
      `{image_sha}:{plantnet_lang}:{family}` (`main.py:2330`). A cache hit
      returns the original `scan_id` (`:2338-2352`), which authorises
      feedback mutations.
- [ ] **Forgeable second opinions (half fixed).** The durable
      `X-Community-Author-Key` exists, and the tally dedupes on
      `COALESCE(author_id, author_pseudo_id)` (`community.py:1194-1205`). But
      the header is still optional on the opinion endpoint (`main.py:3879`),
      so a client that omits it votes under a self-chosen pseudo-id. The only
      remaining bound is the 60/day per-IP opinion quota.
- [ ] **Pseudo-id in GET URLs.** The pseudo-id is a bearer capability, and URLs
      land in access logs. It travels as a path param on
      `GET /api/community/my-scan-opinions/{user_pseudo_id}`
      (`main.py:4130`, `ScanRepository.kt:880`), and as a query param on
      opinions, nearby, map and by-species (`main.py:4102`, `:4360`, `:4557`,
      `:4627`).
- [ ] **No lockfile.** `server/requirements.txt` pins four packages exactly
      (fastapi, uvicorn, python-multipart, Pillow) and gives only floors for
      the rest (numpy, onnxruntime, requests, firebase-admin, ImageHash).
      Starlette and httpx are unconstrained. CI turns a resolution break into
      a red job; a lockfile actually fixes it.
- [ ] **Host named publicly.** The host IP and SSH user `root` are in git
      history (`cd4bda2`, removed with the admin panel in #149). No credentials
      leaked. The action is ordinary hardening (`PermitRootLogin
      prohibit-password`, `PasswordAuthentication no`, SSH rate-limiting).
      DEPLOYMENT.md covers none of it today.

---

## Model track

`MODEL_VERSION = '2.7.5'` (notebook §1). The default mode is `combined`: gamma
ViT-S/14 plus the distilled Aqua MobileNetV3-Small. Per-run numbers and the
release-comparison rules are in `MODEL_HISTORY.md`. Use them, not this file.

### ★ The 2.7.5 decision

All of this is in `MODEL_HISTORY.md`, row 16 and the 2.7.5 notes.

**For shipping:**
- On 1,019 never-trained iNat photos, served disease mF1 is +0.059
  [+0.029, +0.091] over 2.6.8. Powdery recall goes 0.73 → 0.81, and pests
  precision goes 0.75 → 0.90.
- On the 160 holdout rows neither model saw, argmax mF1 is +0.131
  [+0.052, +0.216]. Served it is +0.100, but that CI straddles 0.
- No safety rate differs significantly.
- Aqua scores 0.801. 2.6.8's Aqua INT8 was degenerate (0.011).
- It has a calibrated served OOD gate and honest conformal coverage.
- It is the only bundle that boots the current server.

**Against:**
- 2.6.8 beats it on the full 535-row holdout: accuracy 0.940 vs 0.908
  (p=0.02), scorch recall 0.897 vs 0.810, and diseased → "healthy" 11 vs 25.
  The edge comes from the 375 older rows 2.6.8 may have trained on.
- Holdout healthy precision is 0.784: 25 of its 116 "healthy" calls are sick
  plants, 15 of them scorch.
- Scorch, nutrient, leaf_spot, viral and gray_mold have no never-trained
  comparison set at all.
- On the v2 sentinel, 2.7.5 scores 0/31 vs 31/31 and misreads more healthy
  hibiscus (7.9 % vs 2.6 %, p=0.02). This is weak evidence. 2.6.x trained on
  hibiscus through `pappu54`, and 2.7.5 has never seen the species, so the
  sentinel measures same-species training more than scorch skill.

**Cheap evidence before deciding (no GPU):** pair 2.7.5 against 2.7.2 on the
shared 535-row holdout with `tools/eval_bootstrap.py`. Neither model trained on
it, and the row-16 mF1 gain is still unconfirmed.

### Open — code only (no data needed)

- [ ] **Strict determinism is switched off by §3.** Ultralytics calls
      `unset_deterministic()` when `detector.train()` returns (§3 cell 8),
      and nothing re-asserts it. §1 cell 2 is the only place
      `use_deterministic_algorithms(True)` / `CUBLAS_WORKSPACE_CONFIG` are
      set, so 2.7.5 trained §4/§5 non-deterministically. Re-assert after §3
      (restore the cuBLAS env var first), preferably with `warn_only=True`.
      Record the warnings, the algorithm flag and the env var in
      `run_manifest.json`, since §6 records only the cudnn flags today.
- [ ] **The augmentation profile is unablated.** `TRAIN_AUGMENTATION_PROFILE`
      (§4 cell 10, `serve_parity_v2_unablated`, `efficacy_status: not
      established`) has no off switch. `sensor_artifact_p = 0` disables the
      block, but the version label is hard-coded. Add a flag that also
      relabels the profile, then run one paired run at `SEED=0` scored with
      `tools/eval_bootstrap.py` on capture families.
- [ ] **`paired_eval --argmax` has a weaker binding check than boot.**
      `_load_bundle_binding` (`tools/paired_eval.py:118-263`) accepts
      `size_bytes == 0` (`:210`) and follows symlinks (`:213`), where
      `bundle.verify_bundle` rejects both. Reuse the latter. Neither
      `predict()` nor `predict_served()` has a test, and
      `build_ood_benchmark.report()` has only one synthetic test.
- [ ] **OOD benchmark v3 tool gaps.** `build_ood_benchmark.py` hard-codes one
      source, one folder layout and a two-entry `CONDITION_MAP` with no
      `not_a_plant` mapping. It writes `manifest.csv`, but §6 reads
      `manifest_arms.csv`. Full list: `DATASET_LABELING_PLAN.md`, "OOD
      benchmark v3". This blocks building v3.
- [ ] **Aqua has no classifier-side OOD signal.** `AquaClassifier.classify()`
      is a plain softmax argmax (`util/AquaClassifier.kt:46-53`). Aqua
      abstains only on blur or a `not_a_plant` argmax, and its `classes.json`
      carries no temperature or OOD block. A MaxLogit floor fitted in §5 is
      the cheap version.

### Data — the critical path

Specified in `DATASET_LABELING_PLAN.md` (P0 is the minimum, "Sequencing" is
the order). Not started.

- [ ] ★ **Early-stage marginal necrosis, 400 families with boxes.** That
      means `brown_frac < 0.05`, damage confined to the edge, and the blade
      still mostly green. Judge each one against `leaf_spot` exemplars:
      scorch↔`leaf_spot` is the boundary that fails, while scorch↔healthy
      already probes at 0.95. On 2.7.5's holdout, scorch is the class most
      often called healthy (15 of the 25 wrong "healthy" calls). Boxes matter because Stage 1 returns no box on 0.386 of
      gray_mold, 0.381 of pests and 0.211 of scorch, against 0.191 for
      healthy (2.7.5 served pass). A run is worth doing at ≥ 200 scorch and
      ≥ 100 houseplant `leaf_spot` families.
- [ ] ★ **Locked test set T_v1.** Adjudicate the 80 export rows. This needs
      no collection. T is the only slice that can be fair to 2.6.8 and to a
      new candidate alike, and it must be carved before any export row
      enters a supplement.
- [ ] ★ **OOD benchmark v3**: 8–12 species, plus a non-plant /
      difficult-plant arm. The non-plant arm needs no plants and can go first.
      Until v3 exists, `REQUIRE_OOD_BENCHMARK` stays `False`.

### Open experiments that are not data

None of these is expected to fix scorch alone. All of them fit alongside the
collection round.

- [ ] **Capacity: `frontier` (ViT-B/14) or DINOv3.** Scorch-vs-`leaf_spot`
      sits at 48.4 % on a linear probe of the frozen ViT-S features. That is a
      fine-grained dense-feature problem. `frontier` is a plain mode and costs
      one run. **Check DINOv3's licence before committing.** It is not
      DINOv2's Apache-2.0.
- [ ] **`USE_REGISTERS`** (§4, default `False`). `dinov2_vits14_reg` gives
      cleaner features. One flag, one run, one paired bootstrap.
- [ ] **The crop-vs-whole gap.** 2.7.5's end-to-end `served_vote` scores 0.824
      against 0.865 on the whole image, and nutrient loses most
      (0.86 → 0.69). Diagnose why crop and vote dilute nutrient before
      treating segmentation (2.8) as the fix.
- [ ] **Domain-robust training objective.** Something stronger than a
      sampling nudge.

### Settled — do not retry

Each of these was run as a single knob and measured. Re-proposing one costs a
run and returns nothing. The notebook's §1 falsification record points here.
The scorch numbers were measured on the v2 hibiscus arm, which is a regression
alarm, not an independent instrument (see 2.7.5 above). Each verdict still
stands on its own evidence.

| lever | verdict | evidence |
|---|---|---|
| `CLASS_LOGIT_BIAS` | **dead** | on 2.7.3, the best bias within the 0.12 false-fire ceiling is 1.44, which gives hibiscus 0.323. Reaching the floor needs false-fire 0.417, i.e. a third of never-trained healthy plants misdiagnosed |
| regime-aware sampling (2.7.3) | **falsified** | stratum draw share 25 % → 50 %, hibiscus 3/31 → 4/31, Fisher p=1.000. Code removed |
| regime-stratified conformal | **falsified** | marginal coverage 0.967; low-chroma 0.972 vs rest 0.965, so the dull stratum was already better covered. Code removed |
| margin-preserving preprocessing | **not supported** | probe delta +0.013 against a +0.03 bar set in advance; letterboxing at inference made 2.7.2 *worse*, 3/31 → 1/31 |
| sampler weights generally | **falsified** | 2.7.1's `kaggle_ornamental` ablation (1.0 → 0.35) did not move ornamental scorch at all and cost healthy (recall 0.947 → 0.861, McNemar p=0.0074) |
| temperature refitting | **arithmetically impossible** | `argmax(z/T) == argmax(z)` for every `T > 0`: it moves confidence and set sizes, never a prediction |

**Run-to-run noise is ~2 pp.** Two runs of an identical config at `SEED=0`
gave `val_indomain` 0.884 and 0.864. Read any single-knob delta inside that
band as noise.

### 2.8 — the leap

> From "a good leaf classifier" to a **holistic, trustworthy plant doctor**.

1. **Real-distribution data at scale.** Production scans are what users
   actually shoot. DoctorP (real app photos) is already the source that
   supplies the starved classes.
2. **Multi-label instead of single-label.** A softmax cannot say
   "underwatered **and** nutrient-deficient". That calls for a sigmoid head,
   per-class thresholds and per-class conformal. Do it after the boundary is
   stable, never during.
3. **Segmentation, not a box.** Lesion **area**, so severity is measured
   rather than heuristic and one diseased leaf is not diluted across a whole
   frame.

---

## Architecture follow-ups

From the 2026-08 review: the server's domain layer and the app's layering are
sound, and the debt sits in a few god-modules and in HTTP-layer idiom. Each
item below needs its own PR. Don't fold one into another change.

**Server**

- [ ] **`analyze_image` is ~400 lines** (`main.py:2187-2585`), and roughly 150
      of them are response assembly. Extract `build_analyze_payload()`. That
      makes the entropy guard, the dual-care split and the `not_a_plant`
      species suppression unit-testable without a TestClient. Best
      value-for-effort item here.
- [ ] **Split `main.py` (5,438 lines) into routers.** It holds 38 handlers on
      one `APIRouter`, 6 middlewares, 15 Pydantic models, 2 thread pools, the
      limiter/ban/quota state, the metrics and the lifespan. `Depends` is
      used 0 times. Order of work:
      1. Extract `deps.py` so the hand-copied quota preambles become one
         `Depends`.
      2. Replace the ~408-line duplicated alias-import block (`:66-473`) with
         module imports.
      3. Move one router group per PR, smallest first, community last.

      Hard constraint: `conftest.py` patches names on `main` in place (the
      `FakePipeline` swap), and 10 test modules `importlib.reload` the
      modules main imports from. So a router doing `from main import
      pipeline` binds before the patch. `openapi.json` and
      `test_openapi_contract.py` catch a dropped route.
- [ ] **Group the Pydantic models into `schemas.py`** with shared `Annotated`
      types. `scan_id` is `8..64` plus a charset check in two models
      (`main.py:2819`, `:2969`) and `1..128` with no charset check in three
      (`:3863`, `:4024`, `:4218`). Each item in `CommunityViewRequest.scan_ids`
      is unbounded. `user_pseudo_id` has the same split. Standardise on the
      looser bound; tightening it is a separate decision, because the Android
      client mints these ids.
- [ ] **No `response_model` on any route.** So `openapi.json` describes
      request bodies only, and the 35-line contract test compares paths and
      methods. The ~30-key `/api/analyze` payload can change without failing
      CI while a frozen APK parses it. Cheapest first step: assert the
      response keys in the contract test.
- [ ] **`ai_services.py` (3,453 lines) is three modules**: transport, five
      features, and ~700 lines of bilingual content that belongs with
      `diseases_data.py`. `community.py` (2,892 lines, 62 functions) wants to
      become a package.
- [ ] **Five hand-rolled SQLite stores** (`community`, `feedback`,
      `labeling`, `push`, `telemetry`). Each has its own WAL `_connect` and
      `init_db`, `_add_column_if_missing` and `prune_older_than` are copied
      three times each, and there is no `PRAGMA user_version`, so every boot
      replays every migration. The five `daily_*_count_for_ip_hash` functions
      now all use a rolling 24 h window. `telemetry.daily_writes`
      (`telemetry.py:139`) is the odd one out, counting from UTC midnight.
- [ ] **Nothing type-checks the annotations.** No mypy or pyright config, and
      not in CI.

**Android**

- [ ] **`AnalysisActivity` re-runs a paid inference on recreate.** Rotation is
      locked (`screenOrientation="portrait"`, `AndroidManifest.xml:60-63`),
      but night mode, locale, font scale and multi-window resizes still
      recreate it. `onCreate` calls `checkBlurAndAnalyze()` unconditionally
      (`ui/analysis/AnalysisActivity.kt:103`), and the server round-trip plus
      the Room write run in `lifecycleScope` (`:166`). A recreate between the
      response and `saveScan` loses a scan the server already issued a
      `scan_id` for. On `targetSdk` 36, Android 16 ignores orientation locks
      on large screens, so bumping from 35 brings rotation back. The only
      outright correctness bug the review found.
- [ ] **`ScanRepository` is a 1,708-line god object** spanning seven bounded
      contexts, with ~29 dependents (17 field injections, 4 EntryPoint
      lookups, 6 controllers). Split it by context.
- [ ] **Programmatic view construction.** `DetailExtraCardsController` (1,312
      lines) and `CarePlanCardView` (1,186) build whole hierarchies in code
      with hardcoded dp and colours. `DetailUiKit.baseCard()` exists for this
      but has 2 call sites, and `DetailExtraCardsController` builds 8
      `MaterialCardView`s by hand. The two styles look different on screen
      today.
- [ ] **Retire Gson.** It is still live for Room field JSON, `ResponseCache`
      and intent payloads (5 importing files). That keeps 389 hand-mirrored
      `@SerializedName` in `Models.kt`, a `KSerializer<JsonObject>` bridge and
      the Gson ProGuard rules alive. M/L effort.
- [ ] **`data/GuideDataProvider.kt` (1,638 lines) is a second localization
      system.** It has 78 EN/RU pairs as Kotlin strings outside the resource
      framework, which drive 41 `LocaleHelper.isRussian(` branches across 29
      files. Nothing enforces RU parity there.
- [ ] **Navigation Component manages a two-destination graph**
      (`res/navigation/nav_graph.xml`), while 23 Activities navigate by
      explicit Intent. It now costs more than it contributes.
- [ ] **No DAO returns a `Flow`**, so screens re-read on resume. That is the
      root cause of the 17 `launch(start = UNDISPATCHED)` sites, 16 of them
      inside `NonCancellable`. The odd one out,
      `GardenAttachController.attachScanToPlant` (`ui/result/GardenAttachController.kt:133`),
      can lose the attach if the activity finishes mid-write. Migrate one
      screen per PR, after the `AnalysisActivity` and `ScanRepository` items.
- [ ] **Ratchet the detekt thresholds down** as the splits land: LongMethod
      210, LargeClass 1300 (`android/config/detekt/detekt.yml`). Its
      "worst today" comment (ScanRepository at 1,165) is stale.
      `GuideDataProvider.kt` stays excluded from the size rules until it stops
      being a content blob.

---

## Documentation debt

None of it blocks a release.

- [ ] **README licence section under-lists runtime deps** (`README.md:316-323`).
      Name osmdroid (`build.gradle.kts:391`) and the RuStore appupdate/review
      SDKs (`:337`, `:345`).
- [ ] **`android/app/build.gradle.kts:407-409` still says the Aqua model is
      provisioned at `assets/models/aqua_classifier.tflite`.** It is
      downloaded into `filesDir/aqua/` (`AquaModelManager`), as `:78-79` of
      the same file says.
- [ ] **Notebook header is two versions stale.** Cell 0 is titled
      "PlantVision 2.6" and says "v2.5 continues maximising gamma".
- [ ] **Code comments cite this file by old section names.** `Database.kt:590`
      cites "ROADMAP §4", and the `PlantHealth` KDoc quotes a line this file
      no longer carries. The Garden references resolve to "Garden refactor —
      follow-ups" above. Point the stragglers there when the code is next
      touched.

---

## Later / maybe

- [ ] **Spider-mite split** from `pests` (deferred; `pests` precision is already
      high).
- [ ] **More UI languages.** Server content is already bilingual.
- [ ] **Expert-human consultation.** Ties into 2.8 safety escalation and
      builds on the shipped second-opinion loop.

---

## Decisions

- **Reliability and safety first.** Optimise against a confident wrong
  diagnosis (precision, honest uncertainty, OOD/blur refusal) over headline
  recall/F1.
- **Data over architecture.** The ceiling is data and precision. Spend on the
  flywheel and scope before backbone size.
- **One species surface.** `SpeciesDetailActivity` is the only species
  screen. Everything else embeds `ui/common/SpeciesInfoCardView`. Never build
  a second card.
- **New knowledge cards are best-effort, never blocking.** LLM plus cache,
  with a graceful `available:false`.
- **No new disease classes until the scorch/nutrient boundary is stable.**
  Scope expansion waits for 2.8.
- **Target store: RuStore** (RU audience, app already bilingual). No Google
  Play.
- **No Compose rewrite, single `:app` module.** Keep XML Views and modernise
  the guts (Hilt, Ktor). Do not modularise. The one util → ui edge that
  blocked a `:core:data` extraction is fixed anyway.
- **Stay free and honest.** Calibrated, OOD-aware, on-device offline,
  privacy-first. There is no account, login, email or password. Community V2
  creates a persistent anonymous per-device handle only after Community
  opt-in or an explicit one-scan share. Account linkage stays deferred. The
  handle and same-device linkability are disclosed in `privacy_policy.html`.
- **The data loop is local.** Collect and judge offline → Kaggle supplement →
  the notebook pulls it.

---

## Working agreements

**Training runs on Colab, never locally.** The target is a Colab A100 (40 GB
VRAM, 80 GB RAM, 100 GB disk) driving `server/train_notebook.ipynb`, ≈20–35 min
per full run including data prep. For ML work, edit the notebook or the
pipeline code and hand the maintainer the cells to run.

**Model choice for delegated work.** *Is there already a pattern in this repo
to follow?* Yes → **Sonnet 5**. It follows instructions literally, so spell
out the scope. New architecture across many files → **Opus 5** (`effort:
xhigh`, generous `max_tokens`). It verifies its own work, so "double-check
your answer" makes the output *worse*, and it expands scope readily, so state
the boundary. Nobody has decided what it should be → **Fable 5**, once, to
decide it. Omit `thinking` entirely; it degrades when you enumerate steps, so
give intent and constraints, then get out of the way.

**RU string review is a standing gate.** Machine-written Russian UI copy reads
wrong in ways tests never catch. Review the `values-ru/strings.xml` diff on
every merge.

**CI** is described in `CLAUDE.md` and in the header of
`.github/workflows/ci.yml`. The one rule to remember: a branch that matches no
push glob gets no CI, and its PR shows an empty status rather than a red one.
That is how #158–#167 merged unbuilt. Adding any branch automation means
adding its prefix to the trigger list in the same commit.
