# PlantDiseases — Roadmap

Server-only inference is the product baseline: the Android client is a thin
camera + UX shell over a FastAPI two-stage pipeline. The server refuses to
start without trained weights, and the Android app refuses to diagnose
without the server — quality matters more than offline parity. Items
below are sized against actual market gaps versus PictureThis, Plantix,
NatureID and PlantIn, grouped by area + priority, not by sprint
calendar. The list intentionally drops anything already shipped on
`main`.

---

## Server / ML

### P0 — required to ship

- **Train and publish PlantVision 1.1 weights.** Run the Colab
  notebook end-to-end on T4 / A100, push `detector.pt`,
  `classifier.pth` (or ONNX), `temperature.json` and an updated
  `classes.json` to `server/models/`. `/api/health` should report
  `pipeline_mode = full`. The server now refuses to start without
  these files, so this is a hard ship gate.
- **Calibrated accuracy targets.** Hold the validation bar at YOLOv8n
  mAP50 ≥ 0.65 (single-class leaf) and EfficientNet-B0 top-1 ≥ 0.82
  with TTA + temperature scaling. Sweep `NAP_CLASSIFIER_MSP_FLOOR`
  against a real-world OOD set so the not-a-plant gate is honest
  rather than tuned for the lab.
- **Per-disease F1 floor on every class.** A few classes
  (`bacterial_leaf_spot`, `late_blight`, `sunburn`) train on visual
  proxies from non-houseplant data and score F1 ≈ 0 on real shots.
  More broadly, the per-class real-world floor today is ~108 images,
  which is a sanity baseline rather than a training set. Either
  curate ≥ 250 in-domain images per class or drop the class from
  production. Half-trained classes are worse than no class.

### P1 — competitive must-haves

- **Wider houseplant disease coverage.** Add at least four classes
  PictureThis / Plantix already detect and we don't: edema, leaf
  scorch, root-rot foliar symptoms, southern blight. Each one needs a
  curated multi-source dataset with SHA-256 dedup and EXIF strip.
- **Pest visual detection.** Promote the top three pest cards
  (spider mites, aphids, scale) from "hint card" to a small dedicated
  classifier head trained on macro-crop datasets. Even a binary
  "pest visible / not visible" flag is a measurable competitive win.
- **YOLO-cropped training data parity.** The notebook already runs
  YOLO crops on the houseplant-photo source so `healthy` is
  close-cropped foliage, but PlantVillage and iNat samples for the
  disease classes go in uncropped. The structural mismatch
  (`healthy` = close-up, disease = wide frame) lets the classifier
  shortcut on framing instead of pathology. Run the same detector
  pre-pass on PV / iNat before materialising the classifier dataset
  — internal estimate +5–10 pp F1.
- **Multi-label classifier head.** A real photo can carry two
  problems (iron chlorosis + sunburn on the same leaf). The current
  argmax head forces one of them to disappear. Move to a sigmoid
  multi-label head with calibrated per-class thresholds; the API
  surface stays the same (`top_k` already supports it), only the
  training loss and `class_name` selection change.
- **Per-species feature embedding.** When the user has the species in
  Garden, send it to `/api/analyze` and concatenate a learned species
  embedding into the classifier's penultimate features. Drastically
  reduces FPs that look like a disease but are normal markings on the
  species (variegated *Calathea* ≠ chlorosis, red-edged *Anthurium*
  ≠ anthracnose). Falls back cleanly to "unknown species" when the
  client doesn't know.
- **Per-class temperature (vector scaling).** The current global T is
  pessimistic: it under-confidences confident classes and barely
  helps the proxy-trained ones. Replace with per-class vector
  scaling fitted on val NLL, persisted next to `temperature.json`.
