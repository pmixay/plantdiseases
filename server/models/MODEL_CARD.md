# PlantVision — Model Card

> **What this file is.** A durable description of the *contract*: what the
> model does, how the two stages and the guards around them are wired, what
> the class taxonomy promises, how calibration and the ship gates work, and
> where the model must not be trusted. It is written to survive a training
> run unchanged.
>
> **What this file is not.** It carries **no run-specific numbers** — no
> macro-F1, no temperature, no per-class CIs, no artefact sizes, no version
> history. Two other places own those, and each fact has exactly one home:
>
> | You want | Look in |
> |---|---|
> | per-run provenance for the bundle in your hands | `model_card.json`, shipped beside the artefacts |
> | which version is live, what shipped, what failed the gate, what is next | [`docs/ROADMAP.md`](../../docs/ROADMAP.md) → **Model track** |
> | the version string itself | `MODEL_VERSION` in `train_notebook.ipynb` §1 |
>
> If an edit here needs a version number to make sense, it belongs in the
> roadmap instead.

## Bundle

Artefacts are **never committed and never auto-downloaded**. Boot is
fail-fast: `server/models/` must hold `detector.onnx` (the configured
`detector.pt` path is an anchor — serving resolves the ONNX sibling,
`server/detector.py:_resolve_detector_path`) and `classifier_int8.onnx` or
`classifier.onnx`. `classes.json`, `temperature.json`, `conformal.json` and
`ood_embedding.npz` are each optional in the sense that the server still
boots without them — and each one absent silently downgrades behaviour, so
treat the full set as required. Validate a bundle with
`.claude/skills/houseplant-disease-ml/scripts/check_bundle.py`.

Notebook §6b packs one archive per **training mode**,
`models/plantvision_<train_mode>_v<version>.tar` (`…_gamma_…`,
`…_combined_…`, `…_frontier_…`). Server drop-in artefacts sit at the archive
**root** so a flatten drops them straight into `models/`; analysis artefacts
(`model_card.json`, `training_metrics.json`, `run_log.jsonl`) ride along; the
on-device `aqua/` subdir (`aqua_classifier.tflite` + its own `classes.json`)
is included when the run produced one. Torch checkpoints are excluded — the
runtime is ONNX/TFLite only.

`model_card.json` also carries a `benchmark` block: batch-1 latency
(p50/p95/mean ms) + on-disk size for the shipped classifier ONNX and, on a
distilling run, the aqua TFLite. `cpu_1thread` is the canonical cross-tier
number (single-core server budget) and **includes hflip TTA** when
`inference.tta = "hflip"`; a `cuda` timing is added when a GPU provider is
present. It is relative, not an SLA.

## Pipeline

Two stages, orchestrated by `server/pipeline.py`:

1. **Stage 1 — YOLOv8n**, single class `plant_leaf`, trained in notebook §3
   from PlantDoc bounding boxes with `not_a_plant` images added as
   empty-label background negatives (without them the detector fires at 0.9
   on pure noise), then **re-selected on recall** across the saved epochs
   rather than on Ultralytics' fitness. It localises the leaf so Stage 2 sees
   a tight crop, not a whole room.
2. **Stage 2 — classifier** on each crop at the `classes.json` `img_size`.
   The "is it diseased / which disease" decision is 100 % Stage 2.

Evaluation order, with the constants that govern each step (all in
`pipeline.py` unless noted):

| # | Step | Governed by |
|---|---|---|
| 1 | blur gate — short-circuits to a synthetic `not_a_plant`, classifier never runs | `BLUR_LAPLACIAN_FLOOR = 50.0`, `BLUR_DOWNSCALE = 320` |
| 2 | detect → crop; whole frame folded in as an extra vote | `FOLD_WHOLE_FRAME_VOTE = True` |
| 3 | batched classify, temperature applied per crop | `temperature.json` |
| 4 | mean vote over crops + whole frame | `_fold_votes` |
| 5 | OOD gate (three-of-three, below) → `not_a_plant` reroute | `classes.json → ood` |
| 6 | postprocessing: weak-healthy reroute, viral caveat, confidence warnings | `HEALTHY_*`, `VIRAL_MIN_CONFIDENCE = 0.65` |
| 7 | severity, escalated one level on a high-confidence `viral_disease` | `_DEGENERATIVE_DISEASES` |
| 8 | RAPS prediction set — **omitted on a `not_a_plant` verdict** | `conformal.json` |

