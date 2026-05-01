# PlantDiseases — Roadmap

Server-only inference is the product baseline: the Android client is a thin
camera + UX shell over a FastAPI two-stage pipeline. Items below are sized
against actual market gaps versus PictureThis, Plantix, NatureID and PlantIn
— not against an internal sprint calendar — and grouped by area + priority.

The list intentionally drops anything already shipped on `main`. Work is
ordered top-to-bottom inside each priority bucket; pick from the top.

---

## Server / ML

### P0 — required to ship
- **Train and publish PlantVision 1.1 weights.** Run the Colab notebook end
  to end on T4, push `detector.pt`, `classifier.pth`, `temperature.json`
  and an updated `classes.json` to `server/models/`. `/api/health` should
  report `pipeline_mode = full` in production. Demo mode stays as a local
  dev convenience only.
- **Calibrated accuracy targets.** Hold the validation bar at YOLOv8n
  mAP50 ≥ 0.65 (single-class leaf) and EfficientNet-B0 top-1 ≥ 0.82 with
  TTA + temperature scaling. Sweep `NAP_CLASSIFIER_MSP_FLOOR` against a
  real-world OOD set so the not-a-plant gate is honest rather than tuned
  for the lab.
- **Per-disease F1 floor.** The currently weakest classes
  (`bacterial_leaf_spot`, `late_blight`, `sunburn`) train on visual
  proxies from non-houseplant data and score F1 ≈ 0 on real shots. Either
  curate ≥ 250 in-domain images per class or drop the class from
  production and remove it from the response taxonomy. Half-trained
  classes are worse than no class.

### P1 — competitive must-haves
- **Wider houseplant disease coverage.** Add at least four classes that
  PictureThis / Plantix already detect and we don't: edema, leaf scorch,
  root-rot foliar symptoms, southern blight. Each one needs a curated
  multi-source dataset with SHA-256 dedup and EXIF strip.
- **Pest visual detection.** Promote the top three pest cards
  (spider mites, aphids, scale) from "hint card" to a small dedicated
  classifier head trained on macro-crop datasets. Even a binary
  "pest visible / not visible" flag is a measurable competitive win.
- **Severity time-series API.** `/api/progression` is point-in-time
  today. Accept a list of historical scans, return a smoothed
  trajectory + recovery / decline rate. The Android Plant timeline can
  then chart it without doing the math client-side.
- **Care-plan v2.** Replace the static `_BASE_PLANS` rule table with a
  species-aware plan that reads PlantNet `commonNames` and a curated
  light/water/temperature CSV (derived from RHS / Missouri Botanical).
  Today every species gets the same numbers, which is the obvious gap
  versus PictureThis.

### P2 — accuracy & cost work
- **Active learning loop.** When `/api/analyze` returns `low_confidence`
  or `nap_ood_gated`, opt-in users can flag the photo for the training
  pool through a `/api/feedback` endpoint. Stored anonymised in S3,
  weekly re-train.
- **Quantised ONNX in production.** Confirm `classifier_int8.onnx` is
  the loaded path (not the FP32 ONNX) and benchmark against a CPU VPS
  budget. Target end-to-end `/api/analyze` p50 ≤ 250 ms on a 2-vCPU
  box.
- **Smarter prompt cache for `/api/chat`.** The system prompt is cached
  on Anthropic's side already; add a per-disease few-shot block that
  rotates based on the diagnosis class so Claude sees real examples
  instead of just the taxonomy bullets.

---

## Android

### P0 — required to ship
- **Production demo APK.** Cut a signed release that runs the full
  online golden path on a real device. CI should produce the APK on
  every tag.
- **Server-error UX hardening.** When `/api/analyze` fails (timeout,
  rate limit, no network) the user sees a static error card today.
  Promote it to an actionable surface: explain the failure mode, link
  to a status indicator on Profile, offer a one-tap retry. Make
  absolutely clear there is no on-device fallback.

### P1 — competitive must-haves
- **Disease progression chart.** Plot severity-over-time per plant
  using `/api/progression`. Side-by-side before/after thumbnails on the
  same screen are what NatureID gets credit for.
- **Compare-with-healthy reference.** Result screen split view: user
  photo on one side, a canonical healthy reference for the species on
  the other. Bundled as a small image catalogue keyed by PlantNet
  `scientificName`.
- **Time-lapse mode.** Schedule a fixed-angle daily reminder, stitch
  the captures into a short MP4 via MediaCodec. This is a content
  hook the competitors don't do well.
- **Material You dynamic colours + hero transitions.** Camera →
  analysis → result is a three-screen flow with no shared-element
  continuity right now; that's the easy polish win on Android 12+.

### P2 — quality of life
- **Smart species ID flow.** Wire `/api/identify` (PlantNet) into the
  Add-to-Garden dialog: pre-fill `species` when the user accepts a
  suggestion. Today the dialog asks for a free-text species the user
  rarely fills.
- **Notification permission UX.** Android 13+ shows the runtime prompt
  too early. Defer it to the moment a reminder is first saved, with a
  rationale dialog.
- **Multi-photo plant log.** The journal accepts one photo per entry.
  Multi-photo galleries (RecyclerView inside the entry) are useful for
  "before / after watering" notes.

---

## Out of scope for this roadmap

- On-device inference (TFLite, ONNX, MediaPipe). The server is the
  single source of truth — quality matters more than offline parity, and
  a degraded local model misleads the user about plant health.
- Google Play public release, Privacy Policy, Data Safety form.
- Outdoor crops, agricultural workflows, hydroponics.
- Community / social features and expert consultations.
- Toxicity / pet-safety database (separate label space, separate
  legal review).
