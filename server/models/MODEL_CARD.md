# PlantVision — MODEL_CARD (v2.0)

## Pipeline
**Stage 1:** YOLOv8n single-class `plant_leaf` detector, trained on
300–800 hand-labelled **indoor** leaf bboxes plus targeted Roboflow
Universe houseplant slices. PlantDoc is excluded by design.

**Stage 2:** EfficientNetV2-S (`tf_efficientnetv2_s.in21k_ft_in1k`) teacher
→ distilled MobileNetV3-Large student (KD, α=0.35, τ=3.5).

## Classes (11)
`bacterial_leaf_spot`, `edema`, `gray_mold_botrytis`, `healthy`,
`leaf_scorch_underwatering`, `leaf_spot_fungal`, `not_a_plant`,
`nutrient_deficiency`, `powdery_mildew`, `sunburn_or_scorch`,
`viral_disease`.

Removed vs v1.x: `late_blight`, `iron_chlorosis` (subsumed into
`nutrient_deficiency`). Pests are out of the taxonomy entirely.

Coarse-group rationale:
- `leaf_spot_fungal` covers anthracnose + cercospora + alternaria.
- `viral_disease` covers mosaic + leaf curl + ringspot.
- `nutrient_deficiency` covers Fe + N + Mg + K.

## Validation contract
**Primary metric:** disease macro-F1 on `indoor_real_world_v1`
(frozen 500–1000 user photos, varied devices/windows/lamps/distances).
Decisions to ship are made on this number.

`plantvillage_studio` is kept as an **isolated transfer / background-bias
slice** (Noyan 2022: 49 % accuracy from 8 background pixels). It is
**never** in train.

## Training recipe
- Optimiser: AdamW, weight_decay=1e-4, label_smoothing=0.05
- LR schedule: cosine + 1-epoch warmup
- 256 px input, batch=128, bf16-mixed
- Phase A (head-only, 3 epochs, lr=3e-3) → Phase B (unfreeze last 2
  stages, 12–15 epochs, head lr=5e-4 / backbone lr=5e-5)
- Loss: **Logit Adjustment** (Menon ICLR 2021), τ=1.0
- Augs: indoor-targeted — WB shift, glare, defocus/motion blur, JPEG,
  ISP-imitation, soft shadows. MixUp 0.10, CutMix 0.10–0.20. No strong
  hue shift / Solarize (colour is a disease signal).
- Distillation: KD into MobileNetV3-Large (5.5 M params), 12 epochs.
- Calibration: temperature scaling on indoor val NLL.
- Quantisation: dynamic INT8 on MatMul.

## OOD detection
- Score: **MaxLogit** (Hendrycks 2019) — robust to temperature scaling
  and label smoothing where MSP fails.
- Production combines: detector confidence + calibrated classifier
  confidence + MaxLogit score.
- For ViT branches we'd swap in GEN (Liu CVPR 2023).

## Pre-training data hygiene
- SHA-256 + pHash (Hamming ≤ 8) dedup.
- DINOv2 ViT-B/14 embeddings → drop > 2σ from class centroid.
- Cleanlab label-issue ranking + FiftyOne embedding browser to triage
  top-N suspects.
- Manual validation pass: 200–300 photos per class.
- Hierarchical sidecar metadata per crop: `plant_id`, `species`,
  `diagnosis`, `severity`, `leaf_or_whole_plant`, `capture_session`,
  `source`, `label_confidence`, `bbox`. Splits are disjoint by
  `plant_id` (never let the same plant appear in train and val).

## Time budget on 1× A100-40
- 5–10 min DINOv2-S frozen + linear probe (mandatory diagnostic)
- ~35 min EfficientNetV2-S fine-tune (teacher)
- 10–15 min KD into MobileNetV3-Large student
- 5 min temperature calibration + ONNX export

## Known caveats
- `viral_disease` is incurable; the pipeline auto-escalates severity
  by one level on any high-confidence viral verdict.
- `edema` and `nutrient_deficiency` are physiological / nutritional —
  neither is an infection. They were added to give the AI Doctor
  honest options for the most common indoor mis-diagnoses.
