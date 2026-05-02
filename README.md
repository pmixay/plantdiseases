# PlantDiseases

AI-powered **houseplant** disease detection — Android app (Kotlin) talking
to a Python FastAPI server. The server runs a two-stage ML pipeline and
returns a bilingual (EN / RU) diagnosis with treatment and prevention
advice.

---

## Project status

The Android client and the server contracts are feature-complete; the
ML pipeline is migrating to **PlantVision 2.0**, which changes the
class taxonomy, backbones and training recipe. While the v2.0 weights
are being trained the server runs in **demo mode** for the new
classes (the API contract is unchanged).

What changed vs v1.x:

- **11 classes** (was 10): `bacterial_leaf_spot`, `edema`,
  `gray_mold_botrytis`, `healthy`, `leaf_scorch_underwatering`,
  `leaf_spot_fungal` (anthracnose + cercospora + alternaria),
  `not_a_plant`, `nutrient_deficiency` (Fe + N + Mg + K),
  `powdery_mildew`, `sunburn_or_scorch`, `viral_disease`
  (mosaic + leaf_curl + ringspot). `late_blight` and `iron_chlorosis`
  are removed. **Pests are out of the taxonomy entirely** (no more
  "possible pest causes" hint).
- **Stage 1 detector** retrained on 300–800 indoor leaf bboxes
  (Roboflow Universe houseplant slices, hand-labelled user uploads).
  PlantDoc removed — its agricultural close-ups bias localisation
  toward field shots and hurt indoor recall.
- **Stage 2 backbone:** EfficientNetV2-S
  (`tf_efficientnetv2_s.in21k_ft_in1k`) teacher distilled into
  MobileNetV3-Large (5.5 M) student.
- **Loss:** Logit Adjustment (Menon ICLR 2021), τ=1.0 — replaces the
  hand-tuned inverse-frequency CE × `HEALTHY_DOWNWEIGHT=0.25`.
- **OOD score:** **MaxLogit** (Hendrycks 2019) — robust to temperature
  scaling and label smoothing where MSP collapses.
- **PlantVillage** moved out of the train set; kept only as a frozen
  `val_studio` slice (transfer / background-bias monitor — Noyan 2022:
  49 % accuracy from 8 background pixels).
- **Primary metric:** disease macro-F1 on `indoor_real_world_v1` —
  a frozen 500–1000 user-photo validation set across different
  devices, windows, lamps, distances. Without it, no shipping decision.

Per-week, per-engineer task list lives in [`ROADMAP.md`](ROADMAP.md).
Full training recipe lives in
[`server/train_notebook.ipynb`](server/train_notebook.ipynb) and the
[`server/models/MODEL_CARD.md`](server/models/MODEL_CARD.md).

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
│  • Profile & stats   │         │   ┌──────────▼───────────────┐   │
│  • NotAPlant verdict │         │   │ Stage 2: Classifier      │   │
│    as separate state │         │   │ MobileNetV3-L (KD ←      │   │
│                      │         │   │ EfficientNetV2-S) @ 256  │   │
│                      │         │   │ 11 houseplant classes    │   │
│                      │         │   │ incl. not_a_plant        │   │
│                      │         │   │ + MaxLogit OOD gate      │   │
│                      │         │   └──────────────────────────┘   │
└──────────────────────┘         └──────────────────────────────────┘
```

### Two-stage pipeline (PlantVision 2.0)

```
Full photo (any composition — hand, wall, background are fine)
    │
    ▼
┌────────────────────────────────┐
│  Stage 1: Plant Leaf Detector   │  YOLOv8n (~3.2 M, single-class plant_leaf)
│  Indoor-trained, recall-biased  │  No detection → fall through to Stage 2 on
└──────────┬─────────────────────┘  full frame; classifier may return not_a_plant
           │
           ▼
    Crop ≤4 highest-confidence bboxes (mean-voting)
           │
           ▼
┌────────────────────────────────┐
│  Stage 2: Disease Classifier    │  MobileNetV3-Large student (5.5 M, 256 px)
│  KD ← EfficientNetV2-S teacher  │  11 classes incl. not_a_plant
│                                 │  ONNX INT8 (~10-15 ms on CPU VPS)
└──────────┬─────────────────────┘
           │
           ▼
    Mean-voted softmax + temperature scaling
           │
           ▼
    Healthy fallback (margin / threshold gates)
           │
           ▼
    OOD gate: low detector conf + low MaxLogit → not_a_plant
           │
           ▼
    Final disease name + confidence + per-class probabilities
    + treatment / prevention advice (EN / RU)
