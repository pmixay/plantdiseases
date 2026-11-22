# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## Repository layout

| Dir | Stack | What it is |
|---|---|---|
| `server/` | Python, FastAPI, ONNX Runtime | ML inference API + the two-stage pipeline. |
| `account-server/` | Python, FastAPI, SQLAlchemy/Alembic, PostgreSQL | Separate account service: sign-in, sessions, deletion, daily quotas (PRO entitlements exist but are dormant). |
| `android/` | Kotlin, Gradle, Material 3 | Phone client (CameraX, Room, Ktor + kotlinx.serialization, Hilt). |
| `site/` | Static HTML | One self-contained `index.html`; figures copied by hand from docs (`site/README.md` lists which). |
| `docs/` | Markdown, SVG | Roadmap, model ledger, labeling plan. `docs/assets/*.svg` come from `docs/assets/generate_assets.py` — edit that, not the SVGs. |
| `deploy/` | compose, nginx, scripts | Account stack: `account-beta-test/` (staging), `production-vds/` (production). |
| `.claude/skills/` | Markdown, Python | The project's ML skill (see "ML work"). |

**Naming:** **PlantDiseases** is the whole project (app, repo, server, docs).
**PlantVision** is only the internal name of the recognition system (pipeline,
calibration/OOD layers, model bundles, model card, ledger). Machine-bound identifiers
keep the old spelling — don't rename `classifier_name: "plantvision"`,
`plantvision_*.tar` / `plantvision_export_*` names or the `plantvision-aqua-bundle-v1`
digest tag.

**Privacy policy:** `android/app/src/main/assets/privacy_policy.html` (parsed by
`ui/legal/PrivacyPolicyParser` and rendered as native views by `PrivacyPolicyActivity`, no
WebView) is the canonical data-disclosure surface: it must work offline and describe the
build the reader is running. Keep its `h1`/`h2`/`p`/`li` + `data-ru`/`data-en` structure. The hosted copy is root
`privacy.html`; its structured markers (contact, deleted-actor disclosure) must match
`deploy/account-beta-test/release-policy-gates.json` (checked by
`server/tests/account/test_deployment_handoff.py`) — change them together.

## Where documentation lives

`README.md` (overview, class table, API table, model tiers) · `DEPLOYMENT.md`
(production config, proxy/service setup, Android signing + FCM) · `docs/ROADMAP.md`
(open work only: next steps, model/data track, architecture and doc debt) · `docs/MODEL_HISTORY.md` (per-bundle run
ledger + cross-release comparison rules) · `docs/DATASET_LABELING_PLAN.md` ·
`server/models/MODEL_CARD.md` (version-agnostic pipeline overview) ·
`server/train_notebook.ipynb` (full training recipe, §1–§7) · `site/README.md`.

**Rule for this file:** release-scoped facts (model versions, metrics, file sizes, class
counts, what shipped when) belong in `docs/ROADMAP.md`. CLAUDE.md holds only what stays
true across releases.

## Working rules

- **Don't build or compile locally — push and let CI run it.** Editing, reading and cheap
  targeted `pytest` / single unit tests are fine; a full `assembleRelease` is not.
- **Never train models locally** (no GPU here; no distillation or ONNX/TFLite export
  either). Training/eval/export runs on the user's Colab via `server/train_notebook.ipynb`
  — edit the notebook/pipeline code and hand the user the cells to run.
- **Git:** feature work on `claude/**` branches (cloud sessions push to `ccr-*`). Don't push to `main` without explicit
  permission. Don't open a PR unless asked.

## Common commands

### Server (`cd server`)
```bash
pip install -r requirements-dev.txt
ruff check .
pytest tests -q -n auto                        # full suite in parallel (pytest-xdist); no model weights needed
pytest tests/test_endpoints.py::test_name -q   # single test
./start.sh                                     # venv + uvicorn; refuses to start without a bundle
```
- `tests/conftest.py` swaps a `FakePipeline` into `main` and `pipeline` before the
  lifespan runs, redirects all DBs and `AI_CACHE_DIR` to tmp dirs, neutralises the rate
  limiter in `client` (`rate_limited_client` keeps it on), and per test clears
  `main._analyze_cache` (same-photo dedup would leak verdicts), `main._DAILY_QUOTAS`
  and the inference admission slots (all requests share one TestClient peer; quota tests
  lower `_DailyQuota.limit` via monkeypatch). A test that needs its own community store
  takes `community_store` / `community_http_store` — never `importlib.reload(community)`,
  which re-creates classes `main` already holds. New pipeline behaviour means keeping
  `FakePipeline`'s response shape in sync, or patching `main.pipeline`.
