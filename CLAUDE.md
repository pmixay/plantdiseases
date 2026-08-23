# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## Repository layout

Two deliverables, each with its own toolchain:

| Dir | Stack | What it is |
|---|---|---|
| `server/` | Python, FastAPI, ONNX Runtime | ML inference API + the two-stage pipeline. The brains. |
| `android/` | Kotlin, Gradle, Material 3 | The phone client (CameraX, Room, Ktor + kotlinx.serialization, Hilt DI). |

**Naming:** **PlantDiseases** is the app/repo, **PlantVision** the ML pipeline/brand —
both appear in code and docs and mean the same project.

**Privacy policy:** `android/app/src/main/assets/privacy_policy.html`, shown by
`ui/legal/PrivacyPolicyActivity` (a WebView over that one local asset) from the
onboarding consent screen and the in-app consent dialog. It is the canonical
data-disclosure surface — it must work offline and must describe the build the reader
is actually running.

## Where documentation lives

`README.md` — product overview, the class table, the API table, the model tiers ·
`DEPLOYMENT.md` — production config, proxy/service setup, Android release signing +
FCM · `docs/ROADMAP.md` — model status, release history, in-flight feature work,
documentation debt · `docs/DATASET_LABELING_PLAN.md` — the data/labeling loop ·
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
./start.sh                            # venv + CPU torch + uvicorn (PORT env or --port; default 8000)
```
- The suite **needs no trained weights**: `tests/conftest.py` swaps a `FakePipeline`
  into both `main` and `pipeline` before the FastAPI lifespan runs, redirects the
  labeling/telemetry DBs and `AI_CACHE_DIR` to tmp dirs, neutralises the rate limiter
  in the `client` fixture (use `rate_limited_client` when you want it on), and clears
  `main._analyze_cache` per test — the image fixtures hash stably, so the same-photo
  dedup cache leaks verdicts between tests otherwise. Adding pipeline behaviour means
  keeping `FakePipeline`'s response shape in sync, or patching `main.pipeline`.
- Run server modules as a flat package from `server/` (`uvicorn main:app`) — imports
  resolve both flat (`server/`) and as `server.*` (repo root) via a try/except in `main.py`.

### Android (`cd android`)
```bash
./gradlew ktlintCheck detekt testDebugUnitTest    # exactly what CI's android-check job runs
./gradlew testDebugUnitTest --tests "com.plantdiseases.app.util.CareScheduleTest"   # single test
./gradlew assembleRelease                # R8/minified release APK (the shipping artefact)
./gradlew :app:assembleDebug -PAPI_BASE_URL=http://10.0.2.2:8000/   # point at a local server
./gradlew connectedDebugAndroidTest      # instrumented/Espresso tests (device/emulator required); CI never runs these
```
- JVM unit tests run under **Robolectric** (real resources, in-memory Room, Bitmap),
  so most of the pipeline is covered without a device — prefer `src/test/` over
  `src/androidTest/`.
- Release builds are always signed: a real keystore via `-PRELEASE_STORE_FILE=…` (or
  the matching env vars), else a debug-key fallback so the APK still installs.

### CI (`.github/workflows/ci.yml`)
Push-only on `main`, `claude/**`, `work/**`, `codex/**`; there is deliberately no
`pull_request` trigger (rationale in the workflow header). A `paths-filter` job gates
the **android** job on `android/**` and the **server** job on `server/**`, each plus
the workflow file. It gates **per branch, not per push**: with no `base` input
paths-filter diffs `origin/main...origin/<branch>`, so once any commit on a branch
touches `android/**`, every later push on that branch runs the android job — a
docs-only one included. Only a branch whose *entire* diff against main is root files
or `docs/**` runs neither job. (On `main` the compared branch is the one pushed, so
there it falls back to `github.event.before` and does gate per push.)
Server job: `ruff check .` then `pytest tests -q`. Android job: one Gradle invocation
of `ktlintCheck detekt testDebugUnitTest assembleRelease`, then an APK signing/FCM
verification step.

**Where the Android minutes go**, from a `--profile` run of a cold build — worth reading
before trying to speed it up, because the plain-console task headers mislead. Gradle
overlaps this work as async workers (task time summed to 15m44 against a 9m33 build),
so R8 and the tests run *concurrently* and R8 is the tail: `minifyReleaseWithR8` 5m08,
`testDebugUnitTest` 2m34, `compileDebugKotlin` and `compileReleaseKotlin` 1m58 each,
KSP 43s/37s. Two support pieces are easy to undo by accident: `maxParallelForks` in
`app/build.gradle.kts` (little wall clock in a full invocation, where it competes with
R8 for the runner's two cores, but a large win whenever the test task *is* the critical
path — a cache-served release variant, or `./gradlew testDebugUnitTest` alone) and the
`~/.m2/repository/org/robolectric` cache step (Robolectric fetches 338 MB of
`android-all` jars at test runtime through its own Maven resolver, which the
Gradle-home cache does not cover).

Expect ~1.5 min when a push changes no Android source (main's build cache serves
almost every task) and ~8-10 min when it does. `gradle/actions/setup-gradle` only
*writes* that cache on `main`; branches read it.

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
(`ood.py`) with a MaxLogit-floor fallback. Calibration adds temperature scaling +
RAPS conformal prediction sets (`conformal.py`) so the server can say "I'm not sure"
honestly.

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
- `feedback.py` (append-only training-signal store + per-class accuracy) / `labeling.py` / `labeling_auto.py` — the data loop; `tools/` has offline eval + data export
- `community.py` — nearby-scans feed + second-opinion features
- `push.py` — FCM push (lazy/no-op without credentials) · `telemetry.py` — anonymous error reports (`TELEMETRY_*` keys; stores a hashed client IP)
- `plant_photos.py` / `popular_plants.py` / `countries.py` — photo proxy/cache, popular-plants bundle, country lookups · `config.py` — config loader (see Conventions)

**Android structure** (`com.plantdiseases.app`): `ui/<feature>/` (camera, analysis,
result, garden, gallery, guide, community, species, …) over `data/` (`repository/`,
`remote/ApiService.kt`, `local/Database.kt` Room, `model/Models.kt` DTOs), `di/`
(`AppModule.kt` Hilt singletons + `AppEntryPoint.kt`), `util/` (incl.
`AquaClassifier`/`AquaInference`/`AquaModelManager` for on-device TFLite), `worker/`
(WorkManager reminders, opinion polling, guide-photo prefetch), `push/`. XML layouts
with view binding — no Compose, no ViewModel/LiveData: screens are activity +
controller + repository. Bilingual via `res/values*/strings.xml`. The server URL is
baked at build time into `BuildConfig.API_BASE_URL`; there is no in-app override.

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
  `config.example.json`) **wins over** the same-named env var **wins over** the
  default. Blank handling is asymmetric — a blank value in `config.json` counts as
  unset (so the empty `""` secrets in `config.example.json` let an env var win), but a
  **blank env var does not**: it wins as an empty string and shadows the default.
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
- **Robolectric has no `robolectric.properties`** — every Robolectric test annotates
  `@Config(sdk = [33])` itself; a new test omitting it runs on a different SDK and can
  behave differently.
- **`/api/feedback` accepts labels the head does not predict.** The allowlist is
  `_FEEDBACK_OUT_OF_HEAD_CLASSES` in `main.py`, incl. `is_a_plant` — the app's
  single-action "the reject was wrong" signal on a `not_a_plant`/blur verdict, and the
  rows that retrain the rejection head. They are tagged `out_of_head` and feed the
  retraining loop, not inference. The in-head set derives from
  `classifier.DEFAULT_CLASS_NAMES`; keep it derived, never a hard-coded list.
