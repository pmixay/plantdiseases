# PlantDiseases — roadmap

**Open work only.** Finished work lives in git history; every model run, with its
numbers, lives in `MODEL_HISTORY.md`. Companions: `DATASET_LABELING_PLAN.md` (data loop, v9
protocol), `../DEPLOYMENT.md` (deploy, Android signing/FCM),
`../server/models/MODEL_CARD.md` (version-agnostic pipeline overview).

★ = needs a human decision or a human-only task. Code-checkable items were
re-verified against the tree on 2026-11-14; data and human-task items are as last
recorded. Line counts are approximate; re-measure before acting on one.

---

## Status

| track | now |
|---|---|
| **App** | 1.9 / versionCode 19. Accounts merged to `main` (#182); the signed release APK is built and taken from CI; not on RuStore yet. |
| **Model** | 2.7.5 deployed (dataset v8). Weak spot: scorch read as healthy. A statistical tie with 2.7.2 on the shared holdout. |
| **Accounts backend** | Live on the production VDS under `/id/`; real Yandex flow and deletion verified. `/id/` is public on the host; the privacy policy is served at `/privacy.html`; a Telegram alert watches for stuck account deletions (`deploy/production-vds/alerts/`). No backup job (owner's decision). |
| **Data** | v8 is the corpus. v9 collection, locked test set T and OOD benchmark v3 are specified; none started. |
| **Tests / CI** | Server suite runs in parallel (`pytest -n auto`); `ccr-*` branches build in CI on a pinned `ubuntu-26.04`. The 2026-11 test rework and the `main.py` route split are green in CI. |

---

## Next up — in this order

- [ ] ★ **Ship accounts to RuStore** (section below).
- [ ] ★ **Carve T_v1** (adjudicate the 80 export rows). Nothing done to the scorch
      class — real or synthetic — can be judged without it.
- [ ] ★ **Scorch data**: real v9 collection (early, dull scorch, 400 families with
      boxes; takes calendar time) and/or a second synthetic batch that meets the
      review's conditions (Model track, "Data").
- [ ] ★ **Run the paired ON/OFF augmentation ablation on Colab at `SEED=0`** (human
      run; Model track, "Experiments that are not data").
- [ ] **Finish the `main.py` split**: `deps.py`, then module imports instead of the
      alias block (Architecture follow-ups).

When 2.7.5's successor ships, refresh `site/index.html` (list in `site/README.md`),
the README status section and `docs/assets/per-class.svg`.

---

## Ship accounts to RuStore

1.9 ships accounts only: Yandex sign-in. PRO is not part of it and is hidden in the app (see Decisions). In order.

**Before the release** (code and host)

- [x] **Old clients: production logs read (2026-11-16).** No 5xx. Fixed: plant
      photos 404'd for every client because Wikipedia moved lead images to
      `thumb.wikimedia.org` (`server/plant_photos.py`). Left as is: pre-1.9 apps
      still call the pseudo-id `GET /api/community/my-scan-opinions/{id}`, removed
      on purpose in #182, so they get no "new opinion" notifications until they
      update.
- [x] **`/id/` is public on the VDS** (the live snippet already had no allowlist).
- [x] **Privacy policy served** at `/privacy.html` (`nginx/pd-privacy.conf`).
- [x] **Backup job is off on the VDS** (no timer installed, no dumps left).
- [x] **Alert** on stuck account deletions: `deploy/production-vds/alerts/`,
      every 15 minutes, to the owner's Telegram group.
- [ ] **Hide PRO in the app.** `ProUi.ENABLED = false`: no PRO card, screen,
      lock badge or dialog text; the lunar calendar is open to everyone. Needs
      the release APK rebuilt from `main` after this merges.

**Release** (★ owner)

- [ ] ★ **One Yandex sign-in on the signed APK.** The flows themselves are already
      verified against production; only the certificate fingerprint registered
      with Yandex is new.
- [ ] ★ **RuStore upload** with release notes, then cut the 1.9 tag.

**Not a 1.9 gate**

- [ ] **PRO subscriptions (distant future, not scheduled).** Not planned for
      any upcoming release. The purchase validation, callback inbox and Android
      payment client stay in the code, switched off and invisible
      (`ProUi.ENABLED = false` in the app, `RUSTORE_LIFECYCLE_ENABLED` /
      `RUSTORE_CHECKOUT_ENABLED` off on the server). Reviving it would need
      RuStore keys and products, a sandbox purchase test and the app switch.
- [ ] **VK ID sign-in (after 1.9).** Merged, switched off until VK registration.

---

## Garden

- [ ] ★ **Check `util/PlantHealth.kt` thresholds against real scan histories.**
      The tunables are reasoned, not measured. Compare with a few real plants
      before trusting a red "Poor condition".

---

## Security

Config keys behind every limit: `DEPLOYMENT.md` §3.2.

- [ ] **The privacy policy still describes backups.** Both copies
      (`privacy.html`, the in-app `privacy_policy.html`) and
      `release-policy-gates.json` say "Encrypted operational backups may remain
      for up to four weeks". That stays true while the old dumps exist on the
      VDS; once they are deleted, drop the sentence from all three together. The
      in-app copy ships inside the APK, so it follows the next app release.

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

- [ ] ★ **Synthetic scorch experiment — batch 1 reviewed, not usable yet**
      (owner's choice, 2026-11-14: try generated images for the weak class).
      An independent review of `scorch_synthetic_100` found the stage right (all
      early, edge-confined) but said do not train on it: only scorch is synthetic,
      so "generator look ⇒ scorch" is easier to learn than the rim; the 100
      images are about 10 templates (lesion position tied to species, 10 fixed
      scenes); about 20 are ambiguous against healthy / `leaf_spot`; 3 fail the
      server's blur statistic and 11 boxes need redrawing. It also runs against
      plan rule P3 ("no … AI-looking images") — change that rule on purpose or
      not at all. Before a batch 2 is worth a run:
      (1) carve T_v1 — with ~39 unseen scorch rows in the holdout no result
      could be told from noise; (2) a matched synthetic *healthy* image per
      scorch image, same generator and scene; (3) lesion position, extent and
      colour randomised independently of species, more genera, fewer flat-lays,
      no tan backgrounds, every visible leaf boxed; (4) human adjudication of
      each image; (5) a `supplement_synthetic` source that sits in no
      `allowed_sources`, at reduced weight, train-only, with template-level
      family ids. Then one paired `SEED=0` run, real-only evaluation, and stop
      if healthy false alarms rise or scorch recall's CI straddles 0.
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

- [ ] **Finish the `main.py` split (~4,790 lines left).** The 42 route handlers
      now live in six `routes_*.py` modules that reach `main` as `m.name` at call
      time, which is what keeps the tests' in-place patches working. Still in `main.py`: 6 middlewares, 16 Pydantic
      models, 2 thread pools, limiter/ban/quota/admission state, metrics, lifespan
      and every helper. Next, one PR each: (1) `deps.py` — `Depends` appears only
      as the `community_bearer` route dependency and the quota preambles are still
      hand-copied; (2) replace the ~400-line duplicated alias-import block with
      module imports, moving the tests' patch targets with it, then drop
      `main.py`'s F401 exemption (`server/pyproject.toml`); (3) move each route
      group's helpers next to its routes, smallest first, community last.
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
- [ ] **Consolidate the four SQLite stores** (`community`, `training_store`,
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
      payloads. Keeps ~390 mirrored `@SerializedName`, the `KSerializer<JsonObject>`
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

## Tests and CI

The 2026-11 rework removed tests that guarded nothing, merged same-state tests,
made the server suite parallel and passed CI. What it left open:

- [ ] **Move pure functions out of Activity companions.**
      `LightMeterActivity.pickSpeciesBucket` and
      `WaterCalculatorActivity.computeWatering` are pure, but their tests need
      Robolectric only to load the Activity class.
- [ ] **Last two module reloads in tests**: `test_plant_photos.py` and
      `test_push.py` still `importlib.reload` their own module to redirect a path.
      Harmless today (nothing holds their classes), but the same
      `monkeypatch.setattr` redirection the community and `ai_services` tests now
      use would retire `importlib.reload` from the suite.
- [ ] **Untested pipeline branches**: the confidence-threshold arm of
      `_is_diseased`, and the served-threshold adoption in
      `PlantDiseasePipeline.__init__` (its resolvers are tested, the adoption is not).

---

## Documentation debt

None blocks a release.

- [ ] ★ **README app screenshots** (scan result, garden, care calendar, community
      map) → `docs/assets/screenshots/` + a row in "What it does". Needs a device.
- [ ] **`docs/assets/per-class.svg`** holds hand-copied numbers; regenerate with
      `generate_assets.py` when a model ships.
- [ ] **`android/app/build.gradle.kts`** (TFLite dependency comment) still says Aqua
      is provisioned at `assets/models/aqua_classifier.tflite`; it is downloaded to
      `filesDir/aqua/`. Its test-fork comment also still counts "~58 Robolectric classes".
- [ ] **README route count.** The status line says 39 API routes; `main.py` registers
      42 on `api_router`.
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
- **No backups of production data** (owner's decision, 2026-11-14). A lost VDS
  loses the accounts and the community store; there is nothing to restore from.
- **Cost cap is the OpenRouter key's own spend limit**; no in-server price
  ceiling and no dependency lockfile are planned.
- **Release signing runs in CI** (owner's decision, 2026-11-14): the android job
  signs with the permanent key from repository secrets.
- **1.9 is accounts only.** VK ID stays switched off until a later release.
- **PRO is a distant-future idea, not a plan** (owner's decision, 2026-11-16).
  The app and server show no PRO wording and lock nothing behind it; only
  documents may mention it, and only as something far off. Signed-in accounts
  share one set of daily limits, including one frontier analysis a day.
- **Account data stays on the German VDS** (owner's decision, 2026-11-13).
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
