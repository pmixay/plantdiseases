# PlantDiseases — Roadmap

A short, honest record of what is done, what is still worth doing, and what we
deliberately skip. The project is an Android client plus a Python FastAPI
pipeline with two stages — a **YOLOv8n leaf detector** and an
**EfficientNetV2-S houseplant disease classifier** (9 classes including a
`not_a_plant` rejection bucket). The training data is PlantDoc + COCO
negatives + optional houseplant species — explicitly indoor-plant focused.

---

## Done in this audit pass

### Major ML refactor (v3.0)
- Replaced the binary MobileNetV3-Small "healthy / diseased" head with a
  **YOLOv8n object detector** that returns one or more bounding boxes per
  image, labelled `leaf` or `diseased_leaf`. The classifier now receives a
  real crop, not a Grad-CAM rectangle.
- Replaced the EfficientNet-B0 9-class tomato classifier with
  **EfficientNetV2-S** (~21 M params) retrained on a houseplant-focused
  class set: `blight, healthy, leaf_mold, leaf_spot, mosaic_virus,
  not_a_plant, powdery_mildew, rust, spider_mites`.
- Added the `not_a_plant` rejection class so noisy frames (fingers, walls,
  furniture, fabric) land in a dedicated "retake photo" branch instead of
  being predicted as a random disease.
- Switched the default dataset to PlantDoc + COCO negatives (+ optional
  Kaggle Houseplant Species). PlantVillage Tomato is no longer used: lab
  photos don't generalise to indoor plants.
- Rewrote `train_notebook.ipynb` end-to-end for free-tier Colab T4 (auto
  dataset download, YOLO conversion, both stages trained in ~90 min total).
- Server API: `detection` is now `{is_diseased, detector_confidence,
  regions: [...], primary_region: {...}}` — multi-bbox aware. Android
  `HeatmapOverlayView` renders every box and highlights the primary one.
- Android Room schema bumped to v4 with a migration that folds the legacy
  single-region columns into a JSON `regions` array + `primary_region_index`.

### Server hardening and correctness
- Client IP is derived through `_client_ip()`; `X-Forwarded-For` is honoured
  only when the direct peer is in the `TRUSTED_PROXIES` allow-list.
- Rate limiter emits an accurate `Retry-After` header on HTTP 429.
- Upload bytes are validated by magic-byte sniff (JPEG, PNG, WebP, BMP).
  The declared `Content-Type` is not trusted.
- Pillow decompression-bomb guard. `Image.MAX_IMAGE_PIXELS` is set explicitly,
  and `DecompressionBombError` is handled with a clean 413 response.
