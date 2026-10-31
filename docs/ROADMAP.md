# PlantDiseases — roadmap

**Open work only.** Finished work lives in git history; every model run, with its
numbers, lives in `MODEL_HISTORY.md`. Companions: `ACCOUNTS_PRODUCTION_PLAN.md`
(accounts release, steps and gates), `DATASET_LABELING_PLAN.md` (data loop, v9
protocol), `../DEPLOYMENT.md` (deploy, Android signing/FCM),
`../server/models/MODEL_CARD.md` (version-agnostic pipeline overview).

★ = needs a human decision or a human-only task. Code-checkable items were
re-verified against the tree on 2026-10-31; data and human-task items are as last
recorded. Line counts are approximate; re-measure before acting on one.

---

## Status

| track | now |
|---|---|
| **App** | 1.9 / versionCode 19. Accounts merged to `main` (#182); unsigned by the permanent key, not on RuStore yet. |
| **Model** | 2.7.5 deployed (dataset v8). Weak spot: scorch read as healthy. A statistical tie with 2.7.2 on the shared holdout. |
| **Accounts backend** | Live on the production VDS (`/id/`, VPN-only until launch); real Yandex flow, deletion and backups verified. |
| **Data** | v8 is the corpus. v9 collection, locked test set T and OOD benchmark v3 are specified; none started. |

---

## Next up — in this order

- [ ] ★ **Ship accounts to RuStore** (section below).
- [ ] ★ **Start v9 collection** — early, dull scorch, 400 families with boxes. It
      takes calendar time, so start now. In parallel (no collection needed):
      adjudicate the 80 export rows and carve T_v1.
- [ ] ★ **Run the paired ON/OFF augmentation ablation on Colab at `SEED=0`** (human
      run; Model track, "Experiments that are not data").
- [ ] **Split `main.py` into routers**, starting with `deps.py`.

When 2.7.5's successor ships, refresh `site/index.html` (list in `site/README.md`),
the README status section and `docs/assets/per-class.svg`.

---

## Ship accounts to RuStore

Details and current state per step: `ACCOUNTS_PRODUCTION_PLAN.md` (steps 7–10).

- [ ] ★ **Protected `android-signing` environment.** Re-enter the four `RELEASE_*`
      signing secrets from the original keystore (reviewers, `main`-only branch rule).
      The URLs, RuStore IDs and cert fingerprint now live in the workflow file.
- [ ] **Signed APK from the protected workflow**, after green `main` CI. Verify APK
      digest, certificate and endpoints.
- [ ] ★ **Real-provider test of the permanently signed APK** (signup, cancel,
      refresh, logout, deletion). Confirm the Yandex console holds that key's
      certificate fingerprint.
- [ ] ★ **RuStore listing** — console clicks, APK upload, release notes, privacy
      page check. Cut the 1.9 tag only after the above.
- [ ] ★ **Operator legal/region sign-off** on the privacy policy (data stored on the
      German VDS).
- [ ] ★ **Host housekeeping.** SSH key-only login (`PermitRootLogin
      prohibit-password`, `PasswordAuthentication no`, rate-limiting), backup key
      copied off the VDS, alerts on failed backups and `journal_unavailable`
      deletion retries. The host IP and `root` user sit in git history (`cd4bda2`);
      no credentials were ever committed.
- [ ] **Cost-cap release gate** (see Security).
- [ ] **Old-client compatibility.** Older app versions will call the new server and
      have not been tested against it.

---

## Garden

- [ ] ★ **Check `util/PlantHealth.kt` thresholds against real scan histories.**
      The tunables are reasoned, not measured. Compare with a few real plants
      before trusting a red "Poor condition".

---

## Security

Config keys behind every limit: `DEPLOYMENT.md` §3.2.

- [ ] **Cost caps — release gate.** Per-feature daily admission, per-IP allowances
      and serialized Pl@ntNet attempts are in. Still required before launch: a
      reviewed model/route-specific aggregate OpenRouter price ceiling and live
      alert evidence. Until then each new species name is a billable cache miss.
- [ ] **`/api/metrics`:** add a short TTL cache; each request recomputes the
      store snapshots.
- [ ] **`/api/plant-photo`:** bound the WebP variants on the serving side (the LRU
      already has a byte budget).
- [ ] **Feedback images are never deleted.** Retention prune covers rows, not the
      image directory.
- [ ] **No lockfile.** Runtime pins mix exact versions and floors; transitive deps
      (starlette, httpx2) are unconstrained. CI turns a resolution break red, but a
      lockfile is the real fix.

---

## Model track

`MODEL_VERSION = '2.7.5'` (notebook §1, matches the deployed bundle). Default mode
`combined`: gamma ViT-S/14 plus the distilled Aqua MobileNetV3-Small. Numbers and
comparison rules: `MODEL_HISTORY.md`.

- [ ] **Watch feedback correcting "healthy" to scorch**
      (`server/tools/analyse_training_data.py`). 15 of 2.7.5's 25 wrong "healthy" calls
      on the holdout are scorch; production feedback is the only real-distribution
      signal until T exists.

### Code only (no data needed)

- [ ] **`paired_eval --argmax` binding check is weaker than boot.**
      `_load_bundle_binding` accepts `size_bytes == 0` and follows symlinks, where
      `bundle.verify_bundle` rejects both — reuse it. Also untested: `predict()`,
      `predict_served()`, and `build_ood_benchmark.report()` beyond one synthetic test.
- [ ] **OOD benchmark v3 tool gaps** (blocks building v3): one hard-coded source and
      layout, a two-entry `CONDITION_MAP` with no `not_a_plant`, and it writes
      `manifest.csv` where §6 reads `manifest_arms.csv`. Full list in
      `DATASET_LABELING_PLAN.md`.
- [ ] **Aqua has no classifier-side OOD signal.** `AquaClassifier.classify()` is a
      plain softmax argmax; it abstains only on blur or `not_a_plant`. A MaxLogit
      floor fitted in §5 is the cheap fix.

### Data — the critical path

Specified in `DATASET_LABELING_PLAN.md`. Not started. The production recipe needs the
Kaggle houseplant supplement; the private one was still at v8 at the last check
(2026-08-23), which fails the strict v9 manifest contract. A compliant v9 under the
same handle is picked up automatically.

- [ ] ★ **Early-stage marginal necrosis, 400 families with boxes**
      (`brown_frac < 0.05`, edge-confined, blade mostly green), judged against
      `leaf_spot` exemplars. Worth a run at ≥ 200 scorch and ≥ 100 houseplant
      `leaf_spot` families. Boxes matter: Stage 1 returns no box on 21 % of scorch
      vs 19 % of healthy.
- [ ] ★ **Locked test set T_v1.** Adjudicate the 80 export rows; carve T before any
      export row enters a supplement. It is the only slice fair to the deployed
      model and a candidate alike.
- [ ] ★ **OOD benchmark v3** — 8–12 species plus a non-plant arm (the non-plant arm
      needs no plants; do it first). `REQUIRE_OOD_BENCHMARK` stays `False` until it exists.

### Experiments that are not data

None is expected to fix scorch alone; all fit alongside the collection round.

- [ ] **Capacity:** `frontier` (ViT-B/14) or DINOv3 (one run; **check DINOv3's
      licence** — not DINOv2's Apache-2.0).
- [ ] **`USE_REGISTERS`** (§4, default `False`) — one flag, one run, one paired bootstrap.
- [ ] **Augmentation ablation** (§4 `TRAIN_AUGMENTATION`, default `'on'`). One
      `'off'` run (geometry only, profile `serve_parity_v2_geometry_only`) vs a
      default run, both `SEED=0` on the same corpus; score with
      `tools/eval_bootstrap.py` on capture families. Human Colab run.
- [ ] **Crop-vs-whole gap.** `served_vote` 0.824 vs 0.865 whole-image; nutrient
      loses most (0.86 → 0.69). Diagnose before treating segmentation as the fix.
- [ ] **Domain-robust training objective** stronger than a sampling nudge.

### Settled — do not retry

Each was run as a single knob and measured; re-proposing costs a run. (Scorch numbers
come from the v2 hibiscus arm, a regression alarm rather than an independent
instrument.)

| lever | verdict | why |
|---|---|---|
| `CLASS_LOGIT_BIAS` | dead | best bias inside the 0.12 false-fire ceiling gives hibiscus 0.323; the floor needs 0.417 false-fire |
| regime-aware sampling | falsified | hibiscus 3/31 → 4/31, p=1.000 |
| regime-stratified conformal | falsified | dull stratum already better covered (0.972 vs 0.965) |
| margin-preserving preprocessing | not supported | probe +0.013 vs a +0.03 bar; letterboxing made 2.7.2 worse |
| sampler weights generally | falsified | `kaggle_ornamental` 1.0 → 0.35 left scorch flat, cost healthy recall (McNemar p=0.0074) |
| temperature refitting | impossible | `argmax(z/T) == argmax(z)`; moves confidence only |

Run-to-run noise is ~2 pp at `SEED=0`; read any single-knob delta inside it as noise.

### 2.8 — from leaf classifier to plant doctor

Not started; waits for a stable scorch/nutrient boundary.

- [ ] **Real-distribution data at scale** (production scans; DoctorP already feeds starved classes).
- [ ] **Multi-label** — sigmoid head, per-class thresholds and conformal. After the boundary is stable, never during.
- [ ] **Segmentation, not a box** — measured lesion area for severity.

---

## Architecture follow-ups

Each item is its own PR; don't fold one into another.

**Server**

- [ ] **Split `main.py` (~6,640 lines) into routers.** 42 routes on one
      `api_router`, 6 middlewares, 16 Pydantic models, 2 thread pools, limiter/ban/
      quota/admission state, metrics, lifespan. `Depends` appears only as the
      `community_bearer` route dependency; the quota preambles are still
      hand-copied. Order: (1) `deps.py`; (2) replace the ~400-line duplicated
      alias-import block with module imports; (3) one router group per PR,
      smallest first, community last. Constraint: `conftest.py` patches names on
      `main` in place and 10 test modules `importlib.reload`, so a router doing
      `from main import pipeline` binds before the patch.
- [ ] **Group Pydantic models into `schemas.py`** with shared `Annotated` types.
      `scan_id` is `8..64` + charset in some models and `1..128` with no charset in
      others; `scan_ids` items are unbounded. Standardise on the looser bound
      (the Android client mints these ids).
- [ ] **Add `response_model` to routes.** `openapi.json` describes request bodies
      only. `/api/analyze` is pinned by `tests/test_analyze_payload.py`; every other
      response a frozen APK parses can change without failing CI.
- [ ] **Break up `ai_services.py` (~3,600 lines)** — transport, five features, ~700
      lines of bilingual content for `diseases_data.py` — and turn `community.py`
      (~4,400 lines) into a package.
- [ ] **Consolidate the five SQLite stores** (`community`, `feedback`, `labeling`,
      `push`, `telemetry`): shared WAL `_connect`/`init_db`, one
      `_add_column_if_missing` / `prune_older_than`, and `PRAGMA user_version` so a
      boot stops replaying every migration. `telemetry.daily_writes` still counts
      from UTC midnight where the others use a rolling 24 h.
- [ ] **Type-check the annotations** — no mypy/pyright config, not in CI.

**Android**

- [ ] **Split `ScanRepository` (~1,740 lines)** by context (species already moved to
      `SpeciesRepository`); ~29 dependents.
- [ ] **Replace programmatic view construction.** `DetailExtraCardsController`
      (~1,300) and `CarePlanCardView` (~1,190) build hierarchies in code with
      hardcoded dp/colours; `DetailUiKit.baseCard()` has few call sites.
- [ ] **Retire Gson** — still live for Room field JSON, `ResponseCache`, intent
      payloads. Keeps ~389 mirrored `@SerializedName`, the `KSerializer<JsonObject>`
      bridge and the Gson ProGuard rules alive. M/L effort.
- [ ] **Fold `GuideDataProvider.kt` (~1,640 lines) into the resource framework.** 78
      EN/RU pairs as Kotlin strings drive ~40 `LocaleHelper.isRussian(` branches; no
      RU-parity enforcement.
- [ ] **Drop Navigation Component** — a two-destination graph while 30+ Activities
      navigate by Intent.
- [ ] **DAOs returning `Flow`.** Today screens re-read on resume, which is the root
      of the 17 `launch(start = UNDISPATCHED)` sites. `GardenAttachController.
      attachScanToPlant` can lose the attach if the activity finishes mid-write.
      One screen per PR, after the `ScanRepository` split.
- [ ] **Ratchet detekt down** as splits land (LongMethod 210, LargeClass 1300 in
      `android/config/detekt/detekt.yml`; its "worst today" comment is stale).
      `GuideDataProvider.kt` stays excluded until it stops being a content blob.

---

## Documentation debt

None blocks a release.

- [ ] ★ **README app screenshots** (scan result, garden, care calendar, community
      map) → `docs/assets/screenshots/` + a row in "What it does". Needs a device.
- [ ] **`docs/assets/per-class.svg`** holds hand-copied numbers; regenerate with
      `generate_assets.py` when a model ships.
- [ ] **`android/app/build.gradle.kts` (~line 851)** still says Aqua is provisioned
      at `assets/models/aqua_classifier.tflite`; it is downloaded to `filesDir/aqua/`.
- [ ] **Notebook cell 0** is titled "PlantVision 2.6" and says "v2.5 continues
      maximising gamma".

---

## Later / maybe

- [ ] Spider-mite split from `pests` (deferred; precision already high).
- [ ] More UI languages (server content is already bilingual).
- [ ] Expert-human consultation (ties into 2.8 safety escalation; builds on the second-opinion loop).

---

## Decisions

- **Reliability and safety first.** Precision, honest uncertainty and OOD/blur
  refusal over headline recall/F1.
- **Data over architecture.** The ceiling is data and precision.
- **One species surface.** `SpeciesDetailActivity` only; everything else embeds
  `ui/common/SpeciesInfoCardView`.
- **Knowledge cards are best-effort**, never blocking (LLM + cache, graceful `available:false`).
- **No new disease classes** until the scorch/nutrient boundary is stable.
- **Target store: RuStore.** No Google Play.
- **No Compose rewrite, single `:app` module.**
- **Stay free and honest.** Calibrated, OOD-aware, offline-capable, privacy-first.
  Diagnosis and Community reading need no account; Community writes need a verified
  Yandex sign-in (no email/password in production). Posts stay linked to the
  pseudonymous actor until the account is deleted.
- **Not planned:** a first-launch intro video; room-level care defaults
  (`RoomEntity.baselineLux` stays unused).
- **The data loop is local.** Collect and judge offline → Kaggle supplement → notebook pulls it.

---

## Working agreements

- **Training runs on Colab, never locally** (A100, ≈20–35 min per run) via
  `server/train_notebook.ipynb`. Edit the notebook or pipeline code and hand the
  maintainer the cells.
- **Model choice for delegated work.** Pattern already in the repo → Sonnet 5 (spell
  out scope). New architecture across many files → Opus 5 (`effort: xhigh`; state the
  boundary, don't ask it to double-check). Undecided design → Fable 5, once. Omit
  `thinking`; give intent and constraints.
- **RU string review is a standing gate.** Review the `values-ru/strings.xml` diff on every merge.
- **CI:** see `CLAUDE.md` and the `.github/workflows/ci.yml` header. A branch matching
  no push glob gets no CI (that is how #158–#167 merged unbuilt); any new branch
  automation adds its prefix to the trigger list in the same commit.
