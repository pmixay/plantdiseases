# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## Repository layout

Three deliverables, each with its own toolchain, plus supporting dirs:

| Dir | Stack | What it is |
|---|---|---|
| `server/` | Python, FastAPI, ONNX Runtime | ML inference API + the two-stage pipeline. The brains. |
| `account-server/` | Python, FastAPI, SQLAlchemy/Alembic, PostgreSQL | Separate account service: provider sign-in, sessions, deletion, PRO entitlements and quotas. |
| `android/` | Kotlin, Gradle, Material 3 | The phone client (CameraX, Room, Ktor + kotlinx.serialization, Hilt DI). |
| `site/` | Static HTML | One-page project site: a single self-contained `index.html`, served by any static host. Its figures are copied by hand from the docs — `site/README.md` says which. |
| `docs/` | Markdown, SVG | Roadmap, model ledger, labeling plan; `docs/assets/` holds the README's images. |
| `.claude/skills/` | Markdown, Python | The project's ML skill (see "ML work" below). |

`deploy/` holds the compose files, nginx config and operator scripts for the account
stack (`account-beta-test/` for staging, `production-vds/` for production).

**Naming:** **PlantDiseases** is the whole project — the Android app, the repo, the
server and the docs. **PlantVision** is only the internal name of the recognition
system (the two-stage pipeline, its calibration/OOD layers, the model bundles, the
model card and ledger). Machine-bound identifiers keep the old spelling — don't rename
`classifier_name: "plantvision"`, `plantvision_*.tar` / `plantvision_export_*` names or
the `plantvision-aqua-bundle-v1` digest tag.

**Privacy policy:** `android/app/src/main/assets/privacy_policy.html`, shown by
`ui/legal/PrivacyPolicyActivity` (a WebView over that one local asset) from the
onboarding consent screen and the in-app consent dialog. It is the canonical
data-disclosure surface — it must work offline and must describe the build the reader
is actually running. The hosted copy is the root `privacy.html`; the signing workflow
reads its structured markers (contact, deleted-actor disclosure) and exact-matches them
against `deploy/account-beta-test/release-policy-gates.json`, so change them together.

## Where documentation lives

`README.md` — the project's front page (visual: hero, class gallery, pipeline diagram
under `docs/assets/`) plus the product overview, class table, API table and model tiers.
`docs/assets/*.svg` come from `docs/assets/generate_assets.py` (edit that, not the SVGs) · `site/README.md` —
how the one-page site is built and which docs its numbers come from ·
`DEPLOYMENT.md` — production config, proxy/service setup, Android release signing +
FCM · `docs/ROADMAP.md` — model status, release history, in-flight feature work,
documentation debt · `docs/ACCOUNTS_PRODUCTION_PLAN.md` — accounts decisions, release
gates and the step-by-step path to RuStore (the other `docs/ACCOUNT_*`,
`DELETION_JOURNAL_*` and `EMAIL_PASSWORD_*` files are its detail references) ·
`docs/MODEL_HISTORY.md` — per-bundle ledger of every archived run (metrics, knob diffs, data deltas, Drive links) + the cross-release comparison
rules · `docs/DATASET_LABELING_PLAN.md` — the data/labeling loop ·
`server/models/MODEL_CARD.md` — version-agnostic pipeline overview, with per-run
numbers in `model_card.json` beside the artefacts · `server/train_notebook.ipynb` —
the full training recipe (§1–§7).

**Rule for this file:** release-scoped facts — model versions, metrics, file sizes,
class counts, what shipped when — belong in `docs/ROADMAP.md`. CLAUDE.md holds only
what stays true across releases.

## Working rules

- **Don't build or compile locally — push and let CI run it.** Reading code, editing,
  and cheap targeted `pytest` / single unit tests are fine; a full `assembleRelease` is not.
- **Never train models locally.** Training/eval/export happens on the user's Colab GPU
  runtime via `server/train_notebook.ipynb` — edit the notebook / pipeline code and hand
  the user the cells to run. No local training, distillation, or ONNX/TFLite export;
  there is no GPU here.
- **Git:** feature work on `claude/**` branches (CI matches these). Don't push to `main`
  without explicit permission. Don't open a PR unless asked.

## Common commands

