# PlantDiseases — Developer Deep Dive

Companion to [`PROJECT_DEFENSE.md`](PROJECT_DEFENSE.md). That doc is *what to say*;
this one is *how it actually works* — the real call chains, the mental models, and
recipes for reading, running, and extending the code. Pair it with `CLAUDE.md`
(repo conventions) and `README.md` (overview).

> **Freshness caveat:** this project moves fast. When this doc and the code
> disagree, **the code wins.** File/function names below were taken from the
> source, but line numbers drift — search by name.

---

## 1. How to read this codebase (suggested order)

You'll understand the whole system fastest by reading these in sequence:

**Server (the brains):**
1. `server/config.py` — how every setting is resolved (you'll see this pattern everywhere).
2. `server/pipeline.py` → `PlantDiseasePipeline.analyze()` — the master orchestration. **Start here.**
3. `server/detector.py` (`LeafDetector.detect`) and `server/classifier.py` (`DiseaseClassifier.classify`) — the two model wrappers.
4. `server/ood.py` and `server/conformal.py` — the gates and calibration.
5. `server/main.py` — the HTTP layer that calls the pipeline and shapes JSON. It's ~150 KB; **don't read it linearly**, jump to the route you care about (`/api/analyze`).
6. `server/feedback.py` + `server/labeling*.py` — the data flywheel.

**Android (the client):**
1. `data/model/Models.kt` — the wire contract (`@Serializable` DTOs). This *is* the API from the app's side.
2. `data/remote/ApiService.kt` (`PlantApiClient`) — every server call.
3. `di/AppModule.kt` — the Koin graph: what's a singleton and how things get wired.
4. `ui/camera/` → `ui/result/` — the core scan-to-diagnosis screens.
5. `util/AquaClassifier` / `AquaInference` — the offline on-device path.

---

## 2. The request lifecycle (what `analyze()` really does)

This is the single most important flow. The real branch logic in
`PlantDiseasePipeline.analyze(img)`:

```
analyze(img):
  1. focus_score = variance_of_Laplacian(img)        # blur gate
     └─ if too blurry      → _too_blurry_response()   # short-circuit, no fake confidence
  2. stage1 = detector.detect(img)                    # YOLOv8n leaf boxes
     └─ if no leaf found   → _analyze_no_detection()  # classify whole image, lean toward not_a_plant
  3. for each detected box → classifier.classify(crop)
     └─ mean-vote the per-box probabilities
  4. apply temperature scaling  (_apply_temperature)  # calibrate confidence
  5. OOD AND-gate (detector conf  AND  embedding kNN/MaxLogit) → maybe override to not_a_plant
  6. _apply_postprocessing_rules(...)                 # weak-healthy reroute, severity, floors
  7. conformal set (if configured)                    # the "it might be A or B" set
  8. _build_response(...)                             # final JSON: class, confidence, top-k, warnings
```

Key things to internalize:
- **Every exit path returns the same response shape** (`_build_response` /
  `_too_blurry_response` / `_analyze_no_detection` all converge). That's why the
  app can render one result card regardless of which branch fired.
- **Gates short-circuit early.** Blur and "no leaf" are decided *before* trusting
  the classifier. The system would rather say "not a plant / blurry" than emit a
  confident wrong disease.
- **The class set is data, not code.** `classes.json` in the model bundle defines
  the classes and `model_family`; the pipeline adapts to whatever it's loaded with
  (`self.classifier.CLASS_NAMES`). Swapping models can change the class list
  without code edits.

On the app side the mirror flow: `CameraX capture → PlantApiClient.analyze() →
Models.kt DTO → ResultActivity renders → Room saves history`. For the **offline
`aqua`** path, steps 2–7 happen on-device in `AquaInference` (TFLite) with no HTTP.

---

## 3. Config & the fail-fast boot contract

`server/config.py` resolves settings with strict precedence:

```
server/config.json   >   same-named environment variable   >   built-in default
```
- `config.json` is **gitignored**; copy from `config.example.json`.
- Blank values count as *unset*, so an env var can still win over an empty JSON field.
- Nested JSON sections are flattened to flat UPPERCASE keys.

**Fail-fast contract:** the server **refuses to boot** without its model artefacts
in `server/models/`: `detector.{pt,onnx}` + `classifier.onnx` + `classes.json`
(+ calibration JSON). There is **no demo fallback** — a missing model is a hard
error, by design, so production never silently serves garbage.
- Validate a bundle with `server/.claude/.../scripts/check_bundle.py server/models/`
  (the houseplant-disease-ml skill ships it).
- Tests dodge this: `tests/conftest.py` swaps in a `FakePipeline` before the
  FastAPI lifespan runs, disables the rate limiter, and redirects the labeling DB
  to a temp dir. When you change pipeline behavior, **keep `FakePipeline`'s
  response shape in sync** or patch `main.pipeline` in the test.

**Graceful-degradation contract:** missing `PLANTNET_API_KEY` / `OPENROUTER_API_KEY`
/ FCM creds → those endpoints return `available:false`, never crash. Preserve this
whenever you add an optional integration.

---

## 4. The classifier internals (Stage 2)

`DiseaseClassifier` (`server/classifier.py`) serves a **frozen DINOv2 ViT-S/14
backbone + LoRA adapters + MLP head** via **ONNX**:
- **Training (PyTorch, on Colab)** produces the head + LoRA weights; the backbone
  stays frozen. See `server/train_notebook.ipynb` (§6/§6b export).
- **Serving (ONNX Runtime)** — the whole thing is exported to `classifier.onnx`,
  so production has no PyTorch dependency.
- The **CLS embedding** (the transformer's image-summary vector) is exposed because
  the OOD gate needs it (§5).
- Mental model: *the backbone is a fixed, very good "feature camera"; LoRA nudges
  what it pays attention to toward plants; the MLP head reads those features and
  votes on the 9 classes.*

`LeafDetector` (Stage 1) is YOLOv8n, single class, returning boxes + confidences;
its confidence feeds the OOD AND-gate.

---

## 5. The gates, in detail

### Blur gate — `_variance_of_laplacian(img)`
The Laplacian highlights edges; a **sharp** image has high edge variance, a
**blurry** one has low. We downscale, take variance of the Laplacian, and if it's
below a threshold we short-circuit to a blurry/not-a-plant response. Cheap, classic,
no extra dependency.

### OOD AND-gate — `server/ood.py`
"Is this image unlike anything we trained on?" Computed as a **k-NN distance in
DINOv2 CLS embedding space** against stored training embeddings, with a
**MaxLogit-floor fallback** (`_resolve_max_logit_floor`). Crucially it's an **AND**:
`not_a_plant` fires only when **detector confidence is low AND the OOD signal trips**.
Requiring both signals to agree avoids over-rejecting genuine but unusual plants.

### Why gates at all?
A softmax classifier *always* produces a distribution — even for a photo of a chair.
The gates are the difference between "confidently wrong" and "honestly unsure."

---

## 6. Calibration & conformal (honest uncertainty)

`server/conformal.py` + `_apply_temperature`:
- **Temperature scaling:** divide the logits by a learned scalar `T` before softmax.
  `T>1` softens overconfident outputs so the reported confidence matches real
  accuracy. `T` is loaded from the calibration JSON (`_load_temperature`).
- **RAPS conformal sets:** instead of one label, return the smallest label *set*
  whose cumulative calibrated probability meets a coverage target (e.g. 90%). A set
  of size >1 is the model saying "could be A or B." `ConformalConfig` /
  `load_conformal_config` drive it; `off` when not configured.
- The app turns these into top-3 alternatives and soft warnings
  (`uncertain_healthy`, `low_confidence`, `blurry_image`).

---

## 7. The data flywheel (how the model improves)

```
user scan → user correction (feedback.py) → labeling queue (labeling*.py)
          → periodic export → retrain in train_notebook.ipynb (Colab A100)
          → eval + ship gate → new server/models/ bundle → redeploy
```
- `feedback.py` is an **append-only** training-signal store + per-class accuracy
  metrics. The classifier even accepts **deferred** labels (`edema`,
  `sunburn_or_scorch`) it can't yet predict — they feed the *next* training run, not
  inference.
- **Never train locally.** Training/eval/ONNX-TFLite export happen on the user's
  Colab via `train_notebook.ipynb` (§1–§7). For ML edits, change the notebook /
  pipeline code and hand the cells to run.
- Navigate the huge notebook with `scripts/nbtool.py list|section N|grep PATTERN`
  instead of opening it whole.
- **Ship gate:** a new model replaces the old one only after a **paired-bootstrap**
  test on a **group-aware split** confirms the macro-F1 gain is statistically real.
  This is the project's defense against "improvement that's actually noise."

---

## 8. Android internals

- **DI (`di/AppModule.kt`, Koin):** singletons for `PlantApiClient`, the Room
  `Database`, and the repositories. Constructor-inject everywhere; no service
  locators scattered around.
- **Repository pattern (`data/repository/`):** UI talks to repositories, not to the
  network/DB directly. `ScanRepository` is the hub for scan history + stats; it
  reads Room and calls `PlantApiClient`.
- **Wire models (`data/model/Models.kt`):** `@Serializable` DTOs with dual
  `@SerializedName`/`@SerialName` and **safe defaults on every field**, so a partial
  or older server response degrades into defaults instead of crashing.
- **Offline aqua:** `util/AquaClassifier` + `AquaInference` run the downloaded
  **TFLite INT8** model on-device; the model + its `classes.json` sidecar are fetched
  from the server and cached. Selected on the scan screen.
- **Reminders (`worker/`, WorkManager):** watering/fertilizing reminders and
  second-opinion polling, durable across reboots.
- **Bilingual:** `res/values/strings.xml` + `res/values-ru/strings.xml`. Every new
  user-facing string needs both.
- **Build-time server URL:** `BuildConfig.API_BASE_URL` is baked in at assembly;
  point a debug build at a local server with
  `-PAPI_BASE_URL=http://10.0.2.2:8000/`.

---

## 9. Running it locally

**Server:**
```bash
cd server
pip install -r requirements-dev.txt
ruff check .                 # lint (config in pyproject.toml)
pytest tests -q              # full suite — NO trained weights needed (FakePipeline)
./start.sh                   # venv + CPU torch + uvicorn on :8000
```
(For a real diagnosis you still need a model bundle in `server/models/`; tests don't.)

**Android (lean on CI for full builds):**
```bash
cd android
./gradlew ktlintCheck detekt testDebugUnitTest # what CI runs; JVM tests under Robolectric
./gradlew :app:assembleDebug -PAPI_BASE_URL=http://10.0.2.2:8000/   # point at local server
```
Prefer `src/test/` (Robolectric: real resources, in-memory Room, Bitmap) over
device-bound `src/androidTest/`.

**Website:** `cd website && python3 -m http.server` (no build step).

---

## 10. Extension recipes

- **Add a disease class:** retrain with the new label in the notebook; the new
  `classes.json` carries it. Add bilingual content in `server/diseases_data.py`
  (EN+RU) and an icon/strings on the app. The pipeline auto-adapts to the new class
  set — no inference-code change. (Use the houseplant-disease-ml skill's discipline.)
- **Add a server endpoint:** add a route on `api_router` in `main.py` following an
  existing one (e.g. `/api/species-info`); keep it graceful (return `available:false`
  / empty, never 500 on a missing optional dep); add a `tests/test_*.py`; if it
  changes the public surface, refresh `server/openapi.json` (a contract test checks
  paths). Then add the DTO in `Models.kt` + a method on `PlantApiClient`.
- **Add a language:** new `res/values-xx/strings.xml` on the app; bilingual content
  DBs server-side. (Today it's EN/RU end to end.)
- **Add an optional integration:** gate it behind a config key, return
  `available:false` when unset, and render a "not configured" state in the app.

---

## 11. Mental models & gotchas (the stuff that bites)

- **The pipeline would rather refuse than guess.** Blur gate, no-detection path, and
  OOD AND-gate all exist to avoid confident-wrong answers. Don't "simplify" them away.
- **Class set is data.** Logic keys off `classifier.CLASS_NAMES`, not hard-coded
  labels.
- **Fail-fast, no demo mode.** Boot without a model = hard error. Tests use
  `FakePipeline`; keep its shape in sync.
- **Graceful degradation is a contract, not a nicety.** Missing keys must never crash.
- **Bilingual everywhere** (EN+RU) for any user-facing string or diagnosis content.
- **Two quality gates block CI:** server `ruff` (config in `pyproject.toml`) and
  Android **ktlint + detekt** (baselines in `app/config/`; *only newly-introduced*
  issues fail — keep the baseline honest). Run `ktlintFormat` before adding code.
- **Don't build/train locally to "verify."** Push and let CI run lint+tests+R8;
  train only on Colab. Cheap targeted `pytest`/single unit tests are fine.
- **Naming drift:** the app is *PlantDiseases*, the ML brand is *PlantVision*;
  `main.py`'s module docstring may still describe the old Stage-2 model. Trust the
  code.

---

## 12. The 30-second mental model

> A photo enters `pipeline.analyze()`. Blur and leaf-detection gates decide whether
> to even trust the classifier. If trusted, a frozen DINOv2+LoRA+MLP (served via
> ONNX) classifies the cropped leaf, an embedding-space OOD gate can veto to
> `not_a_plant`, and temperature + conformal calibration make the confidence honest.
> The same response shape comes back to a Kotlin app that renders it, stores it in
> Room, and (optionally, offline) could have computed it on-device with TFLite. New
> models ship only after a paired-bootstrap test proves the gain is real.
