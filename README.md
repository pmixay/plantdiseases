<div align="center">

# 🌿 PlantDiseases

**AI-powered plant disease detection for houseplants and garden ornamentals — on-device camera, cloud inference, bilingual care plans.**

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
| 📊 | **Honest uncertainty** — probability-consistent top-3 alternatives, conformal prediction sets, OOD abstention, and soft warnings (`uncertain_healthy`, `disease_suspected:*`, `low_confidence`, `blurry_image`). |

---

## Architecture

```
┌──────────────────────┐         ┌──────────────────────────────────┐
│   Android (Kotlin)   │  HTTP   │   Python server (FastAPI)        │
│                      │ ──────► │                                  │
│  • CameraX capture   │         │   ┌──────────────────────────┐   │
│  • Room scan history │         │   │ Stage 1 — leaf detector  │   │
│  • Ktor + OkHttp     │  JSON   │   │ YOLOv8n single-class     │   │
│  • Material 3 UI     │ ◄────── │   └──────────┬───────────────┘   │
│  • EN / RU strings   │         │              │                   │
│  • Reminders worker  │         │   ┌──────────▼───────────────┐   │
│  • Profile + stats   │         │   │ Stage 2 — classifier     │   │
│                      │         │   │ frozen DINOv2 ViT-S/14   │   │
│                      │         │   │ + LoRA + MLP head        │   │
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
confidence **and** a classifier-side OOD signal both agree **and** the
classifier's own `not_a_plant` head does not veto it (a near-zero
`P(not_a_plant)` means the model is sure this *is* a plant, and an
OOD score that fires anyway is measuring *unfamiliar*, not *not a
plant*). A hit emits explicit `abstained=true`, `abstention_reason="ood"`
(plus the legacy `nap_ood_gated` warning) and leaves the calibrated class
probabilities untouched; only the trained head's natural argmax is reported as
`not_a_plant`. The secondary OOD overlay is active only when the bundle declares
a matching full-served-pipeline calibration contract (crop/whole aggregation,
detector floor and vote mode); historical single-image thresholds stay advisory.
The OOD signal is an embedding-space k-NN distance on the DINOv2 CLS vector
([`ood.py`](server/ood.py)) when the bundle ships a memory bank, falling back
to the legacy MaxLogit floor otherwise. The blur gate (variance-of-Laplacian)
returns an `image_quality` abstention with empty classifier diagnostics; it does
not invent `P(not_a_plant)=1` when the classifier never ran.

Postprocessing likewise never replaces the probability argmax with a
lower-probability disease. Borderline healthy results keep `healthy` while
surfacing the competing class through `disease_suspected:*` and `top_k`.
`severity` is currently a conservative `low` compatibility value: leaf boxes
localise leaves, not lesions, and no trained severity target exists yet.

Detailed training recipe lives in
[`server/train_notebook.ipynb`](server/train_notebook.ipynb) and the
[`MODEL_CARD.md`](server/models/MODEL_CARD.md).

**Model families.** Three capacity tiers (declared in
`classes.json -> model_family`, surfaced by `/api/capabilities`; the client picks
one per scan and `/api/capabilities` reports which the server offers):

| Family | Backbone | Input | Where it runs |
|---|---|---|---|
| `aqua` | MobileNetV3-Small (distilled from `gamma`) → TFLite **float32** | 224 | **on-device, fully offline** (downloaded from the server, cached) |
| **`gamma`** | **frozen DINOv2 ViT-S/14 + (optional) LoRA + MLP head** | **224** | **server — intended production tier, balance of cost/accuracy** |
| `frontier` | frozen DINOv2 ViT-B/14 (~86M) + (optional) LoRA + MLP head | 224 | server — high-capacity; **inference gated OFF by default** (needs a GPU host), the app shows it "unavailable" until enabled |

`gamma` and `frontier` share one frozen-DINOv2 recipe (only the backbone
differs — both run at 224); `aqua` is a knowledge-distilled mobile student
(`train_notebook.ipynb` §5) exported to TFLite.

Aqua enforces blur/quality rejection and retains its trained `not_a_plant`
head, but it has no calibrated detector/embedding OOD overlay. The app does not
invent one from Gamma thresholds; that remains a v9 served-path data task.

Aqua has the trained `not_a_plant` class and blocks photos that fail the
existing on-device blur QA before inference. It does **not** ship the server's
detector + embedding/MaxLogit calibrated OOD overlay; an Aqua class argmax must
not be described as OOD evidence, and low confidence is not an OOD detector.

Aqua updates are activated only after the model/sidecar/bundle digests and the
staged TFLite tensor contract agree (one FLOAT32 NHWC input at the declared
size and one FLOAT32 logits head matching `class_names`); this inspection does
not run inference. A successfully decoded `available:false` manifest is an
authoritative withdrawal and clears the active pointer while retaining the
immutable bytes for rollback. Network errors and non-2xx manifest responses
leave the last-known-good bundle active.

> **Kaggle is required for the default production recipe.** Individual fetch
> attempts report failures cleanly, but strict source floors stop the notebook
> before training if the approved houseplant/ornamental supplement is absent or
> truncated. Only an explicitly degraded, non-production diagnostic run may
> disable that gate. Accepted credential formats are documented at §1's
> `kaggle_install_api_config()` in
> [`server/train_notebook.ipynb`](server/train_notebook.ipynb). Kaggle handles
> resolve to the latest revision visible at download time. The resolved Kaggle
> revision and full corpus digests are bound into each run manifest, but the
> inputs are intentionally not predetermined before a run. An authenticated
> check on 2026-08-23 found the private supplement still at v8; it fails the
> strict v9 manifest contract. A compliant v9 published at the same handle will
> be picked up automatically.

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

The pipeline also accepts **out-of-head** corrections on `/api/feedback`
(`edema`, `sunburn_or_scorch`, `other`, and `is_a_plant` — the one-tap
"the reject was wrong" correction on a `not_a_plant` / blur verdict) even
though the head doesn't predict them. They are stored with `out_of_head=1`
so the retraining loop can prioritise the next round of data collection.

Canonical recipe list: [`DEFAULT_CLASS_NAMES` in
`server/classifier.py`](server/classifier.py). Strict API startup nevertheless
requires the ordered, manifest-bound `classes.json`; defaults are only for
isolated/non-strict tests and cannot rescue a deployment.

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

The script creates a virtualenv, installs the inference-server requirements,
and serves FastAPI on `0.0.0.0:8000` — override with `./start.sh --port N` or an
inherited `PORT` (`start.bat` takes `--port` but ignores `PORT`).

Model weights are **not committed** and **not auto-downloaded**. The server is
**fail-fast** and accepts only one complete notebook-produced tree in
`server/models/`, with `bundle_manifest.json`, the exact selected classifier,
detector, taxonomy, calibration, conformal, data-lineage, and run-lineage files.
It verifies every bound size/SHA-256 and the serving contract before loading a
graph; missing, mixed, changed, unbound, or loose legacy artefacts abort startup.
Extract the whole archive into a fresh directory—do not flatten or combine runs.
The full contract is [`DEPLOYMENT.md`](DEPLOYMENT.md) §2.

Training, calibration fitting, conversion, and model export are **not server
features**. They exist only inside the self-contained Colab notebook
[`server/train_notebook.ipynb`](server/train_notebook.ipynb), which refuses to
train on CPU. Server and Android code are load/verify/inference-only.

### Android client

1. Open `android/` in a current Android Studio — the build needs AGP 9.1.1 /
   Gradle 9.5 / JDK 17, `compileSdk 37`, `minSdk 31`. Sync Gradle, run on a
   device or emulator.
2. A fresh checkout's debug and local release builds enable real Yandex OAuth
   and use the live beta application/account URLs under `/plantdisease-test/`.
   No endpoint arguments are required. Local mock development explicitly sets
   `ACCOUNT_REAL_OAUTH=false`, `APP_API_BASE_URL`, and `ACCOUNT_API_BASE_URL`.

Release signing, FCM push, and TLS are covered in
[`DEPLOYMENT.md`](DEPLOYMENT.md).
Production loads the optional species-ID and AI Doctor integrations from
`PLANTNET_API_KEY_FILE` and `OPENROUTER_API_KEY_FILE`; without them, the server
runs normally and reports the corresponding feature as unavailable.
Direct keys require an explicit `ENVIRONMENT=test` or
`ENVIRONMENT=development`; an unset, staging, or production environment fails
closed and accepts only the corresponding owner-only secret file.

## API

All routes are mounted under `/api`. A curated highlights table follows;
[`server/openapi.json`](server/openapi.json) is the authoritative, complete list.

**Core / meta**

| Method | Path | Description |
|---|---|---|
| `GET`  | `/api/health`              | Liveness + uptime + class count |
| `GET`  | `/api/capabilities`        | Per-family availability (aqua / gamma / frontier) **plus** consolidated server version, model architecture, and bilingual class metadata (this folds in the former `/api/version` & `/api/classes`) |
| `GET`  | `/api/metrics`             | JSON counters (requests, errors, latency) |
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
| `GET`  | `/api/species-profile`     | Flowering pattern + native range for a species (AI, best-effort) |
| `GET`  | `/api/popular`             | Popular-plants bundle (region-aware, cached, stale-while-revalidate) |
| `GET`  | `/api/plant-photo`         | SSRF-guarded Wikimedia/Wikipedia image proxy (`?u=` URL or `?name=` resolver) |

**Community**

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/community/scan`      | Share a scan into the anonymized corpus (opt-in, coarse-rounded coords) |
| `GET`  | `/api/community/nearby`    | Shared-scans feed (`scope=nearby` ~30 km / `country` / `worldwide`; coords stripped, distance only) |
| `GET`  | `/api/community/map` · `/api/community/heatmap` | Map markers (rounded coords) and k-anonymized density grid |
| `GET`  | `/api/community/by-species` | Recent shared scans of a given species |
| `POST` | `/api/community/opinion` · `/api/community/request-opinion` | Cast / request a second opinion on a shared scan |
| `GET`  | `/api/community/opinions/{scan_id}` | Opinions + consensus + request status for a shared scan |
| `POST` | `/api/community/view` · `/api/community/scan-counts` | Record a view / batch-fetch opinion+view counts |
| `POST` | `/api/community/safety/report` · `/api/community/safety/block` | Report content / mutually block its verified author (account required) |
| `GET` · `DELETE` | `/api/community/safety/blocks[/{block_id}]` | List or remove caller-owned blocks by opaque ID |
| `GET`  | `/api/community/thumb/{filename}` | Community scan thumbnail |
| `POST` | `/api/push/register`       | Register an FCM token for opinion notifications (no-op without FCM creds) |

