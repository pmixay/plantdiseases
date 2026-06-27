# Notebook training — `server/train_notebook.ipynb` (§1–§7)

The notebook is the single source of every artefact `server/` reads at runtime.
It is **deterministic** (`SEED = 0`, set in §1) and **selected by one knob**:
`MODEL_FAMILY` in §1. Run it top-to-bottom for a given family and you get that
tier's bundle.

> **Navigate it with `scripts/nbtool.py`** — some code cells are >2000 lines.
> `nbtool.py list` maps cells to sections; `nbtool.py section N` dumps one
> section; `nbtool.py grep PATTERN` finds a knob across all cells. Read a
> section before editing it; don't load the whole notebook into context.
> Concrete constants below are correct for the v2.6.x era but **grep for the
> live value before relying on one** (e.g. `nbtool.py grep "LORA_RANK"`).

## Section map

| § | Cell | What it does | Why it matters |
|---|---|---|---|
| §1 | setup | deps, `MODEL_VERSION`, `MODEL_FAMILY`/`FAMILY_CONFIG`, `SEED`, `CropAnnotation` sidecar schema, eval helpers, class toggles | the control panel — almost every knob you'd change lives here |
| §2 | data | data acquisition + **group-aware splits + leakage guard**; optional Kaggle houseplant supplement (fail-soft) | where train/val integrity is won or lost |
| §3 | detector + probe | dedup, Stage 1 YOLOv8n detector (re)train, **DINOv2 linear-probe diagnostic** (~0.91 ceiling) | proves the frozen features separate the classes before you train a head |
| §4 | train | loss (Logit Adjustment), augs, frozen DINOv2 + LoRA + MLP head, early stopping | the actual classifier training |
| §5 | distill | finalize teacher; for `aqua`: knowledge-distill → MobileNetV3-Small → TFLite | the on-device tier only |
| §6 | export | ONNX FP32 → dynamic INT8, temperature scaling, RAPS conformal, OOD floor, **ship gate** | turns a checkpoint into a shippable, calibrated bundle |
| §6b | bundle | tar the per-family artefacts for `scp` to the server | deploy packaging |
| §7 | contract | the evaluation contract (slices + pass criteria) — markdown, no code | the rules everything else serves |

## §1 — the knobs you'll actually touch

- `MODEL_VERSION` — **single source of truth** for the version string written
  into `classes.json` and `model_card.json`. Servers pin behaviour to it. Bump
  it on any change that alters shipped numerics.
- `MODEL_FAMILY ∈ {aqua, gamma, frontier}` → `FAMILY_CONFIG`. `gamma` is the
  production default (DINOv2 ViT-S/14 @224). An `aqua` run trains the gamma
  teacher *and* distills the on-device student in one pass. `frontier` swaps the
  backbone to ViT-B/14. Everything downstream reads `FAMILY_CONFIG`, so a family
  switch is usually a one-line change.
- `INCLUDE_PHYSIOLOGICAL_CLASSES` — toggles the 9-class head down to a 7-class
  head if the free-text-sourced classes (`leaf_scorch_underwatering`,
  `nutrient_deficiency`) go dark.
- `CropAnnotation` (dataclass) — the sidecar JSON schema written next to every
  crop (`plant_id`, `species`, `diagnosis`, `severity`, `source`, `bbox`, …).
  `plant_id` is what makes the §2 group split leakage-safe.

## §2 — group-aware splits (the integrity step)

The cardinal rule: **all crops of the same physical plant / capture session
must land in the same split**. Random per-image splits leak — two photos of the
same leaf in train and val inflate every metric. The split groups by
`plant_id` (from the sidecar) so a plant is wholly train or wholly val.

The optional Kaggle supplement is **fail-soft**: provide credentials and it
pulls real indoor-houseplant disease sets; omit them and it skips cleanly. It
is **train-only**, drops folders it can't confidently label-map, and prints
every discovered folder so you can refine the map. Never let a supplement bleed
into a val slice.

*Portable principle:* group leakage is the most common silent inflator in
plant-disease ML (same-plant, same-background, same-session). Decide the
grouping key first; split on it; never split on the image.

