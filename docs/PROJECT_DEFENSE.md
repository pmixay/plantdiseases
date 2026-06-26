# PlantDiseases / PlantVision — Project Defense Guide

A one-stop crib sheet for presenting and **defending** this project: what it is,
how it works end to end, why each library and design choice was made, the app
code logic, and the ML fundamentals behind it. Read top to bottom once; keep the
**"Likely questions"** section open during the defense.

> The app/repo is **PlantDiseases**; the ML pipeline is branded **PlantVision**.
> Source of truth is always the code — this doc summarizes it.

---

## 1. The one-paragraph pitch

> *PlantDiseases is an AI app that diagnoses houseplant leaf diseases from a phone
> photo.* The phone captures a leaf, a hardened Python service runs a **two-stage
> ML pipeline** (find the leaf → classify the disease) and returns a bilingual
> (EN/RU) diagnosis with a care plan. The system is built to **say "I'm not sure"
> honestly** instead of guessing: it rejects non-plants, blurry shots, and unknown
> inputs, and calibrates its confidence. There is also a fully **offline** on-device
> model for when there's no network.

The hard part is not "classify a leaf" — that's a solved undergraduate exercise.
The hard part, and the thing this project is actually about, is **trustworthy
predictions on messy real-world phone photos**: rejecting garbage, not being
overconfident, and proving improvements are real rather than noise.

---

## 2. System architecture (three independent parts)

| Part | Stack | Role |
|---|---|---|
| `server/` | Python 3.11, FastAPI, ONNX Runtime | The brains: ML inference + REST API |
| `android/` | Kotlin, Material 3, CameraX, Room, Ktor, Koin | The phone client |
| `website/` | Static HTML/CSS/JS | Marketing + benchmark showcase (no build step) |

```
 Phone (Kotlin)                          Server (FastAPI)
 ┌───────────────────┐   HTTP/JSON   ┌──────────────────────────────────────┐
 │ CameraX capture   │ ───────────► │ Stage 1: YOLOv8n leaf detector (crop)  │
 │ Room scan history │              │ Stage 2: DINOv2 ViT-S/14 + LoRA + MLP   │
 │ Ktor HTTP client  │ ◄─────────── │   → 9 classes incl. a not_a_plant head │
 │ Material 3 UI     │   diagnosis  │   + blur gate + OOD gate + calibration │
 │ EN / RU strings   │              └──────────────────────────────────────┘
 └───────────────────┘
```

Why split into three? **Separation of concerns and independent release cycles.**
The ML can be retrained and redeployed without touching the app; the app ships on
its own Play Store cadence; the website is a static showcase that can't break the
product. Each has its own toolchain and CI job.

---

## 3. The ML pipeline — how a photo becomes a diagnosis

This is the heart of the defense. Walk through it in order.

### Stage 1 — Leaf detector (`server/detector.py`)
- **Model:** YOLOv8n (the "nano" / smallest YOLOv8), single-class ("leaf").
- **Job:** find the leaf in the photo and crop tightly to it.
- **Why:** a phone photo contains a whole room. If you feed that straight to a
  classifier, it classifies wallpaper and carpet. Detecting the leaf first means
  Stage 2 sees a clean, centered subject — the same trick every good two-stage
  vision system uses. YOLOv8n is chosen because it's tiny and fast (real-time on
  CPU) and we only need a rough box, not pixel-perfect segmentation.

### Stage 2 — Disease classifier (`server/classifier.py`)
- **Model:** a **frozen DINOv2 ViT-S/14 backbone + LoRA adapters + a small MLP
  head**, producing **9 classes** including a real `not_a_plant` rejection class.
- **Served via ONNX** (see §5 for why ONNX).
- Predictions are **mean-voted across all detected leaf boxes** for stability.

**Why this exact recipe (the key ML defense):**
- **DINOv2** is a *self-supervised* vision transformer from Meta. It was trained on
  a huge unlabeled image corpus and already "understands" general visual structure
  (edges, textures, shapes). We get those rich features **for free** without
  needing a million labeled plant photos.
- **Frozen backbone:** we do **not** retrain the giant transformer. Retraining it
  on our small dataset would overfit and cost a fortune in compute.
- **LoRA (Low-Rank Adaptation):** instead of updating all the backbone weights, we
  inject tiny trainable low-rank matrices. This adapts the model to *plants*
  cheaply — a few million trainable parameters instead of tens of millions.
- **MLP head:** a small final network that maps the adapted features to our 9
  classes.
- **Net effect:** strong accuracy from a small dataset, fast to train (on one
  Colab A100), and resistant to overfitting. This is modern **transfer learning**
  done correctly.

### The two safety gates (the reason for the whole architecture)
A model that *always answers* is dangerous — it will confidently mislabel a photo
of a sofa as "leaf spot." Two gates prevent that:

1. **Blur gate** — variance-of-Laplacian (a classic, dependency-free sharpness
   measure). If the photo is too unfocused, short-circuit to `not_a_plant` /
   `blurry_image` instead of inventing a confident diagnosis.
