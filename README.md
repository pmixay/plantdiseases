<div align="center">

# 🌿 PlantDiseases

**AI-powered houseplant disease detection — on-device camera, cloud inference, bilingual care plans.**

Two-stage ML pipeline (YOLOv8 leaf detector → frozen DINOv2 ViT-S/14
backbone + LoRA + MLP head classifier) hosted behind a hardened FastAPI
service, paired with a Kotlin Android client that ships scan history,
plant care reminders and an AI plant doctor.

[Quick start](#quick-start) · [Architecture](#architecture) ·
[API](#api) · [Documentation](#documentation) · [License](#license)

</div>

---

## What it does

| | |
|---|---|
| 📷 | **Snap a leaf, get a diagnosis** — 8-class classifier including a real `not_a_plant` rejection head plus a blur gate so unfocused shots don't get fake confidence. |
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
│                      │         │   │ frozen DINOv2 ViT-S/14    │   │
│                      │         │   │ + LoRA + MLP head         │   │
│                      │         │   │ 8 classes incl. NaP head │   │
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

> **Optional data supplement:** §2 can pull a real indoor-houseplant
> disease set from Kaggle. It is **fail-soft** — set `KAGGLE_USERNAME`
> and `KAGGLE_KEY` (or drop `~/.kaggle/kaggle.json`) before the run to
> enable it, otherwise the notebook skips it cleanly. The source is
> **train-only** and drops any folder it can't confidently map, and the
> run prints every discovered folder so you can refine the label map.

---

## Classes

The classifier head is **9 trainable classes** (alphabetical, matching
`sorted(ImageFolder.classes)`). `leaf_spot` merges bacterial and fungal
leaf spot, and `pests` merges mealybugs + scale — in each case the two
are not reliably separable from a phone photo:

| # | Class | EN name | RU name |
|---|-------|---------|---------|
| 0 | `gray_mold_botrytis`         | Gray Mold (Botrytis)          | Серая гниль (ботритис)    |
| 1 | `healthy`                    | Healthy Plant                 | Здоровое растение         |
| 2 | `leaf_scorch_underwatering`  | Leaf Scorch (Under-watering)  | Краевой ожог (недолив)    |
| 3 | `leaf_spot`                  | Leaf Spot (fungal + bacterial)| Пятнистость листьев       |
| 4 | `not_a_plant`                | Not a Plant                   | Не растение               |
| 5 | `nutrient_deficiency`        | Nutrient Deficiency           | Дефицит питания           |
| 6 | `pests`                      | Sap-sucking Pests (mealybug + scale) | Сосущие вредители (червецы + щитовки) |
| 7 | `powdery_mildew`             | Powdery Mildew                | Мучнистая роса            |
| 8 | `viral_disease`              | Viral Disease                 | Вирусное заболевание      |

The pipeline also accepts **deferred** labels on `/api/feedback`
(`edema`, `sunburn_or_scorch`) even though the classifier head doesn't
predict them yet — the retraining loop uses them to prioritise the next
round of data collection.

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

Model weights + calibration artefacts are **not committed** and are
**not auto-downloaded**. Provision them out of band: drop
`detector.{pt,onnx}`, `classifier.onnx` (+ optional `classifier_int8.onnx`),
`classes.json`, and the calibration files (`temperature.json`,
`conformal.json`, `model_card.json`) into `server/models/` before
starting. `train_notebook.ipynb` §6b tars these for you to copy across.
The server is **fail-fast** — it refuses to start if a required artefact
is missing. There is no demo fallback.

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
│   ├── classifier.py       Stage 2 — frozen DINOv2 ViT-S/14 + head
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
  v2.2 training recipe, data hygiene, KD setup, calibration export.

---

## License

[Unlicense](https://unlicense.org) — public domain, free for any use.