## §4 — training recipe

- **Backbone frozen, head trained.** Frozen DINOv2 ViT-S/14 (21M params, 384-d
  CLS) under `torch.no_grad`; a 1-hidden-layer MLP head (`HEAD_HIDDEN=384`)
  learns. LoRA adapters on the later backbone blocks add a little plasticity
  (`USE_LORA`, `LORA_RANK`, `LORA_ALPHA`, `LORA_TARGET_REGEX`). The §3 probe
  justifies this: features already separate at ~0.91, so a heavyweight
  fine-tune (or a weaker distilled student) is the wrong risk/return.
- **Loss-side balancing only.** `LogitAdjustmentLoss` (Menon et al. ICLR 2021,
  `LA_TAU=0.5`) + label smoothing absorbs noisy free-text labels. The
  `WeightedRandomSampler` is **off by default** to avoid double-correcting rare
  classes; a *source*-weighted sampler down-weights field-crop PlantDoc rows so
  the empirical prior shifts toward indoor houseplants.
- **Optimiser:** AdamW, head `@HEAD_LR` (1e-3) + LoRA tensors `@LORA_LR` (1e-4),
  linear warmup → cosine. Early stopping on `val_indomain` disease-F1; the best
  head epoch is restored.

*Portable principle:* on a strong frozen backbone, prefer "frozen features +
light head (+ optional LoRA)" over full fine-tuning or distillation-to-a-weaker
student — and balance with the loss, not aggressive resampling.

## §6 — export + the ship gate

§6 is where a checkpoint becomes shippable. In order: ONNX FP32 export (opset
17, dynamic batch axis, **CLS embedding as a second output** for the OOD bank)
→ dynamic INT8 (`quantize_dynamic`, `QuantType`) → **drift gate** → temperature
scaling (LBFGS) → RAPS conformal grid search → OOD floor calibration → **ship
gate**. Details in `references/export-and-quantization.md` and
`references/eval-and-calibration.md`. The headline rule: **nothing is written to
`models/` until the ship gate passes.**

## Common tasks

**Add a new disease class** (repo-anchored checklist):
1. §1 — add it to the taxonomy so it lands in `sorted(ImageFolder.classes)`;
   keep the head width in sync. Decide if it starts *deferred* (see below).
2. §2 — add a data source for it (iNat query / supplement folder map) and
   confirm it clears the per-class floor (`THRESH_TRAIN_PER_CLASS`, ~60).
3. `server/diseases_data.py` — add the bilingual content card (EN+RU
   description, treatment, prevention, schedule, red-flags).
4. Re-run end-to-end; the new class must clear the §7 ship gate (no disease
   class at recall=0).
5. Bump `MODEL_VERSION`.

> **Deferred-class pattern:** a label can ship its content card and be accepted
> on `/api/feedback` (to prioritise data collection) *before* the head predicts
> it — `edema`, `sunburn_or_scorch` live here today. Turning one on is a §1 +
> data-fetch change, not a content port.

**Add a data source:** wire it in §2, map its folders to canonical class names,
keep it **train-only** unless you deliberately curated a val slice, and let the
leakage guard group by `plant_id`. Re-run §2→§7.

**Switch families:** set `MODEL_FAMILY` in §1; for `aqua` confirm §5's TF
toolchain installs. Each family writes its own `plantvision_<family>_v<ver>.tar`.

**Re-run one section:** sections share state through the kernel, so a clean
re-run is top-to-bottom. To iterate on §4/§6 only, keep the §2/§3 dataset
objects warm in the kernel and re-execute the later cell — but do a full clean
run before trusting a ship decision (determinism depends on the seeded path).

## Determinism contract

`SEED=0` seeds `random`/`numpy`/`torch`. `cudnn.deterministic=False` is a
deliberate speed/identity trade-off (documented in §1), and TF32 is enabled for
residual fp32 paths — neither changes shipped numerics because export + parity
run on CPU. If you need bit-identical reruns for a debugging A/B, note that the
GPU training path is *seeded* but not *bitwise* deterministic; the exported ONNX
is the stable reference.
