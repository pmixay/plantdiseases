<div align="center">

[English](README.md) | [Русский](README.ru.md)

# 🌿 PlantDiseases

**AI-powered houseplant disease detection — on-device camera, cloud inference, bilingual care plans.**

[![CI](https://github.com/pmixay/plantdiseases/actions/workflows/ci.yml/badge.svg)](https://github.com/pmixay/plantdiseases/actions/workflows/ci.yml)

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
| 📷 | **Snap a leaf, get a diagnosis** — 9-class classifier including a real `not_a_plant` rejection head plus a blur gate so unfocused shots don't get fake confidence. |
| 📴 | **Offline scanning (Aqua)** — pick the on-device `aqua` model on the scan screen for a fully offline diagnosis (TFLite, no server round-trip); the model is downloaded from the server and cached. |
| 🌍 | **Bilingual care plans** — every diagnosis ships with EN + RU description, treatment checklist, prevention tips, watering / feeding / humidity schedule, and red-flag escalation list. |
| 🪴 | **Personal garden** — Room-backed scan history, multi-plant tracking, watering / fertilising reminders via WorkManager. |
| 🤖 | **AI plant doctor** — opt-in OpenRouter chat scoped to your scan's diagnosis for follow-up questions. |
| 🔎 | **Species identification** — PlantNet integration (optional) auto-fills the plant species from the scan photo. |
| 📊 | **Honest uncertainty** — top-3 alternatives, conformal prediction sets, weak-healthy reroute, severity scoring + soft warnings (`uncertain_healthy`, `low_confidence`, `blurry_image`). |

---

## Architecture

```
┌──────────────────────┐         ┌──────────────────────────────────┐
│   Android (Kotlin)   │  HTTP   │   Python server (FastAPI)        │
│                      │ ──────► │                                  │
│  • CameraX capture   │         │   ┌──────────────────────────┐   │
│  • Room scan history │         │   │ Stage 1 — leaf detector  │   │
│  • Ktor + OkHttp     │  JSON   │   │ YOLOv8n single-class      │   │
│  • Material 3 UI     │ ◄────── │   └──────────┬───────────────┘   │
│  • EN / RU strings   │         │              │                   │
│  • Reminders worker  │         │   ┌──────────▼───────────────┐   │
│  • Profile + stats   │         │   │ Stage 2 — classifier     │   │
│                      │         │   │ frozen DINOv2 ViT-S/14    │   │
│                      │         │   │ + LoRA + MLP head         │   │
│                      │         │   │ 9 classes incl. NaP head │   │
│                      │         │   │ + embedding-kNN OOD gate │   │
│                      │         │   │ + blur (var-Laplacian)   │   │
│                      │         │   └──────────────────────────┘   │
└──────────────────────┘         └──────────────────────────────────┘
```

Why two stages — letting YOLOv8n find the leaf first means the
classifier sees a tight crop instead of a whole room. The OOD AND-gate
([`pipeline.py`](server/pipeline.py)) catches the residual case where
Stage 1 fires weakly on a non-leaf texture: it fires only when detector
confidence **and** a classifier-side OOD signal both agree. The OOD
signal is an embedding-space k-NN distance on the DINOv2 CLS vector
([`ood.py`](server/ood.py)) when the bundle ships a memory bank, falling
back to the legacy MaxLogit floor otherwise. The blur gate
(variance-of-Laplacian) short-circuits unfocused shots to a
`not_a_plant` verdict so the user re-takes instead of getting a fake
diagnosis.

Detailed training recipe lives in
[`server/train_notebook.ipynb`](server/train_notebook.ipynb) and the
[`MODEL_CARD.md`](server/models/MODEL_CARD.md).

**Model families.** Three capacity tiers (declared in
`classes.json -> model_family`, surfaced by `/api/capabilities`; the client picks
one per scan and `/api/capabilities` reports which the server offers):

| Family | Backbone | Input | Where it runs |
|---|---|---|---|
| `aqua` | MobileNetV3-Small (distilled from `gamma`) → TFLite INT8 | 224 | **on-device, fully offline** (downloaded from the server, cached) |
| **`gamma`** | **frozen DINOv2 ViT-S/14 + MLP head** | **224** | **server — current production, balance of cost/accuracy** |
| `frontier` | frozen DINOv2 ViT-B/14 (~86M) + MLP head | 224 | server — high-capacity; **inference gated OFF by default** (needs a GPU host), the app shows it "unavailable" until enabled |

`gamma` and `frontier` share one frozen-DINOv2 recipe (only the backbone +
input size differ); `aqua` is a knowledge-distilled mobile student
(`train_notebook.ipynb` §5) exported to TFLite.

> **Optional data supplement:** §2 can pull real indoor-houseplant /
> ornamental disease sets from Kaggle (incl. money-plant / pothos disease
> imagery). It is **fail-soft** — provide credentials to
> enable it, otherwise the notebook skips it cleanly. Credentials can be
> the **new-format API token** (a Colab secret or env var
> `KAGGLE_API_TOKEN`, or `~/.kaggle/access_token`) or a legacy
> `kaggle.json`; the §1 helper `kaggle_install_api_config()` lets you
> upload either file in Colab. The source is **train-only** and drops any
> folder it can't confidently map, and the run prints every discovered
> folder so you can refine the label map.

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

The script creates a virtualenv, installs CPU-only PyTorch + `requirements.txt`,
and serves FastAPI on `http://localhost:8000`.

Model weights are **not committed** and **not auto-downloaded** — drop the trained
bundle (`detector.{pt,onnx}`, `classifier.onnx`, `classes.json`, calibration JSON)
into `server/models/` first; the server is **fail-fast** without them, and
`train_notebook.ipynb` §6b tars them for you. Production setup (Docker, nginx/TLS,
systemd, API keys, Android signing/FCM) lives in [`DEPLOYMENT.md`](DEPLOYMENT.md).

### Android client

1. Open `android/` in Android Studio (Hedgehog 2023.1+), sync Gradle, run on a
   device or emulator.
2. The server URL is baked in via `BuildConfig.API_BASE_URL`; override per build:
   `./gradlew :app:assembleDebug -PAPI_BASE_URL=http://192.168.1.42:8000/`.

Release signing, FCM push, TLS, and the optional integrations — `PLANTNET_API_KEY`
(species ID) and `OPENROUTER_API_KEY` (AI doctor); the server runs fine without them
and reports each as "unavailable" — are all covered in
[`DEPLOYMENT.md`](DEPLOYMENT.md).

---

## API

All routes are mounted under `/api`. A curated highlights table follows;
[`server/openapi.json`](server/openapi.json) is the authoritative, complete list.

**Core / meta**

| Method | Path | Description |
|---|---|---|
| `GET`  | `/api/health`              | Liveness + uptime + class count |
| `GET`  | `/api/capabilities`        | Per-family availability (aqua / gamma / frontier) **plus** consolidated server version, model architecture, and bilingual class metadata (this folds in the former `/api/version` & `/api/classes`) |
| `GET`  | `/api/metrics`             | JSON counters (requests, errors, latency, feedback per-class accuracy) |
| `GET`  | `/api/metrics/prometheus`  | Prometheus text exposition (v0.0.4) |

**Diagnosis**

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/analyze`             | Upload image → bilingual diagnosis (`?family=aqua\|gamma\|frontier`, default `gamma`) |
| `POST` | `/api/symptom-diagnose`    | No-photo symptom triage (fallback when the camera/ML pipeline can't be used) |
| `POST` | `/api/identify`            | PlantNet species identification (graceful when unconfigured) |
| `POST` | `/api/chat`                | AI plant doctor (OpenRouter; graceful when unconfigured) |
| `POST` | `/api/care-plan`           | Watering / light / humidity / feeding schedule + treatment checklist |
| `POST` | `/api/progression`         | Compare scans → trend verdict + 5-point severity sparkline |
| `POST` | `/api/feedback` · `/api/feedback/image` | Was this diagnosis correct? Append-only training signal (optionally with the image) |

**Content & media**

| Method | Path | Description |
|---|---|---|
| `GET`  | `/api/guide/photo/{key}` | Disease/pest example photos (served by the server, cached by the app) |
| `GET`  | `/api/aqua/manifest` · `/api/aqua/model` | On-device Aqua TFLite model manifest + bytes (the app downloads/updates it) |
| `GET`  | `/api/species-info`        | Localized Wikipedia summary + lead image for a scientific name |
| `GET`  | `/api/species-diseases`    | Common indoor conditions a species is affected by (AI, best-effort) |
| `GET`  | `/api/popular`             | Popular-plants bundle (region-aware, cached, stale-while-revalidate) |
| `GET`  | `/api/plant-photo`         | SSRF-guarded Wikimedia/Wikipedia image proxy (`?u=` URL or `?name=` resolver) |

**Community**

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/community/scan`      | Share a scan into the anonymized corpus (opt-in, coarse-rounded coords) |
| `GET`  | `/api/community/nearby`    | Nearby-scans feed (≤30 km or country scope; coords stripped, distance only) |
| `GET`  | `/api/community/map` · `/api/community/heatmap` | Map markers (rounded coords) and k-anonymized density grid |
| `GET`  | `/api/community/by-species` | Recent shared scans of a given species |
| `POST` | `/api/community/opinion` · `/api/community/request-opinion` | Cast / request a second opinion on a shared scan |
| `GET`  | `/api/community/opinions/{scan_id}` | Opinions + consensus + request status for a shared scan |
| `POST` | `/api/community/view` · `/api/community/scan-counts` | Record a view / batch-fetch opinion+view counts |
| `GET`  | `/api/community/thumb/{filename}` | Community scan thumbnail |
| `POST` | `/api/push/register`       | Register an FCM token for opinion notifications (no-op without FCM creds) |

**Privacy / data rights**

| Method | Path | Description |
|---|---|---|
| `DELETE` | `/api/community/user/{user_pseudo_id}`  | Delete the caller's shared community contributions |

Response shapes and validation rules are documented inline in
[`server/main.py`](server/main.py).

---

## Project layout

```
plantdiseases/
├── android/                Kotlin app (Material 3, CameraX, Room, Ktor, Koin DI)
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

- [`DEPLOYMENT.md`](DEPLOYMENT.md) — production config, Docker Compose, nginx +
  Let's Encrypt, systemd, monitoring, pre-launch checklist, smoke QA, and Android
  release signing / FCM.
- [`server/models/MODEL_CARD.md`](server/models/MODEL_CARD.md) — classifier
  architecture, training data, calibration, intended use, known limitations.
- [`server/train_notebook.ipynb`](server/train_notebook.ipynb) — full training
  recipe (group-aware splits + leakage guard, the three families, Aqua KD → TFLite,
  calibration + ONNX export).
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — current model status + what's next.
- [`docs/DATASET_LABELING_PLAN.md`](docs/DATASET_LABELING_PLAN.md) — the offline
  data-collection + judging loop.

---

## License

[Unlicense](https://unlicense.org) — public domain, free for any use.

The Unlicense covers first-party code. Third-party model weights (DINOv2,
Ultralytics YOLOv8), datasets (PlantVillage, PlantDoc, iNaturalist, COCO,
Open Images), and runtime libraries remain under their own licenses — see
[`NOTICE`](NOTICE) and [`THIRD_PARTY.md`](THIRD_PARTY.md).