**Weak-healthy reroute** fires only when top-1 `healthy` is below
`HEALTHY_REROUTE_FLOOR` (0.50) — a confident-plurality healthy is never
force-converted — and then on either of two triggers: *absolute*, strongest
disease ≥ `HEALTHY_REROUTE_DISEASE_FLOOR` (0.25); or *gap*, strongest disease
≥ `HEALTHY_GAP_DISEASE_FLOOR` (0.10) trailing healthy by less than
`HEALTHY_GAP_MARGIN` (0.30). Either hit swaps the verdict and emits
`weak_healthy_rerouted`.

## The OOD gate — three-of-three

`_decide_nap_route` rejects a frame as `not_a_plant` only when **all three**
agree. Any one of them abstaining keeps the classifier's verdict.

1. **Head veto, checked first.** If the classifier's own `not_a_plant`
   probability is *below* `ood.nap_head_mass_floor` (default 0.05), the head
   is confident the frame **is** a plant and the gate refuses to fire.
   Setting the key to `null` restores the historical two-of-two gate.
2. **Detector confidence** below `NAP_DETECTOR_CONF_FLOOR` (0.40).
3. **A classifier-side OOD signal**, in priority order: embedding-space k-NN
   distance on the DINOv2 CLS vector (Sun et al. 2022) when the bundle ships
   a bank (`ood_embedding.npz` + `classes.json → ood.embedding`, scorer in
   `server/ood.py`); else the MaxLogit floor (Hendrycks 2019). With neither,
   the gate degrades to detector-confidence-only and the server logs a
   warning at boot — and on the no-detection path it refuses to fire at all.

**MaxLogit scope.** The fallback floor is scored over the **plant logits
only** (`classes.json → ood.max_logit_scope: "plant_only"`). The all-class
max cannot separate, structurally: `not_a_plant` is a *trained* class, so a
non-plant photo produces a confident NAP logit and its all-class max sits
above the plant median — which is why that fallback auto-disabled on every
export before the scope existed. Bundles without the key are read as
`"all_classes"`, so older artefacts keep their historical numerics.

The head veto is **inherited, not fitted**: nothing in §6 re-measures
P(`not_a_plant`) separation per bundle. Its failure direction is safe (a
flatter distribution lifts P(nap), the veto fires less, the gate reverts
toward two-of-two), but re-measure before leaning on it.

## Model families

The bundle declares its capacity tier in `classes.json → model_family`,
surfaced with per-family availability by `/api/capabilities`. All three share
one training/eval/export recipe.

| Family | Backbone → shipped artefact | Role |
|---|---|---|
| `aqua` | MobileNetV3-Small, distilled from `gamma` → TFLite **float32** | **on-device, fully offline** (the app downloads + caches it) |
| **`gamma`** | frozen DINOv2 ViT-S/14 + LoRA + MLP head → ONNX (INT8 when it survives the drift gate) | **production tier** |
| `frontier` | frozen DINOv2 ViT-B/14 + LoRA + MLP head → ONNX | high-capacity, GPU host; **server inference off by default**, returns a graceful `available:false` |

Aqua ships **float32**, not INT8: the dynamic-range INT8 sibling collapses
MobileNetV3-Small to near-constant output, and the float16 build needs a
float16 delegate (XNNPACK/GPU) that both the export box and the app's LiteRT
lack. Notebook §5 ranks the emitted builds float32 ▸ float16 ▸ INT8 and ships
the first that runs on the plain CPU interpreter with no delegate; §6 then
re-evaluates the *shipped* TFLite and warns on a degenerate one.

