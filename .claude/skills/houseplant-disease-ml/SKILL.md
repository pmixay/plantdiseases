---
name: houseplant-disease-ml
description: >-
  ML-specialist playbook for the PlantVision houseplant-disease-detection
  project (the `plantdiseases` repo) and houseplant-disease vision ML in
  general — Python + Jupyter focused. Use this WHENEVER the task touches: the
  training notebook (`server/train_notebook.ipynb`, §1–§7); the two-stage
  pipeline (YOLOv8n leaf detector → frozen DINOv2 ViT-S/14 + LoRA + MLP head);
  the model families (aqua / gamma / frontier); training, evaluation,
  calibration (temperature scaling, RAPS conformal sets), OOD/blur gates, or the
  ship gate; ONNX/TFLite export and INT8 quantization; paired-bootstrap model
  A/B testing; the `server/models/` artefact bundle or why the server won't
  boot; or the feedback → retraining loop. Trigger even when the user names only
  a sub-piece ("add a disease class", "INT8 drift", "why is macro-F1 down",
  "conformal coverage", "classes.json mismatch", "is this improvement real")
  without naming the project. Prefer this skill's discipline over ad-hoc ML
  reasoning anywhere in this codebase.
---

# Houseplant disease detection — ML specialist

A repo-anchored playbook for working as the ML engineer on **PlantVision**
(the `plantdiseases` repo). It's built around this project's concrete files,
constants, and conventions, but the *methodology* (group-aware splits,
calibration discipline, paired-bootstrap ship gates, honest-uncertainty gating)
transfers to houseplant-disease vision ML generally. Scope is **server-side
Python + the training notebook**; ignore the Android/Kotlin client.

## The system in one breath

A **two-stage** pipeline: **Stage 1** YOLOv8n single-class leaf detector crops
the leaf; **Stage 2** a **frozen DINOv2 ViT-S/14 backbone + LoRA + trained MLP
head** classifies the crop into **9 classes** (8 conditions + a real
`not_a_plant` rejection head). Runtime guards — blur gate, an OOD AND-gate,
temperature scaling, RAPS conformal sets, weak-healthy reroute — make it say
"I'm not sure" instead of guessing. Three capacity **families** share one
recipe: `aqua` (MobileNetV3-Small distilled → TFLite INT8, **on-device**),
**`gamma`** (DINOv2 ViT-S/14, **production default**), `frontier` (DINOv2
ViT-B/14, GPU). The whole bundle is produced **deterministically** by
`server/train_notebook.ipynb` and served by a **fail-fast** FastAPI app.

## How to use this skill

1. **Start by locating, not loading.** The training notebook has multi-thousand-
   line cells — never read it whole. Use `scripts/nbtool.py` (`list` /
   `section N` / `grep PATTERN`) to find the right piece first.
2. **Open the matching reference** for the workflow, then act:

   | The task is about… | Read |
   |---|---|
   | running/extending the notebook, adding a class or data source, switching families, the §1–§7 map | `references/notebook-training.md` |
   | metrics, the ship gate, temperature/conformal calibration, OOD/blur tuning, "is this improvement real?" | `references/eval-and-calibration.md` |
   | ONNX/TFLite export, INT8 quantization + drift gate, the `models/` artefact contract, "server won't boot" | `references/export-and-quantization.md` |
   | the serving pipeline (gates, voting, severity), the feedback/retraining loop, the test suite | `references/pipeline-and-feedback.md` |

3. **Measure with a tool, don't eyeball.** Every question below has a tool
   that already answers it; reinventing one is how this project got a
   truncating lister and a strided augmentation count.

   **In the skill** (agent workflow):

   | Tool | Answers |
   |---|---|
   | `scripts/nbtool.py list \| section N \| grep PATTERN \| scaffold OUT.ipynb` | where is X in the notebook? what is the live value of CONST? — **never read the notebook whole**, its cells run to thousands of lines |
   | `scripts/check_bundle.py models/` | does this bundle satisfy the fail-fast boot contract? (`classes.json` ↔ head width, missing calibration, OOD bank drift) |
   | `assets/analysis_boilerplate.py` | canonical taxonomy + disease-macro-F1 for a new analysis notebook, so numbers stay comparable to the gate |

   **In the repo** (`cd server`; these are lint- and CI-tested, which is why
   they live there and not here — one copy, one source of truth):

   | Tool | Answers |
   |---|---|
   | `tools/eval_bootstrap.py` | is this A/B difference real? paired bootstrap CI — **the** answer to "did it improve" |
   | `tools/paired_eval.py` | two bundles, one held-out set, per-class + McNemar |
   | `tools/filter_supplement.py --root D --bench B --out R` | is a downloaded corpus what its manifest claims? benchmark leakage, duplicate clusters, blur, resolution. **Exits non-zero on leakage** |
   | `tools/kaggle_inventory.py --list REF \| --gate REF --folder F` | what is *actually* in a Kaggle dataset, and is it the benchmark? raises rather than truncating |
   | `tools/build_ood_benchmark.py` | build/score the never-trained OOD benchmark |
   | `tools/analyse_feedback.py` | read-only production-feedback corpus analysis |
   | `tools/export_data.py` | bundle the production stores for offline labelling |

