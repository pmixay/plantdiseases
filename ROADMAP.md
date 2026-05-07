# PlantDiseases 2.1 — Sprint Plan (3 weeks)

**Window:** active · **Team:** P1 = Android · P2 = Server / ML

**Class taxonomy is now 7 trainable classes (v2.1):**
`bacterial_leaf_spot`, `gray_mold_botrytis`, `healthy`,
`leaf_spot_fungal` (= anthracnose + cercospora + alternaria),
`not_a_plant`, `powdery_mildew`, `viral_disease` (= mosaic + leaf_curl
+ ringspot).

**Deferred from the v2.0 design:** `edema`, `leaf_scorch_underwatering`,
`nutrient_deficiency`, `sunburn_or_scorch`. Public sources have very
few clean indoor examples for these, and proxy-labelling them
collapses recall on the disease classes we already have data for.
Re-enabling them is a config switch in `train_notebook.ipynb` §1
once a real-world labelled set lands.

Removed vs v1.x: `late_blight`, `iron_chlorosis`. Pests are out of
the taxonomy entirely (no longer surfaced as a "possible pest causes"
hint either).

**Architecture summary:**
- Stage 1 detector retrained on **indoor** leaf bboxes; PlantDoc
  removed from detector training (its agricultural close-ups bias
  localisation toward field shots).
- Stage 2 backbone: **EfficientNetV2-S** teacher distilled into
  **MobileNetV3-Large** student (KD α=0.5, τ=4.0, EMA decay 0.999;
  the EMA copy is what we save and export).
- Loss: **Logit Adjustment** (Menon ICLR 2021) τ=0.7 +
  label_smoothing=0.05. **Loss-side balancing only** —
  `WeightedRandomSampler` is **off** by default; stacking sampler +
  LA over-corrects rare classes (Liu et al. 2024).
- OOD score: **MaxLogit** (Hendrycks 2019).
- Calibration: temperature + RAPS, both fitted on the deployed INT8
  ONNX session; dedicated `temperature_cal` (~5 %) and `conformal_cal`
  (~10 %) splits, both restricted to `plantdoc + inat` so RAPS
  exchangeability holds at deploy.
- Quantisation: **static INT8 QDQ** with a 500-photo `conformal_cal`
  reader; ship gate refuses if disease F1 drops by > 0.05 vs FP32
  (warning at > 0.02).
- Primary metric: disease macro-F1 on **`val_realworld`** (≥ 0.70,
  no class with recall=0, healthy-bias < 1.5).

---

## Confirmed decisions (operator)

These were the open v2.0 → v2.1 migration questions. All three are now
locked; the codebase already reflects them.

1. **Taxonomy:** v2.1 — 7 trainable classes
   (`bacterial_leaf_spot`, `gray_mold_botrytis`, `healthy`,
   `leaf_spot_fungal`, `not_a_plant`, `powdery_mildew`,
   `viral_disease`). The four physiological conditions stay deferred
   until a real-world labelled set lands. v2.2 with 11 classes is a
   separate, post-release effort; some prep work already starts in
   Week 3 below.
2. **Production server URL:** unknown for now → release-build HTTPS
   work is **out of this sprint**. Default `API_BASE_URL` stays at
   the dev box over HTTP, `network_security_config.xml` keeps
   `cleartextTrafficPermitted="true"` for all hosts. See "Deferred
   to a separate sprint" below for the work that will move out
   when the real prod URL lands.
3. **KD hyperparameters:** `α=0.5, τ=4.0`, EMA decay 0.999, 10 epochs.
   Already wired in `train_notebook.ipynb` §5, `classes.json`,
   `MODEL_CARD.md`, README and ROADMAP architecture summary.

A fourth side-question — whether to keep the four deferred disease
cards (`edema`, `leaf_scorch_underwatering`, `nutrient_deficiency`,
`sunburn_or_scorch`) in `server/diseases_data.py` — was defaulted to
**keep** for v2.2 forward-compat. Server lookup tolerates extras
(`DISEASES_DATABASE.get(cls, DISEASES_DATABASE['unknown'])`), and
keeping the cards means re-enabling those classes in v2.2 will be a
training-time switch, not a content port. Override by deleting the
four entries if you'd rather have a strictly minimal database.

---

## Done since v2.0 freeze (no need to re-do)

