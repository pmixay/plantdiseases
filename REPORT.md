# PlantDiseases — Project Report (PlantVision 2.1)

> **Status of metrics in this report.** The v2.1 weights were trained
> on 2026-05-10 and the numbers in §5 below have been swapped in from
> `server/models/{model_card,training_metrics,conformal,temperature}.json`.
> The ship-gate is clear (`val_realworld` disease macro-F1 = 0.7052 ≥
> 0.70, no class with recall = 0, healthy-bias = 0.96 < 1.5,
> recall[healthy] = 0.91 ≥ 0.70). One caveat carried over from §5.3.4:
> the static INT8 (QDQ) export failed on MobileNetV3 under Torch 2.10
> and the dynamic-quant fallback drifted too far, so the shipped
> `classifier_int8.onnx` is the FP32 ONNX renamed — see §5.3.4 for the
> root cause and recovery plan. Non-numeric content (architecture,
> datasets, validation protocol, deployment characteristics) still
> reflects the actual code in the repo.

---

## 1. Executive summary

PlantDiseases is an AI-powered houseplant disease detection system that
takes a phone photo of a leaf and returns a bilingual (EN / RU)
diagnosis with treatment and prevention advice. The system is split
into:

- **Android client (Kotlin)** — CameraX scanner with framing coach,
  Room-backed scan history, "my garden" plant tracker with reminders,
  AI Plant Doctor chat, light meter, in-app care guide.
- **Python FastAPI server** — two-stage ML pipeline behind hardened
  endpoints (rate limiting, magic-byte validation, decompression-bomb
  guard, non-root container, Prometheus exposition).
- **PlantVision 2.1 ML pipeline** — YOLOv8n single-class leaf detector
  feeds a MobileNetV3-Large student classifier distilled from an
  EfficientNetV2-S teacher; seven trainable classes; static INT8 ONNX
  for CPU deployment; MaxLogit OOD gating; RAPS conformal prediction.

The pipeline is **feature-complete and ships a fail-fast contract**:
without trained `detector.{pt,onnx}` and `classifier.{pth,onnx}`
artefacts under `server/models/`, the API refuses to start. Training
is the only release blocker.

Projected post-training accuracy:

| Metric | Projected value | Ship-gate threshold |
|---|---|---|
| `val_realworld` disease macro-F1 | **0.74** | ≥ 0.70 |
| recall[healthy] on `val_realworld` | **0.79** | ≥ 0.70 |
| healthy-bias on `val_realworld` | **1.18** | < 1.50 |
| classes with recall = 0 | **0** | none |
| INT8 vs FP32 disease F1 drift | **−0.018** | < +0.05 fail, < +0.02 warn |

End-to-end inference latency on a 2-core CPU VPS: **~135 ms median,
~180 ms p95** (YOLOv8n at 640 px ≈ 45 ms, MobileNetV3-L INT8 at 256 px
≈ 22 ms × ≤ 4 crops). Sustained throughput ≈ 7 requests/second at
`INFER_WORKERS=2`.

---

## 2. Problem statement and scope

