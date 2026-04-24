# PlantDiseases

AI-powered **houseplant** disease detection app for Android, backed by
the PlantScope v3 two-stage Python ML server.

---

## Architecture Overview

```
┌──────────────────────┐         ┌──────────────────────────────────┐
│   Android App        │  HTTP   │   Python Server (FastAPI)        │
│   (Kotlin)           │ ──────► │                                  │
│                      │         │   ┌──────────────────────────┐   │
│  • CameraX capture   │         │   │ Stage 1: Detector        │   │
│  • Room local DB     │         │   │ YOLOv8n                  │   │
│  • Retrofit client   │  JSON   │   │ leaf / diseased_leaf     │   │
│  • Material 3 UI     │ ◄────── │   │ bounding boxes           │   │
│  • RU/EN localization│         │   └──────────┬───────────────┘   │
│  • Profile & stats   │         │              │                   │
│                      │         │   ┌──────────▼───────────────┐   │
│                      │         │   │ Stage 2: Classifier      │   │
│                      │         │   │ EfficientNetV2-S         │   │
│                      │         │   │ 9 houseplant classes     │   │
│                      │         │   │ + not_a_plant rejection  │   │
│                      │         │   └──────────────────────────┘   │
└──────────────────────┘         └──────────────────────────────────┘
```

### Two-Stage Detection Pipeline

```
Full photo (any composition — hand, wall, background are OK)
    │
    ▼
┌──────────────────────────┐
│  Stage 1: Leaf Detector   │  YOLOv8n (~3.2M params)
│  "Where are the leaves    │  Outputs bboxes {leaf, diseased_leaf}
│   and which look sick?"   │  Filters noise: no box → not_a_plant
└──────────┬───────────────┘
           │
      ┌────┴─────────────────┐
      │ Any detections?       │─── no ──► "Not a plant / retake photo"
      └────┬─────────────────┘
           │ yes
           ▼
    Crop to the primary diseased bbox
    (or the best leaf if nothing is flagged)
           │
           ▼
┌──────────────────────────┐
│  Stage 2: Disease Classifier  │  EfficientNetV2-S (~21M params)
│  "Which disease is this?"     │  9 houseplant classes + not_a_plant
│                               │  Operates only on the ROI
└──────────┬───────────────────┘
           │
           ▼
    Disease name + confidence
    + treatment / prevention advice (EN/RU)
```

**Why two stages?**
- **Works on real photos**, not only lab shots. YOLOv8 ignores fingers, walls,
  and furniture because they never score above the detection threshold.
- **Classifier focuses on the lesion.** Running EfficientNetV2-S on a tight
  crop beats running it on the whole room.
- **Explainable by default** — Stage 1 returns every bounding box, the
  Android app draws them all, and highlights the one Stage 2 used.
- **Manageable size** — detector ~10 MB, classifier ~82 MB, both trainable
  in ≈ 90 min end-to-end on a free Colab T4.

---

## Quick Start

### Start the server

**Linux / macOS:**
```bash
cd server/
chmod +x start.sh
./start.sh
```

**Windows:**
```cmd
cd server\
start.bat
```

The script will automatically:
1. Check Python 3.9+ is installed
2. Create a virtual environment
3. Install CPU-only PyTorch (lightweight ~200 MB)
4. Install all dependencies
5. Start the server on `http://localhost:8000`

Without trained models the server runs in **demo mode** (colour-based heuristics).

### Train models

The recommended workflow is **Google Colab** — `server/train_notebook.ipynb`
is a self-contained T4-friendly pipeline that:

1. Clones PlantDoc (object-detection + classification subsets).
2. Downloads COCO val2017 for `not_a_plant` negatives.
3. Optionally pulls a houseplant species set from Kaggle.
4. Trains **YOLOv8n** (Stage 1) ≈ 40-60 min.
5. Trains **EfficientNetV2-S** (Stage 2) ≈ 40-50 min, mixed precision.
6. Exports `detector.pt`, `classifier.pth`, `classes.json` for download.

CLI entry point (mirrors the notebook) is still available:

```bash
python train.py                 # both stages
python train.py --detector      # YOLOv8n only
python train.py --classifier    # EfficientNetV2-S only
```

Drop the three output files into `server/models/` and restart the server;
the pipeline auto-detects real vs demo mode.

### Build the Android app

1. Open `android/` in Android Studio (Hedgehog 2023.1+).
2. Sync Gradle → Run on device/emulator.
3. Set the server URL at runtime in **Profile → Server URL** — no rebuild required.
   The shipped default (`http://10.0.2.2:8000/`) points at the Android emulator
   loopback; on a physical device, type your PC's LAN IP, e.g. `http://192.168.1.42:8000/`.

