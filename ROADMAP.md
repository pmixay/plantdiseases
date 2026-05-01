# PlantDiseases 1.1 — Sprint Plan

**Window:** 2026-04-30 → 2026-05-30 (4 weeks)
**Team:** P1 = Android · P2 = Server / ML
**Classifier:** PlantVision (10-class houseplant disease pipeline)
**Goal:** lift the project from a working pipeline in demo mode to a
portfolio-grade app comparable to PictureThis on UX, feature breadth and
on-screen accuracy.

---

## Where we stand

- **ML server-side (P2) — implemented in code; weights still need a
  Colab T4 run.** Notebook (`server/train_notebook.ipynb`) trains the
  full PlantVision 1.1 pipeline (10 classes incl. `bacterial_leaf_spot`
  and `mosaic_virus`; +30% iNat scrape; SHA-256 dedup; EXIF strip; B0 +
  optional V2-S ensemble; 5-crop+hflip TTA; OOD calibration sweep;
  auto-generated MODEL_CARD). Server endpoints
  (`/api/identify`, `/api/chat`, `/api/care-plan`, `/api/progression`),
  severity scoring on `/api/analyze`, AI-doctor budget caps + abuse
  logging, ensemble peer auto-discovery in `classifier.py` are all
  shipped on this branch.
- Android client covers the core scan flow (camera, gallery, history,
  guide, profile, share, multi-bbox overlay, top-3, blur/green pre-checks,
  bilingual EN/RU, Material 3, onboarding), plus **My Garden** with plant
  list, add/edit flow, plant detail timeline, free-text journal, photo
  log, watering log, and result-screen "Add to garden".
- Android networking now uses a build-time API base URL only. The default
  production endpoint is `https://78.17.40.155:8000/`; local overrides are
  supplied through the Gradle `API_BASE_URL` property, not through app UI.
- Server is hardened (rate limiter, magic-byte validation, Prometheus,
  Docker, non-root). CI runs on push.
- Confirmed scope: portfolio demo, $20–50/mo budget for paid services,
  add plant **species ID** alongside disease detection, push accuracy as
  far as possible.

## Market gap (vs PictureThis / Plantix / NatureID)

| Feature                     | Us today | After sprint |
|-----------------------------|----------|--------------|
| Disease detection           | demo     | trained      |
| Plant species ID            | —        | yes (PlantNet) |
| My Garden / per-plant log   | yes      | yes          |
| Care reminders + push       | —        | yes          |
| AI Plant Doctor chat        | —        | yes (Claude Haiku) |
| Light meter                 | —        | yes          |
| Severity scoring            | —        | yes          |
| Disease progression chart   | —        | yes          |
| Real-time framing + auto-capture | —   | yes          |
| Offline on-device inference | —        | yes (TFLite) |
| Bilingual EN/RU             | yes      | yes (kept)   |

## Current stage (May 1, 2026)

- Android portfolio shell is active: scan, gallery, guide, profile,
  result details, My Garden, per-plant journal and add-to-garden are in
  code.
- Camera scan UI no longer uses a hard square outline over the preview;
  the analyzer still drives quality hints and auto-capture behavior.
- Runtime server switching has been removed from the app. Server endpoint
  is a build parameter with production default
  `https://78.17.40.155:8000/`.
- CI remains the build verification path for Android. Local Windows SDK
  setup is not treated as a blocker for feature work.
- Main blockers before demo-quality release: run the Colab training job,
  ship model weights, finish care reminders / AI Doctor / light meter,
  and complete golden-path QA on a real device.

## Targets (Definition of Done)

- `pipeline_mode: full` in `/api/health`.
- YOLOv8n single-class leaf **mAP50 ≥ 0.65**.
- Classifier **top-1 ≥ 0.82** on held-out val with TTA + ensemble.
- 10–12 disease classes (10 in PlantVision 1.1).
- New endpoints: `/api/identify`, `/api/chat`, `/api/care-plan`. ✅
- 6 net-new user-visible features (see weeks below) shipped on `main`.
- Demo APK installs and runs golden path end-to-end on a real device,
  online and offline.

---

## Week 1 — Make it real (Apr 30 – May 6)

