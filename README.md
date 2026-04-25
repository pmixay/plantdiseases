# PlantDiseases

AI-powered **houseplant** disease detection — Android app (Kotlin) talking
to a Python FastAPI server. The server runs a two-stage ML pipeline and
returns a bilingual (EN / RU) diagnosis with treatment and prevention
advice.

---

## Architecture

```
┌──────────────────────┐         ┌──────────────────────────────────┐
│   Android App        │  HTTP   │   Python Server (FastAPI)        │
│   (Kotlin)           │ ──────► │                                  │
│                      │         │   ┌──────────────────────────┐   │
│  • CameraX capture   │         │   │ Stage 1: Detector        │   │
│  • Room local DB     │         │   │ YOLOv8n                  │   │
│  • Retrofit client   │  JSON   │   │ leaf / diseased_leaf     │   │
│  • Material 3 UI     │ ◄────── │   │ bounding boxes           │   │
│  • RU / EN strings   │         │   └──────────┬───────────────┘   │
│  • Profile & stats   │         │              │                   │
│                      │         │   ┌──────────▼───────────────┐   │
│                      │         │   │ Stage 2: Classifier      │   │
│                      │         │   │ EfficientNetV2-S         │   │
│                      │         │   │ 9 houseplant classes     │   │
│                      │         │   │ + not_a_plant rejection  │   │
│                      │         │   └──────────────────────────┘   │
└──────────────────────┘         └──────────────────────────────────┘
```

### Two-stage pipeline

```
Full photo (any composition — hand, wall, background are fine)
    │
    ▼
┌──────────────────────────┐
│  Stage 1: Leaf Detector   │  YOLOv8n (~3.2 M params)
│  "Where are the leaves    │  Output: bboxes for leaf / diseased_leaf
│   and which look sick?"   │  No detection → "not a plant, retake"
└──────────┬───────────────┘
           │
      ┌────┴──────────────────┐
      │ Any detections?        │─── no ──► class = not_a_plant
      └────┬──────────────────┘
           │ yes
           ▼
    Crop to the primary diseased bbox
    (or the best healthy leaf if none are flagged)
           │
           ▼
┌──────────────────────────┐
│  Stage 2: Disease Classifier  │  EfficientNetV2-S (~21 M params)
│  "Which disease is this?"     │  9 classes incl. not_a_plant
│                               │  Operates only on the ROI
└──────────┬───────────────────┘
           │
           ▼
    Disease name + confidence + per-class probabilities
    + treatment / prevention advice (EN / RU)
```

**Why two stages.** Letting YOLOv8 find the leaf first means the
classifier sees a tight crop instead of a whole room — the backbone never
has to learn "what is a leaf" again. It also rejects noise: fingers,
walls, and furniture don't score above the detector threshold, so the
pipeline cleanly returns `not_a_plant` instead of guessing a random
disease.

---

## Classes

Stage 1 (YOLOv8n) returns two object classes:

| # | Class |
|---|-------|
| 0 | `leaf` |
| 1 | `diseased_leaf` |

Stage 2 (EfficientNetV2-S) classifies the primary diseased crop into one
of nine houseplant-focused classes (alphabetical, matching
`sorted(ImageFolder.classes)` at training time):

| # | Class | EN name | RU name |
|---|-------|---------|---------|
| 0 | `blight` | Blight | Фитофтороз |
| 1 | `healthy` | Healthy Plant | Здоровое растение |
| 2 | `leaf_mold` | Leaf Mold | Листовая плесень |
| 3 | `leaf_spot` | Leaf Spot (Bacterial / Fungal) | Пятнистость листьев |
| 4 | `mosaic_virus` | Mosaic Virus | Мозаичный вирус |
| 5 | `not_a_plant` | Not a Plant | Не растение |
| 6 | `powdery_mildew` | Powdery Mildew | Мучнистая роса |
| 7 | `rust` | Rust | Ржавчина |
| 8 | `spider_mites` | Spider Mites | Паутинный клещ |

Canonical list: [`server/models/classes.json`](server/models/classes.json).
Bilingual descriptions, treatments and prevention tips live in
[`server/diseases_data.py`](server/diseases_data.py).

The `not_a_plant` bucket is a true rejection class trained on COCO
imagery (fingers, walls, furniture, fabric) so noisy frames don't get
a random disease label.