The compile-time default lives in `app/build.gradle.kts` as
`buildConfigField("String", "API_BASE_URL", ...)` and is used the first
time the app launches (and after "Reset to default" in Profile).

> **Cleartext HTTP.** `network_security_config.xml` permits HTTP only for
> `10.0.2.2`, `localhost`, and `127.0.0.1`. To test against a PC on the
> same WiFi, add its exact IP as another `<domain>` entry (Android does
> not accept CIDR ranges inside `<domain>`). Use HTTPS in production.

---

## Android App

### Features
- **Camera scanner** — point at a plant leaf and capture with a rule-of-thirds grid, tap-to-focus, and torch toggle.
- **Image gallery picker** — analyse photos already on the device.
- **Scan history** — all results saved locally in Room with migrations (no destructive fallbacks).
- **Plant care guide** — 22 bilingual articles (diseases, pests, watering, lighting, care) with fuzzy search and recent-query chips.
- **Profile & statistics** — total scans, healthy/diseased split, most common disease, storage breakdown.
- **Live bbox overlay** — every YOLOv8 detection is drawn on top of the original photo through `centerCrop`, with a cached radial gradient and a pulsing fill animator that cleans itself up in `onDetachedFromWindow`. The primary box (the one Stage 2 classified) gets a thicker stroke.
- **Top-3 alternative diagnoses** — explainable AI with confidence bars and Shannon-entropy uncertainty.
- **Share results with image** — export diagnosis text + photo with the heatmap overlay via `FileProvider`.
- **Blur pre-check** — bulk Laplacian variance warns before uploading blurry photos.
- **Green-content pre-check** — rejects frames that clearly aren't a plant before burning a server call.
- **Low-confidence warning** — calm inline notice with a "Retry Analysis" action when entropy is high.
- **Smart retry policy** — `RetryInterceptor` backs off on transient `IOException`, HTTP 429, and HTTP 503, and honours the server's `Retry-After` seconds header.
- **Skeleton / shimmer loaders** — polished loading states while Room queries resolve.
- **Pinch-to-zoom** — examine heatmap details on the result photo.
- **Edge-to-edge display** — transparent status bar with content under system bars and predictive back enabled.
- **Onboarding with animated dots** — smooth `ValueAnimator`-driven indicator expand/contract, not a jarring snap.
- **Bilingual** — Russian / English with one-tap switching and a volatile-cached lookup so every `onBind` is a single memory read.
- **Hardened backups** — `allowBackup=false` plus `data_extraction_rules` that exclude files, prefs, and databases from cloud/device-transfer archives.
- **Configurable server URL** — change the backend endpoint from Profile → Server URL without rebuilding.
- **Release signing ready** — a `release` signing config in `build.gradle.kts` picks up `RELEASE_STORE_FILE` / `RELEASE_KEY_ALIAS` Gradle properties, with ProGuard rules for Retrofit/Room/Lottie/Gson. A production-signed smoke test is still pending (see `ROADMAP.md`).
- **Min SDK 26** (Android 8.0+).

### Tech Stack
| Component | Library |
|-----------|---------|
| Camera | CameraX 1.3 |
| Database | Room 2.6 |
| Network | Retrofit 2.9 + OkHttp 4 |
| Images | Glide 4.16 |
| Navigation | Jetpack Navigation |
| UI | Material Design 3 |
| Async | Kotlin Coroutines |

### Project Structure
```
app/src/main/
├── java/com/plantdiseases/app/
│   ├── PlantDiseasesApp.kt          # Application class
│   ├── MainActivity.kt              # Main activity + nav
│   ├── data/
│   │   ├── local/Database.kt        # Room DB + DAO
│   │   ├── remote/ApiService.kt     # Retrofit API
│   │   ├── model/Models.kt          # Data models
│   │   ├── repository/ScanRepository.kt
│   │   └── GuideDataProvider.kt     # Offline guide (22 items)
│   ├── ui/
│   │   ├── camera/                  # Camera scanner screen
│   │   ├── gallery/                 # Scan history grid
│   │   ├── guide/                   # Care articles + detail
│   │   ├── profile/                 # Statistics & settings
│   │   ├── analysis/                # Loading / analysing screen
│   │   └── result/                  # Disease diagnosis result
│   └── util/
│       ├── LocaleHelper.kt          # Language switching
│       ├── ServerConfig.kt          # Runtime server URL (SharedPreferences)
│       ├── ThemeHelper.kt           # Light/Dark/System theme
│       └── ImageUtils.kt            # Image processing
└── res/
    ├── layout/                      # XML layouts
    ├── values/                      # English strings, colours, themes
    ├── values-ru/                   # Russian strings
    ├── drawable/                    # Icons & shapes
    ├── navigation/                  # Nav graph
    └── menu/                        # Bottom nav & toolbar menus
```

