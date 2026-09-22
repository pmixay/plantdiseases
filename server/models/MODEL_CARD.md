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

Artefacts are **never committed and never auto-downloaded**. Strict API startup
accepts only one complete notebook-produced tree with `bundle_manifest.json`.
Before a graph loads, `server/bundle.py` verifies its canonical bundle identity,
the exact selected classifier (`fallback_allowed=false`), every bound file's
size and SHA-256, data/run lineage, calibration source, and the full serving
contract. `detector.onnx`, the selected classifier, `classes.json`,
`temperature.json`, `conformal.json`, `data_manifest.json`, and
`run_manifest.json` are mandatory; the OOD bank is mandatory when declared.
Loose legacy artefacts are diagnostic-only. Validate bytes without inference
using `python server/tools/verify_bundle.py server/models/`.

Notebook §6b packs one archive per **training mode**,
`models/plantvision_<train_mode>_v<version>.tar` (`…_gamma_…`,
`…_combined_…`, `…_frontier_…`). Extract the **complete tree** into a fresh
`models/` directory; do not flatten it or copy individual files. Server
artefacts and the manifest sit at the archive root, run evidence sits under
`analysis/`, and a combined run includes the bound on-device `aqua/` subtree.
Torch checkpoints are excluded — the runtime is ONNX/TFLite only.

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
| 1 | blur gate — explicit `image_quality` abstention with empty classifier diagnostics; classifier never runs | `BLUR_LAPLACIAN_FLOOR = 50.0`, `BLUR_DOWNSCALE = 320` |
| 2 | detect → crop; whole frame folded in as an extra vote | `FOLD_WHOLE_FRAME_VOTE` (default on; `limits.fold_whole_frame_vote`) |
| 3 | batched classify, temperature applied per crop | `temperature.json` |
| 4 | mean vote over crops + whole frame | `_fold_votes` |
| 5 | OOD gate (three-of-three, below) → explicit abstention warning; probabilities unchanged | `classes.json → ood` |
| 6 | probability-preserving postprocessing: healthy suspicion, viral caveat, confidence warnings | `HEALTHY_*`, `VIRAL_MIN_CONFIDENCE = 0.65` |
| 7 | conservative `severity="low"` compatibility value until lesion/severity labels exist | `compute_severity` |
| 8 | RAPS prediction set — **omitted on every abstention** | `conformal.json` |

**No post-hoc class substitution.** `class_name` and `confidence` are always the
argmax and its probability from the calibrated `all_probs` distribution.  A
weak `healthy` result can emit `uncertain_healthy` and
`disease_suspected:<class>` and keeps the competing disease in `top_k`, but a
lower-probability class never replaces the measured winner.  The old
weak-healthy reroute was removed because it broke probability/calibration
semantics and encoded screenshot-driven preference rather than a fitted model.

## The OOD gate — three-of-three

`_decide_nap_route` emits the `nap_ood_gated` abstention only when **all three**
agree. Any one of them abstaining keeps an accepted classifier verdict.

1. **Head veto, checked first.** If the classifier's own `not_a_plant`
   probability is *below* `ood.nap_head_mass_floor` (default 0.05), the head
   is confident the frame **is** a plant and the gate refuses to fire.
   Setting the key to `null` restores the historical two-of-two gate.
2. **Detector confidence** below `NAP_DETECTOR_CONF_FLOOR` (0.40).
3. **A classifier-side OOD signal**, in priority order: embedding-space k-NN
   distance on the DINOv2 CLS vector (Sun et al. 2022) when the bundle ships
   a bank (`ood_embedding.npz` + `classes.json → ood.embedding`, scorer in
   `server/ood.py`); else the MaxLogit floor (Hendrycks 2019). With neither,
   the secondary gate stays off on **every** path. Detector confidence is
   localisation evidence and never rejects an image by itself.

There is an additional calibration precondition: `ood.served_gate` must declare
schema v1, `calibration_mode="full_served_pipeline"`, the served embedding and
MaxLogit aggregations, the exact detector-confidence floor, and the current
whole-frame-vote mode — and it carries the **only thresholds the overlay
applies** (`embedding_threshold`, `max_logit_floor`). Notebook §6 fits them on
the served statistic: Stage 1 ONNX → the same crop rules as
`serving_contract.crop` → whole-frame fold → min member k-NN distance / max
member plant logit per image, thresholded at the target plant false-reject
rate on `temperature_cal`, with the resulting three-of-three gate's
`not_a_plant` recall and plant false-reject recorded beside the head-only
numbers (`served_gate.calibration`). The single-image thresholds in
`ood.embedding` / `ood.max_logit_floor` are calibration provenance and never
reject: min-over-members is a smaller statistic than a whole-frame distance,
so applying them would under-fire. A run where the served calibration fails,
or where neither signal separates plants from non-plants on the served
statistic, ships `served_gate: null` and the overlay stays off. A declared
embedding artifact that is missing or invalid fails bundle readiness rather
than silently falling through to a weaker gate.