### Server (`cd server`)
```bash
pip install -r requirements-dev.txt   # runtime + pytest/ruff/sklearn
ruff check .                          # lint (config in pyproject.toml)
pytest tests -q                       # full suite (no model weights needed — see below)
pytest tests/test_endpoints.py -q     # single file
pytest tests/test_endpoints.py::test_name -q   # single test
./start.sh                            # venv + requirements.txt + uvicorn (PORT env or --port; default 8000); refuses to start without a bundle
```
- The suite **needs no trained weights**: `tests/conftest.py` swaps a `FakePipeline`
  into both `main` and `pipeline` before the FastAPI lifespan runs, redirects the
  training/community/telemetry/push DBs and `AI_CACHE_DIR` to tmp dirs, neutralises the rate limiter
  in the `client` fixture (use `rate_limited_client` when you want it on), and clears
  `main._analyze_cache` per test — the image fixtures hash stably, so the same-photo
  dedup cache leaks verdicts between tests otherwise. An autouse fixture also empties
  the per-IP daily quotas (`main._DAILY_QUOTAS`) and the inference admission slots
  before every test: the whole suite posts from one TestClient peer, so a quota test
  lowers one `_DailyQuota.limit` with monkeypatch. Adding pipeline behaviour means
  keeping `FakePipeline`'s response shape in sync, or patching `main.pipeline`.
- Run server modules as a flat package from `server/` (`uvicorn main:app`) — imports
  resolve both flat (`server/`) and as `server.*` (repo root) via a try/except in `main.py`.

### Account server (`cd account-server`)
```bash
pip install -r requirements-dev.txt
ruff check .
pytest -q                             # CI runs it on Python 3.11
```
- Tests use deterministic mock providers: no real Yandex/VK application ID, token or
  RuStore credential is needed or may be committed. The schema/migration tests that need
  a live PostgreSQL run in CI against a service container.
- `openapi.json` is generated by `tools/export_openapi.py <output>` (needs the test
  environment variables from the CI `account` job) and contract-tested; the app API has the
  same pair (`server/tools/export_openapi.py`). Regenerate after an API change.

### Android (`cd android`)
```bash
./gradlew ktlintCheck detekt testDebugUnitTest    # the lint + unit-test core of CI's android job
./gradlew testDebugUnitTest --tests "com.plantdiseases.app.util.CareScheduleTest"   # single test
./gradlew assembleRelease                # R8/minified release APK (CI-only; the shipped one is signed by android-release.yml)
./gradlew :app:assembleDebug -PAPP_API_BASE_URL=http://10.0.2.2:8000/ -PACCOUNT_API_BASE_URL=http://10.0.2.2:18001/ -PACCOUNT_REAL_OAUTH=false   # local app + mock-account stack
./gradlew connectedDebugAndroidTest      # instrumented/Espresso tests (device/emulator required); CI never runs these
```
- JVM unit tests run under **Robolectric** (real resources, in-memory Room, Bitmap),
  so most of the pipeline is covered without a device — prefer `src/test/` over
  `src/androidTest/`.
- Release builds are always signed: a real keystore via `-PRELEASE_STORE_FILE=…` (or
  the matching env vars), else a debug-key fallback so the APK still installs.

### CI (`.github/workflows/ci.yml`)
Push-only on `main`, `account-beta-test`, `claude/**`, `work/**`, `codex/**`, `model-*`;
there is deliberately no `pull_request` trigger (rationale in the workflow header). A
`paths-filter` job gates three jobs — **android** (`android/**` plus the account token/API
code, the workflow files and the release docs it checks), **server** (`server/**`) and
**account** (`account-server/**`, `deploy/account-beta-test/**`, `server/**`) — each also
selected by a workflow change. It gates **per branch, not per push**: with no `base` input
paths-filter diffs `origin/main...origin/<branch>`, so once any commit on a branch
touches `android/**`, every later push on that branch runs the android job — a
docs-only one included. Only a branch whose *entire* diff against main touches none of the
filtered paths runs no job. (On `main` the compared branch is the one pushed, so there it
falls back to `github.event.before` and does gate per push.)
Server job (Python 3.14): `ruff check .` then `pytest tests -q`. Account job (Python 3.11,
PostgreSQL service container): `alembic upgrade head`, compose/config validation, `ruff
check .`, `pytest -q`. Android job: a zero-configuration beta-build check, then one Gradle
invocation of `ktlintCheck detekt` plus the unit tests and non-distributable assemblies of the
release, staging and recovery variants. Ordinary CI holds no signing or provider
credentials; distributable APKs come only from the manually dispatched, protected
`.github/workflows/android-release.yml` (environment `android-signing`).

