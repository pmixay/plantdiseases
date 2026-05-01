# PlantDiseases

AI-powered **houseplant** disease detection — Android app (Kotlin) talking
to a Python FastAPI server. The server runs a two-stage ML pipeline and
returns a bilingual (EN / RU) diagnosis with treatment and prevention
advice.

The Android client is a thin camera + UX shell over the server: every
diagnosis and every chat reply round-trips to the server. There is no
on-device fallback — when the server is unreachable the app surfaces a
clear error rather than a degraded local guess.

---

## Architecture

```
┌──────────────────────┐         ┌──────────────────────────────────┐
│   Android App        │  HTTP   │   Python Server (FastAPI)        │
│   (Kotlin)           │ ──────► │                                  │
│                      │         │   ┌──────────────────────────┐   │
│  • CameraX capture   │         │   │ Stage 1: Detector        │   │
│  • Room local DB     │         │   │ YOLOv8n (single-class)   │   │
│  • Retrofit client   │  JSON   │   │ leaf bounding boxes      │   │
│  • Material 3 UI     │ ◄────── │   └──────────┬───────────────┘   │
│  • RU / EN strings   │         │              │                   │
│  • My Garden + log   │         │   ┌──────────▼───────────────┐   │
│  • AI Doctor (chat)  │         │   │ Stage 2: Classifier      │   │
│  • Light meter       │         │   │ EfficientNet-B0 @ 240 px │   │
│                      │         │   │ 10 houseplant classes    │   │
│                      │         │   │ + not_a_plant rejection  │   │
│                      │         │   │ + MSP/OOD gate           │   │
│                      │         │   └──────────────────────────┘   │
└──────────────────────┘         └──────────────────────────────────┘
```

### Two-stage pipeline (PlantVision 1.1)

```
Full photo (any composition — hand, wall, background are fine)
    │
    ▼
┌──────────────────────────┐
│  Stage 1: Leaf Detector  │  YOLOv8n (~3.2 M params, single-class "leaf")
│  "Where are the leaves?" │  No detection → class = not_a_plant
└──────────┬───────────────┘
           │
      ┌────┴──────────────────┐
      │ Any detections?       │─── no ──► class = not_a_plant
      └────┬──────────────────┘
           │ yes (≥1 leaf bbox, size-filtered)
           ▼
    Crop ≤4 highest-confidence bboxes (mean-voting)
           │
           ▼
┌───────────────────────────────┐
│  Stage 2: Disease Classifier  │  EfficientNet-B0 (~5 M params, 240 px)
│  "Which condition is this?"   │  10 classes incl. not_a_plant
│                               │  ONNX INT8 (~25-40 ms on CPU VPS)
└──────────┬────────────────────┘
           │
           ▼
    Mean-voted softmax + temperature scaling
           │
           ▼
    Healthy fallback (margin / threshold gates)
           │
           ▼
    OOD/NAP gate: low detector conf + low classifier MSP → not_a_plant
           │
           ▼
    Final disease name + confidence + per-class probabilities
    + treatment / prevention advice (EN / RU) + pest hints
```

**Why two stages.** Letting YOLOv8 find the leaf first means the
classifier sees a tight crop instead of a whole room — the backbone
never has to learn "what is a leaf" again. It also rejects noise:
fingers, walls, and furniture don't score above the detector
threshold, so the pipeline cleanly returns `not_a_plant` instead of
guessing a random disease. A second-line MSP-based OOD gate
([`pipeline.py:NAP_*_FLOOR`](server/pipeline.py)) catches the residual
case where Stage 1 fires weakly on a non-leaf texture.

**Why EfficientNet-B0 and not V2-S.** The classifier dataset is on the
order of ~10–12 k images across 10 classes. EfficientNetV2-S (21 M
params) was oversized for that, with elevated overfit risk on the
long-tail classes. B0 (5 M params) is a better capacity match, trains
faster on a free Colab T4, and runs ~4× faster on a CPU-only
production VPS.

---

## Classes

Stage 1 (YOLOv8n) is **single-class**:

| # | Class |
|---|-------|
| 0 | `leaf` |

The "is this leaf diseased?" decision belongs entirely to Stage 2.

Stage 2 (EfficientNet-B0 @ 240) classifies the primary leaf crop into
one of ten houseplant-focused classes (alphabetical, matching
`sorted(ImageFolder.classes)` at training time):

