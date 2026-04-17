# PlantDiseases

AI-powered houseplant disease detection app for Android with a Python ML server.

---

## Architecture Overview

```
┌──────────────────────┐         ┌──────────────────────────────────┐
│   Android App        │  HTTP   │   Python Server (FastAPI)        │
│   (Kotlin)           │ ──────► │                                  │
│                      │         │   ┌──────────────────────────┐   │
│  • CameraX capture   │         │   │ Stage 1: Detector        │   │
│  • Room local DB     │         │   │ MobileNetV3-Small        │   │
│  • Retrofit client   │  JSON   │   │ healthy/diseased + ROI   │   │
│  • Material 3 UI     │ ◄────── │   └──────────┬───────────────┘   │
│  • RU/EN localization│         │              │                   │
│  • Profile & stats   │         │   ┌──────────▼───────────────┐   │
│                      │         │   │ Stage 2: Classifier      │   │
│                      │         │   │ EfficientNet-B0          │   │
│                      │         │   │ 15 disease classes       │   │
│                      │         │   └──────────────────────────┘   │
└──────────────────────┘         └──────────────────────────────────┘
```

### Two-Stage Detection Pipeline

```
Full photo
    │
    ▼
┌─────────────────────────┐
│  Stage 1: Detector       │  MobileNetV3-Small (2.5M params)
│  "Is the plant diseased? │  Binary classifier + Grad-CAM heatmap
│   Where is the damage?"  │  Outputs: healthy/diseased + bounding box
└──────────┬──────────────┘
           │
      ┌────┴─────┐
      │ Healthy?  │─── yes ──► return "Healthy Plant" immediately
      └────┬──────┘
           │ no
           ▼
    Crop image to affected region
           │
           ▼
┌─────────────────────────┐
│  Stage 2: Classifier     │  EfficientNet-B0 (5.3M params)
│  "Which disease is it?"  │  15-class fine-grained classification
│                          │  Works on the cropped ROI for precision
└──────────┬──────────────┘
           │
           ▼
    Disease name + confidence
    + treatment advice (EN/RU)
```

**Why two stages?**
- **Better accuracy** — the classifier focuses on the diseased area, not the whole image
- **Faster for healthy plants** — no need to run the heavy classifier
- **Explainable** — the bounding box shows exactly what the model detected
- **Lightweight** — both models total ~8M parameters (vs 100M+ for larger architectures)

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

```bash
# Linux
./start.sh --train

# Windows
start.bat --train

# Or run training directly
python train.py              # train both models
python train.py --detector   # train detector only
python train.py --classifier # train classifier only
```

### Build the Android app

1. Open `android/` in Android Studio (Hedgehog 2023.1+)
2. Set server URL in `app/build.gradle.kts`:
   ```kotlin
   // Emulator:
   buildConfigField("String", "API_BASE_URL", "\"http://10.0.2.2:8000/\"")
   // Physical device (same WiFi):
   buildConfigField("String", "API_BASE_URL", "\"http://YOUR_PC_IP:8000/\"")
   ```
3. Sync Gradle → Run on device/emulator

---

## Android App

### Features
- **Camera scanner** — point at a plant leaf and capture with a rule-of-thirds grid, tap-to-focus, and torch toggle.
- **Image gallery picker** — analyse photos already on the device.
- **Scan history** — all results saved locally in Room with migrations (no destructive fallbacks).
- **Plant care guide** — 22 bilingual articles (diseases, pests, watering, lighting, care) with fuzzy search and recent-query chips.
- **Profile & statistics** — total scans, healthy/diseased split, most common disease, storage breakdown.
- **Real Grad-CAM heatmap** — pixel-accurate detection region mapped through `centerCrop`, with a cached radial gradient and a pulsing fill animator that cleans itself up in `onDetachedFromWindow`.
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
- **Signed release APK** — signing config with ProGuard rules for Retrofit/Room/Lottie/Gson.
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
| 1 — Detector | MobileNetV3-Small | 2.5 M | Binary (healthy/diseased) + Grad-CAM region |
| 2 — Classifier | EfficientNet-B0 | 5.3 M | 15-class disease identification |

Both models use **ImageNet-pretrained** backbones with two-phase transfer learning:
- Phase A: freeze backbone → train classifier head
- Phase B: unfreeze top layers → fine-tune with lower LR