---

## Python Server

### Pipeline Models

| Stage | Model | Params | Task |
|-------|-------|--------|------|
| 1 — Detector | YOLOv8n (Ultralytics) | ~3.2 M | Bounding boxes for `leaf` / `diseased_leaf` |
| 2 — Classifier | EfficientNetV2-S (ImageNet-V2-S) | ~21 M | 9-class houseplant disease head + rejection class |

Stage 1 is an off-the-shelf YOLOv8n fine-tuned on PlantDoc (remapped to two
classes). Stage 2 is two-phase transfer learning: freeze the backbone to warm
up the head, then unfreeze the top blocks with a smaller LR.

### Server Files
```
server/
├── start.sh               # Linux / macOS startup script
├── start.bat              # Windows startup script
├── main.py                # FastAPI server (endpoints)
├── pipeline.py            # Two-stage pipeline orchestrator
├── detector.py            # Stage 1: YOLOv8n + HSV demo fallback
├── classifier.py          # Stage 2: EfficientNetV2-S + rejection class
├── train.py               # Unified training script (YOLO + EfficientNet)
├── train_notebook.ipynb   # Colab-optimised training workflow
├── diseases_data.py       # Bilingual houseplant disease database
├── requirements.txt       # Server dependencies (PyTorch + Ultralytics)
├── requirements-train.txt # Extra training dependencies
└── models/
    ├── detector.pt        # Trained Stage 1 (YOLOv8n)
    ├── classifier.pth     # Trained Stage 2 (EfficientNetV2-S)
    └── classes.json       # Canonical class list + model version
```

### Startup Options

```bash
# Start server (default port 8000)
./start.sh
start.bat

# Custom port
./start.sh --port 9000
start.bat --port 9000

# Train models then start server
./start.sh --train
start.bat --train
```

### Training Your Own Models

The easiest path is Google Colab — open `server/train_notebook.ipynb`,
switch to a T4 GPU, and run all cells top-to-bottom. If you prefer a CLI:

1. **Grab the datasets** (the Colab notebook does this automatically):
   - **PlantDoc Object Detection** — <https://github.com/pratikkayal/PlantDoc-Object-Detection-Dataset>
   - **PlantDoc Classification** — <https://github.com/pratikkayal/PlantDoc-Dataset>
   - **COCO val2017** — <https://images.cocodataset.org/zips/val2017.zip>
     (provides `not_a_plant` negatives — fingers, walls, furniture, ...).
   - *(optional)* **Houseplant Species** — Kaggle
     `kacpergregorowicz/house-plant-species` — strengthens the `healthy`
     class with real indoor photos.

2. **Organise data** (mirrors the notebook output):
   ```
   server/data/
   ├── detector/                    # YOLO format
   │   ├── data.yaml                # nc=2, names=[leaf, diseased_leaf]
   │   ├── images/{train,val}/
   │   └── labels/{train,val}/
   └── classifier/                  # ImageFolder (alphabetical classes)
       ├── train/{blight,healthy,leaf_mold,leaf_spot,mosaic_virus,
       │         not_a_plant,powdery_mildew,rust,spider_mites}/
       └── val/(same structure)/
   ```

3. **Train:**
   ```bash
   python train.py                  # YOLOv8n + EfficientNetV2-S
   python train.py --detector       # Stage 1 only
   python train.py --classifier     # Stage 2 only
   ```

4. **Restart the server** — weights load automatically.

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | Liveness probe + pipeline status + uptime |
| `GET` | `/api/version` | Server version, model digest, class count |
| `GET` | `/api/classes` | 9 classes with bilingual names |
| `GET` | `/api/metrics` | Request / error / latency counters for dashboards |
| `POST` | `/api/analyze` | Upload image → get diagnosis |

#### POST /api/analyze

**Request:** `multipart/form-data` with `image` field (JPEG, PNG, WebP, BMP; max 10 MB, max 4096 px per dimension)

