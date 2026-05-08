# PlantVision — MODEL_CARD (v2.1)

## Pipeline

**Stage 1 — YOLOv8n** single-class `plant_leaf` detector, trained on
indoor leaf bboxes (300–800 hand-labelled images plus targeted
Roboflow Universe houseplant slices). PlantDoc is excluded from
detector training because its agricultural close-ups bias localisation
toward field shots.

**Stage 2 — EfficientNetV2-S → MobileNetV3-Large** via knowledge
distillation. Teacher checkpoint is `tf_efficientnetv2_s.in21k_ft_in1k`
fine-tuned on our 7-class taxonomy with logit-adjustment cross-entropy.
Student is `torchvision.mobilenet_v3_large` (5.5 M params). KD uses
`α=0.5, τ=4.0`, and an EMA copy of the student (decay=0.999) is what
we evaluate, save and export.

## Classes (7 trainable, v2.1)

`bacterial_leaf_spot`, `gray_mold_botrytis`, `healthy`,
`leaf_spot_fungal`, `not_a_plant`, `powdery_mildew`, `viral_disease`.

`leaf_spot_fungal` is a coarse umbrella for anthracnose / cercospora /
alternaria — visually overlapping at 256 px and sharing treatment
advice (copper fungicide, dry foliage, isolate). `viral_disease`
covers mosaic + leaf curl + ringspot.

### Deferred

`edema`, `leaf_scorch_underwatering`, `nutrient_deficiency`,
`sunburn_or_scorch` were in the v2.0 design but are deferred until
labelled real-world data is added. Public sources have very few clean
indoor examples; including them with proxy labels collapses recall on
the 7 classes we already have data for. Adding them later is a config
change in `train_notebook.ipynb` §1, not a re-architecture.

## Validation contract

| Slice | Role | Pass criterion |
|---|---|---|
| `val_indomain` | training-loop metric, drives early stopping | tracked; best EMA checkpoint restored |
| `val_realworld` | **primary — ship/no-ship gate** | disease macro-F1 ≥ **0.70**; no class with recall = 0; healthy-bias < 1.5 |
| `val_studio` (PlantVillage) | transfer / background-bias monitor | tracked, never blocking |
| `temperature_cal` | INT8 activation + temperature scaling | held out, never reported |
| `conformal_cal` | RAPS calibration | held out, target empirical coverage ≈ 1 − α |

PlantVillage is **excluded** from the real-world gate and from both
calibration splits — its studio backgrounds violate exchangeability
with the deployment distribution and would inflate coverage.

## Training recipe

* Optimiser: AdamW (`weight_decay=1e-4`)
* LR schedule: cosine + 1-epoch linear warmup
* 256 px input, batch=96, bf16-mixed (CUDA) / fp32 (CPU fallback)
* Phase A (head-only, 3 epochs, lr=3e-3) → Phase B (last 2 backbone
  stages, 15 epochs, head lr=5e-4, backbone lr=5e-5; early-stop on
  `val_indomain` disease macro-F1)
* **Loss**: Logit Adjustment cross-entropy (Menon ICLR 2021), τ=0.7,
  label_smoothing=0.05. Loss-side balancing only —
  `WeightedRandomSampler` is **off** by default to avoid double
  rebalancing of rare classes.
* Augs: indoor-targeted — WB shift, glare, defocus / motion blur,
  JPEG re-compression, mild ColorJitter (brightness/contrast/sat
  only), RandomErasing. **No** strong hue rotation / Solarize —
  colour is a disease signal.
* Distillation (`α=0.5, τ=4.0`, 10 epochs, EMA student decay=0.999).
* Calibration: temperature scaling on `temperature_cal` NLL **plus**
  inductive RAPS conformal prediction on `conformal_cal`. Both fitted
  on **INT8 ONNX logits** so the calibration matches the deployed
  model, not the FP32 torch one.
* Quantisation: **static INT8 (QDQ), 500-image calibration reader on
  `conformal_cal`**. Export step re-evaluates on `val_realworld` and
  raises if disease F1 drops by > 0.05 vs FP32 (warning only at > 0.02).

## OOD detection

* **Score**: MaxLogit (Hendrycks 2019). Robust to temperature scaling
  and label smoothing where MSP collapses.
* **Production gate**: low detector confidence (< 0.40) **and** low
  classifier MaxLogit (< 4.0) → re-route the verdict to `not_a_plant`.

## Pre-training data hygiene

* Per-class SHA-256 + pHash dedup (Hamming ≤ 6) on `TRAIN_ROOT` only.
  Cross-class similarities are kept on purpose — they help as hard
  negatives during KD.
* Sidecar JSON per crop: `plant_id`, `species`, `diagnosis`, `severity`,
  `leaf_or_whole_plant`, `capture_session`, `source`, `label_confidence`,
  `bbox`. Group splits use `(source, plant_id)` so the same plant
  never appears in both `TRAIN_ROOT` and a held-out split.
* Exact SHA-256 leakage audit between `TRAIN_ROOT` and every held-out
  split — `RuntimeError` on overlap.
* `ImageFolder.target_transform` remap on every eval loader, so a
  slice that is missing one trained class cannot silently shift
  labels.

## Time budget on 1× A100-40 (rough)

| Phase | Time |
|---|---|
| §2 data fetch (cold cache) | 8–15 min |
| §3 dedup + DINOv2-S probe | 5–10 min |
| §4 EfficientNetV2-S fine-tune | 25–35 min |
| §5 KD into MobileNetV3-Large | 8–12 min |
| §6 ONNX + INT8 + temperature + conformal | 5 min |

Colab T4 is roughly 2-3× slower; reduce `BATCH=64`.

## Known caveats

* `viral_disease` is incurable; the server pipeline auto-escalates
  severity by one level on any high-confidence viral verdict.
* `gray_mold_botrytis` recall depends heavily on the iNat supplement.
  iNat label confidence is set to `0.55` in the sidecar, which is a
  hint to monitor this class separately during error analysis.
* `leaf_spot_fungal` is an umbrella class. Treatment is genuinely the
  same for the three fungi underneath, but downstream reports should
  not claim sub-class accuracy.
* **Dataset domain mix.** PlantDoc contributes outdoor / agricultural
  leaves (tomato, potato, apple, etc.). It supplements the rare
  classes but the eval ratio leans agricultural — real-world recall
  on the actual target domain (Ficus, Monstera, Anthurium, Dracaena,
  Sansevieria, Calathea) lives or dies on the iNat houseplant
  supplement and the labelled real-world batch noted in
  `ROADMAP.md`.
* **`not_a_plant` is COCO-only today.** The notebook downloads only
  COCO val2017 negatives. The wider OE-pool described in `README.md`
  (OpenImages V7 indoor scenes + iNat Animalia + recycled user
  uploads) is a v2.2 task — see `ROADMAP.md`.
