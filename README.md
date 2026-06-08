<div align="center">

[English](README.md) | [Русский](README.ru.md)

# 🌿 PlantDiseases

**AI-powered houseplant disease detection — on-device camera, cloud inference, bilingual care plans.**

Two-stage ML pipeline (YOLOv8 leaf detector → frozen DINOv2 ViT-S/14
backbone + LoRA + MLP head classifier) hosted behind a hardened FastAPI
service, paired with a Kotlin Android client that ships scan history,
plant care reminders and an AI plant doctor.

### 🌐 [**Explore the project website →**](website/index.html)

An interactive, bilingual (RU / EN) showcase of the app's features, the ML
pipeline and live benchmark charts. Run it locally with
`cd website && python3 -m http.server 8080`, or publish the `website/` folder
to GitHub Pages.

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
│                      │         │   │ 9 classes incl. NaP head │   │
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

**Model families.** Three capacity tiers (declared in
`classes.json -> model_family`, surfaced by `/api/version`; the client picks
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
> `kaggle.json`; the §1 helper `kaggle_upload_credentials()` lets you
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
| `GET`  | `/api/version`             | Server version + model architecture + family |
| `GET`  | `/api/classes`             | Trainable classes with bilingual names |
| `GET`  | `/api/capabilities`        | Per-family availability (aqua / gamma / frontier) for the scan-screen picker |
| `GET`  | `/api/metrics`             | JSON counters (requests, errors, latency, feedback per-class accuracy) |
| `GET`  | `/api/metrics/prometheus`  | Prometheus text exposition (v0.0.4) |
| `POST` | `/api/analyze`             | Upload image → bilingual diagnosis (`?family=aqua\|gamma\|frontier`, default `gamma`) |
| `GET`  | `/api/guide/manifest` · `/api/guide/photo/{key}` | Disease/pest example photos (served by the server, cached by the app) |
| `GET`  | `/api/aqua/manifest` · `/api/aqua/model` | On-device Aqua TFLite model manifest + bytes (the app downloads/updates it) |
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
  training recipe (group-aware splits + leakage guard, the three model
  families, Aqua knowledge-distillation → TFLite, calibration + ONNX export).

---

## License

[Unlicense](https://unlicense.org) — public domain, free for any use.
