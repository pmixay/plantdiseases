# PlantDiseases 1.2 — Sprint Plan

**Window:** 2026-05-01 → 2026-05-31 (4 weeks)  
**Team:** P1 = Android · P2 = Server / ML  
**Classifier:** PlantVision 1.2 — server-only two-stage houseplant disease pipeline  
**Goal:** turn the current server-first project into a portfolio-grade, demo-ready app comparable to PictureThis / Plantix / NatureID on UX breadth, visible accuracy, and reliability, while keeping the server as the single source of truth.

---

## Where we stand

- **Product baseline changed to server-only inference.** Android is a thin Kotlin / CameraX / Material 3 client over a Python FastAPI server. Diagnosis and AI Doctor replies round-trip to the server. No on-device fallback is treated as a feature: if the server is unavailable, the app shows a clear error and retry path instead of a degraded local guess.
- **ML architecture is defined.** PlantVision uses Stage 1 YOLOv8n single-class leaf detection and Stage 2 EfficientNet-B0 classification over cropped leaf regions, with mean-voted softmax, temperature scaling, healthy fallback, and MSP/OOD not-a-plant gating.
- **Class space is fixed for the next release.** Current classifier config is 10 classes: `anthracnose`, `bacterial_leaf_spot`, `gray_mold`, `healthy`, `iron_chlorosis`, `late_blight`, `mosaic_virus`, `not_a_plant`, `powdery_mildew`, `sunburn`. `bacterial_leaf_spot`, `late_blight`, and `sunburn` are proxy-trained classes and need extra real-world validation before demo claims.
- **Server endpoints exist.** `/api/analyze`, `/api/identify`, `/api/chat`, `/api/care-plan`, `/api/progression`, `/api/health`, `/api/version`, `/api/classes`, `/api/metrics`, and `/api/metrics/prometheus` are in scope.
- **Server hardening is already strong.** The backend has magic-byte image validation, Pillow decompression-bomb guard, dimension / size limits, per-IP rate limiting, inference timeout, security headers, request logging, metrics, non-root Docker, and health checks.
- **Android core app is broad.** The client has scan, gallery/history, My Garden, per-plant journal/logs, reminders, AI Doctor UI, light meter, TTS, EN/RU strings, Material 3, dark/light/system theme, edge-to-edge, and predictive back.
- **Android networking is intentionally narrow right now.** The current Retrofit surface calls `/api/analyze`, `/api/health`, and `/api/chat`. Server endpoints for species ID, care plan, and progression exist, but Android still needs first-class client wiring for them.
- **Current branch focus:** `claude/cleanup-unused-code-S7D4D` removes local/demo fallbacks and stale config, tightens server-only behavior, and rewrites docs around quality-first deployment.

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

- `/api/health` reports `pipeline_mode: full`.
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
- [ ] Verify `/api/health` reports `pipeline_mode = full` on a clean machine.
- [ ] Export and benchmark ONNX / INT8 classifier path; confirm production loads quantized ONNX before `.pth`.
- [ ] Produce first `MODEL_CARD.md` with dataset sources, class list, validation metrics, known limitations, and proxy-trained classes.
- [ ] Build a small real-world OOD set: hands, desks, walls, fabric, plant pots, empty rooms, screenshots.
- [ ] Sweep not-a-plant MSP/OOD thresholds against that set and persist selected values.

### P1 · Android

- [ ] Build debug APK against the production API base URL and confirm all existing screens compile after cleanup.
- [ ] Add a Profile / diagnostics card that calls `/api/health` and shows: online/offline, pipeline mode, server version, and class count.
- [ ] Polish analysis error states:
  - timeout;
  - no network;
  - 429 rate limit;
  - server starting / pipeline unavailable;
  - unsupported image.
- [ ] Add one-tap retry from the error card and preserve the selected image after failure.
- [ ] Confirm EN/RU strings for the new server-only error copy.
- [ ] Add smoke QA checklist to `README` or `DEPLOYMENT.md`.

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

- [ ] Wire Retrofit models and repository methods for `/api/identify`.
- [ ] Add species suggestion flow to Add-to-Garden:
  - user can scan or reuse current result image;
  - app shows PlantNet suggestions;
  - user accepts one candidate or keeps manual text.
- [ ] Store accepted `scientificName` and display/common name separately where possible.
- [ ] Wire `/api/care-plan` into Plant detail and Result screen.
- [ ] Add a Care Plan card:
  - watering;
  - light;
  - humidity;
  - fertilizing;
  - disease-specific warnings;
  - “generated for species / unknown species” label.
- [ ] Make reminder creation use care-plan defaults when available.

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