Two Android CI support pieces are easy to undo by accident: `maxParallelForks` in
`app/build.gradle.kts` (parallel unit-test forks) and the `~/.m2/repository/org/robolectric`
cache step (Robolectric resolves its `android-all` jars through its own Maven resolver,
which the Gradle-home cache does not cover). The Gradle cache is written only on
`main`; branches read it. Timings drift — measure with `--profile` instead of trusting
a number written here.

## ML work — use the bundled skill

Anything touching the model (the notebook, the two-stage pipeline, the model families,
training/eval/calibration, ONNX/TFLite export + INT8, the ship gate, the `models/`
bundle, the feedback→retraining loop) goes through the project skill at
`.claude/skills/houseplant-disease-ml/` (`SKILL.md` + `references/` + runnable
`scripts/`). Lean on its discipline (group-aware splits, paired-bootstrap ship gates,
calibration + conformal, honest-uncertainty refusal) instead of ad-hoc ML reasoning.
Two helpers: `scripts/nbtool.py list|section N|grep PATTERN` navigates the huge
notebook without reading it whole; `server/tools/verify_bundle.py models/` grades a
notebook-produced `bundle_manifest.json` without loading graphs. The older skill
script `scripts/check_bundle.py` is not the production boot contract.

## Architecture — the invariants

**Two-stage inference pipeline** (`server/pipeline.py` orchestrates):
1. **Stage 1 — leaf detector** (`detector.py`): single-class detector, so Stage 2 sees
   a tight crop, not a whole room.
2. **Stage 2 — classifier** (`classifier.py`): ONNX-only and architecture-neutral —
   class list, input size, architecture, TTA mode and model family all come from
   `classes.json`. Carries a real `not_a_plant` rejection head. Results are mean-voted
   across the top detected crops with the whole frame folded in as one extra vote.
   Canonical class table: `README.md`.

Two safety gates wrap this — they are the reason for the architecture. The **blur
gate** (variance-of-Laplacian) runs *before* Stage 1 and short-circuits an unfocused
shot to an `image_quality` abstention (empty classifier diagnostics). The **OOD AND-gate**
abstains (`nap_ood_gated`) only when detector confidence *and* a classifier-side OOD
signal agree *and* the classifier's own `not_a_plant` head does not veto; a natural
trained-head `not_a_plant` argmax is already a rejection and skips the overlay. The
head-mass floor is checked first and is per-bundle configurable via
`classes.json -> ood.nap_head_mass_floor`, where `null` restores the two-signal
behaviour. The OOD signal is an embedding-space k-NN distance on the CLS vector
(`ood.py`) with a MaxLogit-floor fallback. The thresholds the overlay applies come
only from `classes.json -> ood.served_gate`, which notebook §6 fits on the *served*
statistic (min member distance / max member logit over crops + whole frame); the
single-image `ood.embedding.threshold` is provenance and never rejects, and a bundle
with `served_gate: null` serves with the overlay off. The blur floor (50.0 on a 320 px
long edge, PIL luma, interior Laplacian) is one statistic on both tiers: the Android
`util/BlurGate.kt` mirrors it for the on-device Aqua tier. Calibration adds
temperature scaling + RAPS conformal prediction sets (`conformal.py`) so the server
can say "I'm not sure" honestly; the advertised `coverage` is the notebook's target
when the sidecar's out-of-sample audit backs it, else `1 - alpha`.

**Model families.** Each bundle declares its tier in `classes.json -> model_family`;
`/api/capabilities` reports the tiers to the app's scan-screen picker. `aqua` runs
on-device (its TFLite file is downloaded from `/api/aqua/model`, never bundled in the
APK), `gamma` is the server default, `frontier` loads only under `FRONTIER_ENABLED`
into its own pipeline instance. Two behaviours to preserve: an unknown family string
falls back to the default instead of erroring, and an unavailable family answers HTTP
200 `available:false` rather than failing. Tier capabilities and backbones: `README.md`.