**Class-size safeguard.** The training notebook drops any class whose
training split has fewer than `MIN_TRAIN_PER_CLASS = 50` images. PlantDoc
on its own only yields a handful of `spider_mites` photos, so the
notebook now also sparse-checkouts the
`Tomato___Spider_mites Two-spotted_spider_mite` folder from
[PlantVillage](https://github.com/spMohanty/PlantVillage-Dataset) (~1.3k
photos, capped at 800 after shuffle) to keep the class healthy. If
PlantVillage cannot be reached, the guard simply removes `spider_mites`
from the class list and the model ships as 8-class — the server's
`classifier._load_model` truncates `DEFAULT_CLASS_NAMES` to match.

---

## Quick start

### Server

**Linux / macOS:**
```bash
cd server
chmod +x start.sh
./start.sh
```

**Windows:**
```cmd
cd server
start.bat
```

The script creates a virtual environment, installs CPU-only PyTorch plus
the rest of `requirements.txt`, and launches FastAPI on
`http://localhost:8000`. Without trained weights the server runs in a
**demo mode** driven by colour heuristics — the API contract is
identical, but the results are not meaningful.

### Android app

1. Open `android/` in Android Studio (Hedgehog 2023.1 or newer).
2. Sync Gradle → run on a device or emulator.
3. Set the server URL at runtime in **Profile → Server URL**. The shipped
   default (`http://10.0.2.2:8000/`) points at the emulator loopback; on
   a real device, type the PC's LAN IP, e.g. `http://192.168.1.42:8000/`.

> Cleartext HTTP is only allowed for `10.0.2.2`, `localhost`, and
> `127.0.0.1`. For production, terminate TLS on the server (see
> `DEPLOYMENT.md`).

---

## Training the models

Training runs **only in Google Colab** via
[`server/train_notebook.ipynb`](server/train_notebook.ipynb). The
notebook runs end-to-end on a free-tier T4 in roughly 70–95 minutes:

1. Install dependencies and sanity-check the GPU.
2. Clone **PlantDoc** (object-detection + classification subsets).
3. Download **COCO val2017** to produce `not_a_plant` negatives
   (plant-like categories like *potted plant* and *vase* are filtered
   out via the COCO annotations to avoid label noise).
4. Optionally pull **Houseplant Species** from Kaggle to strengthen the
   `healthy` class with real indoor photos.
5. Train **YOLOv8n** on the remapped leaf / diseased_leaf dataset
   (~30–45 min with `cache='ram'`, mosaic + mixup + hsv-jitter
   augmentations, `close_mosaic=10`, AMP and `patience=12`).
6. Train **EfficientNetV2-S** end-to-end: freeze-backbone warmup
   (`Phase A`, 10 epochs) then fine-tune `features.5-7` + head
   (`Phase B`, 12 epochs) with FocalLoss + inverse-frequency weights,
   CutMix/MixUp, EMA weights, h-flip TTA on val, a class-balanced
   sampler, batch=48 + persistent workers + `channels_last` for T4
   throughput (~40–55 min).
7. Export `detector.pt`, `classifier.pth`, and `classes.json`
   (the notebook regenerates `classes.json` with the alphabetical class
   list and the v3.1.0 model version).

Drop the three files into `server/models/` and restart the server — the
pipeline auto-detects real vs demo mode.

---

## Server API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | Liveness + pipeline status + uptime |
| `GET` | `/api/version` | Server version + model architecture |
| `GET` | `/api/classes` | 9 classes with bilingual names |
| `GET` | `/api/metrics` | Counters for requests, errors, latency |
| `GET` | `/api/metrics/prometheus` | Prometheus exposition format |
| `POST` | `/api/analyze` | Upload image → get diagnosis |

### `POST /api/analyze`

**Request.** `multipart/form-data` with an `image` field (JPEG, PNG,
WebP, or BMP; up to 10 MB, up to 4096 px per side).