| # | Class | EN name | RU name |
|---|-------|---------|---------|
| 0 | `anthracnose`         | Anthracnose          | Антракноз             |
| 1 | `bacterial_leaf_spot` | Bacterial Leaf Spot  | Бактериальная пятнистость |
| 2 | `gray_mold`           | Gray Mold            | Серая гниль           |
| 3 | `healthy`             | Healthy Plant        | Здоровое растение     |
| 4 | `iron_chlorosis`      | Iron Chlorosis       | Железный хлороз       |
| 5 | `late_blight`         | Late Blight          | Фитофтороз            |
| 6 | `mosaic_virus`        | Mosaic Virus         | Мозаичный вирус       |
| 7 | `not_a_plant`         | Not a Plant          | Не растение           |
| 8 | `powdery_mildew`      | Powdery Mildew       | Мучнистая роса        |
| 9 | `sunburn`             | Sunburn              | Солнечный ожог        |

Canonical list: [`server/models/classes.json`](server/models/classes.json).
Bilingual descriptions, treatments and prevention tips live in
[`server/diseases_data.py`](server/diseases_data.py).

The `not_a_plant` bucket is a true rejection class trained on ~1800
COCO val2017 photos (fingers, walls, furniture, fabric) with plant-
like categories filtered out via COCO annotations. The pipeline also
runs an MSP-based OOD gate after voting so even Stage-1 false
positives on plant-coloured textures (curtains, fabric) cleanly route
to NAP rather than getting a random disease label.

**Pests are hints, not classes.** Pest cards (spider mites, aphids,
mealybugs, scale insects, whitefly, thrips) surface as "possible pest
causes" under each diagnosis — see
[`server/diseases_data.py`](server/diseases_data.py) `PEST_INFO`.

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

The script creates a virtual environment, installs CPU-only PyTorch
plus the rest of `requirements.txt`, and launches FastAPI on
`http://localhost:8000`. Without trained weights the server runs in a
**demo mode** driven by colour heuristics — the API contract is
identical, but the results are not meaningful. Demo mode exists only
so contributors can run the API locally; production must ship trained
weights.

### Android app

The server URL is fixed at build time through the Gradle
`API_BASE_URL` property. The shipped default points at the production
endpoint; for local development pass it on the command line:

```bash
cd android
./gradlew :app:assembleDebug -PAPI_BASE_URL=http://10.0.2.2:8000/
```

`10.0.2.2` is the emulator loopback to the host. On a real device,
type the LAN IP (e.g. `http://192.168.1.42:8000/`). Cleartext HTTP is
allowed only for `10.0.2.2`, `localhost` and `127.0.0.1`; for
production, terminate TLS on the server (see `DEPLOYMENT.md`).

---

## Training the models

Training runs **only in Google Colab** via
[`server/train_notebook.ipynb`](server/train_notebook.ipynb). The
notebook runs end-to-end on a free-tier T4 in roughly ~3 hours
including the optional V2-S ensemble peer:

1. Install dependencies and sanity-check the GPU.
2. Clone **PlantDoc** for the YOLO-side and pull supporting sources
   for the classifier (PlantVillage, iNaturalist taxon scrapes,
   Kaggle Houseplant Species).
3. Download **COCO val2017** to produce ~1800 `not_a_plant` negatives
   (plant-like categories like *potted plant* and *vase* are filtered
   out via the COCO annotations to avoid label noise).
4. Build the classifier dataset with stratified per-class 15 % val
   split and group-aware leakage prevention.
5. Train **YOLOv8n** single-class on the leaf-only dataset
   (~20–30 min with `cache='disk'`, mosaic + mixup + hsv-jitter
   augmentations, `close_mosaic=10`, AMP and `patience=12`).
6. Train **EfficientNet-B0 @ 240** end-to-end: freeze-backbone warmup
   then fine-tune `features.5-8` + head, with CrossEntropy +
   `label_smoothing=0.05`, WeightedRandomSampler, CutMix + MixUp, EMA
   weights, h-flip TTA on val, batch 96 + persistent workers +
   `channels_last`.
7. Calibrate temperature on val NLL and write `temperature.json` next
   to the checkpoint.
8. Export `detector.pt`/`detector.onnx`,
   `classifier.pth`/`classifier.onnx`/`classifier_int8.onnx`, and
   `classes.json`.

Drop the model files into `server/models/` and restart the server —
the pipeline auto-detects real vs demo mode and prefers ONNX INT8 when
present.

---