Response shapes and validation rules are documented inline in
[`server/main.py`](server/main.py).
Content criteria and the private operator workflow are documented in
[`docs/COMMUNITY_RULES_AND_MODERATION.md`](docs/COMMUNITY_RULES_AND_MODERATION.md).

---

## Project layout

```
plantdiseases/
├── android/                Kotlin app (Material 3, CameraX, Room, Ktor, Hilt DI)
├── account-server/         FastAPI account service (sign-in, sessions, deletion, PRO entitlements) + PostgreSQL
├── server/                 FastAPI server + ML pipeline
│   ├── main.py             HTTP layer, middleware, validation
│   ├── pipeline.py         Two-stage pipeline + blur/OOD gates
│   ├── detector.py         Stage 1 — YOLOv8n
│   ├── classifier.py       Stage 2 — frozen DINOv2 ViT-S/14 + head
│   ├── ai_services.py      PlantNet, OpenRouter, care plan, progression
│   ├── training_store.py   Consent-gated, de-identified training samples + feedback receipts
│   ├── account_auth.py     Verifies account tokens; account_entitlements.py consumes PRO quotas
│   ├── conformal.py        RAPS prediction sets
│   ├── diseases_data.py    Bilingual disease database
│   ├── train_notebook.ipynb End-to-end training recipe
│   └── models/             MODEL_CARD.md + .gitkeep (weights provisioned out of band)
├── deploy/                 Compose, nginx and operator scripts for the account stack
├── docs/                   ROADMAP.md (live model status), ACCOUNTS_PRODUCTION_PLAN.md, DATASET_LABELING_PLAN.md
├── .github/workflows/      ci.yml — push-only CI (ruff + pytest for server and account
│                           service; ktlint + detekt + unit tests + CI-only APK builds);
│                           android-release.yml — manual protected signing
├── DEPLOYMENT.md           Production deployment guide
├── CLAUDE.md               Working notes for agents in this repo
└── README.md               You are here
```