- Security response headers everywhere: `X-Content-Type-Options`,
  `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, `Cache-Control`.
- New endpoints: `/api/version`, `/api/classes`, `/api/metrics` (counters for
  requests, errors, analyse-latency histogram).
- Docker image runs as a non-root user (uid 10001), ships with
  `HEALTHCHECK`, and `docker-compose.yml` no longer defaults `CORS_ORIGINS` to
  `*`.
- `uvicorn` is started with `--proxy-headers --forwarded-allow-ips` so the
  server trusts only the proxies we configure.
- `pipeline.py` clamps crop bounds and picks the highest-confidence
  diseased bbox as the Stage 2 crop.
- `train.py` rewrite:
  - Drives both stages (YOLOv8n via `ultralytics`, EfficientNetV2-S via
    PyTorch) from one CLI.
  - Two-phase transfer learning for the classifier (freeze backbone →
    unfreeze `features.5-7`), AMP (mixed precision), class-balanced
    sampler for imbalanced data.
  - Uses `SCRIPT_DIR` / `PLANTD_DATA_DIR` so training is CWD-independent.
- Startup scripts (`start.sh`, `start.bat`) accept `--proxy-headers` and no
  longer use box-drawing art.
- Pillow 10.3.0, python-multipart 0.0.9 in `requirements.txt`.

### Android correctness and polish
- `ImageUtils.prepareImageForUpload` downsample loop bug fixed (`&&` → `||`);
  non-square images are now actually downsampled.
- `ImageUtils.prepareImageForUpload` now surfaces a typed
  `ImageDecodeException` instead of NPE'ing on `BitmapFactory.decodeFile`
  returning `null`, and intermediate bitmaps are recycled inside a
  `try { ... } finally { }` so decode/encode failures can't leak memory.
  `AnalysisActivity` runs the whole prepare-and-upload chain on
  `Dispatchers.IO`, catches `ImageDecodeException` and `OutOfMemoryError`
  separately, and shows dedicated, localized error cards.
- `ResultActivity.createShareableImage` is now dispatched to
  `Dispatchers.IO` (decode + canvas draw + JPEG encode), falls back to a
  text-only share if compositing fails, and throws `ImageDecodeException`
  instead of NPE'ing when the source file is corrupt.
- `AnalysisActivity` wraps the upload in `try { ... } finally { delete() }` so
  the temp file is removed even when the user cancels.
- `HeatmapOverlayView` no longer allocates per frame. `RadialGradient` is
  cached and only rebuilt on size or region changes, paints are split
  (`fillPaint` / `dimPaint` / `strokePaint`), and the pulse `ValueAnimator` is
  cancelled in `onDetachedFromWindow`.
- `LocaleHelper.isRussian` caches the language read from `SharedPreferences`
  behind a `@Volatile` field, so each `onBindViewHolder` is a single memory
  read.
- `RetryInterceptor` now retries on transient `IOException` **and** HTTP 429 /
  503, with capped exponential backoff. It reads and obeys the server's
  `Retry-After` seconds header.
- `PlantApiClient` resolves the base URL through `ServerConfig` on every
  request. The Retrofit instance is rebuilt under a lock when the user
  changes the URL, so the OkHttp client (with its retry interceptor, logging,
  and timeouts) is reused and no in-flight request is interrupted.
- `network_security_config.xml` no longer contains a spurious
  `<domain>192.168.0.0/16</domain>` (Android does not interpret CIDR inside
  `<domain>`; the previous entry matched nothing). Cleartext is now scoped
  to `10.0.2.2`, `localhost`, and `127.0.0.1`, with a comment explaining how
  to add a specific LAN IP.
- `GuideFragment` replaced a leaky `Handler(mainLooper)` debounce with a
  cancellable coroutine `Job`.
- `GuideDataProvider` caches the full item list and groups by category once;
  `getByCategory()` is a map lookup.
- Onboarding dot indicator expands and contracts through a `ValueAnimator`
  (accelerate-decelerate interpolator) instead of snapping; the animator is
  cancelled in `onDestroy`.
- `⚠️` emoji in the low-confidence card replaced with a tinted vector
  drawable (`ic_warning`).
- The "Placeholder for symmetry" anonymous `View` in `fragment_camera.xml` is
  now a real `<Space>` with a comment that explains its purpose.
- `AndroidManifest.xml` hardened: `allowBackup=false`,
  `dataExtractionRules` excluding files / prefs / databases,
  `enableOnBackInvokedCallback=true` for predictive back.
- `Database.kt` no longer falls back to destructive migration.
- Unified green-gradient palette across light and dark themes, with a single
  set of `gradient_start` / `gradient_mid` / `gradient_end` tokens and 135°
  gradients for the toolbar, profile header, and button drawables.
- `fragment_profile.xml` no longer hardcodes `#B0FFFFFF`; it references the
  new `on_primary_secondary` color.

### Documentation
- README updated: new endpoint list, server-protection bullets, Android
  feature bullets that reflect what the code actually does, and a "What sets
  this apart" section.
- This ROADMAP rewrite.

---

## Strategic follow-ups

These are not shipped; they are the next things to pull in if there is time.

### 1. Train real models
The pipeline runs in heuristic demo mode without `models/detector.pt` and
`models/classifier.pth`. Use `server/train_notebook.ipynb` on a free Colab
T4 — the notebook pulls PlantDoc (detection + classification subsets),
COCO val2017 for negatives, and trains both stages in ~90 min. Realistic
targets: YOLOv8n mAP50 ≥ 0.60 for two-class leaf detection, EfficientNetV2-S
top-1 ≥ 0.78 with a class-balanced sampler. The classifier ships with a
`not_a_plant` class, so it can honestly reject bad frames instead of
guessing a disease.