### P2 · Server / ML
- [ ] Run `train_notebook.ipynb` on Colab T4; ship `detector.pt`,
      `classifier.pth`, `temperature.json`, `classes.json` to
      `server/models/`. Verify `/api/health` reports full mode.
      *(Code ready on `claude/enhance-ml-model-oWEyI` — actual T4 run is
      operator action.)*
- [x] Expand iNat scrape ~+30 % per existing class; rerun split + train.
      *(`TARGET_PER_TAXON` 600 → 800; new fallback chains for the
      bacterial / mosaic taxa.)*
- [x] Pick 2–4 new diseases (candidates: bacterial leaf spot, edema,
      leaf scorch, root-rot foliar symptoms); collect images.
      *(Picked **bacterial_leaf_spot** + **mosaic_virus** — strongest
      PV+iNat coverage for indoor plants. PV folders +
      `INAT_TAXA` updated.)*
- [x] Severity scoring v0: heuristic from detector bbox area + classifier
      margin → `severity: low | medium | high` in `/api/analyze`.
      *(`pipeline.compute_severity` + new `severity` field in
      `/api/analyze` response.)*

### P1 · Android
- [x] Real-time leaf framing in `CameraFragment`: `ImageAnalysis` every
      ~5th frame at 160 px, HSV ratio + centroid → red/amber/green frame.
      *(`LeafFrameAnalyzer` runs on a single-thread executor with
      `STRATEGY_KEEP_ONLY_LATEST`; YUV→RGB conversion is in-place,
      sampled at the longest-side / 160 step. Quality drives the
      `scan_frame` stroke colour and the on-screen hint.)*
- [x] Auto-capture when frame is green-stable ≥ 1.5 s and Laplacian
      variance is good. *(Hold time `AUTO_CAPTURE_HOLD_MS = 1500`; the
      sharpness gate uses the green-channel Laplacian variance from the
      same downsampled grid, threshold `MIN_SHARPNESS = 120`. Auto
      captures bypass the green-content dialog because both signals are
      already validated.)*
- [x] Onboarding redesign: 3 Lottie pages (scan, diagnose, care).
      *(Renamed `onboarding_treat.json` → `onboarding_care.json`, new
      bilingual copy emphasising the auto-capture + bilingual care plan
      flow.)*
- [x] Room migration v2→v3: add `Plant(id, name, species, nickname,
      iconUri, createdAt)` and nullable `plantId` on `ScanEntity`. No UI
      yet. *(New `PlantEntity` + `PlantDao`; `ScanEntity.plantId`
      foreign-keyed to plants with `ON DELETE SET NULL`. Legacy v1→v2
      migration dropped — never shipped to production.)*

---

## Week 2 — My Garden + smart APIs (May 7 – May 13)

### P2 · Server / ML
- [x] `POST /api/identify` — PlantNet API wrapper, server-side LRU cache.
      *(`server/ai_services.identify_species` + `/api/identify` endpoint;
      SHA-256-keyed LRU; graceful `available: false` on no key.)*
- [x] `POST /api/chat` — AI Plant Doctor via Anthropic SDK
      (`claude-haiku-4-5`), prompt caching for the disease/species
      system context, per-session conversation, per-IP rate limit.
      *(`server/ai_services.chat_reply` + `/api/chat` endpoint;
      `cache_control: ephemeral` on the long system prompt; per-session
      turn + token caps; abuse logging in `main.py`.)*
- [x] Test-Time Augmentation: 5-crop + h-flip on classifier; weighted
      ensemble of B0 + V2-S checkpoints. Target +2–3 % top-1.
      *(B0 trains primary; optional V2-S peer trains when
      `TRAIN_ENSEMBLE_PEER=True`; `classifier.py` blends both at
      inference; demo cell does 5-crop+hflip TTA.)*
- [x] Recalibrate temperature on the expanded val set.
      *(Notebook cell 12 sweeps `T ∈ [0.3, 5.0]`; result persisted to
      `temperature.json`.)*

### P1 · Android
- [x] **My Garden** screen: list, add/edit plant, plant-detail with
      timeline of scans.
- [x] Plant journal: free-text notes, photo log, watering log per plant.
- [x] "Add to garden" CTA on result screen → assigns scan to a plant.
- [x] Empty states with custom illustrations (no plants, no scans, no
      reminders).