---

## Documentation

- [`DEPLOYMENT.md`](DEPLOYMENT.md) — model bundle, production config, nginx +
  Let's Encrypt, the systemd unit, monitoring, go-live checklist, and Android
  release signing / FCM.
- [`server/models/MODEL_CARD.md`](server/models/MODEL_CARD.md) — pipeline,
  classifier architecture, class set, validation contract, calibration & OOD,
  known limitations. Deliberately **version-agnostic**: per-run numbers ship as
  `model_card.json` inside the bundle, and the live model status is the roadmap's
  model track.
- [`server/train_notebook.ipynb`](server/train_notebook.ipynb) — full training
  recipe (group-aware splits + leakage guard, the three families, Aqua KD → TFLite,
  calibration + ONNX export).
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — current model status + what's next.
- [`docs/ACCOUNTS_PRODUCTION_PLAN.md`](docs/ACCOUNTS_PRODUCTION_PLAN.md) — accounts decisions,
  release gates and the path to RuStore; [`account-server/README.md`](account-server/README.md)
  is the service's contract.
- [`docs/DATASET_LABELING_PLAN.md`](docs/DATASET_LABELING_PLAN.md) — the offline
  data-collection + judging loop.

---

## License

[Unlicense](https://unlicense.org) — public domain, free for any use.

The Unlicense covers first-party code. Third-party model weights (DINOv2 —
Apache-2.0, Ultralytics YOLOv8 — AGPL-3.0, used at training/export time only),
datasets (PlantVillage, PlantDoc, iNaturalist, COCO, Open Images), and runtime
libraries remain under their own licenses.
