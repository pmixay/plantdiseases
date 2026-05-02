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

Three independent slices, three reports per release:

| Slice | Role | Pass criterion |
|---|---|---|
| `val_indomain` | early stopping + schedule decisions | non-decreasing for ≥ 4 epochs |
| `val_realworld` (`indoor_real_world_v1`) | **primary — ship/no-ship gate** | disease macro-F1 in **0.70 – 0.78**; no class with recall = 0; healthy-bias < 1.5 |
| `val_studio` (`plantvillage_studio`) | transfer / background-bias monitor (Noyan 2022) | tracked, never blocking |

Each epoch logs disease macro-F1 + per-class recall + confusion matrix
+ healthy-bias on all three slices. Top-1 alone hides healthy collapse
and rare-class death.

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
- Calibration: temperature scaling on `val_realworld` NLL **plus**
  inductive conformal prediction (RAPS, Angelopoulos 2021) calibrated
  on a 1–2 k user-photo holdout. Persisted to `models/conformal.json`;
  consumed by `server/conformal.py`. Coverage target: 90 % (`alpha=0.10`).
- Quantisation: **static INT8 QDQ** with a 500-photo `val_realworld`
  calibration reader. The export step re-evaluates the INT8 model on
  `val_realworld` and refuses to write the artefact if disease F1
  drops by more than 0.02 vs FP32.

## OOD detection
- Score: **MaxLogit** (Hendrycks 2019) — robust to temperature scaling
  and label smoothing where MSP fails.
- Production combines: detector confidence + calibrated classifier
  confidence + MaxLogit score.
- For ViT branches we'd swap in GEN (Liu CVPR 2023).

## Optional follow-ups (post-v2.0)
- **DeepCore** (Guo et al. 2022) coreset / dataset pruning if
  iteration time becomes a bottleneck. Random selection stays as the
  no-op control. **No** dataset distillation — ROI weak at our
  resolution. Model pruning is low priority — KD + static INT8 cover
  ~95 % of the runtime budget.

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
