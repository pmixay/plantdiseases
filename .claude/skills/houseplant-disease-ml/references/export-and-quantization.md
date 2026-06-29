# Export & quantization

The runtime is **ONNX-only and architecture-neutral** (the `aqua` on-device tier
is **TFLite**). Training produces PyTorch; nothing PyTorch ships for inference.
§6 of the notebook turns a checkpoint into the calibrated bundle the server
loads. The `aqua` student is exported to TFLite INT8 in §5.

## The artefact contract (what `server/models/` must contain)

The server is **fail-fast** — missing or mismatched artefacts abort boot, no
demo fallback. Validate before deploy with `scripts/check_bundle.py`.

| Artefact | Required? | Notes |
|---|---|---|
| `detector.onnx` (or `.pt`) | **yes** | Stage 1 YOLOv8n; prod ships ONNX |
| `classifier_int8.onnx` | preferred | dynamic INT8; loaded first |
| `classifier.onnx` | fallback | FP32; used when INT8 absent/failed |
| `classifier_fp32.onnx` | conditional | **only present when INT8 drift gate FAILED** — the explicit "INT8 failed" signal |
| `classes.json` | **yes** | the boot contract: `class_names`, `architecture`, `training.img_size`, `inference.tta`, `ood`, `model_version` |
| `temperature.json` | prod | `{temperature, source∈{int8,fp32_fallback}, tta}` |
| `conformal.json` | optional | RAPS calibration; `fit_source` must match `temperature.source` |
| `ood_embedding.npz` | conditional | required iff `classes.json → ood.embedding` is declared |
| `model_card.json` | analysis | provenance, benchmarks, INT8-vs-FP32 paired CI, per-class recall/precision CIs, `metrics_by_split` (with `eval_mode` = classifier-only vs end-to-end), and `aqua` (the shipped int8 TFLite student's per-split metrics + teacher→student drift, measured not guessed) |
| `training_metrics.json` | analysis | per-class recall/precision + `supported_disease_macro_f1` + confusion matrices per slice |

`classes.json` is the contract: its class set **must match the trained head
exactly** or boot aborts (no migration shim). `check_bundle.py` cross-checks the
ONNX logits width against it when `onnxruntime` is available.

## ONNX export (§6)

```
torch.onnx.export(
    export_wrapped, dummy, raw_path,
    opset_version=17,
    input_names=['input'], output_names=['logits', 'embedding'],
    dynamic_axes={'input': {0: 'N'}, 'logits': {0: 'N'}, 'embedding': {0: 'N'}},
)
quant_pre_process(raw_path, fp32_onnx, skip_symbolic_shape=False)
```

Key points:
- **Two outputs.** `logits` (output 0 — every existing call site reads
  `run(...)[0]`) **and** `embedding` (the DINOv2 CLS vector, output 1) used to
  build/score the OOD bank. A legacy single-output graph simply has no
  embedding to score and OOD falls back to the MaxLogit floor.
- **Dynamic batch axis** `N` on every tensor so the server can batch bbox crops.
- `quant_pre_process` (needs `sympy`) runs symbolic shape inference before
  quantization — skipping it produces a worse INT8 graph.
- Export + the parity check run **on CPU**, so TF32/cuDNN settings in §1 don't
  change shipped numerics.

## Dynamic INT8 + the drift gate

```
quantize_dynamic(fp32_onnx, int8_onnx, weight_type=QuantType.QInt8)
```

Dynamic quantization (weights INT8, activations quantized at runtime) needs **no
calibration set** and suits the transformer/MLP head well. But INT8 can shift
predictions, so §6 measures **drift** = the disease-macro-F1 change INT8 vs FP32
on `val_realworld`, computed on **identical bootstrap indices** so the
comparison is paired:

```
WARN_DRIFT, FAIL_DRIFT = 0.02, 0.05
ship_fp32 = PREFER_FP32 or (drift > FAIL_DRIFT) or int8_significantly_worse
```

Decision logic:
- drift ≤ `WARN_DRIFT` → ship INT8 silently (≈4× smaller, faster).
- `WARN_DRIFT` < drift ≤ `FAIL_DRIFT` → ship INT8 with a printed warning.
- drift > `FAIL_DRIFT`, **or** the paired bootstrap says INT8 is *significantly*
  worse → **ship FP32 instead**, emit `classifier_fp32.onnx` as the failure
  signal, and record `int8.shipped=False` in the model card.

This is the same anti-noise discipline as everywhere else: drift is judged by a
**paired bootstrap CI**, not a bare delta. (INT8 sometimes *beats* FP32 within
noise — that's fine, it still ships INT8 and reclaims the size win.)

*Portable principle:* never ship a quantized model on a point-estimate that says
"close enough". Quantify drift against the *primary* metric on the *real-world*
slice, paired, and have a deterministic fallback to FP32 with an explicit
on-disk signal when it fails.

## Aqua: knowledge-distillation → TFLite INT8 (§5)

The `aqua` tier is a **MobileNetV3-Small student** distilled from the gamma
DINOv2 teacher, exported to **TFLite INT8** for fully offline phone inference
(`models/aqua/aqua_classifier.tflite` + its own `classes.json`). It runs only on
an `aqua` notebook run (which also produces the gamma server bundle from the
same teacher). Distillation here is teacher→smaller-student; vanilla KD degrades
when the teacher is *much* stronger, which is exactly why the **server** tier is
a frozen teacher rather than a distilled student — distillation is reserved for
the on-device size constraint where it's unavoidable.

## §6b — bundling for deploy

`§6b` tars one **per-family** archive: `models/plantvision_<family>_v<ver>.tar`.
Server drop-in artefacts (ONNX, `classes.json`, `temperature.json`,
`conformal.json`, `ood_embedding.npz`) sit at the archive **root**; analysis
artefacts (`model_card.json`, `training_metrics.json`, `run_log.jsonl`) ride
along; the on-device `aqua/` subdir is included on an aqua run. Heavy torch
`.pth` checkpoints are **excluded** (runtime is ONNX/TFLite-only).

## Debugging export / quantization

- **Server won't boot** → run `check_bundle.py models/`. Most often
  `classes.json` class count ≠ ONNX head width (re-export after a taxonomy
  change), or a declared `ood.embedding` with no `ood_embedding.npz`.
- **INT8 keeps failing the drift gate** → inspect which class drives it via
  per-class recall on INT8 vs FP32; small classes are the usual culprit. Shipping
  FP32 is the intended, safe outcome — don't force INT8.
- **Calibration mismatch** → `conformal.json.fit_source` must equal
  `temperature.json.source`; if INT8 fell back to FP32, both must say
  `fp32_fallback`. `check_bundle.py` flags the mismatch.
- **`onnxscript`/`onnxslim` import errors at export** → §1 install floors;
  `torch>=2.10` imports `onnxscript` at `torch.onnx.export` load. Re-run the §1
  install cell.
- **Parity check** — after export, run a CPU forward of the ONNX graph against
  the torch model on a few crops; a large logits divergence means the export
  wrapper or opset is wrong, not quantization.
