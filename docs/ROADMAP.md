# PlantVision — roadmap

**Open work only.** Finished work lives in git history; every model run, with
its numbers, lives in `MODEL_HISTORY.md`. Companions: `DATASET_LABELING_PLAN.md`
(the data loop, the v9 protocol, every dataset finding), `../DEPLOYMENT.md`
(deploy + Android signing/FCM), `../server/models/MODEL_CARD.md`
(version-agnostic; per-run numbers ship as `model_card.json` in each bundle).

Every item below was re-verified against the tree on 2026-10-09, after the
security round on `claude/epic-noether-jl9kez`. Line references are as of
that branch.

---

## Where things stand

| track | state |
|---|---|
| **App** | `versionName` 1.9 / `versionCode` 19 (`android/app/build.gradle.kts:31-32`), carrying the Garden rooms / care calendar / treatment plans, the Option-A community opinion sheet and `minSdk` 31. RuStore uploads are done by hand. |
| **Model** | **2.7.5 is deployed** (trained 2026-10-05 on dataset v8, the first manifest-bound bundle). Its weak spot is scorch read as healthy (`MODEL_HISTORY.md` row 16). |
| **Server** | 39 routes, fail-closed bundle contract, all optional integrations degrade to `available:false`. |
| **Security** | The August audit is closed in code. Second opinions need the author key 1.9 sends, so older builds can no longer post them (decided). One follow-up is open: likes and views. |
| **Data** | v8 is the training corpus. v9 collection, the locked test set T and OOD benchmark v3 are specified in `DATASET_LABELING_PLAN.md`. None of them has started. |

---

## Next up — in this order

Each line names its section below. ★ = a human decision or human-only task.

1. ★ **Start v9 collection**: early, dull scorch, 400 families with boxes. It
   takes calendar time, so start now. In parallel, with no collection needed:
   adjudicate the 80 export rows and carve T_v1.
2. **Bump `MODEL_VERSION`** before the next Colab run (Model track → "2.7.5
   in production"). A one-line notebook change.
3. ★ **Review the health heuristic** (Garden). One threshold does nothing
   today, and a wrong "declining" verdict is worse than none.
4. **Split `main.py` into routers, starting with `deps.py`** (Architecture →
   Server). The next server refactor in line.

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

---

## Security

The config keys behind every limit are in `DEPLOYMENT.md` §3.2.

- [ ] **Likes and views still accept a key-less pseudo-id.** The same
      forgery class as opinions, at lower stakes: a script can inflate a
      post's like or view count by changing the body pseudo-id. Likes are
      bounded by the per-IP like quota (300/day); views only by the rate
      limiter. Requiring the key here would also lock builds before 1.9 out
      of liking, as it already does for second opinions.

---

## Model track

`MODEL_VERSION = '2.7.5'` (notebook §1). The default mode is `combined`: gamma
ViT-S/14 plus the distilled Aqua MobileNetV3-Small. Per-run numbers and the
release-comparison rules are in `MODEL_HISTORY.md`. Use them, not this file.

### 2.7.5 in production — what to watch

The evidence is in `MODEL_HISTORY.md`, row 16 and the 2.7.5 notes. On photos
neither model trained on, 2.7.5 beats 2.6.8: served disease mF1 +0.059
[+0.029, +0.091] on 1,019 iNat photos, and +0.131 argmax on the 160 unseen
holdout rows. Its known weakness is the healthy lean. Holdout healthy
precision is 0.784, and 15 of its 25 wrong "healthy" calls are scorch.
Scorch, nutrient, leaf_spot, viral and gray_mold have no never-trained
comparison set yet.

- [ ] **Watch feedback that corrects "healthy" to scorch**
      (`server/tools/analyse_feedback.py` over the feedback store). That is
      where the holdout says 2.7.5 errs, and production feedback is the only
      real-distribution signal until T exists.
- [ ] **Confirm the holdout gain against 2.7.2.** Pair 2.7.5 against 2.7.2
      on the shared 535-row holdout with `tools/eval_bootstrap.py` (no GPU).
      Neither model trained on it, and row 16 still reads the mF1 gain as
      unconfirmed.
- [ ] **Bump `MODEL_VERSION`** (notebook §1, still `2.7.5`) before the next
      Colab run, so its bundle and `first_seen_model_version` stamps do not
      collide with the deployed one.

### Open — code only (no data needed)

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
      often called healthy (15 of the 25 wrong "healthy" calls). Boxes
      matter because Stage 1 returns no box on 0.386 of gray_mold, 0.381 of
      pests and 0.211 of scorch, against 0.191 for healthy (2.7.5 served
      pass). A run is worth doing at ≥ 200 scorch and
      ≥ 100 houseplant `leaf_spot` families.
- [ ] ★ **Locked test set T_v1.** Adjudicate the 80 export rows. This needs
      no collection. T is the only slice that can be fair to the deployed
      model and to a new candidate alike, and it must be carved before any export row
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
alarm, not an independent instrument: 2.6.x trained on hibiscus through
`pappu54` (`MODEL_HISTORY.md`, 2.7.5 notes). Each verdict still
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

- [ ] **Split `main.py` (5,955 lines) into routers.** It holds 39 handlers on
      one `APIRouter`, 6 middlewares, 15 Pydantic models, 2 thread pools, the
      limiter/ban/quota and admission state, the metrics and the lifespan. `Depends` is
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
      methods. `/api/analyze` is covered: `tests/test_analyze_payload.py` pins
      its key set through `build_analyze_payload()`. Every other response a
      frozen APK parses can still change without failing CI.
- [ ] **`ai_services.py` (3,459 lines) is three modules**: transport, five
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

- [ ] **`ScanRepository` is a 1,726-line god object** spanning seven bounded
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
      screen per PR, after the `ScanRepository` split.
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
- **Second opinions need the author key.** `community_require_author_key`
  stays `true`: app builds before 1.9 cannot post second opinions. The
  legacy pseudo-id URL forms stay for those builds, and the per-IP daily
  allowances keep their defaults.
- **Not planned:** a first-launch intro video, and room-level care defaults
  (`RoomEntity.baselineLux` stays an unused column, so no migration).
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