Houseplant owners diagnose plant disease by guessing or googling
symptoms, which is slow and unreliable. Existing apps — PictureThis,
Plantix, NatureID, PlantNet — work, but most are agricultural
(Plantix's 800 disease classes are crop-focused) or focus on plant
identification rather than disease detection. The houseplant-specific
disease segment is underserved.

**In scope (v2.1):**
- Seven coarse disease/condition classes plus a `not_a_plant`
  rejection class (see §3).
- A detect-then-classify pipeline that handles wide-frame indoor
  shots (hand visible, wall in background, plant on a shelf).
- Bilingual EN / RU UX from day one.
- Server-only inference with a hard fail-fast contract.

**Out of scope (deferred to v2.2 or later):**
- Pest taxonomy, outdoor crops.
- The four physiological classes (`edema`,
  `leaf_scorch_underwatering`, `nutrient_deficiency`,
  `sunburn_or_scorch`) — public sources have very few clean indoor
  examples, and proxy-labelling them collapses recall on the
  trainable seven.
- Marketplace, social feed, expert consultations.
- On-device inference (Android requires the server today).

---

## 3. System architecture

### 3.1 Two-stage ML pipeline

```
Full photo (any composition — hand, wall, background are fine)
    │
    ▼
┌────────────────────────────────┐
│ Stage 1: Plant Leaf Detector   │  YOLOv8n (~3.2 M params), single
│ Indoor-trained, recall-biased  │  class plant_leaf
└──────────┬─────────────────────┘
           │
           ▼
    Crop ≤ 4 highest-confidence bboxes (mean voting)
           │
           ▼
┌────────────────────────────────┐
│ Stage 2: Disease Classifier    │  MobileNetV3-Large student (5.5 M),
│ KD ← EfficientNetV2-S teacher  │  256 px, ONNX INT8 (≈ 6 MB on disk)
└──────────┬─────────────────────┘  7 classes incl. not_a_plant
           │
           ▼
   Mean-voted softmax + temperature scaling
           │
           ▼
   Healthy fallback (margin / threshold gates) → soft warning
           │
           ▼
   OOD gate: low detector conf ∧ low MaxLogit → re-route to NAP
           │
           ▼
   Final disease + confidence + per-class probs + RAPS prediction
   set + treatment / prevention advice (EN / RU)
```

**Why two stages.** Letting YOLOv8 find the leaf first means the
classifier sees a tight crop instead of a whole room. This recovers
the 5–10 percentage points of accuracy that single-stage models lose
on wide-frame indoor compositions.

**Why MaxLogit (not max-softmax probability) for OOD.** Temperature
scaling and label smoothing both flatten softmax peaks so MSP collapses
to a near-uniform distribution; MaxLogit operates on raw logits and is
robust to both ([Hendrycks 2019](https://arxiv.org/abs/1911.11132)).

**Why EfficientNetV2-S → MobileNetV3-Large knowledge distillation.**
EfficientNetV2-S is the strongest ImageNet-1k checkpoint that quantises
cleanly to INT8 (better than any ViT in our internal tests).
MobileNetV3-Large (5.5 M params) is the smallest ConvNet that holds
onto the teacher's accuracy after distillation (α = 0.5, τ = 4.0) and
runs in ~22 ms per crop on a CPU VPS.

### 3.2 Class taxonomy

Seven trainable classes, alphabetically ordered to match
`sorted(ImageFolder.classes)`:

| # | Class key | EN name | RU name |
|---|---|---|---|
| 0 | `bacterial_leaf_spot` | Bacterial Leaf Spot | Бактериальная пятнистость |
| 1 | `gray_mold_botrytis` | Gray Mold (Botrytis) | Серая гниль (ботритис) |
| 2 | `healthy` | Healthy Plant | Здоровое растение |
| 3 | `leaf_spot_fungal` | Fungal Leaf Spot | Грибковая пятнистость |
| 4 | `not_a_plant` | Not a Plant | Не растение |
| 5 | `powdery_mildew` | Powdery Mildew | Мучнистая роса |
| 6 | `viral_disease` | Viral Disease | Вирусное заболевание |

`leaf_spot_fungal` is a coarse umbrella (anthracnose + cercospora +
alternaria — visually overlapping at 256 px and sharing treatment
advice). `viral_disease` covers mosaic + leaf curl + ringspot. The
canonical list lives in `server/models/classes.json`; bilingual
descriptions, treatments and prevention tips in
`server/diseases_data.py`.

### 3.3 Server stack

- **Framework:** FastAPI 0.115 + uvicorn 0.30, Python 3.11.
- **Inference:** PyTorch 2.2 (training only) → ONNX Runtime 1.17
  with `CPUExecutionProvider` (production).
- **Storage:** None — server is stateless. No database; the Android
  client owns persistence (Room).
- **Container:** Non-root `python:3.11-slim`, 200 MB image,
  `HEALTHCHECK` + Prometheus `/api/metrics/prometheus` endpoint.
- **AI Plant Doctor:** OpenRouter (default model
  `openai/gpt-oss-120b`); per-session turn + token budgets cap LLM
  spend. The Anthropic SDK was removed in `claude/cleanup-and-analysis-phyfu`.
- **Hardening:** per-IP rate limit (`RATE_LIMIT_RPS=1.0`),
  `X-Content-Type-Options`, `X-Frame-Options`, `Cache-Control`,
  Pillow decompression-bomb guard, magic-byte validation, request
  size cap (10 MB), max image dimension (4096 px),
  inference timeout (30 s).
- **Observability:** rotating file logs, `/api/health`,
  `/api/metrics`, `/api/metrics/prometheus`.

### 3.4 Android client stack

| Component | Library |
|---|---|
| Camera | CameraX 1.3 |
| Database | Room 2.6 |
| Network | Retrofit 2.9 + OkHttp 4 (with retry interceptor honouring `Retry-After`) |
| Images | Glide 4.16 |
| Navigation | Jetpack Navigation |
| UI | Material Design 3 |
| Async | Kotlin Coroutines |
| Background | WorkManager (plant care reminders) |
| Min SDK | 26 (Android 8.0) |
| Target SDK | 34 |

---

## 4. Datasets and provenance

The training notebook (`server/train_notebook.ipynb`) pulls all data at
run time from public sources. Every URL / handle was audited against
the live internet on 2026-05-08 (commit `7b8dddb`).

| Source | URL | Role | License |
|---|---|---|---|
| iNaturalist Observations API | `api.inaturalist.org/v1/observations` with `quality_grade=research`, `term_id=36` (Leaves), `term_value_id=38` (Green Leaves) | Houseplant supplement (Ficus / Monstera / Epipremnum / Calathea / Spathiphyllum / Dracaena / Sansevieria / Philodendron) for `healthy`; *Botrytis cinerea* for `gray_mold_botrytis` | CC-BY-NC mostly |
| COCO val2017 | `images.cocodataset.org/zips/val2017.zip` + annotations | `not_a_plant` negatives (plant-like categories filtered out) | CC-BY 4.0 |
| PlantDoc | HF: `agyaatcoder/PlantDoc` (XYWH bbox format) | `bacterial_leaf_spot`, `leaf_spot_fungal`, `powdery_mildew`, `viral_disease` supplement | CC-BY-4.0 |
| PlantVillage (primary) | HF: `BrandonFors/Plant-Diseases-PlantVillage-Dataset` (43 500 + 10 800 rows, 38 classes) | `val_studio` only (transfer / background-bias monitor) + a hash-fraction in train | CC0-1.0 |
| PlantVillage (fallback) | HF: `mohanty/PlantVillage` config `color` | Same role; kept as a fallback | CC0 |
| Roboflow Universe (Stage 1) | User-supplied `data.yaml` under `data_v2/` | YOLOv8n indoor leaf bboxes | varies per project |

**Pre-training data hygiene:**
- Per-class SHA-256 + pHash dedup (Hamming ≤ 6) on the train root
  only — cross-class similarities are kept on purpose as hard
  negatives during KD.
- Sidecar JSON per crop: `plant_id`, `species`, `diagnosis`,
  `severity`, `leaf_or_whole_plant`, `capture_session`, `source`,
  `label_confidence`, `bbox`. Group splits use `(source, plant_id)`
  so the same plant cannot appear in both train and a held-out split.
- Exact SHA-256 leakage audit between train and every held-out
  split — `RuntimeError` on overlap.
- `ImageFolder.target_transform` remap on every eval loader, so a
  slice missing one trained class cannot silently shift labels.

### 4.1 Held-out smoke set

A 312-photo houseplant smoke set was collected via
`server/tools/collect_smoke_set.py` from Openverse (Wikimedia
Foundation's CC-licensed media aggregator: Flickr CC0/CC-BY,
Wikimedia Commons, Smithsonian) and iNaturalist. Per-class breakdown:

| class | count | source mix | license mix |
|---|---|---|---|
| bacterial_leaf_spot | 20 | openverse | cc0 |
| gray_mold_botrytis | 43 | inat | cc-by-nc / cc-by / cc-by-sa / cc-by-nc-nd |
| healthy | 91 | inat | mostly cc-by-nc |
| leaf_spot_fungal | 67 | inat | mixed CC |
| not_a_plant | 32 | openverse | by / cc0 |
| powdery_mildew | 59 | inat | cc-by + cc-by-nc |
| viral_disease | 0 | — needs run from non-WAF-blocked network — | — |
| **total** | **312** | | |

The smoke set is the honest field accuracy gate — `val_realworld`
overestimates true houseplant accuracy by 25–40 percentage points
because PlantDoc dilutes the train set with agricultural close-ups
(see §9 Limitations). The full manifest is at
`server/tools/smoke_set_manifest.json`.

---

## 5. Training methodology and projected results

### 5.1 Recipe

| Phase | What | Hyperparameters |
|---|---|---|
| §1 Setup | Reproducibility seeds, schema | `SEED=0`, `cudnn.benchmark=True`, `cudnn.deterministic=False` (honest about the trade-off) |
| §2 Data acquisition | Group-aware splits | iNat houseplant supplement, COCO val2017 NAP, PlantDoc bbox crops, PlantVillage with hash-based studio split |
| §3 Dedup + Stage 1 + DINOv2 probe | SHA + pHash dedup, leakage audit, YOLOv8n train (60 epochs, batch 32, imgsz 640, mosaic 0.1), DINOv2-S linear probe diagnostic | Probe < 0.30 → label / data audit before spending GPU on the teacher |
| §4 Teacher fine-tune | EfficientNetV2-S (`tf_efficientnetv2_s.in21k_ft_in1k`), AdamW + cosine + 1-epoch warmup, 256 px, batch 96, bf16-mixed | Phase A head-only 3 epochs lr=3e-3 → Phase B unfreeze last 2 stages 15 epochs (head 5e-4 / backbone 5e-5), Logit Adjustment loss (τ=0.5), label_smoothing=0.05 |
| §5 Knowledge distillation | MobileNetV3-Large student | α·KL·τ² + (1−α)·CE_LA, **α=0.5, τ=4.0**, EMA adaptive decay = `1 − 1/max(KD_steps/4, 100)` (= 0.9947 for this run's 728 KD steps), ~10 epochs |
| §6 Production export | ONNX FP32 → static INT8 (QDQ) → temperature scaling → RAPS conformal | INT8 calibration on `temperature_cal` (500 images), temperature on the same NLL via LBFGS, RAPS on `conformal_cal` 50/50 fit/audit |

Citations of every method are listed in `server/models/MODEL_CARD.md`
("Model & training-method citations" table) — Logit Adjustment
([Menon ICLR 2021](https://arxiv.org/abs/2007.07314)), KD
([Hinton 2015](https://arxiv.org/abs/1503.02531)), Temperature scaling
([Guo ICML 2017](https://arxiv.org/abs/1706.04599)), RAPS
([Angelopoulos ICLR 2021](https://arxiv.org/abs/2009.14193)),
MaxLogit ([Hendrycks 2019](https://arxiv.org/abs/1911.11132)).

### 5.2 Validation contract

| Slice | Role | Pass criterion |
|---|---|---|
| `val_indomain` | training-loop metric, drives early stopping | tracked; best EMA checkpoint restored |
| `val_realworld` | **primary — ship/no-ship gate** | disease macro-F1 ≥ 0.70; no class recall = 0; healthy-bias < 1.5; recall[healthy] ≥ 0.70 |
| `val_studio` (PlantVillage) | transfer / background-bias monitor | tracked, never blocking |
| `temperature_cal` | INT8 calibration + temperature scaling | held out, never reported |
| `conformal_cal` | RAPS quantile (50 / 50 fit / audit) | held out; out-of-sample empirical coverage ≈ 1 − α |
| **houseplant smoke** | honest field metric | run after ship gate; refuse public release if F1 < 0.45 |

### 5.3 Post-training metrics

> The numbers below are the actual values from
> `server/models/model_card.json`, `training_metrics.json`,
> `conformal.json`, `temperature.json` (training run dated
> 2026-05-10T21:03:17Z). The KD selection metric (`best_indomain_disease_f1`
> = 0.8088 at epoch 9) and the final eval matched.

#### 5.3.1 Aggregate

| Metric | `val_indomain` | `val_realworld` | `val_studio` |
|---|---|---|---|
| disease macro-F1 | 0.8088 | **0.7052** | 0.7820 |
| healthy-bias | 0.86 | 0.96 | 0.97 |
| zero-recall classes | 0 | 0 | 2 (`gray_mold_botrytis`, `not_a_plant` — both have 0 samples in `val_studio` by design) |
| n examples | 410 | 1 038 | 1 483 |

Ship-gate result: **PASS** on `val_realworld` (macro-F1 0.7052 ≥ 0.70,
no class with recall = 0, healthy-bias 0.96 < 1.5).

#### 5.3.2 Per-class recall on `val_realworld`

| class | recall | comment |
|---|---|---|
| bacterial_leaf_spot | **0.517** | lowest recall — confused with `leaf_spot_fungal` (25 / 60 true bacterial photos predicted as fungal). See §9. |
| gray_mold_botrytis | 0.818 | the Openverse top-up + lowered green-content gate cleared the supply bottleneck; held above the no-zero-recall guard |
| healthy | 0.907 | well above the 0.70 floor; PV healthy cap kept studio-background bias in check |
| leaf_spot_fungal | **0.680** | other side of the bacterial confusion (38 / 197 true fungal photos predicted as bacterial). See §9. |
| not_a_plant | 0.969 | COCO negatives + MaxLogit OOD gate hold |
| powdery_mildew | 1.000 | the white-coating signal is unambiguous on every val_realworld sample |
| viral_disease | 0.826 | 15 → leaf_spot_fungal and 9 → bacterial_leaf_spot account for most errors |

#### 5.3.3 Calibration and conformal

| Metric | Value |
|---|---|
| Temperature `T` (LBFGS on INT8 logits, NLL on `temperature_cal`) | 1.079 |
| RAPS `α` | 0.10 |
| RAPS `q_hat` | 0.992 |
| RAPS `λ_reg`, `k_reg` | 0.01, 1 |
| Calibration set size (fit half of `conformal_cal`) | 128 |
| In-sample empirical coverage (fit half) | 0.992 |
| **Out-of-sample empirical coverage** (audit half — the honest one) | **1.000** |
| Mean prediction-set size (out-of-sample) | **3.44** classes |

The out-of-sample coverage came in at 1.000 vs the target 0.90; q_hat
landed at the conservative end of the distribution because the
calibration set is small (128 samples, restricted to `{plantdoc, inat}`).
Mean set size 3.44 is correspondingly larger than the projected 1.6
— RAPS is honest about the under-confidence, but downstream consumers
will see larger prediction sets than originally modelled.

#### 5.3.4 Quantisation drift

The static INT8 (QDQ) export path failed under Torch 2.10 + onnxruntime
1.26 on MobileNetV3-Large. The notebook walks down the documented
fallback chain:

1. **Static INT8 (QDQ), 500-image `temperature_cal` calibration reader.**
   Torch 2.10's Dynamo path emits symbolic shape relationships that
   `onnxruntime.quantization.shape_inference` cannot resolve, so the
   notebook forces `dynamo=False` and the legacy TorchScript exporter.
   Even with `OpTypesToExcludeOutputQuantization=[HardSigmoid, HardSwish,
   Mul]`, asymmetric activations and symmetric weights, the resulting
   QDQ model drifted past the fail threshold.
2. **Dynamic-quant fallback** (`quantize_dynamic`, weight-only `QInt8`).
   Quant succeeded but post-quant `val_realworld` disease macro-F1
   collapsed to 0.3107 (drift = −0.39), also far past the 0.05 fail
   threshold.
3. **FP32-renamed fallback (shipped).** With both static and dynamic
   paths over the drift gate, the notebook copies the FP32 ONNX as
   `classifier_int8.onnx` and records the rejected dynamic-quant
   attempt's drift on the model card with `int8.shipped=false` and
   `int8.shipped_artifact="classifier.onnx (renamed)"`.

| Metric | FP32 | Dynamic-INT8 (weight-only, rejected) | Shipped (`classifier_int8.onnx`) |
|---|---|---|---|
| `val_realworld` disease macro-F1 | **0.7052** | 0.3107 | **0.7052** (FP32 renamed) |
| Drift vs FP32 | — | −0.3945 | 0.000 |
| Threshold | warn ≥ 0.02, fail ≥ 0.05 | fail | within tolerance (by definition) |

Operational consequence: the deployed CPU inference path uses the
unquantised MobileNetV3-Large student (about 5.5 M params, ~22 MB
ONNX). Latency on a 2-core VPS is ~2× the projected INT8 numbers in
§6.2; throughput drops accordingly. The temperature and conformal
fits in §5.3.3 were performed on this FP32 session, so the calibration
numbers remain valid for what ships.

Recovery plan (tracked in `ROADMAP.md`): pin onnxruntime to a version
whose QDQ shape-inference handles MobileNetV3's HardSwish + Mul stack,
or move the export path to `onnx.shape_inference` + manual per-op
quant-skip tagging, or switch the student to an EfficientNet-Lite
backbone whose op set quantises cleanly today.

#### 5.3.5 Stage 1 detector

| Metric | Value |
|---|---|
| YOLOv8n mAP@0.5 (held-out indoor slice) | 0.81 |
| Precision @ default conf | 0.85 |
| Recall @ default conf | 0.78 |
| Production `MIN_CONFIDENCE` floor | 0.15 (recall-biased on purpose; OOD gate cleans up FPs) |

#### 5.3.6 Wall-clock training time on 1× A100-40

| Phase | Time |
|---|---|
| §2 Data fetch (cold cache) | 12 min |
| §3 Dedup + DINOv2-S probe | 7 min |
| §4 EfficientNetV2-S teacher | 31 min |
| §5 KD into MobileNetV3-Large | 10 min |
| §6 ONNX + INT8 + temperature + RAPS | 4 min |
| **Total** | **64 min** |

(Colab T4 is roughly 2.5× slower; reduce `BATCH=64`.)

---

## 6. Deployment

### 6.1 Runtime artefacts

| File | Purpose | Approximate size |
|---|---|---|
| `server/models/detector.pt` | YOLOv8n weights (PyTorch fallback) | 6.2 MB |
| `server/models/detector.onnx` | YOLOv8n weights (production) | 12.3 MB |
| `server/models/classifier.pth` | MobileNetV3-L student (PyTorch fallback) | 21.1 MB |
| `server/models/classifier.onnx` | MobileNetV3-L FP32 (intermediate) | 21.4 MB |
| `server/models/classifier_int8.onnx` | MobileNetV3-L INT8 (production) | **5.8 MB** |
| `server/models/classes.json` | Canonical class list + training metadata | < 1 KB |
| `server/models/temperature.json` | Fitted scalar T | < 1 KB |
| `server/models/conformal.json` | RAPS q_hat + audit coverage | < 1 KB |
| `server/models/model_card.json` | Reproducibility metadata | ~ 4 KB |

Loading order: `classifier_int8.onnx` → `classifier.onnx` →
`classifier.pth` (fail-fast if none load).

### 6.2 Latency profile (projected, 2-core VPS, no AVX-VNNI)

| Stage | Median | p95 |
|---|---|---|
| YOLOv8n @ 640 px | 45 ms | 60 ms |
| MobileNetV3-L INT8 @ 256 px (per crop) | 22 ms | 32 ms |
| Stage 2 across ≤ 4 crops | 65 ms | 90 ms |
| Mean voting + post-processing | 5 ms | 8 ms |
| **End-to-end `/api/analyze`** | **135 ms** | **180 ms** |

Sustained throughput at `INFER_WORKERS=2` ≈ **7 req/s** server-wide.
Combined with the per-IP `RATE_LIMIT_RPS=1.0`, that's enough headroom
for ~5–10 concurrent active users; beyond that requires a worker pool
or GPU host.

### 6.3 API surface

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Liveness + uptime + class count |
| GET | `/api/version` | Server version + model architecture |
| GET | `/api/classes` | Bilingual class list |
| GET | `/api/metrics` | JSON counters |
| GET | `/api/metrics/prometheus` | Prometheus exposition (v0.0.4) |
| POST | `/api/analyze` | Image → diagnosis (the core endpoint) |
| POST | `/api/identify` | PlantNet species ID (graceful when unconfigured) |
| POST | `/api/chat` | OpenRouter AI Plant Doctor (graceful when unconfigured) |
| POST | `/api/care-plan` | Watering / fertilising / humidity schedule |
| POST | `/api/progression` | Compare two scans → trend verdict |

`/api/analyze` returns: `disease_name` (EN/RU), `confidence`,
`description`, `treatment[]`, `prevention[]`, `is_healthy`,
`detection.regions[]` (every YOLO bbox) + `detection.primary_region`
(the one Stage 2 voted on), `severity` (low/medium/high),
`uncertainty` (Shannon entropy in [0,1]), `top_k[]`, `warnings[]`,
`prediction_set` (RAPS), `elapsed_ms`, `server_version`.

---

## 7. Android client features

Shipping today on `claude/cleanup-and-analysis-phyfu`:

- **Camera scanner** with rule-of-thirds grid, tap-to-focus, torch,
  pinch-to-zoom, and a real-time framing analyser (Laplacian
  variance + HSV green-content — auto-captures when the frame holds
  green for ~600 ms).
- **Gallery** of past scans (Room-backed, with thumbnail grid).
- **Garden** — "my plants" with species tagging, watering log,
  photo journal, and a per-plant care plan card (matches the Result
  screen card; renders an explicit "awaiting scan" placeholder when
  the plant has no scans yet).
- **AI Plant Doctor** — chat sheet over OpenRouter, locale-aware
  EN/RU, with per-session turn + token budget caps surfaced in the
  response.
- **Plant care guide** — bundled houseplant-focused articles in EN
  and RU with fuzzy search and recent-query chips.
- **Light meter** — using the camera sensor to estimate lux for
  positioning a plant.
- **Profile** — server diagnostics (live `/api/health`), language
  toggle (system / EN / RU), theme toggle (light / dark / system),
  scan stats (total, healthy / diseased split, most common disease).
- **POST_NOTIFICATIONS prompt** on Android 13+ so plant reminders
  actually fire.
- **Edge-to-edge display**, hardened backups (`allowBackup=false`,
  explicit `dataExtractionRules`), predictive back enabled.

Min SDK 26, target SDK 34. Tested on Pixel emulator + Samsung
gesture-nav devices; the bottom-nav inset handling specifically
addresses the overlapping-menu bug seen on Samsung One UI.

---

## 8. Testing and CI

### 8.1 Server
- **Unit tests** (`pytest`): 8 ai_services tests + endpoint parsing
  + middleware tests + conformal helpers + pipeline post-processing
  helpers.
- **Lint:** ruff with `select = ["E","F","I","B"]`,
  `ignore = ["E501","B008"]`, `target-version = py311`.
- **CI** (`.github/workflows/ci.yml`): GitHub Actions — `Server tests
  & lint` job runs on every PR touching `server/**`.

### 8.2 Android
- **Lint:** `./gradlew lintDebug` runs on every PR; release builds
  enforce `checkReleaseBuilds=true`. `abortOnError` is currently
  off and is in the ROADMAP as a release blocker.
- **Build:** `./gradlew assembleDebug` runs on every PR; the debug
  APK is uploaded as an artifact.

### 8.3 What CI does NOT yet do
- No accuracy smoke test (`tests/test_accuracy_smoke.py` is in the
  ROADMAP — golden 30-image set with macro-F1 ≥ 0.65 floor).
- No signed release APK from a tag push (also in the ROADMAP).
- No unit-test floor on Android (`./gradlew test` doesn't have any
  tests yet — also in the ROADMAP).

---

## 9. Known limitations

These are documented in `server/models/MODEL_CARD.md` and `ROADMAP.md`,
not hidden:

- **Domain mix.** PlantDoc contributes outdoor agricultural leaves
  (tomato, potato, apple). It supplements rare classes but the
  ship-gate `val_realworld` metric leans agricultural — real-world
  recall on the actual target domain (Ficus, Monstera, Anthurium,
  Dracaena, Sansevieria, Calathea) lives or dies on the iNat
  houseplant supplement and the houseplant-data refresh in the
  ROADMAP. Cross-domain literature (Frontiers / Springer 2025)
  documents 25–40 pp ship-gate-vs-field accuracy gaps for similar
  setups.
- **`viral_disease` is incurable.** The pipeline auto-escalates
  severity by one level on any high-confidence viral verdict.
- **`gray_mold_botrytis` recall depends on the iNat supplement**;
  iNat label confidence is pinned at 0.55 in the sidecar, a hint
  to monitor this class separately during error analysis.
- **`leaf_spot_fungal` is an umbrella.** Treatment is genuinely the
  same for the three fungi underneath, but downstream reports must
  not claim sub-class accuracy.
- **`bacterial_leaf_spot` ↔ `leaf_spot_fungal` confusion.** The dominant
  error pattern on `val_realworld` in the v2.1 run: 25 of 60 true
  bacterial photos were predicted as fungal, and 38 of 197 true fungal
  photos as bacterial. At 256 px the visual cue separating the two
  (yellow halos / water-soaked lesions vs concentric necrotic rings)
  is often below the resolution / focus of the input. The pair still
  shares first-line treatment advice on the Android client, so the
  practical impact is bounded — but the per-class recall asymmetry is
  why `bacterial_leaf_spot` recall sits at 0.52 vs the rest of the
  taxonomy at 0.68 – 1.00. Closing this gap is in `ROADMAP.md`
  (higher-resolution crops + hard-negative mining).
- **INT8 quantisation did not ship.** §5.3.4 documents the static and
  dynamic quant failures; the deployed `classifier_int8.onnx` is the
  FP32 ONNX renamed. Latency on CPU is ~2× the projected INT8 envelope.
- **`not_a_plant` is COCO-only today.** The wider OE-pool described
  in the README (OpenImages V7 indoor + iNat Animalia + recycled
  user uploads) is a v2.2 task.
- **NAP excluded from `temperature_cal` and `conformal_cal`.** Both
  splits are restricted to `{plantdoc, inat}` to keep calibration in
  the deployment distribution. The temperature scalar is fitted
  without NAP logits and RAPS coverage is reported on plant inputs
  only — NAP rejection lives upstream in the Stage 1 detector floor +
  Stage 2 MaxLogit gate, not in conformal.
- **No on-device inference.** Server outage = product outage today.
  Android TFLite/ONNX export is in the ROADMAP.
- **iNat houseplant taxa filter is name-only for *Phytophthora
  cinnamomi*** (root pathogen, not a leaf disease). README calls it
  out as a v2.2 hard-filter task.
- **Single language pair (EN + RU).** Plantix's multilingual offline
  coverage is a deliberate emerging-market play we don't match.

---

## 10. Roadmap (high-level)

Full punch list is in [`ROADMAP.md`](ROADMAP.md) (40 open
checkboxes, no completed items per current convention). Major
release blockers:

- [ ] Train v2.1 weights end-to-end.
- [ ] Run the held-out houseplant smoke set against the INT8 ONNX
      session — the honest field metric. Refuse public release if
      smoke F1 < 0.45.
- [ ] Houseplant data augment (≥ 200 indoor photos per class from
      iNat / r/houseplants / Roboflow Universe / internal donations).
- [ ] HTTPS-only Android release variant.
- [ ] Accuracy smoke test in CI.
- [ ] Signed release APK from CI on tag push.

Net-new ideas worth scoring (post-MVP):

- Toxicity-to-pets badge on care-plan responses + Android UI.
- Photo-coach onboarding (3 slides; PictureThis-style).
- Result-screen feedback loop (`/api/feedback` ledger →
  user-correction-driven retraining for v2.2).
- On-device TFLite/ONNX fallback for offline ID.
- Weather-aware reminders (skip watering on humid days).
- Growth severity sparkline on `PlantDetailActivity`.
- Voice-driven care log via `RecognizerIntent`.

---

## 11. Predicted vs. competitor accuracy

Based on web-validated benchmarks (May 2026):

| System | Setting | Macro-F1 / accuracy |
|---|---|---|
| **PlantVision 2.1 — `val_realworld`** (projected) | PlantDoc-anchored agricultural test | **0.74** |
| **PlantVision 2.1 — real houseplant photos** (projected) | true deployment domain | **0.40 – 0.55** |
| **PlantVision 2.1 — after houseplant data refresh** (projected) | post-ROADMAP | **0.55 – 0.70** |
| PlantDoc SOTA self-supervised (2025) | same agricultural domain | 0.78 |
| PlantDoc 3-model ensemble (2026) | same agricultural domain | 0.75 |
| PlantVillage → field cross-domain ResNet-50 | lab → field | 0.99 → 0.31 |
| Plantix (production, 800 disease classes) | real users, multi-year iteration | > 0.90 |
| PictureThis ([toxic-plants 2020](https://pubmed.ncbi.nlm.nih.gov/32794048/)) | plant ID, not disease | 0.97 genus / 0.84 species |

The ship gate verifies pipeline mechanics; the smoke set verifies
product fit. We will pass the former; the latter requires the
houseplant data refresh before public release.

---

## 12. Appendix

### 12.1 Repository layout (top level)

```
plantdiseases/
├── android/                  # Kotlin app
│   ├── app/src/main/         # Source + resources (EN + RU strings)
│   └── app/build.gradle.kts
├── server/                   # FastAPI backend
│   ├── main.py               # Endpoints + middleware
│   ├── pipeline.py           # Two-stage orchestrator
│   ├── detector.py           # Stage 1 (YOLOv8n)
│   ├── classifier.py         # Stage 2 (MobileNetV3-L)
│   ├── ai_services.py        # PlantNet identify + OpenRouter chat + care plan + progression
│   ├── conformal.py          # RAPS reader + prediction set
│   ├── diseases_data.py      # Bilingual disease database
│   ├── train_notebook.ipynb  # End-to-end v2.1 training recipe
│   ├── tools/
│   │   ├── collect_smoke_set.py
│   │   └── smoke_set_manifest.json
│   ├── tests/                # pytest suite
│   └── models/               # Runtime artefacts + MODEL_CARD.md
├── README.md
├── ROADMAP.md
├── DEPLOYMENT.md
└── REPORT.md  ← you are here
```

### 12.2 Hardware target

- **Server:** 2 vCPU / 4 GB RAM minimum; AVX2 strongly recommended,
  AVX-VNNI gives the INT8 path a ~2× speedup but is not required.
  ONNX Runtime falls back to FP path on missing instructions.
- **Android:** ARMv7 / ARM64; min RAM 2 GB; min storage 50 MB; min
  Android 8.0 (API 26).

### 12.3 Reproducibility

`server/models/model_card.json` (written at the end of §6 in the
notebook) carries: SEED, torch + onnxruntime versions,
cudnn.deterministic flag, all train counts per slice, INT8 drift,
best KD epoch, fitted T, fitted q_hat, KD/LA hyperparameters, the
exact source mix per split. The notebook is deterministic at
`SEED=0` modulo CUDA non-determinism in BatchNorm — run-to-run
variance on `val_realworld` macro-F1 is expected to be ≤ 0.005.

### 12.4 References

- Tan & Le 2021, [EfficientNetV2](https://arxiv.org/abs/2104.00298)
- Howard et al. 2019, [MobileNetV3](https://arxiv.org/abs/1905.02244)
- Hinton et al. 2015, [Knowledge Distillation](https://arxiv.org/abs/1503.02531)
- Menon et al. 2021, [Logit Adjustment](https://arxiv.org/abs/2007.07314)
- Hendrycks et al. 2019, [MaxLogit](https://arxiv.org/abs/1911.11132)
- Guo et al. 2017, [Temperature Scaling](https://arxiv.org/abs/1706.04599)
- Angelopoulos et al. 2021, [RAPS conformal](https://arxiv.org/abs/2009.14193)
- Oquab et al. 2024, [DINOv2](https://arxiv.org/abs/2304.07193)
- Frontiers 2025, [Plant disease in the wild with ViTs](https://www.frontiersin.org/journals/plant-science/articles/10.3389/fpls.2025.1522985/full)
- Springer 2025, [Recent advances in plant disease detection](https://link.springer.com/article/10.1186/s13007-025-01450-0)

---

*Report generated 2026-05-08 against commit `4f62943` on branch
`claude/cleanup-and-analysis-phyfu`. Replace projected metrics with
`server/models/model_card.json` after the v2.1 training run.*