- [x] Remove runtime server URL picker from Profile; API host is now
      provided only at build time through `API_BASE_URL`.
- [x] Remove hard square scan-frame outline from Camera preview.

---

## Week 3 — AI Doctor, reminders, light meter (May 14 – May 20)

### P2 · Server / ML
- [x] `POST /api/care-plan` — given last diagnosis + species, return
      watering / light / fertilising schedule (rules + LLM polish).
      *(`server/ai_services.generate_care_plan` + `/api/care-plan`; rules
      table for offline; optional Claude polish when key is set.)*
- [x] Disease progression: compare new scan to prior scans on same
      `plantId`, return `delta` and `trend`.
      *(`server/ai_services.compare_progression` + `/api/progression`
      endpoint; pure function, deterministic.)*
- [x] Ship final PlantVision 1.1 model checkpoint with the new disease
      classes from Week 1 research.
      *(Notebook now produces PlantVision 1.1 artefacts — 10 classes,
      ensemble peer, MODEL_CARD; awaits Colab run.)*

### P1 · Android
- [ ] WorkManager + FCM-free local notifications for per-plant reminders;
      editable schedule UI.
- [ ] AI Plant Doctor chat: bottom-sheet conversation, Markdown rendering,
      suggested follow-up questions, persisted per plant.
- [ ] Light meter: ambient-light sensor + camera luminance fallback,
      lux estimation, recommendation vs species ideal.
- [ ] Voice-over diagnosis (TTS read-aloud toggle on result screen).

---

## Week 4 — Differentiators, polish, offline (May 21 – May 30)

### P2 · Server / ML
- [ ] Export TFLite int8 for detector and classifier; bundle into APK
      assets (≤ 25 MB combined).  *(intentionally deferred — out of
      scope for the current ML pass per stakeholder direction.)*
- [x] OOD calibration sweep on real-world photos; final benchmark report
      + model card in `server/models/MODEL_CARD.md`.
      *(Notebook cell 14 sweeps `NAP_CLASSIFIER_MSP_FLOOR`; suggested
      value persisted to `classes.json["ood_calibration"]`. Static
      MODEL_CARD shipped at `server/models/MODEL_CARD.md`; final cell
      regenerates it from real metrics each run.)*
- [x] Server hardening: per-session AI Doctor budget cap, abuse logging.
      *(`CHAT_SESSION_TURN_CAP` + `CHAT_SESSION_TOKEN_CAP` in
      `ai_services.py`; cap-rejection logged at WARNING with IP +
      session ID in `main.py`; counters surfaced in `/api/metrics`.)*

### P1 · Android
- [ ] On-device inference fallback (TFLite runtime) when network fails or
      airplane mode is on.
- [ ] **Disease progression view**: severity-over-time chart per plant,
      before/after image side-by-side.
- [ ] **Time-lapse mode**: schedule daily fixed-angle reminder, stitch
      frames into a short MP4 via MediaCodec.
- [ ] **Compare-with-healthy** mode on result screen: split view of user
      photo + canonical healthy reference for the species.
- [ ] UX polish: Material You dynamic colours, glassmorphism cards on
      result screen, hero transitions camera → analysis → result.
- [ ] Final QA pass: dark-mode, edge-to-edge, RTL safety, golden-path
      smoke test online + offline. Cut signed demo APK.

---

## Out of scope (intentionally)

- Google Play public release, Privacy Policy, Data Safety form,
  monetisation (portfolio target).
- Outdoor-crop / agricultural workflows.
- Community / social features, expert consultations.
- Hilt migration, full ViewModel/StateFlow rewrite.
- Hardware sensor integrations beyond camera + ambient-light.
- Toxicity / pet-safety database (separate label space).

## Risks & mitigations

- **Colab session limits during training** → snapshot every epoch to
  Drive; resume support already in notebook.
- **PlantNet free tier (500/day)** → add server-side cache + fall back to
  "unknown species" gracefully.
- **LLM cost overrun** → strict per-session token cap, prompt caching,
  Haiku not Sonnet.
- **Scope creep on UX polish** → Week 4 checklist is the budget; defer
  anything not on it to a follow-up sprint.
