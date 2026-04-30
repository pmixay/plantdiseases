# PlantScope v5.0 — Model Card

**Status:** trained checkpoints not bundled in the repo (size). Reproduce
via `server/train_notebook.ipynb` on a Colab T4. The notebook drops
artefacts into `server/models/` after `Run All`.

## What the model does

Two-stage houseplant pipeline. Stage 1 detects leaves; Stage 2 classifies
the cropped region into one of 10 conditions plus a `not_a_plant`
rejection class.

| Stage | Architecture | Params | Input | Output |
|-------|--------------|--------|-------|--------|
| Detector | YOLOv8n single-class | ~3.0 M | 640 × 640 RGB | `leaf` bboxes |
| Classifier (primary) | EfficientNet-B0 (torchvision IMAGENET1K_V1) | ~5.3 M | 240 × 240 RGB | 10-way softmax |
| Classifier (peer, optional) | EfficientNetV2-S (torchvision IMAGENET1K_V1) | ~21 M | 300 × 300 RGB | 10-way softmax, blended at weight 0.45 |

## Class taxonomy (alphabetical)

`anthracnose`, `bacterial_leaf_spot`, `gray_mold`, `healthy`,
`iron_chlorosis`, `late_blight`, `mosaic_virus`, `not_a_plant`,
`powdery_mildew`, `sunburn`.

Pests (spider mites, aphids, mealybugs, scale, whitefly, thrips) are
**not** classifier outputs — they appear as `possible_pest_causes` UI
hints driven by `server/diseases_data.py::PEST_INFO`.

## Training data

| Source | Licence | Used for | Approx volume (v5.0) |
|--------|---------|----------|----------------------|
| PlantDoc OD | CC-BY 4.0 | YOLO single-class leaf | ~2 600 images / ~8 700 bboxes |
| PlantVillage (sparse-checkout) | CC-BY-3.0 | classifier — `powdery_mildew`, `iron_chlorosis`, `late_blight`, `sunburn`, `bacterial_leaf_spot`, `mosaic_virus` | up to 800 / class |
| iNaturalist Open Data (CC0/CC-BY/CC-BY-SA) | per-photo | classifier — fungal diseases + `bacterial_leaf_spot` (Pseudomonas syringae, Xanthomonas) and `mosaic_virus` (Tobamovirus) | up to 800 / class |
| Houseplant Species (Kaggle) | mixed (research-only) | classifier — `healthy` via Stage-1 multi-crop | up to 2 500 |
| COCO val2017 | CC-BY 4.0 | classifier — `not_a_plant`, plant-like categories filtered out | up to 1 800 |

The notebook deduplicates by SHA-256 hash before ingest and groups iNat
photos by observation ID so the train/val split never leaks photos of the
same physical leaf across the boundary.

## Training recipe

| Aspect | Value |
|--------|-------|
| Resolution (primary) | 240 × 240 |
| Resolution (peer V2-S) | 300 × 300 |
| Optimiser | AdamW, weight decay 1e-4 |
| Schedule | Linear warmup 1 ep + cosine annealing |
| Phase A (head only) | 8 epochs @ 1e-3 |
| Phase B (fine-tune `features.5+` ⇢ classifier) | 14 epochs @ 5e-5, early-stopping patience 5 |
| Loss | CE with label smoothing 0.05 (focal-CE optional via `USE_FOCAL_LOSS`) |
| Sampler | `WeightedRandomSampler` (single source of class balancing) |
| Augmentations | TrivialAugmentWide + warm-light tint + random side shadow + motion blur + JPEG quality jitter + RandomErasing |
| Mixing | CutMix prob 0.4 (α=1.0) and MixUp prob 0.2 (α=0.2), NAP-safe permutation |
| EMA decay | 0.999 |
| Gradient clip | 1.0 |
| Mixed precision | AMP on (auto-disabled on CPU) |
| Determinism | seeded; `deterministic=True` flag |
| TTA at eval | h-flip; final test pass also adds 5-crop averaging |
| Calibration | Temperature scaling on val NLL, grid 0.3-5.0 |
| Quantisation | Dynamic INT8 on MatMul only (Conv stays FP32) |
| OOD calibration | Sweep across `NAP_DETECTOR_CONF_FLOOR` × `NAP_CLASSIFIER_MSP_FLOOR` on a held-out real-world set |

## Compute budget

Targets a Colab T4 free tier (16 GB GPU, 13 GB system RAM, 60 GB disk).
End-to-end is ~3.5 hours: detector ~70 min, classifier B0 ~70 min, optional
V2-S peer ~40 min on a smaller resolution.

## Inference characteristics

| Path | Latency (2-core CPU VPS, batch=1) | Notes |
|------|----------------------------------|-------|
| Detector ONNX FP32 | ~80–110 ms | 640 px |
| Classifier B0 ONNX INT8 | ~25–40 ms | 240 px, MatMul-only quant |
| Classifier B0 + V2-S ensemble | ~80–120 ms | weighted-average softmax |
| Full pipeline `/api/analyze` (4 bboxes batched) | ~150–250 ms | ensemble off → ~120 ms |

## Known limitations

* Trained on ~10–12 k images across 10 classes; expect the long tail
  (uncommon variegated species, atypical lighting) to confuse the model.
* `mosaic_virus` and `iron_chlorosis` overlap visually on Hoya / Ficus;
  the temperature-scaled top-1 is rarely > 0.7 on those.
* `late_blight` proxy data come from PV tomato/potato; visual transfer
  to ornamentals is reasonable but imperfect.
* The `not_a_plant` class is calibrated against COCO indoor textures.
  Outdoor / agricultural backgrounds may be flagged less reliably.

## Risks and mitigations

* **Hallucinated certainty** — the disease classes have built-in confidence
  gates (`HEALTHY_FALLBACK_MARGIN`, `DISEASE_MIN_CONFIDENCE`,
  `NAP_*_FLOOR`) that downgrade weak verdicts to `healthy` or
  `not_a_plant` rather than asserting a disease.
* **Pest confusion** — pests with disease-like leaf damage are surfaced
  as `possible_pest_causes` hints rather than misclassified.
* **Bacterial vs fungal** — bacterial_leaf_spot and anthracnose share
  brown spotting; the classifier separates them via halo / texture
  cues but the UI advice is conservative (always recommends isolation
  + sanitation regardless of which one wins).

## Evaluation

Generated automatically by the final notebook cell:

* Per-class precision / recall / F1 on the held-out 15 % val split,
  with TTA h-flip.
* Confusion matrix (printed + saved to `classes.json`).
* PyTorch ⇄ ONNX FP32 ⇄ ONNX INT8 drift sanity-check.
* Temperature scaling delta NLL.
* `ensemble_size=2` blended val accuracy when the V2-S peer trained.

Targets (Definition of Done):

* YOLOv8n single-class leaf **mAP@50 ≥ 0.65**
* Classifier ensemble + TTA top-1 ≥ **0.82** on held-out val
