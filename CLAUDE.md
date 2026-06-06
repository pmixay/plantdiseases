# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A houseplant disease detector split into two deployables that talk over HTTP/JSON:

- `server/` — FastAPI service wrapping a two-stage ML pipeline (YOLOv8n leaf detector → frozen DINOv2 ViT-S/14 + LoRA + MLP head classifier, run via ONNX Runtime). Pure Python, CPU-only.
- `android/` — Kotlin Android client (Material 3, CameraX, Room, Retrofit/OkHttp, WorkManager). Sends photos to the server, stores scan history + a personal garden locally.

The two halves are independent — CI builds/tests them in separate jobs gated by changed paths (`.github/workflows/ci.yml`). A change touching only one side never runs the other's checks.

## Commands

### Server (run from `server/`)

```bash
# First-time dev setup: CPU torch is NOT in requirements.txt, install it separately
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements-dev.txt      # extends requirements.txt with pytest, ruff, httpx, scikit-learn

ruff check .                             # lint (config in pyproject.toml: select E,F,I,B; line-length 110, E501 ignored)
pytest tests -q                          # full suite
pytest tests/test_pipeline_helpers.py -q                 # one file
pytest tests/test_pipeline.py::test_name -q              # one test

./start.sh                               # prod-style launch: builds venv, installs deps, runs uvicorn (fail-fast on missing weights)
```

Tests need **no model weights** — `tests/conftest.py` swaps `PlantDiseasePipeline` for a `FakePipeline` before the FastAPI lifespan runs, and pins `RATE_LIMIT_RPS` high. Tests that exercise real pipeline behaviour patch `main.pipeline` directly.

Running the real server: `uvicorn main:app` from `server/`, or `uvicorn server.main:app` / `python -m server.main` from the repo root — both work because every intra-package import in `server/` is wrapped in a `try: from .x ... except ImportError: from x ...` block (Docker copies the files flat; local runs use the `server.` package). Preserve this pattern when adding modules.

### Android (run from `android/`)

```bash
./gradlew lintDebug                      # lint (abortOnError = true)
./gradlew testDebugUnitTest              # JVM unit tests (Robolectric — no device needed)
./gradlew testDebugUnitTest --tests "com.plantdiseases.app.util.ImageUtilsTest"   # single class
./gradlew assembleDebug                  # build APK
./gradlew assembleDebug -PAPI_BASE_URL=http://10.0.2.2:8000/   # point a build at a local server
./gradlew connectedDebugAndroidTest      # instrumented/Espresso tests — needs a device or emulator
```

JDK 17. The server URL is compiled in via `BuildConfig.API_BASE_URL` (default `https://pdis.arbuz.lol/`), overridable with `-PAPI_BASE_URL`; it can also be changed at runtime via Profile → Server URL (`util/ServerConfig.kt`). Cleartext HTTP is only permitted for hosts listed in `res/xml/network_security_config.xml`.

## Architecture

### Server pipeline (`server/pipeline.py`)

`PlantDiseasePipeline.analyze()` is the heart of the system and the file to read first. The ordering of its decision gates is load-bearing — each gate exists to fix a specific real-world failure mode documented inline:

1. **Blur gate** (variance-of-Laplacian) — short-circuits unfocused shots to a synthetic `not_a_plant` verdict *before* running the model, so soft-focus smears don't get a fake diagnosis.
2. **Stage 1 detect** → if no leaf bbox, classify the whole frame and let the classifier's `not_a_plant` head decide.
3. **Mean voting** across size-filtered crops, optionally folding in a whole-frame vote (`FOLD_WHOLE_FRAME_VOTE`) to recover leaf-centric inputs that over-cropping would hurt.
4. **Temperature scaling** applied to probabilities (math operates on already-softmaxed probs; see `_apply_temperature`).
5. **OOD / NAP gate** (`_decide_nap_route`) — two-of-two AND-gate: low detector confidence AND a classifier-side OOD signal (embedding k-NN distance if a bank ships, else MaxLogit floor, else detector-only).
6. **Post-processing** (`_apply_postprocessing_rules`) — weak-healthy reroute, `uncertain_healthy` / `low_confidence` / `very_low_confidence` warnings.
7. **Severity scoring** (`compute_severity`) from bbox area × confidence margin, with viral disease always escalated.

