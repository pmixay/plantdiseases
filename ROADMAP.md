# PlantDiseases — Roadmap

A short, honest record of what is done, what is still worth doing, and what we
deliberately skip. The project is an Android client plus a Python FastAPI
pipeline with two stages: a MobileNetV3-Small detector with Grad-CAM, and an
EfficientNet-B0 classifier over 15 disease classes.

---

## Done in this audit pass

### Server hardening and correctness
- Grad-CAM is now thread-safe. Activations and gradients live in
  `threading.local`, and the backward pass is serialized by a lock so
  concurrent requests never corrupt each other's CAM.
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
- `pipeline.py` clamps crop bounds; the classifier severity (fraction of
  heatmap pixels above threshold) is part of the region payload.
- `train.py` rewrite:
  - Uses `SCRIPT_DIR = Path(__file__).resolve().parent` so training does not
    depend on the caller's CWD.
  - Supports `PLANTD_DATA_DIR` override.
  - Unfreeze by named children (`_unfreeze_top_features(model, top_blocks=3)`),
    not by the brittle `params[-40:]` / `params[-60:]` indices.
  - Saves the best checkpoint each time validation accuracy improves.
- Startup scripts (`start.sh`, `start.bat`) accept `--proxy-headers` and no
  longer use box-drawing art.
- Pillow 10.3.0, python-multipart 0.0.9 in `requirements.txt`.

### Android correctness and polish
- `ImageUtils.prepareImageForUpload` downsample loop bug fixed (`&&` → `||`);
  non-square images are now actually downsampled.
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
The pipeline runs in heuristic demo mode without `models/detector.pth` and
`models/classifier.pth`. Binary detector at ≥ 92 % and 15-class classifier at
≥ 78 % is realistic on a PlantVillage-like subset with a
`WeightedRandomSampler`, because PlantVillage is heavily imbalanced (many
`tomato_healthy`, few `root_rot`). The training script already supports
per-epoch checkpointing, so a Colab T4 run of a couple hours is enough for a
defensible accuracy story. The inference path already reports Shannon entropy
and top-3 alternatives, which means we can show calibration and failure modes
honestly.

### 2. Configurable server URL
`API_BASE_URL` still lives in `build.gradle.kts`. A Profile field backed by
DataStore would let demo hardware point at any server without a rebuild; the
`RetryInterceptor` and `Retry-After` handling are already in place for a
"Test connection" button.

### 3. Plant tracker (My Garden)
One Room entity (`Plant(id, name, species, createdAt, icon)`) and a nullable
`plantId` on `ScanEntity` unlocks a per-plant timeline. This is the feature
that makes users come back. All competitors gate it behind a paywall.

### 4. Care reminders
WorkManager + notifications, keyed off the new `plantId`. Default intervals
come from `DISEASES_DATABASE` based on the last diagnosis (wetter schedule
for fungal, drier for root rot, and so on).

### 5. On-device offline inference (TFLite)
Convert EfficientNet-B0 to TFLite with float16 quantization. This is the one
technically heavy feature that genuinely differentiates the app ("works on a
plane"). Keep the server path as the source of truth, fall back to on-device
when offline. Budget several days.

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
| Server runs in Docker with healthcheck and non-root user | done |
| Rate limiter, magic-byte validation, security headers | done |
| Grad-CAM thread-safe under concurrency | done |
| Retry with `Retry-After` on the client | done |
| Green gradient palette, unified light + dark | done |
| Temp-file cleanup on cancellation | done |
| `allowBackup=false`, data-extraction rules | done |
| Predictive back enabled | done |
| Detector + classifier weights trained and shipped | pending |
| Configurable server URL from the app | pending |
| Release APK signed, installed, smoke-tested | pending |
| My Garden persistent tracker | pending |
| Care reminders | pending |
| Offline TFLite inference | stretch |
