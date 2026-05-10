# PlantDiseases

AI-powered **houseplant** disease detection — Android app (Kotlin) talking
to a Python FastAPI server. The server runs a two-stage ML pipeline and
returns a bilingual (EN / RU) diagnosis with treatment and prevention
advice.

---

## Project status

The Android client and the server contracts are feature-complete; the
ML pipeline runs **PlantVision 2.0**. The server is **fail-fast** —
without trained `detector.{pt,onnx}` and `classifier.{pth,onnx}`
artefacts under `server/models/`, the API refuses to start.
There is no offline / demo / heuristic fallback on either side.

What changed vs v1.x and v2.0:

- **7 trainable classes (v2.1):** `bacterial_leaf_spot`,
  `gray_mold_botrytis`, `healthy`, `leaf_spot_fungal` (anthracnose +
  cercospora + alternaria), `not_a_plant`, `powdery_mildew`,
  `viral_disease` (mosaic + leaf_curl + ringspot). The four
  physiological conditions originally planned for v2.0 — `edema`,
  `leaf_scorch_underwatering`, `nutrient_deficiency`,
  `sunburn_or_scorch` — are **deferred until labelled real-world
  data is added**: public sources have very few clean indoor
  examples and proxy-labelling them collapses recall on the disease
  classes. Re-enabling them later is a config switch in
  `train_notebook.ipynb` §1, not a re-architecture. **Pests are out
  of the taxonomy entirely** (no more "possible pest causes" hint).
- **Stage 1 detector** retrained on 300–800 indoor leaf bboxes
  (Roboflow Universe houseplant slices, hand-labelled user uploads).
  PlantDoc removed from detector training — its agricultural close-
  ups bias localisation toward field shots and hurt indoor recall.
- **Stage 2 backbone:** EfficientNetV2-S
  (`tf_efficientnetv2_s.in21k_ft_in1k`) teacher distilled into
  MobileNetV3-Large (5.5 M) student. Distillation uses an **EMA
  copy** of the student (adaptive decay sized from total KD steps —
  0.9947 in the v2.1 run, formula in §5) with **α=0.5, τ=4.0** and the
  EMA weights are what ship.
- **Loss:** Logit Adjustment (Menon ICLR 2021), τ=0.5,
  label_smoothing=0.05. **Loss-side balancing only** —
  `WeightedRandomSampler` is **off** by default; stacking sampler +
  LA over-corrects rare classes (Liu et al. 2024).
- **OOD score:** **MaxLogit** (Hendrycks 2019) — robust to
  temperature scaling and label smoothing where MSP collapses.
- **PlantVillage** moved out of the train set; kept only as a frozen
  `val_studio` slice (transfer / background-bias monitor — Noyan 2022:
  49 % accuracy from 8 background pixels).
- **Calibration:** temperature scaling **plus** inductive conformal
  prediction (RAPS, Angelopoulos 2021), both fitted on the deployed
  INT8 ONNX session (not the FP32 torch model). Two dedicated
  calibration splits: `temperature_cal` (~5 %) is the **INT8
  static-quantisation calibration reader and** the temperature-scaling
  fit set; `conformal_cal` (~10 %) is reserved for RAPS, split 50/50
  fit/audit so the reported empirical coverage is honest
  out-of-sample. Both restricted to the deployment distribution
  (PlantDoc + iNat), so RAPS exchangeability holds at deploy.
- **Quantisation:** **static INT8 (QDQ)** with a 500-photo
  `temperature_cal` calibration reader (the design originally pointed
  at `conformal_cal`, but reusing the conformal split for INT8
  activation shaping would weaken its exchangeability — `temperature_cal`
  is also restricted to plantdoc + inat, same exchangeability story);
  the export step re-evaluates on `val_realworld` and refuses to ship
  if disease F1 drops by > 0.05 vs FP32 (warning at > 0.02).
- **Eval protocol — three independent val slices:** `val_indomain`
  (early stopping), `val_realworld` (primary ship gate; target
  macro-F1 ≥ 0.70, no class with recall=0, healthy-bias < 1.5),
  `val_studio` (transfer / background-bias monitor — never blocking).
  Each epoch logs disease macro-F1 + per-class recall + confusion
  matrix + healthy-bias on all three.

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
│                      │         │   │ 7 trainable classes      │   │
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
│  KD ← EfficientNetV2-S teacher  │  7 classes incl. not_a_plant
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
(α=0.5, τ=4.0; matches `train_notebook.ipynb` §5 and `MODEL_CARD.md`)
and runs in ~10-15 ms on a CPU VPS.

---

## Classes

Stage 1 (YOLOv8n) is **single-class**:

| # | Class |
|---|-------|
| 0 | `plant_leaf` |

The "is this leaf diseased?" decision belongs entirely to Stage 2.