These items are merged on the working branch and do **not** appear in
the per-week plan below — listed for tracking only.

- [x] **Notebook rewrite to v2.1** — `train_notebook.ipynb` now covers
      the full 7-class pipeline: group-aware splits, dedup + leakage
      audit, two dedicated calibration splits, EMA student during KD,
      mandatory `quant_pre_process`, INT8 drift gate, ship gate.
- [x] **Server taxonomy sync** — `classes.json`, `classifier.py`
      `DEFAULT_CLASS_NAMES`, `MODEL_CARD.md`, `tests/test_pipeline_helpers.py`,
      doctor LLM prompt, README, ROADMAP all on v2.1 (7 classes).
- [x] **AI Doctor real `/api/chat` flow** — `DoctorChatSheet` no
      longer uses the local stub. `ChatRequest` / `ChatResponse`
      models match the server contract (sessionId, message, locale,
      diagnosis dict; available + reply + reason + fallback +
      usage + session). Network failures get translated EN/RU error
      messages and persist in chat history.
- [x] **`/api/progression` wired** — Models, ApiService and
      ScanRepository all carry the request / response shapes plus a
      `scanToProgressionPayload(scan)` helper. UI integration is in
      Week 2 below.
- [x] **Rate-limiter clock fix** — `RateLimitMiddleware` now uses
      `time.monotonic()` for both `_last_cleanup` init and per-request
      `now`, so an NTP step or DST jump can no longer lock every
      client out for several minutes.
- [x] **Pipeline OOD-gate clarity refactor** —
      `_should_route_to_nap` now evaluates detector_low and
      max_logit_low explicitly (semantics unchanged, but the new
      version clearly states "two-of-two", with off-mode when
      `max_logit_floor ≤ 0`).
- [x] **`PlantRepository.getLatestScanForPlant(plantId)`** convenience
      wrapper (delegates to `scanDao.getScansForPlant` which is
      already `ORDER BY timestamp DESC`).
- [x] **Dual-mode imports** — `server/main.py`, `ai_services.py` and
      `pipeline.py` use a try/except wrapper that prefers package
      imports (`from .config …`) and falls back to flat imports
      (`from config …`). Both `python -m server.main` and the Docker
      `uvicorn main:app` run without `PYTHONPATH` hacks.
- [x] **Retry-After cap raised** — `RetryInterceptor.maxDelayMs` 8 s
      → 30 s. Server cold-start path can legitimately ask for ~20 s
      while artefacts deserialise.
- [x] **`IdentifyResponse.cached`** annotated — server returns
      `cached: true` on PlantNet cache hits but Android UI does not
      yet surface it. Field is kept on the wire with a docstring;
      Week 3 cleanup will surface a "served from cache" indicator.

---

## Week 1 — Train v2.1 weights · feed real chat into UI

### P2 — Server / ML

- [ ] **Train v2.1 weights end-to-end.** Run the rewritten
      `train_notebook.ipynb` in full on a single A100-40 (or T4 with
      `BATCH=64`). Time budget: 50–70 min on A100, 2–3× on T4.
      Expected outputs in `models/`: `classifier_int8.onnx`,
      `classifier.onnx`, `classifier.pth`, `classes.json`,
      `temperature.json`, `conformal.json`, `model_card.json`,
      `training_metrics.json`, `dinov2_probe.json`, `run_log.jsonl`.
      **Checkpoint:** ship gate must pass — disease macro-F1 ≥ 0.70 on
      `val_realworld`, no class with recall=0, healthy-bias < 1.5.
- [ ] **Detector reuse decision.** `detector.{pt,onnx}` is shipped in
      `server/models/`. If we have a YOLO `data.yaml` for indoor
      leaves, drop it under `data_v2/` and let §3 of the notebook
      retrain (mosaic=0.1, 60 epochs, single class). Otherwise re-use
      the shipped pair as-is.
- [ ] **Verify start-up against real artefacts.** Boot the server
      with the freshly-trained `models/` and run
      `pytest server/tests/`. **Checkpoint:** all 57 tests still
      green; `/api/version` returns `2.1`; `/api/classes` returns 7
      classes.

### P1 — Android