- Run as a flat package from `server/` (`uvicorn main:app`); `main.py` resolves imports
  both flat and as `server.*` via try/except.

### Account server (`account-server/`, tests live in `server/tests/account/`)
```bash
cd server && pytest tests/account -q          # account tests; also part of the full server run
```
- Account tests share the server's pytest run and venv (`server/requirements-dev.txt`
  includes `account-server/requirements.txt`; Python 3.14 everywhere). `tests/account/conftest.py`
  puts `account-server/` on `sys.path` and sets the account env per test — it must not leak
  into server tests. `app.main.app` is built lazily so importing it needs no env.
- Tests use deterministic mock providers — no real Yandex/VK/RuStore credential is
  needed or may be committed. There is no live-PostgreSQL CI job any more.
- `openapi.json` is generated by `account-server/export_openapi.py <output>` (needs the
  account env vars) and contract-tested; the app API has the same pair
  (`server/tools/export_openapi.py`, `server/openapi.json`, `tests/test_openapi_contract.py`).
  Regenerate in the same commit as any API change.

### Android (`cd android`)
```bash
./gradlew ktlintCheck detekt testDebugUnitTest
./gradlew testDebugUnitTest --tests "com.plantdiseases.app.util.CareScheduleTest"   # single test
./gradlew :app:assembleDebug -PAPP_API_BASE_URL=http://10.0.2.2:8000/ -PACCOUNT_API_BASE_URL=http://10.0.2.2:18001/ -PACCOUNT_REAL_OAUTH=false   # local app + mock-account stack
```
- `assembleRelease` is CI-only for agents. The CI android job signs the release and
  APK with the permanent key from repository secrets, checks the certificate
  and uploads them as the `app-release-apk` artifact (`DEPLOYMENT.md` §8).
- Every build targets the production server (URLs fixed in `app/build.gradle.kts`);
  there is no staging variant. Only a local mock-stack build overrides them, as above.
  `connectedDebugAndroidTest` needs a device and CI never runs it.
- Unit tests run under Robolectric (real resources, in-memory Room) — prefer `src/test/`
  over `src/androidTest/`.

### CI (`.github/workflows/ci.yml`)
- Push-only on `main`, `account-beta-test`, `claude/**`, `work/**`, `codex/**`, `model-*`, `ccr-*`;
  deliberately no `pull_request` trigger (rationale in the workflow header). A branch
  matching none of these gets no CI.
- A `paths-filter` job gates the **android** and **server** (server + account) jobs. It gates
  **per branch, not per push**: it diffs `origin/main...origin/<branch>`, so once any
  commit on a branch touches `android/**`, every later push there runs the android job,
  docs-only ones included. Only a branch whose entire diff touches no filtered path runs
  nothing. (On `main` it uses `github.event.before`, so it gates per push.)
- The android job holds the release signing key (env of the Gradle step only); no job
  holds provider credentials. Every branch in the trigger list can use that key.