The many module-level threshold constants (`HEALTHY_REROUTE_FLOOR`, `NAP_DETECTOR_CONF_FLOOR`, `BLUR_LAPLACIAN_FLOOR`, …) are empirically tuned against the feedback corpus — the comments record *why* each value is what it is. Pure decision helpers (`_decide_nap_route`, `_fold_votes`, `compute_severity`) are module-level specifically so the test suite can hit them without a model.

### Server modules

- `main.py` — HTTP layer: all endpoints, middleware (rate limit, request IDs, gzip, CORS), Pydantic validation, the analyze dedup cache, fail-fast model loading in the lifespan handler. Response shapes are documented inline.
- `detector.py` / `classifier.py` — Stage 1 / Stage 2 model wrappers. Class set is read from `models/classes.json` at boot, so the runtime adapts to whatever the shipped artefact declares (`DEFAULT_CLASS_NAMES` is the fallback).
- `ai_services.py` — optional external integrations: PlantNet identify, OpenRouter chat ("plant doctor") + care-plan polish, progression comparison. All degrade to graceful `available: false` payloads when unkeyed.
- `community.py` / `feedback.py` — SQLite-backed community scans and append-only feedback store + per-class accuracy metrics.
- `conformal.py` / `ood.py` — RAPS conformal prediction sets and embedding-space OOD scorer.
- `diseases_data.py` / `species_info.py` — bilingual (EN/RU) disease database + pet-toxicity lookup.
- `config.py` — single config source. Lookup order per key: `config.json` → same-named env var → hard-coded default. Blank string values count as unset. Nested JSON sections are flattened (`server.port` → `PORT`). Use `get_str/get_int/get_float/get_list` rather than reading env directly.

**Model artefacts are never committed and never auto-downloaded.** The server is fail-fast — it refuses to start if `detector.{pt,onnx}`, a classifier ONNX, or `classes.json` is missing from `server/models/`. There is no demo fallback. Calibration files (`temperature.json`, `conformal.json`) are optional and no-op when absent. `train_notebook.ipynb` produces all of these.

### Android

Manual dependency injection (no Hilt/Dagger): `PlantDiseasesApp` lazily constructs the `AppDatabase`, `PlantApiClient`, and repositories; activities/fragments reach them through the application instance. Layered as `ui/` (Activities, Fragments, BottomSheets, custom Views) → `data/repository/` → `data/local/` (Room) + `data/remote/` (Retrofit). `ScanRepository`/`PlantRepository` map server DTOs (`data/model/Models.kt`) to Room entities (`data/local/Database.kt`) and translate HTTP status codes into typed exceptions (`RateLimitException`, `ServerStartingException`, etc.) that the UI renders as specific messages. Fire-and-forget work (e.g. community upload) runs on the app-scoped `appScope` so it survives activity navigation.

## Conventions

- **Bilingual everywhere.** User-facing strings ship EN + RU — server disease/care data carries both, and Android has `values/strings.xml` + `values-ru/strings.xml`. Add both when adding user-facing text.
- **Classes are data, not code.** The canonical trainable class list lives in `server/models/classes.json` (9 classes incl. `not_a_plant`); `README.md` and `MODEL_CARD.md` document them. `/api/feedback` additionally accepts "deferred" labels the head can't yet predict (used to prioritise the next training round).
- **Graceful degradation over hard failure** for optional features (PlantNet, OpenRouter) — return `available: false`, never 500.
- Optional integration keys go in `server/config.json` (gitignored; copy from `config.example.json`) or env vars. See `DEPLOYMENT.md` for the full key table.