```

**Why two stages.** Letting YOLOv8 find the leaf first means the
classifier sees a tight crop instead of a whole room. A second-line
**MaxLogit-based** OOD gate ([`pipeline.py`](server/pipeline.py))
catches the residual case where Stage 1 fires weakly on a non-leaf
texture. We use MaxLogit (not MSP) because temperature scaling and
label smoothing make the max-softmax-probability collapse — MaxLogit
operates on raw logits and is robust to both.

**Why EfficientNetV2-S → MobileNetV3-Large KD.** V2-S is the strongest
ImageNet-1k checkpoint that still quantises cleanly to INT8 (better
than any ViT we tested). MobileNetV3-Large (5.5 M) is the smallest
ConvNet that holds onto the teacher's accuracy after distillation
(α=0.35, τ=3.5) and runs in ~10-15 ms on a CPU VPS.

---

## Classes

Stage 1 (YOLOv8n) is **single-class**:

| # | Class |
|---|-------|
| 0 | `plant_leaf` |

The "is this leaf diseased?" decision belongs entirely to Stage 2.

Stage 2 (MobileNetV3-Large student @ 256 px, KD ← EfficientNetV2-S
teacher) classifies the primary leaf crop into one of 11
houseplant-focused classes (alphabetical, matching
`sorted(ImageFolder.classes)` at training time):

| # | Class | EN name | RU name |
|---|-------|---------|---------|
| 0  | `bacterial_leaf_spot`        | Bacterial Leaf Spot         | Бактериальная пятнистость       |
| 1  | `edema`                      | Edema (Oedema)              | Эдема (отёчность листьев)        |
| 2  | `gray_mold_botrytis`         | Gray Mold (Botrytis)        | Серая гниль (ботритис)          |
| 3  | `healthy`                    | Healthy Plant               | Здоровое растение               |
| 4  | `leaf_scorch_underwatering`  | Leaf Scorch (Under-watering) | Краевой ожог (недолив)         |
| 5  | `leaf_spot_fungal`           | Fungal Leaf Spot            | Грибковая пятнистость            |
| 6  | `not_a_plant`                | Not a Plant                 | Не растение                     |
| 7  | `nutrient_deficiency`        | Nutrient Deficiency         | Дефицит питания                  |
| 8  | `powdery_mildew`             | Powdery Mildew              | Мучнистая роса                  |
| 9  | `sunburn_or_scorch`          | Sunburn / Light Scorch      | Солнечный ожог                   |
| 10 | `viral_disease`              | Viral Disease               | Вирусное заболевание             |

Canonical list: [`server/models/classes.json`](server/models/classes.json).
Bilingual descriptions, treatments and prevention tips live in
[`server/diseases_data.py`](server/diseases_data.py).

`leaf_spot_fungal` is a coarse umbrella for anthracnose, cercospora and
alternaria — the three are visually overlapping at 256 px and share
treatment advice (copper fungicide, dry foliage, isolate). Same logic
for `viral_disease` (mosaic + leaf curl + ringspot) and
`nutrient_deficiency` (Fe + N + Mg + K).

The `not_a_plant` bucket is a true rejection class trained on a curated
mix of COCO val2017, OpenImages V7 indoor scenes (filtered subset),
iNat Animalia, and discarded user uploads. The pipeline also runs a
**MaxLogit-based** OOD gate after voting so even Stage-1 false
positives on plant-coloured textures (curtains, fabric) cleanly route
to NAP rather than getting a random disease label.

**Pests are not in the taxonomy.** Generic pest education still lives
in the Guide section of the Android app, but the classifier itself
emits no pest labels and the diagnosis screen no longer surfaces a
"possible pest causes" hint card.

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

Training runs end-to-end on **1× A100-40** in roughly **1 hour** via
[`server/train_notebook.ipynb`](server/train_notebook.ipynb):

1. **Data hygiene first** — SHA-256 + pHash dedup, DINOv2 ViT-B/14
   embeddings + per-class kNN (drop > 2σ outliers), Cleanlab
   label-issue ranking, FiftyOne triage of top-N suspects, manual
   validation of 200–300 photos per class.
2. **Stage 1 — YOLOv8n single-class `plant_leaf`** trained on 300–800
   hand-labelled **indoor** leaf bboxes plus targeted Roboflow Universe
   houseplant slices. PlantDoc is **not** used.
3. **Diagnostic baseline (5–10 min)** — DINOv2-S frozen + linear probe.
   If the probe ≫ student, the bottleneck is data; if probe ≈ chance,
   the bottleneck is labels / semantics; if probe ≈ student, scheduler
   tweaks pay off.
4. **Stage 2 teacher (~35 min)** — `tf_efficientnetv2_s.in21k_ft_in1k`,
   AdamW + cosine + 1-epoch warmup, 256 px batch=128 bf16-mixed,
   head-only 3 epochs (lr=3e-3) → unfreeze last 2 stages 12–15 epochs
   (head lr=5e-4 / backbone lr=5e-5), Logit Adjustment loss (τ=1.0),
   indoor-targeted augs, MixUp 0.10 / CutMix 0.10–0.20.
5. **Knowledge distillation into MobileNetV3-Large student (10–15 min)**
   — α·CE + (1−α)·τ²·KL, α=0.35, τ=3.5, ~12 epochs.
6. **Calibration & export (5 min)** — temperature scaling on
   `indoor_real_world_v1` val NLL → `temperature.json`. ONNX FP32
   export → dynamic INT8 on MatMul. MaxLogit OOD floor sweep on
   held-out OE-pool.

Datasets:

* **Money Plant Disease Atlas** (Biswas 2024, ~3k Pothos crops).
* **Roboflow Universe** — point-pick houseplant disease projects only.
* **iNaturalist** — `quality_grade=research` +
  `field:plant_part_observed=leaf` + correct indoor-genus taxon IDs.
  **Drop Phytophthora cinnamomi** (root pathogen).
* **`indoor_real_world_v1`** — 500–1000 user photos with the
  hierarchical schema (`plant_id`, `species`, `diagnosis`, `severity`,
  `leaf_or_whole_plant`, `capture_session`, `source`,
  `label_confidence`, `bbox`). Splits are disjoint by `plant_id`.
* **PlantVillage** — frozen `val_studio` slice only, never train.
* **OE-pool** for `not_a_plant` — filtered OpenImages V7 indoor scenes,
  COCO val2017, iNat Animalia, recycled discarded user photos. Do
  **not** download the full OpenImages tarball.

Drop the model files into `server/models/` and restart the server — the
pipeline auto-detects real vs demo mode and prefers ONNX INT8 when
present.

---

## Server API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | Liveness + pipeline status + uptime |
| `GET` | `/api/version` | Server version + model architecture |
| `GET` | `/api/classes` | 11 classes with bilingual names |
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
       "class": "plant_leaf", "confidence": 0.93},
      {"x": 340, "y": 210, "width": 160, "height": 150,
       "class": "plant_leaf", "confidence": 0.71}
    ],
    "primary_region": {"x": 120, "y": 80, "width": 200, "height": 170,
                       "class": "plant_leaf", "confidence": 0.93}
  },
  "uncertainty": 0.12,
  "all_probs": {
    "healthy": 0.05, "powdery_mildew": 0.87, "leaf_spot_fungal": 0.04,
    "...": "..."
  },
  "pipeline_mode": "full",
  "elapsed_ms": 145.3,
  "server_version": "2.0"
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
├── classifier.py            # Stage 2: MobileNetV3-Large (KD ← V2-S)
├── diseases_data.py         # Bilingual houseplant disease database
├── train_notebook.ipynb     # End-to-end v2.0 training recipe
├── requirements.txt         # Server dependencies
├── Dockerfile               # Non-root, HEALTHCHECK-enabled image
├── docker-compose.yml       # Compose stack for production
└── models/
    ├── detector.pt          # YOLOv8n indoor plant_leaf weights
    ├── classifier.pth       # MobileNetV3-Large student weights
    └── classes.json         # Canonical class list + model version
```

---

## License

[Unlicense](https://unlicense.org) — public domain, free for any use.