- Easy to undo by accident: `maxParallelForks` in `app/build.gradle.kts` and the
  `~/.m2/repository/org/robolectric` cache step (Robolectric resolves `android-all` via its
  own Maven resolver, which the Gradle-home cache doesn't cover). Measure timings with
  `--profile`; don't write numbers here.

## ML work — use the bundled skill

Anything touching the model (notebook, pipeline, model families, training/eval/calibration,
ONNX/TFLite export + INT8, ship gate, `models/` bundle, feedback→retraining loop) goes
through `.claude/skills/houseplant-disease-ml/` (`SKILL.md`, `references/`, `scripts/`).
Follow its discipline (group-aware splits, paired-bootstrap ship gates, calibration +
conformal, honest-uncertainty refusal) rather than ad-hoc ML reasoning. Helpers:
`scripts/nbtool.py list|section N|grep PATTERN` navigates the huge notebook;
`server/tools/verify_bundle.py models/` grades a `bundle_manifest.json` (the skill's older
`scripts/check_bundle.py` is not the production boot contract).

## Architecture — the invariants

**Two-stage pipeline** (`server/pipeline.py`): Stage 1 leaf detector (`detector.py`,
single-class, so Stage 2 sees a tight crop) → Stage 2 ONNX classifier (`classifier.py`,
architecture-neutral: class list, input size, TTA and model family all come from
`classes.json`; has a real `not_a_plant` rejection head). Results are mean-voted across
the top crops plus the whole frame as one extra vote. Class table: `README.md`.

Safety gates (the reason for the architecture):
- **Blur gate** runs *before* Stage 1 and short-circuits to an `image_quality` abstention.
  It is one statistic on both tiers — Android `util/BlurGate.kt` mirrors the server's.
- **OOD AND-gate** abstains (`nap_ood_gated`) only when detector confidence *and* a
  classifier-side OOD signal (embedding k-NN distance, `ood.py`; MaxLogit fallback) agree
  *and* the classifier's own `not_a_plant` head does not veto. A natural `not_a_plant`
  argmax is already a rejection. The head-mass floor is checked first
  (`classes.json -> ood.nap_head_mass_floor`; `null` restores the two-signal behaviour).
- Overlay thresholds come **only** from `classes.json -> ood.served_gate` (fit in notebook
  §6 on the *served* statistic); `ood.embedding.threshold` is provenance and never
  rejects; `served_gate: null` serves with the overlay off.
- Temperature scaling + RAPS conformal sets (`conformal.py`) let the server say "I'm not
  sure" honestly.

**Model families** (`classes.json -> model_family`; `/api/capabilities` reports tiers):
`aqua` runs on-device (TFLite downloaded from `/api/aqua/model`, never bundled in the APK),
`gamma` is the server default, `frontier` loads only under `FRONTIER_ENABLED` into its own
pipeline instance. Preserve: an unknown family string falls back to the default; an
unavailable family answers HTTP 200 `available:false`, not an error. Details: `README.md`.

**Server layout:** `main.py` is the HTTP layer (app, middleware, shared state, helpers) and
wires the flat modules beside it (`ls server/`). The route handlers live in `routes_*.py`;
each `register(router, m)` gets the `main` module and must reach anything defined in or
imported by `main` as `m.name` at call time — never `from main import …` — because the
tests patch names on `main`. That is also why `main.py` is exempt from F401. Privacy-sensitive: `training_store.py` and `telemetry.py` are
consent-gated and de-identified (no account, actor, device or IP data; no free text).
`account_*.py` are fail-closed clients to the account service. `beta_runtime.py` /
`synthetic_pipeline.py` are a weight-free pipeline for local/staging only — never a
production fallback. `server/tools/` is offline helpers, never imported by the server.

**Android** (`com.plantdiseases.app`): `ui/<feature>/` over `data/`, `di/` (Hilt),
`util/`, `worker/`, `push/`, `auth/`, `billing/`. XML layouts with view binding — no
Compose, no ViewModel/LiveData: screens are activity + controller + repository. Server
URLs are baked at build time into `BuildConfig.APP_API_BASE_URL` / `ACCOUNT_API_BASE_URL`;
no in-app override.

**Accounts.** Identity lives in `account-server/` (own PostgreSQL), not the app API. It owns
provider identities, sessions/refresh tokens, deletion state and quotas. PRO is a
distant-future idea: its billing/entitlement code stays in place but is switched off and
invisible (`ProUi.ENABLED = false` in the app, `RUSTORE_*` off), and no user-facing text in
the app or server may mention it. The app
API and Community only see an Ed25519 access token (random community actor + nickname),
verify it locally against the service's JWKS, then confirm the session is still current
with the account service. Diagnosis and Community reading need no account; Community
writes and metered features do. Entitlements/quotas are server-authoritative: the app never
trusts a billing result, and the inference service asks the account service to consume a
unit before cost-bearing work. Deletion tombstones public content via an outbox before the private account rows are erased.

**One species surface.** Species info lives on `SpeciesDetailActivity` alone; every other
screen embeds `ui/common/SpeciesInfoCardView`. Never build a second species card.

**Garden navigation.** The care month grid is its own screen
(`ui/garden/care/CareCalendarActivity`); the lunar calendar is per-plant, on the plant
detail screen only.

## Conventions & gotchas

- **Fail-fast bundle contract.** Model artefacts are never committed or auto-downloaded;
  there is no demo fallback. Boot requires a notebook-produced `models/bundle_manifest.json`;
  `server/bundle.py` / `tools/verify_bundle.py` bind every artefact by path, size and
  SHA-256, reject mixed/legacy trees and select exactly one classifier graph.
  `classes.json`, `temperature.json`, `conformal.json` are required; `ood.embedding: null`
  is the MaxLogit-only path, a declared embedding requires its bank. A class list that
  mismatches the ONNX head width raises `ClassifierLoadError` — no pad/truncate shim.
  Preflight: `python tools/verify_bundle.py models/`. The Aqua TFLite model and
  disease-guide photos are provisioned out of band in `server/static/`.
- **Config precedence** (`config.py`): `server/config.json` (gitignored; copy from
  `config.example.json`; `PLANTDISEASES_CONFIG` overrides the path) **>** same-named env
  var **>** default. A blank value in `config.json` counts as unset (env var wins), but a
  **blank env var wins as an empty string** — except via `get_bool`, which treats blank or
  unrecognised values as unset and warns. Nested JSON sections flatten to UPPERCASE keys
  without the section name (`api_keys` stays a dict). Read once at import: restart to apply.
- **Optional integrations degrade gracefully, never crash.** Missing `PLANTNET_API_KEY` /
  `OPENROUTER_API_KEY` / FCM creds → endpoints return `available:false` and the app shows a
  specific "not configured" UX. Preserve this.
- **Typography: no grey text, plain punctuation.** Text uses `@color/on_surface`; the muted
  ink `on_surface_secondary` (also `colorOnSurfaceVariant`) is for icon tints only, never
  text; don't fade text with alpha to make it secondary. Captions are 12sp minimum and bold. UI text
  (strings, layouts, Kotlin literals, server content) has no middle dot, em dash or en
  dash: use `-`, `,` or `:`. `DesignSystemRulesTest` (app) and `tests/test_text_style.py`
  (server) enforce this; LLM and Wikipedia text is normalised by `server/text_style.py`.
- **Plain page headers, no hero blocks.** Every screen's header follows the Home and Community
  tabs: page background, a 20sp black title in page ink (`TextAppearance.App.Toolbar` for
  toolbars, whose colour is `toolbar_title` = page ink), actions beside it, supporting text
  below. No green gradient bands or cards behind headers; `DesignSystemRulesTest` fails if a
  hero drawable or colour comes back. Light pages use dark status-bar icons; screens that draw
  a photo or the camera under the status bar switch to light icons themselves.
- **Bilingual everywhere.** New diagnosis content, error cards and UI strings need EN and
  RU (server: `diseases_data.py`; app: `strings.xml` + `values-ru`).
- **Lint is zero-tolerance.** Server: ruff (`server/pyproject.toml`). Android: ktlint +
  detekt with `ignoreFailures = false` and **empty baselines**
  (`android/config/detekt/baseline.xml`, `android/app/config/ktlint/baseline.xml`) — never
  baseline a new violation. Android Lint is deliberately not the gate; ktlint relaxations
  live in `android/.editorconfig`.
- **Dual-annotated DTOs.** kotlinx.serialization is the wire format, but Gson is still live
  (Room field JSON, `util/ResponseCache.kt`, intent payloads). DTOs in `data/model/Models.kt`
  carry both `@SerialName` and `@SerializedName`, and `proguard-rules.pro` keeps both —
  drop either and R8 builds break silently. `data/remote/ApiService.kt` has no Retrofit
  interface: it is `RetryInterceptor` + the Ktor `PlantApiClient`.
- **No `HiltWorkerFactory`, on purpose** (`hilt-work` would double-initialise WorkManager
  against `androidx.startup`). Workers get DI via `di/AppEntryPoint.kt`.
- **Room `exportSchema = false`.** Every migration is hand-written and registered
  (destructive fallback is the last resort); migration tests hand-build old-shaped DBs and
  let `onValidateSchema` assert (see `Migration6To7Test.kt`).
- **Robolectric tests must annotate `@Config(sdk = [33])`** — there is no
  `robolectric.properties`. Add `application = Application::class` unless the test needs
  the Hilt graph: by default Robolectric constructs the real `PlantDiseasesApp` (component,
  channels, background launches) before every test method. `PlantDiseasesAppBootTest` is
  the one that boots it on purpose.
- **`/api/feedback` accepts labels the head does not predict** via
  `_FEEDBACK_OUT_OF_HEAD_CLASSES` in `main.py` (incl. `is_a_plant`, the "the reject was
  wrong" signal that retrains the rejection head). They are tagged `out_of_head` and feed
  retraining, not inference. The in-head set derives from `classifier.DEFAULT_CLASS_NAMES`;
  never hard-code it.
