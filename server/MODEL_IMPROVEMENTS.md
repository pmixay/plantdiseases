# Model-improvement report — implementation status

This tracks the recommendations from the external report *"Improving the
Houseplant Disease Classifier: From 0.73 to the 0.91 Ceiling"* against
this codebase. Several were already shipped before the report; this
change set lands the rest that are implementable **in-repo without
retraining**, and rewrites the training notebook to train the
recommended backbone.

> **Important:** this environment cannot retrain (no GPU, no training
> data, no DINOv2 weights). The shipped artefacts remain the MobileNetV3
> v2.2 model. The notebook rewrite produces the new model **when run on a
> GPU box**; serving keeps working unchanged until that model is trained
> and its artefacts are dropped into `server/models/`.

## Already in the codebase before the report

| Recommendation | Where |
|---|---|
| Logit-adjustment CE loss (Menon ICLR 2021, τ=0.5) | `classes.json` / notebook §4 |
| Instance-balanced sampling, **not** stacked with loss reweighting (the "don't double-correct" rule) | notebook §4 `USE_*_SAMPLER` guards |
| MaxLogit OOD (robust under temperature + label smoothing) | `pipeline.py`, notebook §6 |
| Temperature scaling | `temperature.json`, notebook §6 |
| Conformal RAPS prediction sets | `conformal.json`, notebook §6 |
| DINOv2-S frozen probe demonstrating the ~0.91 ceiling | notebook §3, `dinov2_probe.json` |
| Keep the YOLOv8n detector unchanged (multi-leaf UI depends on it) | §3 untouched |

## Landed in this change set

| Recommendation | Status | Where |
|---|---|---|
| **Stage 1 — frozen DINOv2 ViT-S/14 + trained head** as the production model (no KD) | Notebook rewritten (§4/§5/§6) | `train_notebook.ipynb` |
| **Dynamic INT8** for the ViT (ORT guidance: dynamic for transformers, static for CNNs) | Implemented | notebook §6 `quantize_dynamic` |
| **Stage 0 — paired-bootstrap macro-F1 CIs** ("0.73 > 0.69 is noise") | Implemented + unit-tested | `tools/eval_bootstrap.py`, `tests/test_eval_bootstrap.py`, notebook §6 |
| **Stage 0 — per-class recall bootstrap CIs** (paired + single-model; the weak-class deltas the report tracks) | Implemented + unit-tested | `tools/eval_bootstrap.py` (`bootstrap_per_class_recall_ci` / `paired_per_class_recall`), notebook §6 model_card |
| **Stage 3 — horizontal-flip TTA (2×)** | Implemented, **off by default** | `classifier.py` (`inference.tta`), notebook §6 (`USE_TTA_HFLIP`) |
| Architecture-neutral serving (a DINOv2 ONNX is a drop-in) | Implemented | `classifier.py` `architecture` field + ONNX path |
| Drop distillation; if a small model is ever needed use **DIST/DKD** not vanilla KD | Documented | notebook §5 |

### Design-fit notes (why these are safe)

* **Serving is unchanged by default.** The ONNX inference path is already
  architecture-agnostic (reads `img_size` from `classes.json`, uses
  ImageNet normalisation = DINOv2's). The only serving additions are an
  `architecture` field (steers the `.pth` *dev* fallback only; production
  is ONNX) and the `inference.tta` flag — both default to the historical
  behaviour, so the shipped MobileNetV3 v2.2 model behaves identically.
* **TTA cannot silently decalibrate.** `hflip` averages **logits** (not
  softmax probs) so temperature scaling, the MaxLogit OOD score and
  conformal RAPS all keep operating on the quantity they were fit on. The
  notebook fits all of them through the same TTA mode and writes
  `inference.tta`, so enabling it requires a matching recalibrated model —
  the default stays `none`.
* **DINOv2 input is 224 px** (16×14; DINOv2 patches are 14 px), down from
  256. Serving picks this up from `classes.json -> training.img_size`
  with no code change. An ONNX↔torch parity assert in §6 fails loudly if
  the ViT position-embedding interpolation breaks the export.

## Deferred — needs data / GPU / a real-hardware benchmark (not doable here)

| Recommendation | Why deferred |
|---|---|
| Supplement weak classes with real abiotic-stress datasets (CoLeaf, banana/lettuce/maize NPK, OLID I, sugar-beet) | Requires data acquisition + retraining |
| Clean noisy iNaturalist free-text labels (noise-robust loss / manual audit) | Data-labelling effort |
| cRT/LWS classifier re-training (decoupling) beyond the frozen-backbone + LA-loss head | Marginal once on a frozen backbone; revisit if weak-class recall stalls |
| Single-core ONNX latency / RAM benchmark on the target Ryzen 7950X3D | Must be measured on real hardware (`tools/benchmark_backends.py` is ready) |
| LoRA fine-tune of the backbone for the last few points | Optional follow-up once the frozen-head ceiling is confirmed in prod |

## How to ship the new model

1. Run `train_notebook.ipynb` on a GPU box (it pip-installs its own deps
   and fetches public data). It writes `classifier{,_int8}.onnx`,
   `classes.json` (with `architecture` + `inference.tta`),
   `temperature.json`, `conformal.json`, `model_card.json`.
2. Benchmark on the target CPU: `python tools/benchmark_backends.py
   --model-dir server/models` (must fit the 1–2 s single-core budget).
3. Drop the artefacts into `server/models/` and deploy. No server code
   change is required.