## Server API

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/health`            | Liveness + pipeline status + uptime |
| `GET`  | `/api/version`           | Server version + model architecture |
| `GET`  | `/api/classes`           | 10 classes with bilingual names |
| `GET`  | `/api/metrics`           | Counters for requests, errors, latency |
| `GET`  | `/api/metrics/prometheus`| Prometheus exposition format |
| `POST` | `/api/analyze`           | Upload image → get diagnosis |
| `POST` | `/api/identify`          | PlantNet species identification |
| `POST` | `/api/chat`              | AI Plant Doctor (Claude Haiku) |
| `POST` | `/api/care-plan`         | Care schedule for a diagnosis + species |
| `POST` | `/api/progression`       | Compare two scans → trend |

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
       "class": "leaf", "confidence": 0.93}
    ],
    "primary_region": {"x": 120, "y": 80, "width": 200, "height": 170,
                       "class": "leaf", "confidence": 0.93}
  },
  "uncertainty": 0.12,
  "severity": "medium",
  "all_probs": {
    "healthy": 0.05, "powdery_mildew": 0.87, "...": "..."
  },
  "pipeline_mode": "full",
  "elapsed_ms": 145.3,
  "server_version": "1.1"
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
- Per-session AI Doctor budget caps (turn count + token count) with
  abuse logging.
- Docker image runs as a non-root user and has a `HEALTHCHECK`.
- `uvicorn` started with `--proxy-headers --forwarded-allow-ips`.

Full production notes are in [`DEPLOYMENT.md`](DEPLOYMENT.md).

---

## Android app

### Features

- Camera scanner with rule-of-thirds grid, tap-to-focus, torch and
  pinch-to-zoom.
- Real-time leaf framing with auto-capture on a stable green frame.
- Gallery picker, blur pre-check (Laplacian variance) and green-content
  pre-check (HSV ratio).
- Scan history saved locally in Room with multi-bbox overlay, top-3
  alternatives and Shannon-entropy uncertainty score.
- **My Garden** with per-plant timeline, free-text journal, photo log,
  watering log and editable WorkManager-backed reminders.
- **AI Plant Doctor** chat that round-trips through `/api/chat`
  (Claude Haiku with prompt caching) and persists per-plant
  conversation in Room. Requires a server connection.
- **Light meter** using the ambient light sensor with a CameraX
  luminance fallback.
- TTS read-aloud on the result screen, share with image (composited
  primary-region highlight via `FileProvider`).
- Bilingual EN / RU, Material 3, dark / light / system theme,
  edge-to-edge with predictive back.

### Tech stack

| Component | Library |
|-----------|---------|
| Camera     | CameraX 1.3 |
| Database   | Room 2.6 |
| Network    | Retrofit 2.9 + OkHttp 4 |
| Images     | Glide 4.16 |
| Navigation | Jetpack Navigation |
| UI         | Material Design 3 |
| Async      | Kotlin Coroutines |
| Reminders  | WorkManager |

### Project layout

```
android/app/src/main/
├── java/com/plantdiseases/app/
│   ├── PlantDiseasesApp.kt           # Application class
│   ├── MainActivity.kt               # Main activity + nav
│   ├── data/
│   │   ├── local/Database.kt         # Room DB + entities + DAOs
│   │   ├── remote/ApiService.kt      # Retrofit API + retry interceptor
│   │   ├── model/Models.kt           # Wire data models
│   │   ├── repository/ScanRepository.kt
│   │   ├── repository/PlantRepository.kt
│   │   ├── GuideDataProvider.kt      # Offline care guide content
│   │   └── PestCauses.kt             # Pest hint cards
│   ├── ui/
│   │   ├── camera/                   # Camera scanner + light meter
│   │   ├── gallery/                  # Scan history grid
│   │   ├── garden/                   # My Garden, journal, doctor chat
│   │   ├── guide/                    # Care articles
│   │   ├── profile/                  # Stats & settings
│   │   ├── analysis/                 # Loading screen
│   │   ├── onboarding/               # Onboarding flow
│   │   └── result/                   # Diagnosis result
│   ├── util/                         # Locale / theme / haptics / images
│   └── worker/                       # Notification reminder worker
└── res/
    ├── layout/                       # XML layouts
    ├── values/ + values-ru/          # English / Russian strings
    ├── drawable/ + drawable-night/   # Icons & shapes
    ├── navigation/                   # Nav graph
    ├── menu/                         # Bottom nav menus
    └── raw/                          # Lottie animations
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
├── classifier.py            # Stage 2: EfficientNet-B0 (+ V2-S ensemble peer)
├── ai_services.py           # PlantNet, Claude chat, care-plan, progression
├── diseases_data.py         # Bilingual houseplant disease database
├── train_notebook.ipynb     # Colab-only end-to-end training notebook
├── requirements.txt         # Server dependencies
├── Dockerfile               # Non-root, HEALTHCHECK-enabled image
├── docker-compose.yml       # Compose stack for production
└── models/
    ├── detector.pt          # YOLOv8n weights
    ├── classifier.pth       # PlantVision EfficientNet-B0 weights
    └── classes.json         # Canonical class list + model version
```

---

## License

[Unlicense](https://unlicense.org) — public domain, free for any use.