- [ ] **Rebuild after Models.kt + DoctorChatSheet rewrite.**
      `ChatRequest` / `ChatResponse` shapes changed; old call-sites
      that referenced `ChatContext`, `suggestedQuestions` will break
      the build. Sweep, fix, run a debug install on a real device.
      **Checkpoint:** debug build runs; AI Doctor sheet posts to
      `/api/chat` and receives a real reply.
- [ ] **AI Doctor session budget UI.** `ChatResponse.session` carries
      `turns_used / turns_remaining` and `tokens_used /
      tokens_remaining`. Add a discreet progress chip in
      `DoctorChatSheet` (visible only when remaining ≤ 20 % of cap).
      **Checkpoint:** the user sees a "3 / 12 messages left" hint
      before the cap fires.
- [ ] **Translate `available=false` into actionable copy.** The
      `formatChatUnavailable` switch I added handles all current
      reasons (`session_turn_cap`, `session_token_cap`, `no_api_key`,
      `message_too_long`, `empty_message`, `empty_reply`,
      `api_error`). Verify EN + RU strings against the reasons that
      actually fire by inspecting `server/ai_services.py` after
      training is live, and add any missing branches.
- [ ] **Result-screen feedback prompt for low-confidence verdicts.**
      When `low_confidence=true` or any warning is non-empty, show
      a single "Did this match what you saw?" thumbs-up / thumbs-
      down chip on `ResultActivity`. Persist locally for now — the
      server endpoint comes in Week 3. **Checkpoint:** thumbs press
      writes a row to a new Room table `feedback`.

---

## Week 2 — Progression · split ResultActivity · CI signed APK

### P2 — Server / ML

- [ ] **`/api/progression` historical extension.** Today the endpoint
      compares two payloads. Extend it to optionally accept a
      `history: list[dict]` and return a 5-point sparkline of disease
      severity scores so the Android timeline can draw a chart.
      Backwards-compatible with the `current` + `previous` shape.
      **Checkpoint:** new contract covered by 3 unit tests in
      `tests/test_endpoints.py`.
- [ ] **Per-session lock granularity.** `ai_services._chat_lock` is
      a process-global `asyncio.Lock` that serialises every chat
      session. Switch to a per-`session_id` lock map (with a
      janitor that evicts after 10 min idle). **Checkpoint:** two
      simulated parallel sessions complete in roughly the time of
      one (load test in `tests/test_ai_services.py`).
- [ ] **Deploy artefact bundle.** Wire a `make ship-bundle` target
      that ZIPs `server/models/{detector.pt,detector.onnx,
      classifier.pth,classifier.onnx,classifier_int8.onnx,
      classes.json,temperature.json,conformal.json,model_card.json,
      MODEL_CARD.md}` plus a `MANIFEST.json` with SHA-256s. Used by
      the rollback path in Week 3.
- [ ] **MODEL_CARD post-train refresh.** After training is live,
      copy real `train_counts`, `val_realworld_counts`, INT8 drift
      number, best KD epoch, T_value, `q_hat` from
      `model_card.json` into `MODEL_CARD.md`. **Checkpoint:** the
      printed model card matches `model_card.json` byte-for-byte
      on the per-class counts.

### P1 — Android

- [ ] **Wire `/api/progression` into PlantDetailActivity.** When
      the user opens a plant with ≥ 2 scans, fetch a verdict for
      `latestScan` vs the one before; display the trend chip
      (`improving` / `stable` / `worsening` / `baseline`) above the
      timeline. Use the `scanToProgressionPayload(scan)` helper
      from `ScanRepository`. **Checkpoint:** the chip changes when
      a fresh scan flips the trend.
- [ ] **Before / after thumbnails on the trend chip.** Tapping the
      chip opens a half-sheet with the two compared photos side-
      by-side, the disease label, the confidence delta and a
      one-line `delta_severity` explanation.
- [ ] **Split `ResultActivity.kt` (1189 lines).** Extract the four
      visible sections into separate composables / view-builders:
      `ResultHeaderView` (verdict + confidence + prediction set),
      `ResultTreatmentView` (steps + prevention),
      `ResultCarePlanView` (already a card; promote to component),
      `ResultDoctorView` (chat CTA). Keep the activity as a thin
      coordinator. **Checkpoint:** each new file ≤ 350 lines.
