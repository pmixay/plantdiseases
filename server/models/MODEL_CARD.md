# PlantVision — Model Card

**Status:** trained checkpoints not bundled in the repo (size). Reproduce
via `server/train_notebook.ipynb` on a Colab A100 (or T4 with reduced
batch). The notebook drops artefacts into `server/models/` after `Run All`
and overwrites this file with run-specific metrics.

## What the model does

Two-stage houseplant pipeline. Stage 1 detects leaves; Stage 2 classifies
the cropped region into one of 10 conditions plus a `not_a_plant`
rejection class.

| Stage | Architecture | Params | Input | Output |
|-------|--------------|--------|-------|--------|
| Detector | YOLOv8n single-class | ~3.0 M | 640 × 640 RGB | `leaf` bboxes |
| Classifier | EfficientNet-B0 (torchvision IMAGENET1K_V1) | ~5.3 M | 256 × 256 RGB | 10-way softmax |

## Class taxonomy (alphabetical)

`anthracnose`, `bacterial_leaf_spot`, `gray_mold`, `healthy`,
`iron_chlorosis`, `late_blight`, `mosaic_virus`, `not_a_plant`,
`powdery_mildew`, `sunburn`.

Pests (spider mites, aphids, mealybugs, scale, whitefly, thrips) are
**not** classifier outputs — they appear as `possible_pest_causes` UI
hints driven by `server/diseases_data.py::PEST_INFO`.

## Training data

| Source | Licence | Used for | Approx volume |
|--------|---------|----------|---------------|
| PlantDoc OD | CC-BY 4.0 | YOLO single-class leaf | ~2 600 images / ~8 700 bboxes |
| PlantVillage (sparse-checkout) | CC-BY-3.0 | classifier disease classes | up to 800 / class |
| iNaturalist Open Data | per-photo CC0/CC-BY/CC-BY-SA | classifier diseases | up to 800 / class |
| Houseplant Species (Kaggle) | mixed (research-only) | classifier `healthy` via Stage-1 single-crop | 800 photos |
| COCO val2017 | CC-BY 4.0 | classifier `not_a_plant` (plant-like categories filtered) | up to 1 800 |
| User real-world (RAW/real_world/) | proprietary | classifier — all classes, in-train + held-out 20 % | per-class |

The notebook deduplicates by SHA-256 hash before ingest and groups iNat
photos by observation ID so the train/val split never leaks photos of the
same physical leaf across the boundary.

## Training recipe

| Aspect | Value |
|--------|-------|
| Resolution | 256 × 256 |
| Optimiser | AdamW, weight decay 1e-4 |
| Schedule | Linear warmup 1 ep + cosine annealing |
| Phase A (head only) | 10 epochs @ 1e-3·sqrt(B/96) |
| Phase B (fine-tune `features.5+` ⇢ classifier) | 22 epochs @ 5e-5·sqrt(B/96), early-stopping patience 8 (on disease F1) |
| Primary metric | **disease macro-F1** (8 диагнозов, без `healthy` / `not_a_plant`) |
| Loss | class-balanced CE: inverse-frequency weights × asymmetric down-weighting `healthy` × 0.35; label smoothing 0.05 |
| Sampler | natural distribution (no `WeightedRandomSampler` — would double-weight against the loss) |
| HP→healthy build | 800 photos × 1 crop (largest bbox), 30 % with hard-aug (blur + JPEG q=15-30) |
| Augmentations | TrivialAugmentWide + warm-light tint + random shadow + motion blur + JPEG quality jitter + RandomErasing |
| Mixing | CutMix prob 0.4 (α=1.0), MixUp prob 0.2 (α=0.2), NAP-safe permutation |
| EMA decay | 0.999 |
| Gradient clip | 1.0 |
| Mixed precision | bf16 on Ampere+, fp16+GradScaler otherwise |
| Acceleration | `torch.compile` reduce-overhead (auto-disabled on CPU) |
| TTA at eval | h-flip; demo also runs 5-crop averaging |
| Calibration | Temperature scaling on val NLL, grid 0.3-5.0 |
| Quantisation | Dynamic INT8 on MatMul only (Conv stays FP32) |
| OOD calibration | Sweep across `NAP_DETECTOR_CONF_FLOOR` × `NAP_CLASSIFIER_MSP_FLOOR` |

## Compute budget

A100-40 GB end-to-end ≈ 30-40 min (detector ~10 min, classifier ~25 min).
T4 fallback ≈ 2-3 hours.

## Inference characteristics

| Path | Latency (2-core CPU VPS, batch=1) | Notes |
|------|----------------------------------|-------|
| Detector ONNX FP32 | ~80–110 ms | 640 px |
| Classifier ONNX INT8 | ~30–45 ms | 256 px, MatMul-only quant |
| Full pipeline `/api/analyze` (4 bboxes batched) | ~120–180 ms | |

## Known limitations

* `mosaic_virus` ↔ `iron_chlorosis` пересекаются на жёлтых листьях.
* `late_blight` proxy — field-crop (PV tomato/potato), transfer на indoor ornamentals имперфектный.
* `bacterial_leaf_spot` / `sunburn` обучены через визуальные proxy.
* `anthracnose` слабый класс (мало iNat данных) — добавь real-world фото в `RAW/real_world/anthracnose/` для следующего прогона.
* The `not_a_plant` class is calibrated against COCO indoor textures.

## Risks and mitigations

* **Healthy bias** — main risk vector. Mitigated by class-balanced CE
  with `healthy × 0.35`, dropped `WeightedRandomSampler`, hard-aug on
  30 % HP crops, and a per-epoch `predicted_healthy / true_healthy`
  monitor that alarms above 2.0.
* **Hallucinated certainty** — disease classes have built-in confidence
  gates (`HEALTHY_FALLBACK_MARGIN`, `DISEASE_MIN_CONFIDENCE`,
  `NAP_*_FLOOR`) that downgrade weak verdicts to `healthy` or
  `not_a_plant`.
* **Pest confusion** — pests with disease-like leaf damage are surfaced
  as `possible_pest_causes` hints rather than misclassified.

## Evaluation

Generated automatically by the final notebook cell:

* Per-class precision / recall / F1 on the held-out val split (15 %
  stratified + 20 % of real-world per class), with TTA h-flip.
* Confusion matrix (printed + saved to `classes.json`).
* PyTorch ⇄ ONNX FP32 ⇄ ONNX INT8 disease-F1 drift check.
* Temperature scaling delta NLL.
* Healthy bias ratio.

Targets (Definition of Done):

* YOLOv8n single-class leaf **mAP@50 ≥ 0.65**
* Classifier **disease macro-F1 ≥ 0.55** on held-out val
* Healthy bias ratio ≤ 1.5
