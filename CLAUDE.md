# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Freshness caveat.** This is a fast-moving project and parts of this file
> (and of the in-repo docs) may be out of date. Treat the code as the source of
> truth. Known drift to watch for: the project is referred to as both
> **PlantDiseases** (app/repo) and **PlantVision** (the ML pipeline/brand).
> The Stage-2 classifier is a **frozen DINOv2 ViT-S/14 + LoRA + MLP head**
> (the `aqua` on-device tier is a distilled MobileNetV3-Small — not the
> server model; see `README.md`, `docs/ROADMAP.md`, `server/models/MODEL_CARD.md`).
> Version numbers in `docs/ROADMAP.md` and `server/models/MODEL_CARD.md` move
> with each training run.

## Repository layout

Two deliverables, each with its own toolchain:

| Dir | Stack | What it is |
|---|---|---|
| `server/` | Python 3.11, FastAPI, ONNX Runtime | ML inference API + the two-stage pipeline. The brains. |
| `android/` | Kotlin, Gradle, Material 3 | The phone client (CameraX, Room, Ktor + kotlinx.serialization, Hilt DI). |

The bilingual privacy policy is a single standalone page, `privacy.html` (repo
root) — the canonical data-disclosure surface referenced by the app's consent
screen.

Key docs: `README.md` (overview + API table), `DEPLOYMENT.md` (production config
+ Android release signing/FCM), `docs/ROADMAP.md` (current model status + next
steps), `docs/DATASET_LABELING_PLAN.md` (data loop),
`server/models/MODEL_CARD.md` (**stale — see the roadmap's model track**),
`server/train_notebook.ipynb` (full training recipe).

## Common commands

> **Don't build/compile/train locally — lean on CI and Colab.**
> - **Builds & checks run in CI** (`.github/workflows/ci.yml` on every push to
>   `claude/**`): server `ruff` + `pytest`, Android `ktlintCheck` + `detekt` + `testDebugUnitTest`
>   + R8 `assembleRelease`. Don't kick off full local Gradle builds to "verify" —
>   push and let CI do it. Reading code, editing, and (cheap) targeted `pytest`/single
>   unit tests are fine; a full `assembleRelease` is not.
> - **Never train models locally.** Training/eval/export happens on the user's
>   **Colab (A100, 40 GB VRAM, 80 GB RAM, 100 GB disk)** via
>   `server/train_notebook.ipynb`. For ML work, edit the notebook / pipeline code and
>   hand the user the notebook cells to run — do not attempt local training, distillation,
>   or ONNX/TFLite export, and don't expect a GPU here.

### Server (`cd server`)
```bash
pip install -r requirements-dev.txt   # installs runtime + pytest/ruff/sklearn
ruff check .                          # lint (config in pyproject.toml)
pytest tests -q                       # full suite (no model weights needed — see below)
pytest tests/test_endpoints.py -q     # single file
pytest tests/test_endpoints.py::test_name -q   # single test
./start.sh                            # create venv, install CPU torch, run uvicorn on :8000
```
- The test suite **needs no trained weights**: `tests/conftest.py` swaps in a
  `FakePipeline` before the FastAPI lifespan runs, disables the rate limiter,
  and redirects the labeling DB to a tmp dir. When adding pipeline behaviour,
  keep `FakePipeline`'s response shape in sync or patch `main.pipeline` in the test.
- Run server modules as a flat package from `server/` (`uvicorn main:app`) — imports
  resolve both flat (`server/`) and as `server.*` (repo root) via a try/except in `main.py`.

### Android (`cd android`)
```bash
./gradlew ktlintCheck detekt testDebugUnitTest    # what CI runs (ktlint + detekt + JVM unit tests)
./gradlew testDebugUnitTest --tests "com.plantdiseases.app.util.CareScheduleTest"   # single test
./gradlew assembleRelease                # R8/minified release APK (the shipping artefact)
./gradlew :app:assembleDebug -PAPI_BASE_URL=http://10.0.2.2:8000/   # point at a local server
./gradlew connectedDebugAndroidTest      # instrumented/Espresso tests (device/emulator required)
```
- JVM unit tests run under **Robolectric** (real resources, in-memory Room, Bitmap),
  so most of the pipeline is covered without a device — prefer adding `src/test/`
  unit tests over `src/androidTest/` instrumented ones.
- Release builds are always signed (real keystore via `-PRELEASE_STORE_FILE=…`,
  else a debug-key fallback so the APK still installs). `lint.abortOnError = true`.

### CI (`.github/workflows/ci.yml`)
Push-only, triggers on `main`, `claude/**`, `work/**`. A `paths-filter` job runs
the **android** and **server** jobs only when their trees (or the workflow)
change. One gap worth knowing: `codex/**` branches match no trigger — work
merged from them reaches `main` with zero CI.

## ML work — use the bundled skill

For anything touching the model (the notebook, the two-stage pipeline, the model
families, training/eval/calibration, ONNX/TFLite export + INT8, the ship gate, the
`models/` bundle, or the feedback→retraining loop) there's a project skill at
`.claude/skills/houseplant-disease-ml/` (`SKILL.md` + `references/` + runnable
`scripts/`). Lean on its discipline (group-aware splits, paired-bootstrap ship gates,
calibration + conformal, honest-uncertainty refusal) instead of ad-hoc ML reasoning.
Two helpers worth knowing: `scripts/nbtool.py list|section N|grep PATTERN` navigates the
huge `train_notebook.ipynb` without reading it whole, and `scripts/check_bundle.py
server/models/` validates a bundle against the fail-fast boot contract.

## Architecture — the big picture

**Two-stage inference pipeline** (`server/pipeline.py` orchestrates):
1. **Stage 1 — leaf detector** (`detector.py`): YOLOv8n, single class, finds the leaf
   so Stage 2 sees a tight crop, not a whole room.
2. **Stage 2 — classifier** (`classifier.py`): frozen DINOv2 ViT-S/14 + LoRA + MLP head,
   **9 trainable classes** including a real `not_a_plant` rejection head. Mean-voted
   across detected boxes, served via ONNX.

Two safety gates wrap this (the reason for the architecture): a **blur gate**
(variance-of-Laplacian) short-circuits unfocused shots to `not_a_plant`, and an
**OOD AND-gate** fires `not_a_plant` only when detector confidence *and* a
classifier-side OOD signal agree. The OOD signal is an embedding-space k-NN distance
on the DINOv2 CLS vector (`ood.py`) with a MaxLogit-floor fallback. Calibration adds
temperature scaling + RAPS conformal prediction sets (`conformal.py`) for honest
"I'm not sure" output.

**Model families** (declared per-bundle in `classes.json -> model_family`, surfaced —
with per-family availability — by `/api/capabilities`): `aqua` (MobileNetV3-Small,
distilled → TFLite INT8, runs **on-device/offline** in the app), `gamma` (production
DINOv2 ViT-S/14, the default), `frontier` (DINOv2 ViT-B/14, **off by default** —
needs a GPU host; returns a graceful `available:false` when disabled).

**Server module map** (`main.py` is the HTTP layer — ~185KB of routes, middleware,
validation, metrics — and wires everything together):
- `pipeline.py` / `detector.py` / `classifier.py` / `ood.py` / `conformal.py` — inference + calibration
- `ai_services.py` — PlantNet species ID, OpenRouter chat doctor, care plans, progression (all **optional**, graceful without keys)
- `diseases_data.py` / `symptoms.py` / `species_info.py` — bilingual (EN/RU) content databases + no-photo symptom triage
- `feedback.py` — append-only training-signal store + per-class accuracy metrics
- `community.py` — nearby-scans feed + second-opinion features
- `labeling.py` / `labeling_auto.py` — data-collection/labeling loop; `tools/` has offline eval + data export
- `push.py` — FCM push (lazy/no-op without credentials)
- `telemetry.py` — anonymous error reports (`TELEMETRY_*` keys; stores a hashed client IP)
- `plant_photos.py` / `popular_plants.py` / `countries.py` — photo proxy/cache, the popular-plants bundle, country lookups
- `config.py` — config loader (see below)

**One species surface:** species information lives on `SpeciesDetailActivity`
alone. Every other screen references a species through the reusable
`ui/common/SpeciesInfoCardView` (Wikipedia photo + name + description + "more
about this species"), which is embedded on the scan result and species
identify. The community opinion sheet dropped it in Community v2 — the photo
is the subject there, not the species. Do not add species cards elsewhere.

**Android structure** (`com.plantdiseases.app`): `ui/<feature>/` (camera, result,
garden, gallery, guide, community, …) over `data/` (`repository/`, `remote/ApiService.kt`
— now the **Ktor `PlantApiClient`** over an OkHttp engine; the Retrofit interface was removed,
`local/Database.kt` Room), `di/AppModule.kt` (**Hilt** singletons), `util/` helpers (incl.
`AquaClassifier`/`AquaInference` for on-device TFLite), `worker/` (WorkManager reminders/opinion
polling). DTOs in `data/model/Models.kt` are `@Serializable` (kotlinx). Bilingual via
`res/values*/strings.xml`. The server URL is baked at build time into
`BuildConfig.API_BASE_URL`.

## Conventions & gotchas

- **Model artefacts are never committed and never auto-downloaded.** The server is
  **fail-fast**: it refuses to start without `detector.{pt,onnx}` + `classifier.onnx` +
  `classes.json` (+ calibration JSON) in `server/models/`. Tests work around this with
  the fake pipeline. There is no demo fallback. Build artefacts with
  `train_notebook.ipynb` (§6/§6b). Likewise the Aqua TFLite model and disease guide
  photos are provisioned out of band (`server/static/`).
- **Config precedence** (`config.py`): `server/config.json` (gitignored; copy from
  `config.example.json`) **wins over** same-named environment variables **wins over**
  defaults. Blank values count as unset so an env var can still win. Nested JSON
  sections are flattened to flat UPPERCASE keys.
- **Optional integrations degrade gracefully, never crash.** Missing `PLANTNET_API_KEY`
  / `OPENROUTER_API_KEY` / FCM creds → endpoints return `available:false` and the app
  renders a specific "not configured" UX. Preserve this pattern.
- **Bilingual everywhere.** New diagnosis content, error cards, and UI strings need
  both EN and RU (server content in `diseases_data.py`; app in `strings.xml`).
- **Ruff** is configured in `server/pyproject.toml` (E/F/I/B; line-length warnings and
  `B008` ignored; `tests/*` skip `B`; the notebook/`models`/venvs are excluded).
- The classifier also accepts **deferred** feedback labels (`edema`, `sunburn_or_scorch`)
  the head doesn't yet predict — they feed the retraining loop, not inference.

## Git workflow

Feature work happens on `claude/**` branches (CI matches these). Do not push to `main`
without explicit permission. Do not open a PR unless asked.