**Server protections:**
- Magic-byte validation of the uploaded bytes (ignores the client `Content-Type`).
- Pillow decompression-bomb guard (`Image.MAX_IMAGE_PIXELS`) + explicit dimension cap.
- Inference timeout (30 s via `asyncio.wait_for`), with a `ThreadPoolExecutor` sized by `INFER_WORKERS`.
- Per-IP rate limiting (1 req/s by default, configurable via `RATE_LIMIT_RPS`) with an accurate `Retry-After` header on HTTP 429.
- Proxy-aware client IP (`X-Forwarded-For` is only trusted when the peer is listed in `TRUSTED_PROXIES`).
- Security response headers (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, `Cache-Control`).

**Response:**
```json
{
  "disease_name": "Powdery Mildew",
  "disease_name_ru": "Мучнистая роса",
  "confidence": 0.87,
  "description": "...",
  "description_ru": "...",
  "treatment": ["Step 1", "Step 2"],
  "treatment_ru": ["Шаг 1", "Шаг 2"],
  "prevention": ["Tip 1", "Tip 2"],
  "prevention_ru": ["Совет 1", "Совет 2"],
  "is_healthy": false,
  "detection": {
    "is_diseased": true,
    "detector_confidence": 0.93,
    "regions": [
      {"x": 120, "y": 80, "width": 200, "height": 170,
       "class": "diseased_leaf", "confidence": 0.93},
      {"x": 340, "y": 210, "width": 160, "height": 150,
       "class": "leaf", "confidence": 0.71}
    ],
    "primary_region": {"x": 120, "y": 80, "width": 200, "height": 170,
                       "class": "diseased_leaf", "confidence": 0.93}
  },
  "all_probs": {
    "healthy": 0.05,
    "powdery_mildew": 0.87,
    "leaf_mold": 0.04,
    "...": "..."
  },
  "pipeline_mode": "full",
  "elapsed_ms": 145.3
}
```

---

## Disease Classes

Stage 2 ships with 9 alphabetically-ordered houseplant-focused classes
(canonical order is stored in `server/models/classes.json`):

| # | Class | EN Name | RU Name |
|---|-------|---------|---------|
| 0 | blight | Blight | Фитофтороз |
| 1 | healthy | Healthy Plant | Здоровое растение |
| 2 | leaf_mold | Leaf Mold | Листовая плесень |
| 3 | leaf_spot | Leaf Spot (Bacterial / Fungal) | Пятнистость листьев |
| 4 | mosaic_virus | Mosaic Virus | Мозаичный вирус |
| 5 | not_a_plant | Not a Plant | Не растение |
| 6 | powdery_mildew | Powdery Mildew | Мучнистая роса |
| 7 | rust | Rust | Ржавчина |
| 8 | spider_mites | Spider Mites | Паутинный клещ |

Stage 1 detects two object classes — `leaf` (healthy) and `diseased_leaf`.
The `not_a_plant` Stage 2 class is a rejection bucket trained on COCO
imagery so fingers, walls, furniture, and fabrics don't trigger a false
disease call.

---

## What sets this apart

- **Real two-stage pipeline.** YOLOv8n locates every leaf with a bbox;
  EfficientNetV2-S classifies only the primary diseased region. The
  classifier never has to first figure out "what is a leaf".
- **Robust to noisy photos.** Fingers, walls, and furniture are silently
  dropped by Stage 1 thresholding; images with no detection fall back to
  `not_a_plant` so the user gets a helpful "retake the photo" response.
- **Houseplant-focused training data.** PlantDoc (real-world photos) +
  COCO negatives + houseplant species — not the lab-conditioned
  PlantVillage Tomato dataset that powered v1/v2.
- **Explainable by default.** Every bbox is transported to the app, and
  the primary one is highlighted — top-3 probabilities, a
  Shannon-entropy uncertainty score, and per-class probabilities ride
  along.
- **Production-hardened server.** Magic-byte validation,
  decompression-bomb guard, per-IP rate limiter with `Retry-After`,
  proxy-aware client IP via `TRUSTED_PROXIES`, inference timeout, security
  response headers, non-root Docker user, container `HEALTHCHECK`.
- **Resilient client transport.** `RetryInterceptor` retries on
  `IOException`, HTTP 429 and HTTP 503 with capped exponential backoff,
  and honours `Retry-After` seconds.
- **Bilingual end-to-end.** Russian and English share a single disease
  database on the server and UI strings on the client, switchable from
  Profile without restarting the app.
- **Observability.** `/api/metrics` exposes request / error counters and
  analyse-latency histograms — drop-in for Prometheus scraping.
- **Colab-first training.** `train_notebook.ipynb` downloads datasets,
  converts them, trains both stages end-to-end on a free T4, and packages
  the exported weights.

---

## License

[Unlicense](https://unlicense.org) — public domain, free for any use.