- **Per-class `HEALTHY_DISEASE_FLOOR`.** Today a single global floor
  (0.18) decides when a healthy verdict carries an
  `uncertain_healthy` warning. Make it per-class so proxy-trained
  classes use a lower floor (we don't trust them as much) and clean
  classes use a higher one (we do).
- **Severity time-series API.** `/api/progression` is point-in-time
  today. Accept a list of historical scans and return a smoothed
  trajectory + recovery / decline rate so the Android Plant timeline
  can chart it without doing the math client-side.
- **Care-plan v2.** Replace the static `_BASE_PLANS` rule table with
  a species-aware plan that reads PlantNet `commonNames` and a
  curated light / water / temperature CSV (RHS, Missouri Botanical).
  Today every species gets the same numbers — the obvious gap versus
  PictureThis.
- **Active-learning feedback loop.** When `/api/analyze` returns
  `low_confidence`, `uncertain_healthy`, or `nap_ood_gated`, surface
  a one-tap "was this right? — yes / no / different disease" card on
  the Android result screen and POST the verdict to `/api/feedback`.
  Photos opt-in; stored anonymised; weekly re-train. Fixes the
  bootstrap problem on per-disease F1 without paying for paid
  annotation.

### P2 — accuracy, ops & cost work

- **Confidence-aware loss.** Replace `label_smoothing=0.05` with
  focal cross-entropy (γ ≈ 2) on the disease classes to boost hard
  examples; keep smoothed CE on `healthy` and `not_a_plant` where
  noise is mostly label noise rather than visual hardness.
- **Curriculum augmentation.** First ~5 epochs train on the clean
  pipeline, then progressively introduce the harder transforms
  (50 % degraded HP crops, mixup, CutMix). Dropping straight into
  hard aug from epoch 0 hurts early feature learning.
- **Tune Mixup / CutMix.** Current Mixup α and CutMix probability
  are conservative (`α 0.2`, `p 0.4`). The trained model is overconfident
  on the live eval — pushing both up (`α 0.4`, `p 0.5`) is the cheap
  softening lever before retraining from scratch.
- **Synthetic disease augmentation for the long tail.** SDXL + LoRA
  fine-tuned on ~30 real anthracnose / mosaic-virus crops to
  generate ~500 synthetic ones each. Medical imaging precedent for
  this is solid; needs a holdout-against-real eval to confirm the
  synthetic set transfers.
- **CLIP retrieval head for open-set fallback.** When the
  classifier's MSP is below the OOD floor *and* the detector is
  confident there's a leaf, fall back to a CLIP-based nearest-class
  retrieval and label the verdict as "looks like X, low certainty"
  rather than guessing or routing to NAP. Removes hallucinated
  certainty without sacrificing recall on rare presentations.
- **Per-request strictness.** `?strictness=high` lowers
  `DISEASE_MIN_CONFIDENCE` (0.65 → 0.55) for users who'd rather see a
  false alarm than miss something — useful for valuable plants. Default
  stays the current "low false-positive" preset.
- **Per-class telemetry.** Extend the existing class_counts metric
  with per-class confidence buckets. The class that most often
  scores below 0.5 in production is the next one to retrain — pulls
  prioritisation out of guesswork.
- **A/B classifier rollouts.** Add a `CLASSIFIER_VARIANT` env var
  that swaps between `models/classifier-A/` and `models/classifier-B/`
  trees, and route a configurable percentage of traffic through the
  candidate. Safe rollback the moment live metrics regress.
- **Daily OOD calibration job.** Cron pulls the previous day's
  low-confidence + low-MSP requests, recomputes
  `NAP_CLASSIFIER_MSP_FLOOR` against the new distribution, writes the
  result to `classes.json` and reloads. Keeps OOD honest as the
  user-photo distribution drifts.
- **Streaming batch collector.** A 50 ms-window collector merges
  concurrent `/api/analyze` calls into a single batched forward pass.
  At GPU prices the synchronous loop wastes most of the silicon;
  expected uplift on a single instance is 30–50 rps.
- **Quantised ONNX in production.** Confirm `classifier_int8.onnx` is
  the loaded path (not the FP32 ONNX) and benchmark against a CPU
  VPS budget. Target end-to-end `/api/analyze` p50 ≤ 250 ms on a
  2-vCPU box.
- **Smarter prompt cache for `/api/chat`.** The system prompt is
  cached on Anthropic's side already; add a per-disease few-shot
  block that rotates based on the diagnosis class so Claude sees
  real examples instead of just the taxonomy bullets.

---

## Android

### P0 — required to ship

- **Production demo APK.** Cut a signed release that runs the full
  online golden path on a real device. CI should produce the APK on
  every tag.
- **Server-error UX hardening.** When `/api/analyze` fails (timeout,
  rate limit, no network) the user sees a static error card today.
  Promote it to an actionable surface: explain the failure mode,
  link to a status indicator on Profile, offer a one-tap retry. Make
  absolutely clear there is no on-device fallback.

### P1 — competitive must-haves

- **Disease progression chart.** Plot severity-over-time per plant
  using `/api/progression`. Side-by-side before/after thumbnails on
  the same screen — that's what NatureID gets credit for.
- **Compare-with-healthy reference.** Result-screen split view:
  user photo on one side, a canonical healthy reference for the
  species on the other. Bundled as a small image catalogue keyed by
  PlantNet `scientificName`.
- **Time-lapse mode.** Schedule a fixed-angle daily reminder, stitch
  the captures into a short MP4 via MediaCodec. Content hook the
  competitors don't do well.
- **Material You dynamic colours + hero transitions.** Camera →
  analysis → result is a three-screen flow with no shared-element
  continuity right now; the easy polish win on Android 12+.
- **Feedback prompts on uncertain verdicts.** Show the
  yes / no / different-disease tap-card the active-learning loop
  needs. Submission is opt-in, no PII; UX has to survive the user
  hitting "no" without feeling broken.

### P2 — quality of life

- **Smart species ID flow.** Wire `/api/identify` (PlantNet) into
  the Add-to-Garden dialog and pre-fill `species` when the user
  accepts a suggestion. Today the dialog asks for a free-text
  species the user rarely fills.
- **Notification permission UX.** Android 13+ shows the runtime
  prompt too early. Defer it to the moment a reminder is first
  saved, with a rationale dialog.
- **Multi-photo plant log.** The journal accepts one photo per
  entry. Multi-photo galleries (RecyclerView inside the entry) are
  useful for "before / after watering" notes.
- **Per-plant species hint to `/api/analyze`.** Pass the saved
  species (when the user has one in Garden) so the server can apply
  the species-aware embedding (P1 ML item above). Falls back
  cleanly when no species is set.

---

## Out of scope for this roadmap

- On-device inference (TFLite, ONNX, MediaPipe). The server is the
  single source of truth — quality matters more than offline parity,
  and a degraded local model misleads the user about plant health.
- Demo / heuristic server fallback. Production fails fast without
  weights; "running in demo mode" is no longer a configuration.
- Google Play public release, Privacy Policy, Data Safety form.
- Outdoor crops, agricultural workflows, hydroponics.
- Community / social features and expert consultations.
- Toxicity / pet-safety database (separate label space, separate
  legal review).