**Server module map** (`main.py` is the HTTP layer — the `/api` router, middleware
stack, request validation, metrics — and wires everything together):
- `bundle.py` / `pipeline.py` / `detector.py` / `classifier.py` / `ood.py` / `conformal.py` — fail-closed artefact contract + inference + calibration
- `ai_services.py` — PlantNet species ID, OpenRouter chat doctor, care plans, progression (all **optional**, graceful without keys)
- `diseases_data.py` / `symptoms.py` / `species_info.py` — bilingual (EN/RU) content databases + no-photo symptom triage
- `training_store.py` — consent-gated, de-identified training samples + feedback receipts (no account, actor, device or IP columns; receipts are stored hashed and expire after 30 days); `tools/` has the offline eval, the privacy-checked `export_training_data.py` / `analyse_training_data.py` and the one-off `migrate_legacy_training_data.py`
- `community.py` — nearby-scans feed + second-opinion features; writes need a verified account (below)
- `account_auth.py` / `account_entitlements.py` / `account_internal.py` / `plantnet_budget.py` — the app API's fail-closed clients to the account service (token verification, quota consumption, community-deletion calls, the shared PlantNet budget)
- `beta_runtime.py` / `synthetic_pipeline.py` / `runtime_paths.py` — the weight-free synthetic pipeline for the local/staging account stack (never a production fallback; `main.py` builds it only behind explicit test-mode guards) and reverse-proxy prefix handling
- `push.py` — FCM push (lazy/no-op without credentials) · `telemetry.py` — consent-gated, errors-only reports (`TELEMETRY_*` keys; allowlisted enums and coarse buckets only: no free text, raw IP or IP hash)
- `plant_photos.py` / `popular_plants.py` / `countries.py` — photo proxy/cache, popular-plants bundle, country lookups · `config.py` — config loader (see Conventions)
- `tools/` — offline helpers, never imported by the server: `verify_bundle` (boot preflight),
  `export_data` + `analyse_feedback` (feedback/training-data export and analysis),
  `paired_eval` → `eval_bootstrap` (paired model A/B), `build_ood_benchmark`,
  `filter_supplement` + `kaggle_inventory` (supplement-data QA)
- `openapi.json` — the checked-in API contract; `tests/test_openapi_contract.py` fails
  when the app's generated spec drifts from it, so an API change updates it in the same commit

**Android structure** (`com.plantdiseases.app`): `ui/<feature>/` (camera, analysis,
result, garden, gallery, guide, community, species, …) over `data/` (`repository/`,
`remote/ApiService.kt`, `local/Database.kt` Room, `model/Models.kt` DTOs), `di/`
(`AppModule.kt` Hilt singletons + `AppEntryPoint.kt`), `util/` (incl.
`AquaClassifier`/`AquaInference`/`AquaModelManager` for on-device TFLite), `worker/`
(WorkManager reminders, opinion polling, guide-photo prefetch), `push/`, `auth/`
(sign-in session, refresh-token vault, logout revocation), `billing/` (entitlements +
the RuStore PRO client; the server stays authoritative) with `ui/account` and `ui/pro`.
XML layouts with view binding — no Compose, no ViewModel/LiveData: screens are activity +
controller + repository. Bilingual via `res/values*/strings.xml`. The server URL is
baked at build time into `BuildConfig.APP_API_BASE_URL` (the account service into
`ACCOUNT_API_BASE_URL`; on the shared host that is the app URL plus `id/`); there is no
in-app override.

**Accounts.** Identity lives in `account-server/`, a separate service with its own
PostgreSQL, not in the app API. It owns provider identities, sessions and refresh tokens,
deletion state and PRO entitlements; the app API and Community only ever see an Ed25519
access token carrying a random community actor and a nickname, verify it locally against
the service's JWKS, and then confirm the session is still current with the account
service, so logout and deletion take effect immediately. Diagnosis and Community reading
need no account; Community writes and metered features do. Entitlements and quotas are
server-authoritative: the app never trusts a billing result, and the inference service
asks the account service to consume a unit before doing cost-bearing work. Deleting an
account tombstones its public content through a durable outbox and a **deletion replay
journal** with backup epochs, so restoring an old backup never resurrects deleted
accounts or content. Which sign-in providers are on is per-deployment config;
production rejects email/password. The synthetic mock-provider stack under
`deploy/account-beta-test/` exists so a clean checkout can exercise the process
boundaries with no real credentials. Decisions, release gates and status:
`docs/ACCOUNTS_PRODUCTION_PLAN.md`.