### Server Files
```
server/
├── start.sh               # Linux / macOS startup script
├── start.bat              # Windows startup script
├── main.py                # FastAPI server (endpoints)
├── pipeline.py            # Two-stage pipeline orchestrator
├── detector.py            # Stage 1: MobileNetV3 + Grad-CAM
├── classifier.py          # Stage 2: EfficientNet-B0
├── train.py               # Unified training script
├── diseases_data.py       # Bilingual disease database
├── requirements.txt       # Server dependencies
├── requirements-train.txt # Extra training dependencies
└── models/
    ├── detector.pth       # Trained Stage 1 model
    └── classifier.pth     # Trained Stage 2 model
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

1. **Download PlantVillage dataset:**
   - GitHub: https://github.com/spMohanty/PlantVillage-Dataset
   - Kaggle: https://www.kaggle.com/datasets/emmarex/plantdisease

2. **Organise data:**
   ```
   server/data/
   ├── train/
   │   ├── healthy/
   │   ├── bacterial_spot/
   │   ├── early_blight/
   │   ├── powdery_mildew/
   │   └── ...
   └── val/
       ├── healthy/
       ├── bacterial_spot/
       └── ...
   ```

3. **Train:**
   ```bash
   python train.py              # both models
   python train.py --detector   # Stage 1 only
   python train.py --classifier # Stage 2 only
   ```

4. **Restart server** — models load automatically.

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | Liveness probe + pipeline status + uptime |
| `GET` | `/api/version` | Server version, model digest, class count |
| `GET` | `/api/classes` | 15 disease classes with bilingual names |
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
- Grad-CAM inference is serialized by a lock and uses `threading.local` storage to keep backward-pass buffers per-request safe under concurrency.

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
    "region": {
      "x": 120,
      "y": 80,
      "width": 200,
      "height": 170
    }
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

| # | Class | EN Name | RU Name |
|---|-------|---------|---------|
| 0 | healthy | Healthy Plant | Здоровое растение |
| 1 | bacterial_spot | Bacterial Spot | Бактериальная пятнистость |
| 2 | early_blight | Early Blight | Ранний фитофтороз |
| 3 | late_blight | Late Blight | Фитофтороз |
| 4 | leaf_mold | Leaf Mold | Листовая плесень |
| 5 | septoria_leaf_spot | Septoria Leaf Spot | Септориоз |
| 6 | spider_mites | Spider Mites | Паутинный клещ |
| 7 | target_spot | Target Spot | Мишеневидная пятнистость |
| 8 | mosaic_virus | Mosaic Virus | Мозаичный вирус |
| 9 | yellow_leaf_curl | Yellow Leaf Curl | Жёлтое скручивание |
| 10 | powdery_mildew | Powdery Mildew | Мучнистая роса |
| 11 | rust | Rust | Ржавчина |
| 12 | root_rot | Root Rot | Корневая гниль |
| 13 | anthracnose | Anthracnose | Антракноз |
| 14 | botrytis | Gray Mold | Серая гниль |

---

## What sets this apart

- **Two-stage pipeline beats single-pass baselines.** Stage 1 finds the affected region with Grad-CAM, Stage 2 classifies the ROI crop — on PlantVillage-style data that raises top-1 over a flat 16-class classifier because the fine-grained model never has to learn "what is a leaf" again.
- **Heatmap is a real overlay, not a picture of one.** The region is transported as pixel coordinates in the original image and re-projected through Glide's `centerCrop` on device, so zooming stays crisp and the overlay survives configuration changes.
- **Explainable by default.** Every diagnosis ships with top-3 probabilities, a Shannon-entropy uncertainty score, and a severity estimate (fraction of heatmap pixels above threshold).
- **Production-hardened server.** Magic-byte validation, Pillow decompression-bomb guard, per-IP rate limiter with `Retry-After`, proxy-aware client IP via `TRUSTED_PROXIES`, inference timeout, security response headers, non-root Docker user, and a container `HEALTHCHECK`.
- **Thread-safe Grad-CAM.** The activation / gradient storage is `threading.local`, and the backward pass is serialized by a lock — so concurrent requests never corrupt each other's CAM.
- **Resilient client transport.** `RetryInterceptor` retries on `IOException`, HTTP 429 and HTTP 503, capped exponential backoff, and honours `Retry-After` seconds.
- **Bilingual end-to-end.** Russian and English share a single disease database on the server and UI strings on the client, switchable from Profile without restarting the app.
- **Observability.** `/api/metrics` exposes request / error counters and analyse-latency histograms — drop-in for Prometheus scraping.

---

## License

[Unlicense](https://unlicense.org) — public domain, free for any use.