Stage 2 (MobileNetV3-Large student @ 256 px, KD ← EfficientNetV2-S
teacher) classifies the primary leaf crop into one of **7 trainable
classes** (alphabetical, matching `sorted(ImageFolder.classes)` at
training time and `server/models/classes.json`):

| # | Class | EN name | RU name |
|---|-------|---------|---------|
| 0 | `bacterial_leaf_spot`        | Bacterial Leaf Spot          | Бактериальная пятнистость |
| 1 | `gray_mold_botrytis`         | Gray Mold (Botrytis)         | Серая гниль (ботритис)    |
| 2 | `healthy`                    | Healthy Plant                | Здоровое растение         |
| 3 | `leaf_spot_fungal`           | Fungal Leaf Spot             | Грибковая пятнистость     |
| 4 | `not_a_plant`                | Not a Plant                  | Не растение               |
| 5 | `powdery_mildew`             | Powdery Mildew               | Мучнистая роса            |
| 6 | `viral_disease`              | Viral Disease                | Вирусное заболевание      |

**Deferred to v2.2 (not in the trained head):** `edema`,
`leaf_scorch_underwatering`, `nutrient_deficiency`, `sunburn_or_scorch`.
Public sources have very few clean indoor examples; including them
with proxy labels collapsed recall on the 7 classes we already have
data for. Bilingual descriptions for these are kept in
`diseases_data.py` so re-enabling them later is a config flip in
`train_notebook.ipynb` §1, not a re-architecture.

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

The script creates a virtual environment, installs CPU-only PyTorch
plus the rest of `requirements.txt`, and launches FastAPI on
`http://localhost:8000`. Trained weights (`detector.{pt,onnx}` and
`classifier.{pth,onnx}` plus `classes.json`) must be present under
`server/models/`; the script refuses to start without them — there
is no demo fallback.

### Android app

1. Open `android/` in Android Studio (Hedgehog 2023.1 or newer).
2. Sync Gradle → run on a device or emulator.
3. The server URL is baked in at build time via `BuildConfig.API_BASE_URL`.
   Override it from the command line, e.g.
   `./gradlew :app:assembleDebug -PAPI_BASE_URL=http://192.168.1.42:8000/`.

> Cleartext HTTP is allowed only for the IPs listed in
> `network_security_config.xml`. For production, terminate TLS on the
> server (see [`DEPLOYMENT.md`](DEPLOYMENT.md)).

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
   AdamW + cosine + 1-epoch warmup, 256 px batch=96 bf16-mixed,
   head-only 3 epochs (lr=3e-3) → unfreeze last 2 stages 15 epochs
   (head lr=5e-4 / backbone lr=5e-5), Logit Adjustment loss (τ=0.5,
   matches `MODEL_CARD.md`), indoor-targeted augs (no MixUp/CutMix —
   they were dropped after they collapsed `gray_mold_botrytis` recall).
5. **Knowledge distillation into MobileNetV3-Large student (10–15 min)**
   — α·KL·τ² + (1−α)·CE, α=0.5, τ=4.0, ~10 epochs (matches the actual
   notebook constants in §5).
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
pipeline prefers `classifier_int8.onnx` over `classifier.onnx` over
`classifier.pth`, and `detector.onnx` over `detector.pt`.

---

## Server API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | Liveness + uptime + class count |
| `GET` | `/api/version` | Server version + model architecture |
| `GET` | `/api/classes` | 7 trainable classes with bilingual names |
| `GET` | `/api/metrics` | Counters for requests, errors, latency |
| `GET` | `/api/metrics/prometheus` | Prometheus exposition format |
| `POST` | `/api/analyze` | Upload image → get diagnosis |
| `POST` | `/api/identify` | PlantNet species identification (graceful when unconfigured) |
| `POST` | `/api/chat` | AI Plant Doctor (OpenRouter, default model `openai/gpt-oss-120b`; graceful when unconfigured) |
| `POST` | `/api/care-plan` | Watering / fertilising / humidity schedule |
| `POST` | `/api/progression` | Compare two scans → trend verdict |

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
│   │   └── GuideDataProvider.kt     # Bundled care guide content
│   ├── ui/
│   │   ├── camera/                  # Camera scanner
│   │   ├── gallery/                 # Scan history grid
│   │   ├── guide/                   # Care articles + detail
│   │   ├── profile/                 # Stats & settings
│   │   ├── analysis/                # Loading screen
│   │   └── result/                  # Diagnosis result
│   └── util/
│       ├── LocaleHelper.kt          # Language switching
│       ├── ServerConfig.kt          # Build-time API base URL
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
├── detector.py              # Stage 1: YOLOv8n single-class plant_leaf
├── classifier.py            # Stage 2: MobileNetV3-Large (KD ← V2-S)
├── diseases_data.py         # Bilingual houseplant disease database
├── train_notebook.ipynb     # End-to-end v2.1 training recipe
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