**One species surface.** Species information lives on `SpeciesDetailActivity` alone;
every other screen reaches a species through the reusable
`ui/common/SpeciesInfoCardView` (photo + name + description + "more about this
species") — embedded on the scan result, species identify and the community opinion
sheet, because knowing what the plant *is* changes what you would call the problem.
Never build a second species card: if a screen needs one, embed this view.

**Garden navigation.** The care month grid is its own screen
(`ui/garden/care/CareCalendarActivity`); the lunar calendar is per-plant, on the plant
detail screen only.

## Conventions & gotchas

- **Model artefacts are never committed and never auto-downloaded**; there is no demo
  fallback. The server is **fail-fast**: boot requires a notebook-produced
  `models/bundle_manifest.json`. `server/bundle.py` / `tools/verify_bundle.py`
  bind every listed artefact by path, byte size and SHA-256, reject mixed or
  legacy trees, and select exactly one classifier graph (`fallback_allowed=false`).
  `classes.json`, `temperature.json` and `conformal.json` are required by that
  manifest. `ood.embedding: null` is the MaxLogit-only path; a declared embedding
  object requires the matching bank. A class list that does not match the ONNX
  head width still raises `ClassifierLoadError` with no pad/truncate shim.
  Preflight: `python tools/verify_bundle.py models/`. Build artefacts with
  `train_notebook.ipynb` (§6/§6b); the Aqua TFLite model and disease-guide
  photos are provisioned out of band in `server/static/`.
- **Config precedence** (`config.py`): `server/config.json` (gitignored; copy from
  `config.example.json`; `PLANTDISEASES_CONFIG` points at another file) **wins over** the same-named env var **wins over** the
  default. Blank handling is asymmetric — a blank value in `config.json` counts as
  unset (so the empty `""` secrets in `config.example.json` let an env var win), but a
  **blank env var does not**: it wins as an empty string and shadows the default —
  except through `get_bool`, which treats a blank or unrecognised value as unset and
  warns (`1/true/yes/on`, `0/false/no/off` only).
  Nested JSON sections flatten to flat UPPERCASE keys, discarding the section name
  (`api_keys` is the one section kept whole as a dict). Config is read once at import,
  so a change needs a process restart.
- **Optional integrations degrade gracefully, never crash.** Missing `PLANTNET_API_KEY`
  / `OPENROUTER_API_KEY` / FCM creds → endpoints return `available:false` and the app
  renders a specific "not configured" UX. Preserve this pattern.
- **Bilingual everywhere.** New diagnosis content, error cards and UI strings need both
  EN and RU (server content in `diseases_data.py`; app in `strings.xml` + `values-ru`).
- **Lint gates.** Server: **ruff** (`server/pyproject.toml`: E/F/I/B; `E501` and
  `B008` ignored; `tests/*` skip `B`; notebook/`models`/venvs excluded). Android:
  **ktlint + detekt**, both `ignoreFailures = false` with **empty baselines**
  (`android/config/detekt/baseline.xml`, `android/app/config/ktlint/baseline.xml`) —
  zero tolerance, don't baseline a new violation. Android Lint is deliberately *not*
  the gate (`abortOnError = false`, `checkReleaseBuilds = false`); ktlint relaxations
  live in `android/.editorconfig`.
- **kotlinx.serialization is the wire format; Gson is still live** for Room field JSON,
  `util/ResponseCache.kt` and intent payloads. DTOs in `data/model/Models.kt` are
  **dual-annotated** `@SerialName` (kotlinx) + `@SerializedName` (Gson) and
  `proguard-rules.pro` keeps rules for both — drop either and something breaks silently
  under R8. `data/remote/ApiService.kt` holds no Retrofit interface despite the
  filename: it is `RetryInterceptor` + the Ktor `PlantApiClient` over a shared OkHttp
  engine.
- **No `HiltWorkerFactory`, on purpose.** `androidx.hilt:hilt-work` is deliberately
  absent — a worker factory would double-initialise WorkManager against the
  `androidx.startup` initializer. Workers reach DI through `di/AppEntryPoint.kt`
  instead (see the notes in `app/build.gradle.kts` and `PlantDiseasesApp.kt`).
- **Room runs `exportSchema = false`** — no schema JSON to diff, so migration tests
  hand-build old-shaped DBs and let Room's `onValidateSchema` assert
  (`app/src/test/java/com/plantdiseases/app/data/local/Migration6To7Test.kt`). Every
  migration is hand-written and registered; destructive fallback is the last resort.
- **Robolectric has no `robolectric.properties`** — Robolectric tests annotate
  `@Config(sdk = [33])` themselves; a new test omitting it runs on a different SDK and can
  behave differently.
- **`/api/feedback` accepts labels the head does not predict.** The allowlist is
  `_FEEDBACK_OUT_OF_HEAD_CLASSES` in `main.py`, incl. `is_a_plant` — the app's
  single-action "the reject was wrong" signal on a `not_a_plant`/blur verdict, and the
  rows that retrain the rejection head. They are tagged `out_of_head` and feed the
  retraining loop, not inference. The in-head set derives from
  `classifier.DEFAULT_CLASS_NAMES`; keep it derived, never a hard-coded list.
