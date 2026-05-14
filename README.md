<div align="center">

# 🌿 PlantDiseases

**AI-powered houseplant disease detection — on-device camera, cloud inference, bilingual care plans.**

Two-stage ML pipeline (YOLOv8 leaf detector → MobileNetV3-Large classifier
distilled from EfficientNetV2-S) hosted behind a hardened FastAPI
service, paired with a Kotlin Android client that ships scan history,
plant care reminders and an AI plant doctor.

[Quick start](#quick-start) · [Architecture](#architecture) ·
[API](#api) · [Documentation](#documentation) · [License](#license)

</div>

---

## What it does

| | |
|---|---|
| 📷 | **Snap a leaf, get a diagnosis** — 7-class classifier including a real `not_a_plant` rejection head plus a blur gate so unfocused shots don't get fake confidence. |
| 🌍 | **Bilingual care plans** — every diagnosis ships with EN + RU description, treatment checklist, prevention tips, watering / feeding / humidity schedule, and red-flag escalation list. |
| 🪴 | **Personal garden** — Room-backed scan history, multi-plant tracking, watering / fertilising reminders via WorkManager. |
| 🤖 | **AI plant doctor** — opt-in OpenRouter chat scoped to your scan's diagnosis for follow-up questions. |
| 🔎 | **Species identification** — PlantNet integration (optional) auto-fills the plant species from the scan photo. |
| 📊 | **Honest uncertainty** — top-3 alternatives, conformal prediction sets, weak-healthy reroute, severity scoring + soft warnings (`uncertain_healthy`, `detector_classifier_mismatch`, `blurry_image`). |

---

## Architecture

```
┌──────────────────────┐         ┌──────────────────────────────────┐
│   Android (Kotlin)   │  HTTP   │   Python server (FastAPI)        │
│                      │ ──────► │                                  │
│  • CameraX capture   │         │   ┌──────────────────────────┐   │
│  • Room scan history │         │   │ Stage 1 — leaf detector  │   │
│  • Retrofit + OkHttp │  JSON   │   │ YOLOv8n single-class      │   │
│  • Material 3 UI     │ ◄────── │   └──────────┬───────────────┘   │
│  • EN / RU strings   │         │              │                   │
│  • Reminders worker  │         │   ┌──────────▼───────────────┐   │
│  • Profile + stats   │         │   │ Stage 2 — classifier     │   │
│                      │         │   │ MobileNetV3-Large (KD ←  │   │
│                      │         │   │ EfficientNetV2-S teacher)│   │
│                      │         │   │ 7 classes incl. NaP head │   │
│                      │         │   │ + MaxLogit OOD gate      │   │
│                      │         │   │ + blur (var-Laplacian)   │   │
│                      │         │   └──────────────────────────┘   │
└──────────────────────┘         └──────────────────────────────────┘
```

Why two stages — letting YOLOv8n find the leaf first means the
classifier sees a tight crop instead of a whole room. The MaxLogit OOD
gate ([`pipeline.py`](server/pipeline.py)) catches the residual case
where Stage 1 fires weakly on a non-leaf texture. The blur gate
(variance-of-Laplacian) short-circuits unfocused shots to a
`not_a_plant` verdict so the user re-takes instead of getting a fake
diagnosis.

Detailed training recipe lives in
[`server/train_notebook.ipynb`](server/train_notebook.ipynb) and the
[`MODEL_CARD.md`](server/models/MODEL_CARD.md).

---

## Classes

The classifier head is **7 trainable classes** (alphabetical, matching
`sorted(ImageFolder.classes)`):

| # | Class | EN name | RU name |
|---|-------|---------|---------|
| 0 | `bacterial_leaf_spot`        | Bacterial Leaf Spot    | Бактериальная пятнистость |
| 1 | `gray_mold_botrytis`         | Gray Mold (Botrytis)   | Серая гниль (ботритис)    |
| 2 | `healthy`                    | Healthy Plant          | Здоровое растение         |
| 3 | `leaf_spot_fungal`           | Fungal Leaf Spot       | Грибковая пятнистость     |
| 4 | `not_a_plant`                | Not a Plant            | Не растение               |
| 5 | `powdery_mildew`             | Powdery Mildew         | Мучнистая роса            |
| 6 | `viral_disease`              | Viral Disease          | Вирусное заболевание      |

The pipeline also emits four **deferred** physiological classes
(`edema`, `leaf_scorch_underwatering`, `nutrient_deficiency`,
`sunburn_or_scorch`) as feedback labels, even though the classifier
head doesn't predict them yet — the retraining loop uses them to
prioritise the next round of data collection.

Canonical list: [`server/models/classes.json`](server/models/classes.json).

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
plus `requirements.txt`, and launches FastAPI on
`http://localhost:8000`.

Model weights (`detector.{pt,onnx}`, `classifier.{pth,onnx,_int8.onnx}`
and `classes.json`) must live under `server/models/` — the server is
**fail-fast** and refuses to start without them. There is no demo
fallback.

### Android client

1. Open `android/` in Android Studio (Hedgehog 2023.1+).
2. Sync Gradle, run on a device or emulator.
3. The server URL is baked in at build time via
   `BuildConfig.API_BASE_URL`. Override at build time:

   ```bash
   ./gradlew :app:assembleDebug -PAPI_BASE_URL=http://192.168.1.42:8000/
   ```

> Cleartext HTTP is allowed only for the IPs declared in
> [`network_security_config.xml`](android/app/src/main/res/xml/network_security_config.xml).
> Terminate TLS in front of the server for production deployments — see
> [`DEPLOYMENT.md`](DEPLOYMENT.md).

### Optional integrations

| Feature | Key | Setup |
|---|---|---|
| Species identification | `PLANTNET_API_KEY` | <https://my.plantnet.org/account/doc> — see [`DEPLOYMENT.md §1.1`](DEPLOYMENT.md#11-third-party-api-keys) |
| AI plant doctor | `OPENROUTER_API_KEY` | <https://openrouter.ai> — see [`DEPLOYMENT.md §1.1`](DEPLOYMENT.md#11-third-party-api-keys) |

Without these keys the server still starts and returns graceful
"unavailable" payloads; the client renders specific UX messages so
the user knows the feature is configurable, not broken.

---

## API

| Method | Path | Description |
|---|---|---|
| `GET`  | `/api/health`              | Liveness + uptime + class count |
| `GET`  | `/api/version`             | Server version + model architecture |
| `GET`  | `/api/classes`             | Trainable classes with bilingual names |
| `GET`  | `/api/metrics`             | JSON counters (requests, errors, latency, feedback per-class accuracy) |
| `GET`  | `/api/metrics/prometheus`  | Prometheus text exposition (v0.0.4) |
| `POST` | `/api/analyze`             | Upload image → bilingual diagnosis |
| `POST` | `/api/identify`            | PlantNet species identification (graceful when unconfigured) |
| `POST` | `/api/chat`                | AI plant doctor (OpenRouter; graceful when unconfigured) |
| `POST` | `/api/care-plan`           | Watering / light / humidity / feeding schedule + treatment checklist |
| `POST` | `/api/progression`         | Compare scans → trend verdict + 5-point severity sparkline |
| `POST` | `/api/feedback`            | Was this diagnosis correct? Append-only training signal |

Response shapes and validation rules are documented inline in
[`server/main.py`](server/main.py).

---

## Project layout

```
plantdiseases/
├── android/                Kotlin app (Material 3, CameraX, Room, Retrofit)
├── server/                 FastAPI server + ML pipeline
│   ├── main.py             HTTP layer, middleware, validation
│   ├── pipeline.py         Two-stage pipeline + blur/OOD gates
│   ├── detector.py         Stage 1 — YOLOv8n
│   ├── classifier.py       Stage 2 — MobileNetV3-Large student
│   ├── ai_services.py      PlantNet, OpenRouter, care plan, progression
│   ├── feedback.py         Append-only feedback store + metrics
│   ├── conformal.py        RAPS prediction sets
│   ├── diseases_data.py    Bilingual disease database
│   ├── train_notebook.ipynb End-to-end training recipe
│   └── models/             Trained weights + classes.json + MODEL_CARD.md
├── DEPLOYMENT.md           Production deployment guide
└── README.md               You are here
```

---

## Documentation

- [`DEPLOYMENT.md`](DEPLOYMENT.md) — production configuration, Docker
  Compose, nginx + Let's Encrypt, systemd, monitoring, pre-launch
  checklist, post-deploy smoke QA.
- [`server/models/MODEL_CARD.md`](server/models/MODEL_CARD.md) —
  classifier architecture, training data, calibration, intended use,
  known limitations.
- [`server/train_notebook.ipynb`](server/train_notebook.ipynb) — full
  v2.1 training recipe, data hygiene, KD setup, calibration export.

---

## License

[Unlicense](https://unlicense.org) — public domain, free for any use.
