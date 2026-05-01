# PlantVision — MODEL_CARD

Generated: 2026-05-01T19:42:21+00:00

## Pipeline
Stage 1: YOLOv8n single-class leaf detector. Stage 2: EfficientNet-B0 @ 288.

## Classes
anthracnose, bacterial_leaf_spot, gray_mold, healthy, iron_chlorosis, late_blight, mosaic_virus, not_a_plant, powdery_mildew, sunburn

## Held-out val metrics

Primary metric: **disease macro-F1** (8 диагнозов, без `healthy` / `not_a_plant`).

| Path | top-1 | disease F1 |
|------|-------|------------|
| Torch B0 (EMA + h-flip TTA) | 0.9102 | 0.8732 |
| ONNX FP32 | 0.9122 | 0.8768 |
| ONNX INT8 | 0.9122 | 0.8768 |

Healthy bias (predicted_healthy / true_healthy): **0.84** (norm <1.5, alarm >2.0)
Temperature: 0.520 (val NLL 0.4679 → 0.3080)

## Artefact sizes
| File | Size |
|------|------|
| detector.pt | 6.23 MB |
| detector.onnx | 12.27 MB |
| classifier.pth | 16.38 MB |
| classifier_int8.onnx | 16.06 MB |

## Config
- IMG_SIZE: 288
- BATCH_SIZE: 192
- Loss: class-balanced CE (inverse-freq × healthy 0.25), label_smoothing=0.05
- CutMix / MixUp: 0.4 / 0.2
- EMA decay: 0.999
- Sampler: natural (no WeightedRandomSampler)
- HP target: 800 photos, 1 crop/photo, 30% with hard-aug (blur + low-quality JPEG)
- Real-world фото: материализованы из RAW/real_world/ (80/20 split)
- Suggested NAP_CLASSIFIER_MSP_FLOOR: 0.35

## Known limitations
- `mosaic_virus` ↔ `iron_chlorosis` пересекаются на жёлтых листьях.
- `late_blight` proxy — field-crop, transfer на indoor ornamentals имперфектный.
- `bacterial_leaf_spot` / `sunburn` обучены через визуальные proxy.
- `anthracnose` слабый класс — мало iNat данных, добавь real-world фото в `RAW/real_world/anthracnose/`.