2. **OOD AND-gate** ("out-of-distribution") — fires `not_a_plant` only when **two
   independent signals agree**: (a) the detector's confidence is low **AND** (b) a
   classifier-side OOD signal trips. The OOD signal (`server/ood.py`) is a
   **k-NN distance in DINOv2 embedding space** on the CLS vector — i.e., "how far
   is this image from anything we trained on?" — with a MaxLogit-floor fallback.
   Requiring *both* (an AND, not an OR) avoids over-rejecting real plants.

### Calibration & honest uncertainty (`server/conformal.py`)
- **Temperature scaling:** neural nets are typically **overconfident** (they say
  99% when they're right 80% of the time). Temperature scaling is a one-parameter
  post-hoc fix that makes the confidence numbers *mean* what they say.
- **RAPS conformal prediction sets:** instead of one label, the model can return a
  **set** of labels with a statistical **coverage guarantee** (e.g. "the true
  answer is in this set 90% of the time"). When the set has >1 label, that's the
  app honestly showing "it might be A or B."
- The app surfaces this as top-3 alternatives + soft warnings (`uncertain_healthy`,
  `low_confidence`, `blurry_image`) and a weak-healthy reroute.

### Model families (`classes.json → model_family`)
Three trained variants, picked per use case:
| Family | Backbone | Use |
|---|---|---|
| **aqua** | MobileNetV3-Small, distilled → **TFLite INT8** | **On-device / offline** in the app |
| **gamma** | DINOv2 ViT-S/14 (the default) | Production cloud inference |
| **frontier** | DINOv2 ViT-B/14 (bigger) | Highest accuracy; needs a GPU host, off by default |

**Distillation + INT8 quantization** for `aqua`: a small student model is trained
to mimic the big one, then compressed to 8-bit integers so it runs on a phone CPU
with no server round-trip. Classic accuracy-vs-size trade-off.

---

## 4. ML basics — vocabulary you'll be asked to explain

Keep these definitions in your pocket:

- **Classification:** given an image, output a probability over a fixed set of
  labels (here, 9 disease/healthy/not-a-plant classes).
- **Transfer learning:** reuse a model pre-trained on a huge task, adapt it to a
  small one. (Here: DINOv2 → houseplant diseases.)
- **Embedding:** the model's internal numeric "summary" of an image (a vector).
  Similar images → nearby vectors. We use distances between embeddings to detect
  unknown inputs (OOD).
- **LoRA:** cheap fine-tuning — train tiny add-on matrices, freeze the rest.
- **Calibration:** making predicted confidence match real accuracy.
- **OOD detection:** flagging inputs unlike anything in training, so we don't
  confidently answer nonsense.
- **Conformal prediction:** a method that returns a label *set* with a provable
  coverage guarantee — the rigorous way to express uncertainty.
- **Quantization (INT8):** store/compute weights as 8-bit integers → smaller,
  faster, slightly less precise. Enables on-device inference.
- **Distillation:** train a small "student" model to copy a big "teacher."
- **Macro-F1:** the headline accuracy metric — average of per-class F1, so a rare
  disease counts as much as a common one (better than plain accuracy on
  imbalanced data).

### How we prove the model actually got better (anti-noise discipline)
This is what separates an engineering project from a toy:
- **Group-aware splits:** photos of the *same plant* never straddle the
  train/test boundary, so we don't fool ourselves with leakage.
- **Paired-bootstrap ship gate:** before a new model replaces the old one, we
  resample the test set thousands of times and check the improvement holds — i.e.,
  it's a **real** gain, not luck. A model only ships if it passes this gate.

---

## 5. Why each major technology (the "why these libraries" defense)

### Server
- **FastAPI** — async (handles concurrent scans), automatic request validation via
  **Pydantic**, and auto-generated OpenAPI docs. Less boilerplate than Flask, safer
  than raw frameworks.
- **ONNX Runtime** — we *train* in PyTorch but *serve* in ONNX. ONNX is a
  framework-neutral model format with a fast CPU runtime, so the server has no
  heavy PyTorch/GPU dependency in production. Train once, export, serve cheaply.
- **Fail-fast model contract** — the server **refuses to boot** without its model
  artefacts (`detector`, `classifier.onnx`, `classes.json`, calibration). No silent
  "demo mode" that quietly returns garbage. (Tests swap in a fake pipeline so they
  need no weights.)
- **Optional integrations degrade gracefully** — PlantNet (species ID), OpenRouter
  (AI chat doctor), and FCM (push) are all optional. Missing an API key → that
  endpoint returns `available:false` and the app shows a tidy "not configured"
  state. Nothing ever crashes for a missing key.

### Android
- **Kotlin + Material 3** — the modern Android standard; Material 3 gives a
  consistent, themeable UI.
- **CameraX** — lifecycle-aware camera that handles the device fragmentation Camera2
  exposes; far less boilerplate.
- **Room** — type-safe SQLite ORM for offline scan history and the garden.
- **Ktor client + kotlinx.serialization** (over an OkHttp engine) — a coroutine-
  native HTTP client; pairs with compile-time-safe JSON serialization. (This
  replaced the older Retrofit interface.)
- **Koin** — lightweight dependency injection (singletons for the API client, DB,
  repositories) — simpler than Dagger/Hilt for a project this size.
- **WorkManager** — guaranteed background execution for watering/fertilizing
  reminders and second-opinion polling, surviving reboots.
- **Bilingual everywhere** — every user-facing string exists in EN and RU
  (`res/values/` + `res/values-ru/`); diagnosis content is bilingual on the server.

---

## 6. App code logic (where things live)

### Server module map
- `main.py` — the HTTP layer (~150 KB): routes, middleware, validation, rate
  limiting, metrics. Wires everything together.
- `pipeline.py` / `detector.py` / `classifier.py` / `ood.py` / `conformal.py` —
  inference + calibration (the §3 pipeline).
- `ai_services.py` — optional PlantNet species ID, OpenRouter chat doctor, care
  plans (all graceful without keys).
- `diseases_data.py` / `symptoms.py` / `species_info.py` — bilingual content
  databases + no-photo symptom triage.
- `feedback.py` — append-only training-signal store + per-class accuracy metrics
  (the data flywheel: user corrections feed the next training run).
- `community.py` — nearby-scans feed, second opinions.
- `labeling.py` / `labeling_auto.py` — the data-collection/labeling loop.
- `config.py` — config loader. **Precedence:** `config.json` > same-named env vars
  > defaults.

### Android structure (`com.plantdiseases.app`)
- `ui/<feature>/` — one package per screen (camera, result, garden, gallery, guide,
  community, profile, home, …).
- `data/` — `repository/` (business logic), `remote/` (the Ktor `PlantApiClient`),
  `local/` (Room DB), `model/` (`@Serializable` DTOs).
- `di/AppModule.kt` — Koin singletons.
- `util/` — helpers, incl. the on-device `AquaClassifier`/`AquaInference` (TFLite).
- `worker/` — WorkManager reminders + opinion polling.
- The server URL is baked in at build time (`BuildConfig.API_BASE_URL`).

### Example end-to-end flow (use this to narrate a live demo)
1. User snaps a leaf in the **camera** screen (CameraX).
2. The app POSTs the image to `/api/analyze` via the Ktor client.
3. Server: blur gate → YOLOv8n crop → DINOv2+LoRA+MLP classify → OOD gate →
   temperature scaling + conformal set → bilingual care-plan lookup.
4. Server returns JSON: class, confidence, top-3, warnings, treatment/prevention.
5. App renders the **result** card, saves it to **Room** history, optionally calls
   PlantNet for species and OpenRouter for follow-up chat.
6. If the user picks **aqua** (offline), steps 2–4 happen on-device via TFLite, no
   network.

---

## 7. Likely defense questions (and crisp answers)

- **"Why not just a single CNN?"** A single classifier on raw room photos
  classifies background clutter and is confidently wrong on non-plants. The
  two-stage crop + rejection head + OOD gate is what makes it usable in the wild.
- **"Why a transformer (DINOv2) and not ResNet?"** DINOv2's self-supervised
  features transfer extremely well with little labeled data, which is exactly our
  constraint. We freeze it and adapt cheaply with LoRA.
- **"How do you avoid overconfidence?"** Temperature scaling calibrates the
  probabilities; conformal prediction gives a set with a coverage guarantee.
- **"How do you handle a photo that isn't a plant?"** A dedicated `not_a_plant`
  class **plus** an independent OOD AND-gate **plus** a blur gate — three lines of
  defense, not one.
- **"How do you know a new model is actually better?"** Group-aware splits prevent
  data leakage; a paired-bootstrap ship gate confirms the gain is statistically
  real before deployment.
- **"What if the network is down?"** The `aqua` TFLite model runs fully offline
  on-device.
- **"What if an API key is missing?"** Those features report `available:false` and
  the UI degrades gracefully — the core diagnosis still works.
- **"Is it production-ready?"** Hardened FastAPI (rate limiting, validation,
  metrics), fail-fast model contract, signed release builds, CI on every push
  (lint + tests + R8 build).

---

## 8. Honest limitations (say these before the examiner does)

- Accuracy is bounded by dataset coverage — rare diseases and exotic species are
  weaker; that's why there's a feedback → retraining loop.
- The cloud model needs connectivity; the offline `aqua` model trades some accuracy
  for portability.
- Optional AI features (chat doctor, species ID) depend on third-party services.
- It's a **decision-support** tool, not a substitute for an agronomist — the app
  states this and leans on honest-uncertainty UX rather than false certainty.

---

### TL;DR to memorize
> Two-stage pipeline (YOLOv8n leaf crop → frozen DINOv2 + LoRA + MLP, 9 classes
> with a not-a-plant head), wrapped in blur + OOD gates and temperature/conformal
> calibration so it's **honest about uncertainty**. Served by a hardened FastAPI +
> ONNX backend; consumed by a Kotlin/Material-3 app with offline TFLite, Room
> history, and bilingual care plans. Improvements ship only after a paired-bootstrap
> test proves they're real.
