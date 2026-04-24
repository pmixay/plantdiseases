# PlantDiseases — Roadmap

A short, honest record of what is still worth doing and what we
deliberately skip. The project is an Android client plus a Python
FastAPI pipeline: **YOLOv8n** locates leaves and flags diseased ones,
**EfficientNetV2-S** classifies the primary diseased crop into nine
houseplant-focused classes (including a `not_a_plant` rejection bucket).

---

## Follow-ups worth picking up

### 1. Train the real weights
The pipeline runs in heuristic demo mode without
`models/detector.pt` and `models/classifier.pth`. Use
`server/train_notebook.ipynb` on a free Colab T4: the notebook
downloads PlantDoc (detection + classification), COCO val2017 for
negatives, and trains both stages in roughly 90 min. Realistic
targets on that data mix are YOLOv8n mAP50 ≥ 0.60 for two-class leaf
detection and EfficientNetV2-S top-1 ≥ 0.78 with a class-balanced
sampler. The classifier owns the `not_a_plant` rejection class, so
it can honestly refuse to diagnose bad frames instead of guessing.

### 2. Plant tracker (My Garden)
One Room entity (`Plant(id, name, species, createdAt, icon)`) plus a
nullable `plantId` on `ScanEntity` unlocks a per-plant timeline. This
is the feature that brings users back and most competitors gate it
behind a paywall.

### 3. Care reminders
WorkManager + notifications keyed off the new `plantId`. Default
intervals come from `DISEASES_DATABASE` based on the last diagnosis —
wetter schedule for fungal, drier for root-rot patterns, and so on.

### 4. On-device offline inference (TFLite)
Ultralytics exposes `YOLO(...).export(format='tflite')` for Stage 1;
for Stage 2 we can go PyTorch → ONNX → TFLite with float16
quantisation. Keep the server path as the source of truth and fall
back to on-device when the network is unavailable. Budget several
days.

### 5. Real-time camera leaf framing
A lightweight `ImageAnalysis` use case running every ~5th frame at
160 px can colour the scan frame red / amber / green based on HSV
green ratio plus centroid position. No OpenCV dependency needed.
Cheap demo-time wow.

### 6. Symptom-assisted diagnosis
When the Shannon-entropy uncertainty is high, let the user tick
symptoms ("white powder", "yellow spots", "webs") and run a Bayesian
update on the returned `all_probs` using a hand-authored
symptom × disease matrix. Makes the model feel less like a black
box.

### 7. Crash reporting and release-APK verification
Firebase Crashlytics or Sentry. Build, sign, install, and run
through the golden path once a release is cut. R8 full mode is
worth flipping on.

### 8. CI
GitHub Actions: lint + `assembleDebug` on push, tag-triggered
release APK build and GitHub Release. `docker build` + push to GHCR
for the server.

### 9. Observability polish
`/api/metrics` already exports counters and a latency gauge, and
`/api/metrics/prometheus` speaks the Prometheus exposition format. A
minimal Grafana dashboard that graphs request rate, error rate, and
analyse-latency percentiles is the missing last step — optional for
a demo, mandatory for any real deployment.

---

## What we deliberately skip

- **Expert consultation features.** Outside the scope of this
  project.
- **Toxicity / taxonomy databases.** Our labels are disease classes,
  not plant species.
- **Outdoor-crop workflows.** Different product.
- **Full Hilt migration.** The service-locator pattern in
  `PlantDiseasesApp` is adequate at this size.
- **ViewModel / StateFlow rewrite across the UI.** Worth doing
  eventually, not worth doing under time pressure.