### 2. Configurable server URL — **done**
`ServerConfig` persists a user-set base URL to `SharedPreferences` (with the
`BuildConfig.API_BASE_URL` as the default). `PlantApiClient` rebuilds its
Retrofit instance under a lock when the URL changes and reuses the same
OkHttp client (retry interceptor, logging, timeouts). The Profile screen
exposes a validated text field, a **Save** button that accepts bare
`host:port` (auto-prefixing `http://`), and a **Reset to default** action.
Server-health re-checks immediately after a change.

### 3. Plant tracker (My Garden)
One Room entity (`Plant(id, name, species, createdAt, icon)`) and a nullable
`plantId` on `ScanEntity` unlocks a per-plant timeline. This is the feature
that makes users come back. All competitors gate it behind a paywall.

### 4. Care reminders
WorkManager + notifications, keyed off the new `plantId`. Default intervals
come from `DISEASES_DATABASE` based on the last diagnosis (wetter schedule
for fungal, drier for root rot, and so on).

### 5. On-device offline inference (TFLite)
Convert YOLOv8n (Ultralytics built-in `export(format='tflite')`) and
EfficientNetV2-S (PyTorch → ONNX → TFLite with float16 quantization).
Keep the server path as the source of truth, fall back to on-device when
offline. Budget several days.

### 6. Real-time camera leaf framing
A lightweight `ImageAnalysis` use case running every ~5th frame at 160 px can
colour the scan frame red / amber / green based on HSV green ratio plus
centroid position. No OpenCV dependency required. Cheap demo-time wow.

### 7. Symptom-assisted diagnosis
When entropy is high, let the user tick symptoms ("white powder", "yellow
spots", "webs") and run a Bayesian update on the returned `all_probs`
using a hand-authored symptom × disease matrix. Makes the model look less
like a black box.

### 8. Crash reporting and release-APK verification
Firebase Crashlytics or Sentry. Build, sign, install, and run through the
golden path once — it has not been done since the palette and manifest
changes. R8 full mode is worth flipping on.

### 9. Room migration tests
Two migrations exist; neither has an instrumentation test using
`MigrationTestHelper`. One broken migration bricks every existing install.

### 10. CI
GitHub Actions: lint + `assembleDebug` on push, tag-triggered release APK
build and GitHub Release. `docker build` + push to GHCR for the server.

---

## Observability and metrics

The server already exports `/api/metrics` as counters plus latency buckets.
The next step is translating that to a Prometheus exposition format under a
separate content-type, and wiring a minimal Grafana dashboard. This is
optional for a defence and mandatory for any actual deployment.

---

## What we deliberately skip

- Expert consultation features — outside the scope of a student project.
- Toxicity / taxonomy databases — our labels are disease classes, not genera.
- Outdoor-crop workflows — Plantix territory; a different product.
- Full Hilt migration — the service-locator pattern in `PlantDiseasesApp` is
  adequate at this size and the refactor is not free.
- ViewModel / StateFlow rewrite across the UI — worth doing eventually, not
  worth doing during an audit pass.

---

## Defence checklist

| Item | Status |
|------|--------|
| Two-stage pipeline (YOLOv8n detector + EfficientNetV2-S classifier) | done |
| Houseplant-focused class set with `not_a_plant` rejection | done |
| Colab-ready training notebook (PlantDoc + COCO + optional Kaggle) | done |
| Server runs in Docker with healthcheck and non-root user | done |
| Rate limiter, magic-byte validation, security headers | done |
| Retry with `Retry-After` on the client | done |
| Green gradient palette, unified light + dark | done |
| Temp-file cleanup on cancellation | done |
| `allowBackup=false`, data-extraction rules | done |
| Predictive back enabled | done |
| Image pipeline off-main-thread, typed decode errors, OOM handled | done |
| Network-security-config scoped to real hostnames (no bogus CIDR) | done |
| Multi-bbox overlay in the Android result screen | done |
| Room schema v4 migration (region_* → regions JSON array) | done |
| Detector + classifier weights trained and shipped | pending |
| Configurable server URL from the app | done |
| Release APK signed, installed, smoke-tested | pending |
| My Garden persistent tracker | pending |
| Care reminders | pending |
| Offline TFLite inference | stretch |