The gate is a rejection verdict, not another softmax class.  Detector
confidence, embedding distance and MaxLogit are not calibrated
`P(not_a_plant)`, so a gate hit does **not** move probability mass or fabricate
a NAP confidence. `class_name`, `confidence`, `all_probs` and `top_k` preserve
the calibrated classifier output for diagnostics; explicit `abstained=true`,
`abstention_reason="ood"` plus the legacy `nap_ood_gated` warning tell clients
to reject it, `detection.is_diseased` is false, and the conformal set is
suppressed. `class_name="not_a_plant"` is used only when the trained NAP head
itself is the probability argmax. The pre-classifier blur gate instead reports
`class_name="unknown"`, confidence 0 and empty probability diagnostics because
no classifier evidence exists. Healthy top-1 frames still pass through eligible
OOD checks; only an already-natural NAP top-1 bypasses them.

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
lack. Notebook §5 emits exactly one FLOAT32 TFLite candidate and combined mode
fails if its dtype/shape, torch parity, or real-world evaluation fails; it never
substitutes a FLOAT16/INT8 sibling.

Aqua retains the trained `not_a_plant` head and the app applies its existing
blur QA as a hard pre-inference `image_quality` abstention. It does **not**
carry the server's detector + embedding/MaxLogit OOD calibration contract.
Low confidence must not be relabelled as OOD, and the app does not invent an
Aqua OOD gate from server thresholds fitted to a different served pipeline.

The client verifies the staged TFLite tensor metadata before activation: one
FLOAT32 `[1, img_size, img_size, 3]` input and one FLOAT32
`[1, len(class_names)]` logits output. It performs no inference during install.
A successful `available:false` manifest authoritatively deactivates the bundle
pointer while retaining immutable rollback bytes; transient network/non-2xx
manifest failures preserve the last-known-good active generation.

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

`DEFAULT_ARCHITECTURE` / `DEFAULT_CLASS_NAMES` in `classifier.py` support
isolated tests and explicitly non-strict construction only. Production API
startup requires the manifest-bound `classes.json`; it never silently uses
these defaults.

## Classes — the contract

The canonical list is `classes.json → class_names`, in the order the head was
trained on (`sorted(ImageFolder.classes)`). Two rules make it a contract
rather than a list:

- **Head width must match.** If the ONNX output dimension is a known integer
  and differs from `len(class_names)`, boot raises `ClassifierLoadError`.
  There is no pad/truncate migration shim — a taxonomy change means a new
  bundle, never a patched server.
- **Absent `classes.json`, strict API startup aborts.** A present but mismatched
  taxonomy also aborts. Defaults exist only for tests/non-strict library use and
  are not a deployment recovery path.

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

This notebook is the **only training/export boundary**. Server and Android code
load verified artefacts and perform inference only; they contain no optimizer,
fit, calibration-fit, conversion, or model-export entry point. A regression
test enforces that boundary. The notebook hard-fails on CPU and is intended to
run as a self-contained Colab upload.

- **§2 — data + splits.** Group-aware, class-aware splits over a mix of
  PlantDoc, PlantVillage, iNaturalist, DoctorP and a Kaggle eval supplement,
  with pHash leakage resolution and a per-source inventory persisted so a
  version-over-version delta can be checked for source drift before it is
  believed. Per-image licence and provenance are **recorded, never gated**
  (study-project policy, 2026-09-21): `source_inventory.license_policy`
  tallies what the bundle trained on.
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
  significantly worse), temperature, conformal, and classifier-side OOD
  diagnostics. The current private unseen-domain set is diagnostic-only and
  cannot enable a served OOD overlay.
- **§6b** bundles the run; **§7** states the evaluation contract.

## Validation contract

| Slice | Role | Criterion |
|---|---|---|
| `val_indomain` | training-loop metric, early stopping | best head checkpoint restored |
| `val_realworld` | **in-domain ship/no-ship gate, blocking** | absolute floors: disease macro-F1 ≥ 0.70; no disease class with recall 0; healthy-bias < 1.50; healthy recall ≥ 0.70 |
| private unseen-domain regression set v2 | **diagnostic only** | not a valid OOD/release gate: no non-plant arm, one source/studio regime, augmentation families, repeated tuning; `REQUIRE_OOD_BENCHMARK = False` until an independent v3 replaces it |
| `val_studio` (PlantVillage) | transfer / background-bias monitor | tracked, never blocking |
| `temperature_cal` | temperature scaling | held out |
| `conformal_cal` | RAPS calibration | held out; 80/20 fit/audit when n ≥ 200 |