**Response (abridged):**
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
      {"x": 120, "y":  80, "width": 200, "height": 170,
       "class": "diseased_leaf", "confidence": 0.93},
      {"x": 340, "y": 210, "width": 160, "height": 150,
       "class": "leaf",          "confidence": 0.71}
    ],
    "primary_region": {"x": 120, "y": 80, "width": 200, "height": 170,
                       "class": "diseased_leaf", "confidence": 0.93}
  },
  "uncertainty": 0.12,
  "all_probs": {
    "healthy": 0.05, "powdery_mildew": 0.87, "leaf_mold": 0.04,
    "...": "..."
  },
  "pipeline_mode": "full",
  "elapsed_ms": 145.3,
  "server_version": "3.0.0"
}
```

### Server hardening

- Magic-byte validation of the uploaded bytes; the declared
  `Content-Type` is not trusted.
- Pillow decompression-bomb guard + explicit `MAX_IMAGE_DIMENSION`.
- Per-IP rate limiter (`RATE_LIMIT_RPS`, default 1 req/s) with an
  accurate `Retry-After` header on HTTP 429.
- Proxy-aware client IP — `X-Forwarded-For` is honoured only for peers
  listed in `TRUSTED_PROXIES`.
- Inference timeout (`INFERENCE_TIMEOUT`, default 30 s) with a
  `ThreadPoolExecutor` sized by `INFER_WORKERS`.
- Security response headers: `X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, `Permissions-Policy`, `Cache-Control`.
- Docker image runs as a non-root user and has a `HEALTHCHECK`.
- `uvicorn` started with `--proxy-headers --forwarded-allow-ips`.

Full production notes are in [`DEPLOYMENT.md`](DEPLOYMENT.md).

---

## Android app

### Features
- Camera scanner with a rule-of-thirds grid, tap-to-focus and torch.
- Gallery picker for photos already on the device.
- Scan history saved locally in Room.
- Plant care guide — houseplant-focused articles in EN and RU with
  fuzzy search and recent-query chips.
- Profile & statistics — total scans, healthy/diseased split, most
  common disease, storage breakdown.
- Multi-bbox overlay on the result screen — every YOLOv8 detection is
  rendered, and the primary box (the one Stage 2 classified) is
  emphasised with a thicker stroke.
- Top-3 alternative diagnoses with confidence bars and a
  Shannon-entropy uncertainty score.
- Share with image — diagnosis text plus a composited photo with the
  primary-region highlight, via `FileProvider`.
- Blur pre-check (Laplacian variance) and green-content pre-check
  (HSV ratio) to skip obviously bad uploads.
- Configurable server URL from Profile → Server URL, no rebuild
  required.
- Retry interceptor with capped exponential backoff; honours the
  server's `Retry-After` seconds header.
- Pinch-to-zoom on the result image, edge-to-edge display, predictive
  back enabled.
- Hardened backups (`allowBackup=false`, explicit
  `dataExtractionRules`).
- Min SDK 26 (Android 8.0+).

### Tech stack
| Component | Library |
|-----------|---------|
| Camera | CameraX 1.3 |
| Database | Room 2.6 |
| Network | Retrofit 2.9 + OkHttp 4 |
| Images | Glide 4.16 |
| Navigation | Jetpack Navigation |
| UI | Material Design 3 |
| Async | Kotlin Coroutines |

### Project layout
```
android/app/src/main/
├── java/com/plantdiseases/app/
│   ├── PlantDiseasesApp.kt          # Application class
│   ├── MainActivity.kt              # Main activity + nav
│   ├── data/
│   │   ├── local/Database.kt        # Room DB + DAO
│   │   ├── remote/ApiService.kt     # Retrofit API
│   │   ├── model/Models.kt          # Data models
│   │   ├── repository/ScanRepository.kt
│   │   └── GuideDataProvider.kt     # Offline care guide
│   ├── ui/
│   │   ├── camera/                  # Camera scanner
│   │   ├── gallery/                 # Scan history grid
│   │   ├── guide/                   # Care articles + detail
│   │   ├── profile/                 # Stats & settings
│   │   ├── analysis/                # Loading screen
│   │   └── result/                  # Diagnosis result
│   └── util/
│       ├── LocaleHelper.kt          # Language switching
│       ├── ServerConfig.kt          # Runtime server URL
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

## Server layout

```
server/
├── start.sh                 # Linux / macOS startup script
├── start.bat                # Windows startup script
├── main.py                  # FastAPI server (endpoints, middleware)
├── pipeline.py              # Two-stage pipeline orchestrator
├── detector.py              # Stage 1: YOLOv8n (+ HSV demo fallback)
├── classifier.py            # Stage 2: EfficientNetV2-S
├── diseases_data.py         # Bilingual houseplant disease database
├── train_notebook.ipynb     # Colab-only end-to-end training notebook
├── requirements.txt         # Server dependencies
├── Dockerfile               # Non-root, HEALTHCHECK-enabled image
├── docker-compose.yml       # Compose stack for production
└── models/
    ├── detector.pt          # YOLOv8n weights
    ├── classifier.pth       # EfficientNetV2-S weights
    └── classes.json         # Canonical class list + model version
```

---

## License

[Unlicense](https://unlicense.org) — public domain, free for any use.