- [ ] **Cut a signed release APK on tag via CI.** Add a GitHub
      Actions workflow that, on `v*.*.*` tag push, runs `./gradlew
      assembleRelease` + signs with the keystore from secrets and
      uploads the APK as a release asset. (HTTPS / cleartext
      stripping deferred until the prod URL lands — see "Deferred
      to a separate sprint" below.) **Checkpoint:** tag → CI green →
      APK downloadable.

---

## Week 3 — Feedback loop · rollback path · v2.2 prep

### P2 — Server / ML

- [ ] **`POST /api/feedback`** endpoint (was Week 3 of the v2.0
      plan, now finally landing). Body: `scan_id`, `correct: bool`,
      optional free-text. Persist to a SQLite append-only ledger
      under `server/feedback.db`. **Checkpoint:** Android's
      Week 1 thumbs-press now POSTs through; rate-limited to 1/s
      per IP.
- [ ] **Rollback path.** Add `models/current` + `models/previous`
      symlink-style indirection: the server reads from
      `models/current/`, an ops switch flips to `previous` if the
      ship gate misfires post-deploy. Wire into the boot path so
      a missing or partial bundle falls back to `previous`.
      **Checkpoint:** removing one file from `current/` boots
      `previous/` cleanly with a warning log entry.
- [ ] **Begin v2.2 data work for the four deferred classes.**
      Cold-start a `data_v2_pending/` tree with at least 100 real
      indoor photos per deferred class (`edema`,
      `leaf_scorch_underwatering`, `nutrient_deficiency`,
      `sunburn_or_scorch`), each with sidecar JSON. Source mix:
      r/houseplants, iNaturalist (filtered), targeted Roboflow
      Universe slices, internal photo donations. **Checkpoint:**
      400 net images with sidecars; `python -c "from train_notebook
      ... fetch_inat_supplement"`-style smoke test confirms they
      thread through the loader.
- [ ] **Negative-sample audit** on the trained `not_a_plant`
      class. Production logs likely show the OOD gate misfiring on
      darker leaves and shiny succulents. Pull the lowest-MaxLogit
      true-positives from prod logs (or staging traffic), label
      them as additional `not_a_plant` training images for v2.2.

### P1 — Android

- [ ] **Wire feedback POST.** Replace the local Room write from
      Week 1 with a `POST /api/feedback` round-trip; on failure,
      keep the row queued in Room and retry on next launch.
- [ ] **Cold-start UX.** Surface a single 5-second toast "Server is
      warming up…" once per session when the retry interceptor
      backs off on a 503 — visible feedback for the now-30-second
      retry budget on first request after deploy.
- [ ] **`IdentifyResponse.cached` UI hook.** Server returns
      `cached: true` on PlantNet cache hits; surface a small "served
      from cache" pill next to species suggestions on the camera
      results screen. Field is already on the model — only the UI
      hook is missing.

---

## Deferred to a separate sprint

Work that is intentionally **not** part of the 3-week plan above —
listed here so it doesn't get lost. Schedule once the relevant
prerequisite (prod URL, real-world labelled data, scaling pressure)
materialises.

### Network security hardening (waiting on prod URL)

- [ ] **HTTPS-only release variant.** Once the operator provides the
      production URL, do all of the following in one PR:
    - Replace the default `API_BASE_URL` in `app/build.gradle.kts` for
      the **release** variant with the HTTPS URL. The **debug** variant
      keeps the IP/HTTP default for LAN testing.
    - Split `res/xml/network_security_config.xml` into
      `network_security_config.xml` (release: cleartext disabled
      globally, certificates from system) and
      `network_security_config_debug.xml` (debug: cleartext permitted).
      Wire via `manifestPlaceholders` per build type.
    - Add a banner on the Profile → Server URL screen that warns when
      the user overrides the release default with an HTTP host.
    - Verify `Strict-Transport-Security` and TLS 1.2+ enforcement on
      the production endpoint before merging.
    - **Checkpoint:** Release APK refuses cleartext to any non-debug
      host; debug LAN testing still works.

### Real-world labelled data for v2.2 (4 physiological classes)

- [ ] **`edema`, `leaf_scorch_underwatering`,
      `nutrient_deficiency`, `sunburn_or_scorch`.** Target ≥ 200 clean
      indoor photos per class, with sidecar JSON, before adding them
      back to the trainable taxonomy. Initial cold-start happens in
      Week 3 (`data_v2_pending/` tree, ~100 each); the v2.2 training
      run kicks off when each class clears 200. Sources: r/houseplants,
      iNaturalist (filtered), Roboflow Universe, internal donations.
- [ ] **Disease cards** for the four deferred classes are already in
      `server/diseases_data.py` (forward-compat). Re-enabling them in
      v2.2 is a one-line config change in `train_notebook.ipynb` §1
      plus an update to `classifier.py:DEFAULT_CLASS_NAMES` and
      `tests/test_pipeline_helpers.py:EXPECTED_CLASSES`.

### Scale-driven optimisations (only if hot-path metrics demand it)

- [ ] **Per-`session_id` chat lock map.** The current global
      `_chat_lock` in `ai_services.py` is held only briefly (around
      counter reads/writes, never during the LLM call), so contention
      is theoretical for now. If `/api/metrics` shows lock-wait
      latency > 5 ms under load, swap to a per-session map with a TTL
      janitor.
- [ ] **DeepCore coreset / dataset pruning** (Guo et al. 2022) if
      iteration time becomes a bottleneck. Random selection stays as
      the no-op control.

---

## Eval contract (unchanged from notebook §7)

| Slice | Role | Pass criterion |
|---|---|---|
| `val_indomain` | early stopping + schedule decisions | tracked; best EMA checkpoint restored |
| `val_realworld` | **primary — ship/no-ship gate** | disease macro-F1 ≥ **0.70**; no class with recall=0; healthy-bias < 1.5 |
| `val_studio` (PlantVillage) | transfer / background-bias monitor | tracked, never blocking |
| `temperature_cal` | INT8 + temperature scaling | held out, never reported |
| `conformal_cal` | RAPS calibration | held out; target empirical coverage ≈ 1 − α |

**Engineering keepers from the legacy pipeline** (good already, do not rewrite): disease macro-F1 as primary metric, healthy-bias monitor each epoch, group-aware splits by `(source, plant_id)`, hardlinks for image stores, sparse checkout for big repos, ONNX sanity drift-check after every quantization step.

**Explicit non-goals for this sprint:**
- **No dataset distillation.** ROI is weak at our resolution and
  ambiguous label boundaries.
- **No model pruning.** KD into MobileNetV3-Large + static INT8
  already covers ~95 % of the runtime budget. Revisit only if INT8
  inference latency on the production server falls outside the SLA.

See `server/train_notebook.ipynb` for the full v2.1 recipe (data
hygiene → DINOv2 probe → V2-S fine-tune → KD with EMA → ONNX/INT8
export → temperature + RAPS → ship gate).

---

# PlantDiseases 1.2 — Sprint Plan (legacy, superseded by 2.0)

**Window:** 2026-05-01 → 2026-05-31 (4 weeks)  
**Team:** P1 = Android · P2 = Server / ML  
**Classifier:** PlantVision 1.2 — server-only two-stage houseplant disease pipeline  
**Goal:** turn the current server-first project into a portfolio-grade, demo-ready app comparable to PictureThis / Plantix / NatureID on UX breadth, visible accuracy, and reliability, while keeping the server as the single source of truth.

---

## Where we stand

- **Product baseline changed to server-only inference.** Android is a thin Kotlin / CameraX / Material 3 client over a Python FastAPI server. Diagnosis and AI Doctor replies round-trip to the server. No on-device fallback is treated as a feature: if the server is unavailable, the app shows a clear error and retry path instead of a degraded local guess.
- **ML architecture is defined.** PlantVision 2.0 uses Stage 1 YOLOv8n single-class leaf detection and Stage 2 distilled MobileNetV3-Large student (KD ← EfficientNetV2-S teacher) over cropped leaf regions, with mean-voted softmax, temperature scaling, healthy fallback, MaxLogit-based OOD gate, and conformal RAPS prediction sets.
- **Class space is fixed at 11 (PlantVision 2.0):** `bacterial_leaf_spot`, `edema`, `gray_mold_botrytis`, `healthy`, `leaf_scorch_underwatering`, `leaf_spot_fungal`, `not_a_plant`, `nutrient_deficiency`, `powdery_mildew`, `sunburn_or_scorch`, `viral_disease`. Pests are out of the taxonomy; `late_blight` and `iron_chlorosis` were retired vs v1.x.
- **Server endpoints exist.** `/api/analyze`, `/api/identify`, `/api/chat`, `/api/care-plan`, `/api/progression`, `/api/health`, `/api/version`, `/api/classes`, `/api/metrics`, and `/api/metrics/prometheus` are in scope.
- **Server hardening is already strong.** The backend has magic-byte image validation, Pillow decompression-bomb guard, dimension / size limits, per-IP rate limiting, inference timeout, security headers, request logging, metrics, non-root Docker, and health checks.
- **Android core app is broad.** The client has scan, gallery/history, My Garden, per-plant journal/logs, reminders, AI Doctor UI, light meter, TTS, EN/RU strings, Material 3, dark/light/system theme, edge-to-edge, and predictive back.
- **Android networking is intentionally narrow right now.** The current Retrofit surface calls `/api/analyze`, `/api/health`, and `/api/chat`. Server endpoints for species ID, care plan, and progression exist, but Android still needs first-class client wiring for them.
- **Server / Android behaviour is fail-fast.** Server refuses to start without trained weights; Android has no on-device inference. Client errors are explicit ("server unreachable", "rate limited", etc.) with a retry CTA — never a silent local guess.

---

## Market gap vs PictureThis / Plantix / NatureID / PlantIn

| Feature | Us today | After sprint |
|---|---:|---:|
| Disease detection | architecture ready; weights are ship gate | trained + validated |
| Not-a-plant rejection | yes, model + OOD gate design | real-world calibrated |
| Plant species ID | server endpoint | Android flow wired |
| My Garden / per-plant log | yes | yes, with species-aware scan context |
| Care reminders | local reminders | permission UX polished |
| AI Plant Doctor chat | server-backed | polished error / budget states |
| Care plan | server endpoint | Android care-plan card |
| Disease progression | server endpoint, pairwise | chart + trend timeline |
| Compare with healthy | — | result-screen split view |
| Active-learning feedback | — | uncertain-verdict feedback loop |
| Offline inference | intentionally out | intentionally out |
| Demo release APK | not cut | signed APK + golden-path QA |

---

## Current stage — May 1, 2026

- Android analysis flow uploads the prepared image to the server, saves successful responses locally, and routes to Result. Server timeout, no-network, and rate-limit states have dedicated user-facing errors and retry.
- Android no longer bundles or depends on `TfliteOfflineAnalyzer`. That keeps the demo honest: a failed server means no diagnosis, not a fake local estimate.
- Scan history stores multi-region detections, all class probabilities, and pipeline warnings. Statistics exclude `not_a_plant` from healthy/diseased rollups.
- AI Doctor on Android calls `/api/chat`; placeholder local replies are not part of the product baseline.
- Server startup is fail-fast: if trained detector/classifier artefacts are missing, the API does not start.
- `classes.json` declares PlantVision 1.2, EfficientNet-B0, 288 px classifier input, 10 classes, and proxy-trained classes that require extra scrutiny.
- Main blockers before release-quality demo:
  - run the Colab training notebook and publish real model artefacts;
  - validate per-class F1 on real indoor plant photos;
  - wire Android to `/api/identify`, `/api/care-plan`, and `/api/progression`;
  - cut signed APK through CI;
  - complete real-device online golden-path QA.

---

## Targets — Definition of Done

- `/api/health` returns 200 with `status: "ok"` and the current `num_classes`.
- Model artefacts shipped under `server/models/`: `detector.pt` or `detector.onnx`, `classifier.pth` or ONNX, `temperature.json`, updated `classes.json`, and `MODEL_CARD.md`.
- YOLOv8n single-class leaf detector: **mAP50 ≥ 0.65**.
- EfficientNet-B0 classifier: **top-1 ≥ 0.82** on held-out validation with TTA + calibration.
- Disease macro-F1: **≥ 0.70** on curated in-domain validation.
- Per-class real-world F1 floor: **≥ 0.50** or the class is demoted from release claims.
- `not_a_plant` OOD false-positive rate is manually checked on indoor non-plant photos.
- `/api/analyze` p50 ≤ 250 ms on target deployment after ONNX / INT8 preference path is confirmed.
- Android golden path works on a real device:
  - scan → analyze → result;
  - add result to Garden;
  - open AI Doctor;
  - view care plan;
  - compare progress for a plant;
  - handle server error / retry cleanly.
- CI produces a signed release APK on tag.
- No offline / demo / heuristic fallback is advertised or silently used.

---

## Week 1 — Make the server real (May 1 – May 7)

### P2 · Server / ML

- [ ] Run `server/train_notebook.ipynb` end-to-end on Colab T4 / A100.
- [ ] Publish `detector.pt`, `classifier.pth` or ONNX, `temperature.json`, and updated `classes.json` into `server/models/`.
- [ ] Verify `/api/health` returns 200 with `status: "ok"` on a clean machine (the server is fail-fast, so a successful health check implies all weights loaded).
- [ ] Export and benchmark ONNX / INT8 classifier path; confirm production loads quantized ONNX before `.pth`.
- [ ] Produce first `MODEL_CARD.md` with dataset sources, class list, validation metrics, known limitations, and proxy-trained classes.
- [ ] Build a small real-world OOD set: hands, desks, walls, fabric, plant pots, empty rooms, screenshots.
- [ ] Sweep not-a-plant MSP/OOD thresholds against that set and persist selected values.

### P1 · Android

- [x] Build debug APK against the production API base URL and confirm all existing screens compile after cleanup.
- [x] Add a Profile / diagnostics card that calls `/api/health` and shows: online/offline, pipeline mode, server version, and class count.
- [x] Polish analysis error states:
  - timeout;
  - no network;
  - 429 rate limit;
  - server starting / pipeline unavailable;
  - unsupported image.
- [x] Add one-tap retry from the error card and preserve the selected image after failure.
- [x] Confirm EN/RU strings for the new server-only error copy.
- [x] Add smoke QA checklist to `README` or `DEPLOYMENT.md`.

---

## Week 2 — Species + care plan become product features (May 8 – May 14)

### P2 · Server / ML

- [ ] Stabilize `/api/identify` response contract:
  - `available`;
  - top species candidates;
  - scientific name;
  - common names;
  - score;
  - quota / missing-key graceful state.
- [ ] Add integration tests for `/api/identify` with mocked PlantNet responses.
- [ ] Upgrade `/api/care-plan` from generic rules to species-aware rules.
- [ ] Add a small curated CSV for common houseplants:
  - light range;
  - watering interval;
  - humidity preference;
  - temperature range;
  - fertilizing cadence.
- [ ] Keep Claude polish optional: rules must return a usable plan even when no LLM key is set.
- [ ] Add per-disease cautions to care plans, e.g. avoid misting for fungal issues.

### P1 · Android

- [x] Wire Retrofit models and repository methods for `/api/identify`.
- [x] Add species suggestion flow to Add-to-Garden:
  - user can scan or reuse current result image;
  - app shows PlantNet suggestions;
  - user accepts one candidate or keeps manual text.
- [x] Store accepted `scientificName` and display/common name separately where possible.
- [x] Wire `/api/care-plan` into Plant detail and Result screen.
- [x] Add a Care Plan card:
  - watering;
  - light;
  - humidity;
  - fertilizing;
  - disease-specific warnings;
  - “generated for species / unknown species” label.
- [x] Make reminder creation use care-plan defaults when available.

---

## Week 3 — Progression, feedback, and visible accuracy (May 15 – May 21)

### P2 · Server / ML

- [ ] Extend `/api/progression` from pairwise comparison to a historical-scan request:
  - accept ordered scan list;
  - return smoothed severity series;
  - trend: improving / stable / worsening;
  - recovery or decline rate;
  - confidence caveat.
- [ ] Add `/api/feedback`:
  - diagnosis id or scan hash;
  - user verdict: correct / wrong / unsure / different disease;
  - optional corrected class;
  - opt-in image upload flag.
- [ ] Add metrics:
  - per-class confidence buckets;
  - OOD-gated count;
  - uncertain healthy count;
  - feedback counts.
- [ ] Add tests for progression edge cases:
  - no previous scan;
  - different disease class;
  - missing severity;
  - not-a-plant scan.
- [ ] Review proxy-trained classes. If a class fails the F1 floor on real photos, mark it as limited or remove it from release claims.

### P1 · Android

- [ ] Disease progression view in Plant detail:
  - severity-over-time chart;
  - before/after thumbnails;
  - trend label;
  - “needs another scan” empty state.
- [ ] Result-screen feedback prompt for uncertain verdicts:
  - “Looks correct”;
  - “Wrong”;
  - “Not sure”;
  - optional corrected disease.
- [ ] Send feedback only after clear opt-in; no silent photo upload.
- [ ] Compare-with-healthy mode:
  - user photo on one side;
  - canonical healthy reference by species when known;
  - generic healthy reference when species is unknown.
- [ ] AI Doctor polish:
  - show budget / unavailable state from server;
  - keep chat history per plant;
  - include current diagnosis and species context in chat request.
- [ ] Add localized copy explaining uncertainty and why feedback helps.

---

## Week 4 — Demo polish, release, and QA (May 22 – May 31)

### P2 · Server / ML

- [ ] Final benchmark pass:
  - detector mAP;
  - classifier top-1;
  - macro-F1;
  - per-class F1;
  - OOD false-positive rate;
  - `/api/analyze` p50 / p95 latency.
- [ ] Regenerate `MODEL_CARD.md` with final numbers and limitations.
- [ ] Production deployment check:
  - Docker image starts as non-root;
  - health check passes;
  - TLS terminated;
  - `CORS_ORIGINS` restricted;
  - logs rotate;
  - metrics endpoint reachable.
- [ ] Add backup rollback path for model artefacts:
  - `models/current`;
  - `models/previous`;
  - documented manual rollback.
- [ ] Document required environment variables:
  - PlantNet key;
  - Anthropic key;
  - rate limits;
  - inference timeout;
  - trusted proxies.
- [ ] Freeze API contracts used by the demo APK.

### P1 · Android

- [ ] Material You / UI polish pass:
  - dynamic colors;
  - result cards;
  - camera → analysis → result transition;
  - Garden hero cards;
  - empty states.
- [ ] Final localization pass for EN/RU.
- [ ] QA pass:
  - dark mode;
  - light mode;
  - edge-to-edge;
  - Android 13+ notification permission;
  - no-network;
  - rate-limit;
  - server 5xx;
  - low-confidence;
  - not-a-plant.
- [ ] Real-device golden path:
  - healthy plant;
  - diseased plant;
  - blurry image;
  - not a plant;
  - Garden + reminders;
  - AI Doctor;
  - Care Plan;
  - Progression chart.
- [ ] Cut signed demo APK from CI.
- [ ] Add a 90-second demo script and screenshots to the repo.
- [ ] Tag release: `v1.2-demo`.

---

## Out of scope

- On-device inference: no TFLite, ONNX Runtime Mobile, MediaPipe, or heuristic fallback.
- Demo / fake model mode on the server.
- Google Play public release, Privacy Policy, Data Safety form, monetization.
- Outdoor crop and agricultural workflows.
- Community feed, expert consultations, marketplace, or social features.
- Full Hilt / Compose rewrite.
- Toxicity / pet-safety database.
- Hardware integrations beyond camera and ambient light sensor.

---

## Risks & mitigations

- **Model weights are not trained in time.**  
  Mitigation: treat Colab run as the Week 1 ship gate; no APK demo claim until `/api/health` is full.

- **Proxy-trained classes underperform on real indoor photos.**  
  Mitigation: require per-class F1 floor; demote weak classes from release claims rather than overpromising.

- **PlantNet quota or missing key breaks species ID.**  
  Mitigation: server returns `available: false`; Android keeps manual species entry.

- **Claude / AI Doctor cost overrun.**  
  Mitigation: keep per-session turn and token caps; surface unavailable/budget state in UI.

- **Server-only design creates a fragile demo.**  
  Mitigation: add health status card, clear retry UX, deployment checklist, and smoke test before recording/demo.

- **Too much Week 4 polish.**  
  Mitigation: prioritize golden path, signed APK, and model card over animations.

- **User trust risk from uncertain diagnoses.**  
  Mitigation: show uncertainty, top alternatives, feedback prompt, and “not medical / horticultural advice” style caveat in UX where needed.

---

## Suggested immediate commit plan

1. Replace the existing `ROADMAP.md` with this sprint-style plan.
2. Add server endpoint contract notes for `/api/identify`, `/api/care-plan`, `/api/progression`, and `/api/feedback`.
3. Add Android TODO issues from Weeks 2–3.
4. Do not merge release copy until trained weights and final metrics exist.