The in-domain gate must pass before deploy artefacts are sealed. It is an
**absolute floor**, not a comparison against an incumbent. A manifest-bound
paired comparison can be run with `server/tools/eval_bootstrap.py` or
`paired_eval.py`, but the present private supplement and regression set are not
independent enough to promote that comparison to a release claim.

From 2.7.5 the served OOD overlay is calibrated in §6 (above) and is on
whenever that calibration produced a threshold. Natural `not_a_plant`
predictions and image-quality rejection work regardless. Dataset v9 still owes
an independent non-plant/difficult-negative OOD set and a separately locked
deployment-representative test, grouped by capture family; until then the
served-gate numbers in `served_gate.calibration` are measured on
`temperature_cal`, not on a locked set. See `docs/ROADMAP.md`.

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

A missing `temperature.json` explicitly means no scaling (`T=1`). A present
sidecar is integrity-critical: it must be an object with a finite numeric
temperature in the notebook's fitted range `[0.05, 10]`, and any declared TTA
must match serving; malformed, boolean, infinite, out-of-range or mismatched
values fail readiness instead of silently reverting to 1. Sidecars without a
model/preprocess serving-contract or digest binding are logged as calibration
advisory until that binding is exported.

The conformal hyper-parameters (`alpha`, `lambda_reg`, `k_reg`) are
grid-searched on a held-out audit split for the **smallest mean set size**
whose audit coverage clears the product target **plus a 2-SE margin**.
The margin is the point: the guard is measured on a few hundred audit rows,
so selecting exactly at the target ships sets whose true coverage falls short
about half the time.

`alpha` is a **search knob**, not the coverage claim: the grid has selected
`alpha = 0.6` since 2.7.0 because a sharp head still audits at ~0.98 coverage
there with sets of ~1.5. The API's `coverage` field is therefore
`target_coverage` (0.90) whenever `conformal.json` proves it on an
out-of-sample audit (`empirical_coverage_kind = "out_of_sample"`, `audit_n ≥
50`, audit coverage at or above the target — `coverage_basis =
"audit_target"`), and the nominal `1 - alpha` otherwise. An in-sample
`empirical_coverage` never overrides the nominal value. Neither number is
delivered coverage measured on a locked independent set; prediction sets
remain explicitly `advisory` until one is shipped.

The pipeline **omits the prediction set entirely on every abstention** (natural
`not_a_plant`, OOD gate, or image-quality rejection). The plant calibration
population does not cover those cases—and a pre-classifier blur rejection has
no probability vector—so attaching its coverage statement would over-claim.

## Intended use

A **triage aid** for houseplant and garden-ornamental foliage photographed by
a phone: narrow "something is wrong with this leaf" to a likely cause, a care
action, and an honest set of alternatives. It is designed to be wrong out loud
— refusing (`not_a_plant`, `blurry_image`, `nap_ood_gated`), demoting
(`low_confidence`, `very_low_confidence`), caveating (`viral_variegation_caveat`)
and offering a prediction set rather than asserting a single label.

The compatibility `severity` field is currently always `low`. The detector
localises the leaf rather than the lesion, and class confidence is not lesion
extent; medium/high severity claims require a future severity-labelled target.

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

- **Scorch-versus-leaf-spot generalisation is unproven.** Historical runs
  suggest early marginal necrosis is a weak boundary, but the supporting
  private set has one source/species, augmentation families, repeated tuning,
  and contradictory archived scoring. It identifies a v9 collection target,
  not a quantified ceiling or a permanently falsified architecture lever.
- **There is no valid deployment OOD gate yet.** The existing private artifact
  contains healthy/scorch plants and no non-plants, so it cannot measure OOD.
  Its robustly deduplicated form remains diagnostic only. v9 needs independent
  multi-species disease tests plus a separate non-plant/difficult-negative set.
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
  out-of-domain benchmark, and soft healthy-suspicion warnings that surface a
  competing disease without replacing the probability argmax. Any lever that
  buys disease recall by spending healthy precision (a class logit bias, for
  instance) is judged against that ceiling, not against recall alone.
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
| Audited current status and v9 blockers | [`docs/ROADMAP.md`](../../docs/ROADMAP.md) |
| Data loop, acceptance criteria, source deny-list | [`docs/DATASET_LABELING_PLAN.md`](../../docs/DATASET_LABELING_PLAN.md) |
| Out-of-domain benchmark builder | `server/tools/build_ood_benchmark.py` |
| Paired-bootstrap A/B | `server/tools/eval_bootstrap.py`, `server/tools/paired_eval.py` |
| Bundle validator | `server/tools/verify_bundle.py` |