**The selection knob is `TRAIN_MODE`** (notebook §1), not `MODEL_FAMILY`:
`gamma` (server bundle alone, skips §5's TensorFlow toolchain), **`combined`**
(the default — trains the gamma teacher *and* distills the aqua student in one
pass) or `frontier`. `MODEL_FAMILY` is *derived* (`FAMILY_CONFIG['export_family']`)
and is what lands in `classes.json → model_family`, so a `combined` run's
server bundle is labelled `gamma`. `'aqua'` is accepted as a pre-2.7 alias for
`combined`. The mode describes the run; the family describes the artefact.

## Classifier architecture

The production artefact is an ONNX graph and the runtime is
**architecture-neutral** — `server/classifier.py` serves whatever the shipped
bundle declares in `classes.json → architecture`. The current recipe is a
frozen DINOv2 ViT-S/14 backbone + LoRA adapters on the later blocks + a small
MLP head (`LayerNorm → Linear → GELU → Dropout → Linear`) on the CLS
embedding, exported as **one graph with two outputs**: `logits` and the CLS
`embedding` that feeds the k-NN OOD scorer. LoRA is merged back into the base
Linears at export (`merge_and_unload`), so serving stays a single graph and
the declared architecture string reads `…+mlp_head+lora`.

`DEFAULT_ARCHITECTURE` / `DEFAULT_CLASS_NAMES` in `classifier.py` are only the
fallback for a bundle with no `classes.json` — not a statement about what the
current model is.

## Classes — the contract

The canonical list is `classes.json → class_names`, in the order the head was
trained on (`sorted(ImageFolder.classes)`). Two rules make it a contract
rather than a list:

- **Head width must match.** If the ONNX output dimension is a known integer
  and differs from `len(class_names)`, boot raises `ClassifierLoadError`.
  There is no pad/truncate migration shim — a taxonomy change means a new
  bundle, never a patched server.
- **Absent `classes.json`, the server boots on `DEFAULT_CLASS_NAMES`** (9
  classes, `img_size` 224, family `gamma`) — which is almost certainly not
  what your bundle trained. A *present but mismatched* file is what aborts
  boot; a missing one fails quietly. Ship it.

The taxonomy the current head carries is nine classes: seven diseases /
disorders, `healthy`, and a real **`not_a_plant` rejection head** — it is a
trained class, which is exactly why the OOD gate needs it as a veto rather
than as a signal. Two merges are deliberate and load-bearing: `leaf_spot`
folds bacterial and fungal (not reliably separable from a phone photo) and
`pests` folds mealybugs and scale.

Content cards exist without a classifier head for the deferred conditions
`edema` and `sunburn_or_scorch`; `/api/feedback` also accepts them (plus
`other` and `is_a_plant`) as **out-of-head corrections**, tagged so
retraining can prioritise expanding the label set. They feed the data loop,
never inference.

## Training recipe, in outline

Full recipe, dataset provenance and audited citations:
[`train_notebook.ipynb`](../train_notebook.ipynb) §1–§7. The shape of it:

- **§2 — data + splits.** Group-aware, class-aware splits over a mix of
  PlantDoc, PlantVillage, iNaturalist, DoctorP and a Kaggle eval supplement,
  with pHash leakage resolution and a per-source inventory persisted so a
  version-over-version delta can be checked for source drift before it is
  believed.
- **§3 — dedup, Stage 1 detector, DINOv2 diagnostic probe.**
- **§4 — Stage 2.** Backbone frozen and in `eval()` so its LayerNorm
  statistics are identical at train, eval and export; **only the head plus
  the LoRA adapters train**. Logit-adjustment cross-entropy for the
  long tail, label smoothing, cosine schedule with warmup, AdamW,
  optional class- and source-weighted sampling. `USE_LORA = False` falls
  back to the pure frozen-head path.
- **§5 — distillation** (on a `combined` run): MobileNetV3-Small student
  from the §4 teacher, then TFLite export and build selection.
- **§6 — export + calibration + gates:** ONNX FP32 → dynamic INT8 (shipped
  only when the drift gate passes *and* a paired bootstrap does not find INT8
  significantly worse), temperature, conformal, OOD calibration, both ship
  gates, then — and only then — the writes to `models/`.
- **§6b** bundles the run; **§7** states the evaluation contract.

## Validation contract

| Slice | Role | Criterion |
|---|---|---|
| `val_indomain` | training-loop metric, early stopping | best head checkpoint restored |
| `val_realworld` | **in-domain ship/no-ship gate, blocking** | absolute floors: disease macro-F1 ≥ 0.70; no disease class with recall 0; healthy-bias < 1.50; healthy recall ≥ 0.70 |
| never-trained OOD benchmark | **out-of-domain ship/no-ship gate, blocking** | **per arm, never pooled** — arm recall floors (`OOD_ARM_FLOORS`) and a healthy false-fire ceiling (`OOD_MAX_HEALTHY_FALSE_FIRE`) on the same never-trained healthy clusters; a missing benchmark fails the run (`REQUIRE_OOD_BENCHMARK = True`) |
| `val_studio` (PlantVillage) | transfer / background-bias monitor | tracked, never blocking |
| `temperature_cal` | temperature scaling | held out |
| `conformal_cal` | RAPS calibration | held out; 80/20 fit/audit when n ≥ 200 |

Both gates must pass before **anything** is written to `models/`. The
in-domain gate is an **absolute floor**, not a comparison against the
incumbent — a paired bootstrap against the live bundle is a manual check, run
with `server/tools/eval_bootstrap.py` or `paired_eval.py`, not a gate.

The out-of-domain gate is not garnish. An in-domain split drawn from the same
supplement is the same photographic regime however carefully it is grouped,
so the in-domain gate is satisfiable by a model that has collapsed
out of domain — releases have passed it while scoring near zero on a
never-trained arm. Per-arm scoring matters for the same reason pooling hid an
earlier failure: two arms measuring different phenomena average into a number
that describes neither. Benchmark builder: `server/tools/build_ood_benchmark.py`;
arm history and current numbers: `docs/ROADMAP.md` → Model track.

PlantVillage is **excluded** from the real-world gate and from both
calibration splits — its studio backgrounds violate exchangeability with the
deployment distribution.

## Calibration & conformal prediction

Temperature scaling (Guo et al. 2017) and inductive RAPS conformal prediction
(Angelopoulos et al. 2021) are fit on the **shipped artefact's** logits — the
INT8 graph when INT8 ships — so calibration matches what is served. When
hflip TTA is on, the fit runs through the same flip-averaging and
`inference.tta` is written so the server reproduces it; TTA averages
**logits**, not probabilities, precisely so temperature, MaxLogit and RAPS
stay calibrated.

The conformal hyper-parameters (`alpha`, `lambda_reg`, `k_reg`) are
grid-searched on a held-out audit split for the **smallest mean set size**
whose audit coverage clears the product target **plus a 2-SE margin**.
The margin is the point: the guard is measured on a few hundred audit rows,
so selecting exactly at the target ships sets whose true coverage falls short
about half the time.

**`alpha` is a search knob, not a promise.** On a classifier this sharp, RAPS
over-covers at every alpha the grid contains, so the value that gets selected
says nothing about realised coverage. The number the API advertises is the
measured out-of-sample `empirical_coverage` recorded in `conformal.json`
(`server/conformal.py:coverage_target` prefers it over `1 - alpha`). Do not
read `1 - alpha` as the guarantee.

The pipeline **omits the prediction set entirely on a `not_a_plant`
verdict** — that verdict comes from the detector + OOD gate, not from RAPS,
so attaching a coverage-guaranteed set would over-claim.

## Intended use

A **triage aid** for houseplant and garden-ornamental foliage photographed by
a phone: narrow "something is wrong with this leaf" to a likely cause, a
severity, a care action, and an honest set of alternatives. It is designed to
be wrong out loud — refusing (`not_a_plant`, `blurry_image`), demoting
(`low_confidence`, `very_low_confidence`), caveating (`viral_variegation_caveat`)
and offering a prediction set rather than asserting a single label.

## Out of scope

- **Not a diagnosis of record.** No pesticide, fungicide or treatment
  decision with cost or safety consequences should rest on this output alone.
- **Not an agricultural crop model.** Field crops appear in the training mix
  as supply, not as a target; row-crop deployment is untested.
- **No sub-class claims.** `leaf_spot` is an umbrella; no fungal-vs-bacterial
  accuracy is claimed, and no pathogen species is identified.
- **Not a species identifier.** Species come from a separate, optional
  PlantNet path (`server/ai_services.py`), not from this model.
- **Not a plant-toxicity or edibility authority.**
- **Not a nutrient assay.** `nutrient_deficiency` is a visual pattern, not an
  element-specific call.

## Known limitations

- **`leaf_scorch_underwatering` does not generalise out of domain.** The
  training corpus defines scorch as end-stage whole-leaf browning; on
  never-trained photos showing it early — dull, edge-confined necrosis on a
  still-green blade — the model's `leaf_spot` prototype ("green leaf +
  discrete dark regions") wins. This is exactly what the out-of-domain ship
  gate measures, and it is the standing blocker on releases. The fix is data:
  early-stage marginal necrosis judged against `leaf_spot` exemplars. A
  bigger backbone and a class logit bias were both tried and falsified — see
  `docs/ROADMAP.md` → Model track and
  [`docs/DATASET_LABELING_PLAN.md`](../../docs/DATASET_LABELING_PLAN.md) §6.
- **The out-of-domain gate itself is one arm.** Its scorch arm is a single
  species from a single photographic session. It separates good from
  collapsed bundles by an order of magnitude, so it is worth blocking on, but
  read a pass as "did not regress on the one arm we can measure", not as
  evidence of cross-species generalisation. A second, multi-species arm is
  outstanding eval work.
- **Thin classes** (`gray_mold_botrytis`, `leaf_scorch_underwatering`,
  `nutrient_deficiency`) are supply-bound, with small eval slices and
  correspondingly wide per-class recall CIs (shipped in `model_card.json`).
  More in-domain data, not a bigger backbone, is the ceiling here.
- **The physiological classes are precision-bound** — they over-trigger, and
  they are what holds macro-F1 below the disease classes' ceiling.
- **`viral_disease` over-triggers on naturally variegated foliage**
  (Calathea/Goeppertia, Maranta, Caladium, Coleus, Croton, Aglaonema,
  Begonia rex…): the classifier reads natural mottling as viral mosaic. It is
  a coverage gap — research-grade healthy photos skew to plain-green
  specimens — mitigated at runtime by demoting a sub-`VIRAL_MIN_CONFIDENCE`
  viral verdict and always attaching a "could be natural variegation" caveat,
  and in data by broadening the healthy-taxa list.
- **The dataset mix leans agricultural** (PlantDoc / PlantVillage); real
  houseplant recall rides on the iNaturalist, DoctorP and first-party
  supplements.
- **Eval labels are largely agent-judged**, not human-verified, so the gate
  measured on them is a **proxy** until verified production volume exists.
- **Aqua is a distilled student**, materially weaker than its gamma teacher;
  `model_card.json` records the teacher→student drift per slice. Treat an
  offline verdict as a lower bound.

## Ethical & safety notes

- **The asymmetry is deliberate.** A false disease verdict costs a user a
  treatment they did not need; the gates encode that — a healthy-bias
  ceiling, a healthy-recall floor, a healthy false-fire ceiling on the
  out-of-domain benchmark, and a weak-healthy reroute that surfaces a
  suspicion instead of asserting it. Any lever that buys disease recall by
  spending healthy precision (a class logit bias, for instance) is judged
  against that ceiling, not against recall alone.
- **Refusal is a feature, and it is measured.** `not_a_plant` is a trained
  class with its own gate rather than a confidence threshold, so "I don't
  know" is a first-class output rather than a low number the UI has to
  interpret.
- **Bilingual by contract.** Every diagnosis, caveat and error surface exists
  in EN and RU (`server/diseases_data.py`, `res/values*/strings.xml`) — a
  user must be able to read the uncertainty, not just the label.
- **User photos feed retraining.** Negative feedback stores the image bytes
  server-side, content-addressed by SHA-256 (`server/feedback.py`); the data
  loop and its retention rules are in `docs/DATASET_LABELING_PLAN.md`, and
  the user-facing disclosure is the bundled privacy policy.
- **Provenance is shipped, not remembered.** Seed, hyper-parameters, per-source
  inventory, INT8 paired bootstrap and per-class CIs all travel inside
  `model_card.json`, because two runs are only comparable when you can diff
  what they were trained on.

## References

| | |
|---|---|
| Training recipe, dataset provenance, audited citations | [`server/train_notebook.ipynb`](../train_notebook.ipynb) (§1–§7) |
| Runtime | `server/pipeline.py`, `server/classifier.py`, `server/detector.py`, `server/ood.py`, `server/conformal.py` |
| Current model status, deployed version, failed runs, next move | [`docs/ROADMAP.md`](../../docs/ROADMAP.md) → Model track |
| Data loop, acceptance criteria, source deny-list | [`docs/DATASET_LABELING_PLAN.md`](../../docs/DATASET_LABELING_PLAN.md) |
| Out-of-domain benchmark builder | `server/tools/build_ood_benchmark.py` |
| Paired-bootstrap A/B | `server/tools/eval_bootstrap.py`, `server/tools/paired_eval.py` |
| Bundle validator, notebook navigator | `.claude/skills/houseplant-disease-ml/scripts/check_bundle.py`, `scripts/nbtool.py` |