## Golden rules (the project's discipline)

These are the non-obvious commitments that make this codebase trustworthy. Honor
them even when a request doesn't mention them.

1. **A point-estimate metric gap is not evidence.** At n≈2k `val_realworld`,
   macro-F1 noise is ≈±0.04–0.05. Before claiming any A/B win — model vs model,
   INT8 vs FP32 — use a **paired bootstrap CI** (`tools/eval_bootstrap.py`). If
   the difference CI straddles 0, it's noise.
2. **`classes.json` is the boot contract.** Its class set must match the trained
   head exactly; the server is **fail-fast** with no migration shim and no demo
   fallback. After any taxonomy change, re-export and run `check_bundle.py`.
3. **Nothing ships until BOTH gates pass — and the in-domain one is not
   enough.** In-domain (`val_realworld`): disease macro-F1 ≥ **0.70**, no
   disease class at recall 0, healthy-bias < 1.5, healthy recall ≥ 0.70.
   Out-of-domain (§6, blocking since 2026-08-18): the never-trained OOD
   benchmark, **per arm, never pooled** — `hibiscus` scorch recall ≥ 0.70 and
   healthy false-fire ≤ 0.12 on the same 151 never-trained clusters.

   The second gate exists because the first one is satisfiable by a collapsed
   model: **2.7.0, 2.7.1 and 2.7.2 each passed in-domain while scoring ~0
   out-of-domain**, because in-domain scorch recall went *up* (0.784 → 0.941)
   over the same releases. An in-domain split drawn from the same supplement is
   the same photographic regime, however carefully it is grouped.

   `val_indomain` is the metric that *stops training*; keep it separate from
   both. `val_studio` never blocks.
4. **Calibrate, then express uncertainty.** Temperature scaling fixes
   probabilities; conformal sets express "either of these two"; OOD/blur gates
   refuse non-leaf/blurry inputs. `temperature.json.source` and
   `conformal.json.fit_source` must agree (the calibration must be fit on the
   numerics path that ships).
5. **Honest uncertainty over confident wrongness.** Never manufacture a disease
   false-positive on a healthy plant, never reassure on a weak-healthy one. Flag
   (`uncertain_healthy`, `low_confidence`, `blurry_image`) rather than commit.
6. **Don't split on the image — split on the plant.** Group-aware splits by
   **`(source, plant_id)`** prevent same-plant/same-session leakage, the silent
   metric inflator in plant-disease data. The supplement is **supply *and*
   eval**, not train-only: §2 routes each row by its own `split_hint`
   (`eval_ok` / `train_only`), and only `hn_kind=boundary` rows are forced
   train-only (via the `supplement_boundary` source, listed in no split's
   `allowed_sources`). A permanent holdout is carved by a stable sha1 bucket
   over `(source, plant_id)`, so membership is a property of the row rather
   than of the draw.
7. **A dataset that is "never trained on" is not automatically usable — it
   also has to not be the thing we grade with, or a re-upload of it.** pHash
   every candidate against the OOD benchmark at Hamming ≤ 6 *before* proposing
   it, and against the datasets the benchmark was **built from**: a re-upload of
   the source passes a cluster-level gate while carrying the same leaves,
   session and background. Deny at the *branch*, not the folder
   (`KAGGLE_DENY_FOLDERS` is substring-matched). Both mistakes have been made
   here — see the deny-list in `docs/DATASET_LABELING_PLAN.md`.
8. **Count distinct photographs, never files, and never trust a lister that can
   truncate.** The benchmark's own source folder is ~1,400 files and 62
   photographs. Report pHash clusters; estimate augmentation from a *contiguous*
   sample (a strided one steps over adjacent duplicates and reports 1.0 on a 37×
   collapsed folder). A negative result from a capped lister is not evidence of
   absence — one dataset re-listed uncapped went 24,000 → 49,878 files, and
   Kaggle's *search* endpoint truncates at 20/page independently of `pageSize`.
9. **`MODEL_VERSION` is the single source of truth.** One string in §1, written
   into `classes.json` + `model_card.json`. Bump it on any change to shipped
   numerics.
