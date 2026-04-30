# PlantDiseases — Sprint Plan

**Window:** 2026-04-30 → 2026-05-30 (4 weeks)
**Team:** P1 = Android · P2 = Server / ML
**Goal:** lift the project from a working pipeline in demo mode to a
portfolio-grade app comparable to PictureThis on UX, feature breadth and
on-screen accuracy.

---

## Where we stand

- Pipeline code is complete; **models are not trained yet** — server runs
  in heuristic demo mode.
- Android client covers the core scan flow (camera, gallery, history,
  guide, profile, share, multi-bbox overlay, top-3, blur/green pre-checks,
  bilingual EN/RU, Material 3, onboarding).
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
| My Garden / per-plant log   | —        | yes          |
| Care reminders + push       | —        | yes          |
| AI Plant Doctor chat        | —        | yes (Claude Haiku) |
| Light meter                 | —        | yes          |
| Severity scoring            | —        | yes          |
| Disease progression chart   | —        | yes          |
| Real-time framing + auto-capture | —   | yes          |
| Offline on-device inference | —        | yes (TFLite) |
| Bilingual EN/RU             | yes      | yes (kept)   |

## Targets (Definition of Done)

- `pipeline_mode: full` in `/api/health`.
- YOLOv8n single-class leaf **mAP50 ≥ 0.65**.
- Classifier **top-1 ≥ 0.82** on held-out val with TTA + ensemble.
- 10–12 disease classes (8 today + 2–4 new).
- New endpoints: `/api/identify`, `/api/chat`, `/api/care-plan`.
- 6 net-new user-visible features (see weeks below) shipped on `main`.
- Demo APK installs and runs golden path end-to-end on a real device,
  online and offline.

---

## Week 1 — Make it real (Apr 30 – May 6)

### P2 · Server / ML
- [ ] Run `train_notebook.ipynb` on Colab T4; ship `detector.pt`,
      `classifier.pth`, `temperature.json`, `classes.json` to
      `server/models/`. Verify `/api/health` reports full mode.
- [ ] Expand iNat scrape ~+30 % per existing class; rerun split + train.
- [ ] Pick 2–4 new diseases (candidates: bacterial leaf spot, edema,
      leaf scorch, root-rot foliar symptoms); collect images.
- [ ] Severity scoring v0: heuristic from detector bbox area + classifier
      margin → `severity: low | medium | high` in `/api/analyze`.

### P1 · Android
- [ ] Real-time leaf framing in `CameraFragment`: `ImageAnalysis` every
      ~5th frame at 160 px, HSV ratio + centroid → red/amber/green frame.
- [ ] Auto-capture when frame is green-stable ≥ 1.5 s and Laplacian
      variance is good.
- [ ] Onboarding redesign: 3 Lottie pages (scan, diagnose, care).
- [ ] Room migration v2→v3: add `Plant(id, name, species, nickname,
      iconUri, createdAt)` and nullable `plantId` on `ScanEntity`. No UI
      yet.

---

## Week 2 — My Garden + smart APIs (May 7 – May 13)

### P2 · Server / ML
- [ ] `POST /api/identify` — PlantNet API wrapper, server-side LRU cache.
- [ ] `POST /api/chat` — AI Plant Doctor via Anthropic SDK
      (`claude-haiku-4-5`), prompt caching for the disease/species
      system context, per-session conversation, per-IP rate limit.
- [ ] Test-Time Augmentation: 5-crop + h-flip on classifier; weighted
      ensemble of B0 + V2-S checkpoints. Target +2–3 % top-1.
- [ ] Recalibrate temperature on the expanded val set.

### P1 · Android
- [ ] **My Garden** screen: list, add/edit plant, plant-detail with
      timeline of scans.
- [ ] Plant journal: free-text notes, photo log, watering log per plant.
- [ ] "Add to garden" CTA on result screen → assigns scan to a plant.
- [ ] Empty states with custom illustrations (no plants, no scans, no
      reminders).

---

## Week 3 — AI Doctor, reminders, light meter (May 14 – May 20)

### P2 · Server / ML
- [ ] `POST /api/care-plan` — given last diagnosis + species, return
      watering / light / fertilising schedule (rules + LLM polish).
- [ ] Disease progression: compare new scan to prior scans on same
      `plantId`, return `delta` and `trend`.
- [ ] Ship final v5 model checkpoint with the new disease classes
      from Week 1 research.

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
      assets (≤ 25 MB combined).
- [ ] OOD calibration sweep on real-world photos; final benchmark report
      + model card in `server/models/MODEL_CARD.md`.
- [ ] Server hardening: per-session AI Doctor budget cap, abuse logging.

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