10. **Determinism.** `SEED=0`; the exported ONNX (parity-checked on CPU) is the
   stable reference, not the seeded-but-not-bitwise GPU training path.

## Mental model — families & the artefact bundle

| Family | Backbone | Where | Export |
|---|---|---|---|
| `aqua` | MobileNetV3-Small (**distilled** from gamma) | **on-device, offline** | TFLite INT8 |
| **`gamma`** | frozen DINOv2 ViT-S/14 + MLP head | server (**production**) | ONNX INT8 |
| `frontier` | frozen DINOv2 ViT-B/14 (~86M) + MLP head | server (GPU, gated off) | ONNX INT8 |

`gamma`+`frontier` share one frozen-DINOv2 recipe; `aqua` is the only distilled
student (its teacher *is* gamma). An `aqua` notebook run produces **both** the
gamma server bundle and the aqua on-device model from one pass.

The bundle the server loads from `models/`: `detector.onnx`,
`classifier_int8.onnx` (or `classifier.onnx`), `classes.json`,
`temperature.json`, `conformal.json`, `ood_embedding.npz`, plus analysis
sidecars (`model_card.json`, `training_metrics.json`). The contract +
per-artefact rules live in `references/export-and-quantization.md`; validate with
`check_bundle.py`.

## The 9 classes

`gray_mold_botrytis, healthy, leaf_scorch_underwatering, leaf_spot,
not_a_plant, nutrient_deficiency, pests, powdery_mildew, viral_disease`
(alphabetical == `sorted(ImageFolder.classes)`; logit index i ↔ name i). `pests`
merges mealybugs+scale; `leaf_spot` merges fungal+bacterial — neither pair is
phone-photo separable. `edema` and `sunburn_or_scorch` are **deferred**: content
cards + `/api/feedback` acceptance exist, but the head doesn't predict them yet.

## Conventions cheat-sheet

- **Lint/format:** `ruff` — line-length 110, target `py314`, rules `E,F,I,B`,
  ignore `E501,B008`, notebook excluded. Run `cd server && ruff check .`.
- **Tests:** `pytest` (45 files); `cd server && pytest -q`. Mirror existing
  invariant tests when you touch the contract. `tests/conftest.py` swaps in a
  `FakePipeline`, so **no trained weights are needed** to run the suite.
- **Deps:** runtime `requirements.txt` is lean (ONNX-only; **no** torch/sklearn);
  eval/dev tools live in `requirements-dev.txt`. Don't add a training/eval dep to
  the runtime image.
- **numpy 2.x** across the stack; `onnxruntime>=1.26`. The notebook leaves
  torch/torchvision as low floors on purpose (Colab ships a CUDA-matched build).
- **Style:** fail-fast over fallbacks; append-only feedback; read-only analysis;
  bilingual (EN/RU) content cards in `diseases_data.py`.

## Repo map (server/)

```
train_notebook.ipynb   §1–§7 end-to-end recipe (the artefact factory)
pipeline.py            two-stage pipeline + blur/OOD/reroute/severity
detector.py            Stage 1 YOLOv8n (640px, plant_leaf)
classifier.py          Stage 2 DINOv2+head ONNX runtime (fail-fast)
conformal.py  ood.py   RAPS prediction sets · embedding-kNN / MaxLogit OOD
diseases_data.py       bilingual disease database (content cards)
feedback.py            append-only feedback store + per-class metrics
ai_services.py         optional PlantNet / OpenRouter / care-plan (graceful)
labeling.py labeling_auto.py   data-collection / labelling loop
community.py feedback.py         nearby-scans feed · append-only training signal
tools/eval_bootstrap.py    paired-bootstrap CIs (the trustworthy A/B)
tools/paired_eval.py       two bundles on one held-out set (per-class + McNemar)
tools/build_ood_benchmark.py   build/score the never-trained OOD benchmark
tools/filter_supplement.py     QA a downloaded corpus on the bytes (leakage, dups, blur)
tools/kaggle_inventory.py      uncapped Kaggle lister + benchmark-contamination gate
tools/analyse_feedback.py  read-only feedback corpus analysis
tools/export_data.py       bundle production stores for offline labelling
models/                drop trained artefacts here (not committed); MODEL_CARD.md
tests/                 45 pytest files encoding the invariants
```

## Portability note

When the work isn't on this exact repo, keep the **principles** (golden rules,
the two-stage + frozen-backbone + light-head pattern, group splits, calibration
+ conformal, paired-bootstrap gating, honest-uncertainty refusal) and drop the
repo-specific paths/constants. The references flag the transferable idea behind
each concrete number as a *"portable principle"*.
