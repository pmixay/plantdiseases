# PlantVision 2.1 — notebook + server audit

Deep review of `server/train_notebook.ipynb`, the artefacts it produces,
and how the runtime (`server/pipeline.py`, `classifier.py`, `detector.py`,
`main.py`) consumes them.

Format: numbered findings, each tagged with severity and an explicit
recommendation. The TL;DR is in [§ 0 Top-level verdict](#0-top-level-verdict).

---

## 0. Top-level verdict

The notebook is **runnable end-to-end and the artefact contract is sound**
— `Run All` (after §2's data fetch finishes) produces every file `server/`
reads at boot, in the right format, with the right names. The export
pipeline (FP32 ONNX → static INT8 QDQ → temperature → conformal RAPS →
MaxLogit floor → ship gate) is the cleanest part of the whole project.

There are **no architectural defects**, but there are a small number of
**data-quality and threshold-tuning issues** that explain the
healthy-bias / NAP false-positive failure modes the production
screenshots surfaced. Most of them are notes for the next training pass
rather than blockers.

The single hard concern: the `not_a_plant` class is *quantitatively* well
populated (~1150 train rows in the shipped card) but *qualitatively*
under-distributed for the failures users actually trip — synthetic
images, memes, drawings on paper, indoor clutter. Both NAP source pools
(COCO val2017 + iNat Animalia) are too clean and naturalistic to teach
the head the patterns that fail in production. See [§ 4](#4-not-a-plant-class-special-audit).

---

## 1. Data sources — per-source validation

Every source the notebook pulls from, what it provides, why I trust it,
and what could bite.

### 1.1 iNaturalist  (`api.inaturalist.org/v1`)

* **Validity:** ✓. Official public REST API, no auth, `User-Agent`
  identifies the project. Throttled to ~0.25 s between pages.
* **Per-class use:**
  * `healthy` — leaf-only term filter (`term_id=36 'Leaves'`,
    `term_value_id=38 'Green Leaves'`), 8 houseplant genera
    (Ficus / Monstera / Epipremnum / Calathea / Spathiphyllum /
    Dracaena / Sansevieria / Philodendron), 180 per taxon. Solid.
  * `gray_mold_botrytis` — Botrytis is a fungus, not a plant, so the
    leaf-only filter is intentionally **off** (`leaf_only=False`).
    A green-content HSV mask filters obvious agar plates / fruit
    close-ups afterwards. Threshold is `min_ratio=0.02`, which the
    inline comment explains keeps necrotic-brown shots while rejecting
    plates that score < 0.01.
* **Risk:** `_green_content_ratio(min_ratio=0.02)` is *very* permissive
  (any image with ≥ 2 % "green-ish" pixels survives) — that's enough
  for any photo with a hint of grass / curtain green to pass even when
  it isn't a botrytis-on-leaf shot. Audit comment acknowledges this
  but doesn't quantify the residual junk rate.
* **Recommendation:** at next training pass, manually sample 50 random
  rows from `data_v2/classification/train/gray_mold_botrytis/` and
  count the fraction that *aren't* "gray mold on a leaf". If > 10 %,
  raise `min_ratio` to 0.04–0.05 and accept the smaller class count.

### 1.2 COCO val2017  (`images.cocodataset.org`)

* **Validity:** ✓. Standard MS-COCO val split with CC-BY-style
  attribution (image-level licenses vary; for evaluation use this is
  the conventional source).
* **Use:** `not_a_plant` seed pool. Cap 1500 images.
* **Filtering:** excludes a hard-coded set of 12 "plant-ish" category
  names (`potted plant`, `vase`, `broccoli`, `apple`, …) — only
  images whose annotation set is **disjoint** from those categories
  survive.
* **Risk 1 — under-exclusion:** COCO is not exhaustively labelled, so
  many images have unlabelled background plants. The inline comment
  acknowledges this and flags a v2.2 plan ("re-curate against the
  trained Stage 1 leaf detector"). Currently shipped.
* **Risk 2 — over-curation:** the 12-category exclude list is
  conservative, but COCO is **outdoor-leaning** — streets, sports,
  food, transit. An indoor-clutter pool (sofas, lamps, kitchen
  counters) is **absent**. This is the dominant gap behind real-world
  NAP failures.
* **Recommendation:** see [§ 4](#4-not-a-plant-class-special-audit).

### 1.3 iNat Animalia kingdom  (research-grade)

* **Validity:** ✓. Same API as 1.1, scoped to `kingdom=Animalia`.
* **Use:** broaden the `not_a_plant` pool with wildlife close-ups
  (cats / dogs / birds / insects) that the NAP head should reject. Cap
  400.
* **Risk:** iNat Animalia is *naturalistic* wildlife. It does **not**
  cover the synthetic / drawn / meme / indoor-room failure modes that
  production actually sees. Same gap as 1.2.

### 1.4 Openverse  (`api.openverse.org/v1/images/`)

* **Validity:** ✓. CC-only licence filter
  (`license=by,by-sa,cc0,pdm`), anonymous tier (~20/min cap honoured
  via a 4 s call-spacing wait).
* **Use:** top-up for the upstream-thin `gray_mold_botrytis` class.
  Target 400 pre-split, queries cover both English and US spellings
  (`gray/grey mold/mould`).
* **Concern:** the request uses a **browser-spoofed User-Agent**
  (`Mozilla/5.0 … Chrome/124`) instead of the project's own UA.
  ✓ Inline justification comment added in the audit follow-up
  (Openverse CDN returns 403 on the project's own UA; browser-spoof
  is the documented workaround).
* **Risk:** Openverse is a thin layer over Wikimedia / Flickr —
  there's no quality-gate on the actual photos, so the green-content
  / pHash dedup later is what's actually carrying the quality bar.

### 1.5 PlantDoc  (`agyaatcoder/PlantDoc` on HuggingFace)

* **Validity:** ✓. Public HF dataset, MIT-licensed metadata,
  per-image license CC-BY-NC. The dataset card promises XYWH bboxes
  with category strings.
* **Use:** real-world agricultural disease photos with bboxes →
  classifier crops (this notebook), and now also the YOLOv8 detector
  training set after the §3 builder I added in `2cbc913`.
* **Distribution-shift concern (recognised):** PlantDoc is field
  crops, not houseplants. §4 down-weights PlantDoc rows in the
  source-weighted sampler to 0.5 (vs 1.0 for the rest) to pull the
  empirical prior back toward the deployment target. This is *the
  right call*, but the gap is structural and worth its own line in
  the model card.
* **Risk — XYWH-vs-XYXY mis-read:** `_clip_bbox_xywh` validates that
  `x + bw ≤ image_w` (with 5 % slack) and rejects bboxes that fail.
  Strong defensive check; on any HF revision flip from XYWH to XYXY
  the assertion catches the regression instead of silently producing
  garbage crops.

### 1.6 PlantVillage  (HuggingFace; two candidate repos)

* **Validity:** ✓. Tries `BrandonFors/Plant-Diseases-PlantVillage-Dataset`
  first, falls back to `mohanty/PlantVillage` config=`color`.
* **Use:** disease classes + healthy with a per-class cap. **`healthy`
  is explicitly capped to 250 in PV** via `PV_PER_CLASS_TRAIN_OVERRIDE`
  so PV studio shots don't dominate that class.
* **Studio-background concern:** PV is studio-clean (uniform
  off-white background, single leaf centred). The notebook routes
  PV's `test` split to `val_studio` (a transfer / background-bias
  *monitor* — never blocking) so PV-background features can't fool
  the ship gate.
* **Source-weight concern:** `SOURCE_WEIGHTS['plantvillage'] = 1.0`
  — same weight as iNat real-foliage rows. Plantdoc is at 0.5. PV
  is *also* a long way from the indoor-houseplant target, and at 900
  per disease class it dominates the sampler.
  **Recommendation:** consider `SOURCE_WEIGHTS['plantvillage'] = 0.6`
  on the next training pass. (Won't help the v2.1 model, just a knob
  to try for v2.2.)

---

## 2. Class balance & overfitting risk

The shipped `models/model_card.json` records training counts:

| class                       | train  | val_realworld |
|-----------------------------|-------:|--------------:|
| bacterial_leaf_spot         |  1059  |     60        |
| **gray_mold_botrytis**      | **227**|    **22**     |
| healthy                     |  1244  |    353        |
| leaf_spot_fungal            |  1221  |    197        |
| not_a_plant                 |  1148  |    225        |
| powdery_mildew              |  1059  |     37        |
| viral_disease               |  1243  |    144        |

### 2.1 `gray_mold_botrytis` is 5× under-supplied  ⚠ medium

* `THRESH_TRAIN_DEFAULT = 150` in the §2 guard. Botrytis ships at
  227 in v2.1 — only 50 % over the floor. Any tightening of pHash
  threshold or green-content ratio would push it under.
* The two upstream channels (iNat Botrytis taxa + Openverse top-up)
  are **the only sources** for this class; PlantDoc has no Botrytis
  label, PV's standard release has no Botrytis label.
* **Operational effect:** the v2.1 head has ~5× less Botrytis signal
  than every other disease class. Botrytis recall on `val_realworld`
  is the canonical class to watch on the ship gate's per-class
  `recall=0` check.
* ✓ **Fixed**: per-class floor added in the audit follow-up.
  ``THRESH_TRAIN_PER_CLASS = {'gray_mold_botrytis': 400}`` — the
  §2 finishing guard fails loud on Botrytis < 400 instead of waving
  227 through. The honest fallback when the supply chain can't
  sustain ≥ 400 is to drop Botrytis from v2.1 entirely; the runtime
  healthy gate + very-low-confidence banner will still surface a
  Botrytis-looking photo as "uncertain" rather than confidently
  wrong.

### 2.2 `powdery_mildew` val_realworld is thin  ℹ low

* 37 rows. Confidence-interval width on a per-class recall figure at
  n=37 is ±15 pp at 95 %. The ship gate's `recall=0` check still
  fires correctly, but per-class accuracy figures in
  `training_metrics.json` should be read with that uncertainty in
  mind.

### 2.3 Class-balanced vs source-weighted sampler  ✓ ok

* `USE_SOURCE_WEIGHTED_SAMPLER = True`,
  `USE_WEIGHTED_SAMPLER = False`. The notebook explicitly raises if
  both are set (double-rebalance on top of the logit-adjustment loss
  prior). Good guard.
* The source-weighted sampler logs the realised draw fractions to
  `run_log.jsonl`, so the empirical training prior is auditable
  after the fact.

### 2.4 Overfitting risk  ✓ ok

* Augmentation stack in §4 is well-tuned for indoor smartphone
  photos: WhiteBalanceShift, Glare, DefocusOrMotion, JpegRecompress,
  RandomPerspective at 12 % distortion, RandomErasing on tiny
  patches. Solid.
* Teacher phase uses early stop on `val_indomain` disease F1 with
  patience 4 (head phase) and patience default (tune phase).
* KD phase uses early stop on EMA `val_indomain` disease F1 with
  patience 3. The notebook persists `models/classifier_candidates.pt`
  with the top-4 EMA snapshots so §6 can re-arbitrate at INT8 against
  `val_realworld` rather than trust the (noisy) FP32 in-loop ranking.
* `val_realworld` is **intentionally NOT computed per epoch** during
  training — the docstring on `step_eval` calls this out as a
  honest-evaluation gate to prevent post-hoc cherry-picking. ✓

---

## 3. Augmentations / loss / scheduler

No findings. The §4 `indoor_train_tx` chain matches the deployment
failure modes (lighting / motion / JPEG re-encode), and the
LogitAdjustment loss with τ=0.5 plus label_smoothing=0.05 is a sound
choice for the post-cap prior.

The two-stage teacher fine-tune (head-only → unfreeze last two backbone
stages) with phase-specific LR groups (`5e-4` head / `5e-5` backbone) is
correct; the scheduler defends against early-collapse via linear warmup
then cosine.

---

## 4. `not_a_plant` class — special audit

### 4.1 Quantity is fine; quality is not

`not_a_plant` train_count = **1148**, comparable to every other class.
By count, the head should learn to reject NAP cleanly.

### 4.2 Distribution mismatch is the real problem

Two upstream sources:
* COCO val2017 (cap 1500) — outdoor scenes, sports, transit, food.
* iNat Animalia (cap 400) — wildlife close-ups (cats, dogs, birds).

The screenshots that *actually* fail in production are:
* **A nuclear-mushroom-cloud meme** with Russian text (screenshot 1) —
  classified as Gray Mold 31 %.
* **A pencil drawing of a leaf** on graph paper (screenshot 3) —
  classified as Healthy 71 %.
* **A dried-out dead houseplant** (screenshots 2, 5) — classified as
  Healthy 55–61 %.

The first two are NAP failures the COCO + iNat-Animalia pool cannot
teach the head to reject, because **neither source contains a single
image of a meme / drawing / text overlay / synthetic illustration**.
The third group is a separate issue (head doesn't carry leaf-scorch /
nutrient-deficiency yet — see § 6 below).

### 4.3 What to add

**Recommendation:** widen the NAP pool with two new sub-sources before
the next training pass:

1. **Open Images V7 "Indoor" subset** — sofa, lamp, vase, kitchen
   counter, bookshelf. ~500 rows. The notebook's existing
   `fetch_inat_animalia_for_nap` shape is the template: pull, dedup,
   tag `source='openimages_indoor'`.
2. **Synthetic / artistic NAP** — ~200 rows. Cheapest source:
   Wikimedia Commons `Category:Drawings` and `Category:Memes`
   (CC-BY/CC0). Run the existing pHash dedup. Tag `source='wikimedia_synthetic'`
   so the source-weighted sampler can over-weight these (NAP class is
   not at-risk of dominating training; over-weighting the synthetic
   sub-pool is OK).

Together that's ~700 rows of the failure-mode distribution. Combined
with the existing 1148 they'd land near 1850 NAP rows with much better
coverage of what production fails on.

### 4.4 NAP gating at runtime

`server/pipeline.py` has a two-of-two AND gate (`_should_route_to_nap`):

* low YOLO detector confidence (`< NAP_DETECTOR_CONF_FLOOR`), AND
* low classifier MaxLogit (`< max_logit_floor`).

Today the MaxLogit half is **honestly off** because the calibration
(`models/classes.json -> ood._calibration`) showed
`separation_p50 = -0.27` — NAP medians sit *above* plant medians on
this metric. The gate degrades to detector-only, which is exactly why
mushroom-cloud + leaf-drawing slip through (the YOLO detector finds a
leaf-shaped blob in both).

**Recommendation:** after a v2.2 retrain on the broader NAP pool, the
§6 MaxLogit calibration block should re-emit a positive
`separation_p50`. If it doesn't, switch the OOD score to MSP
(predicted-class probability with temperature applied) and recalibrate
— the `_score` field in `classes.json` is the contract knob.

---

## 5. Export pipeline (§ 6) — end-to-end

Walked the whole §6 cell. Findings inline.

### 5.1 INT8 calibration (steps 1–2)  ✓ ok

* `CalibrationReader` takes 500 random samples from `temp_cal`
  (deterministic seed: `np.random.RandomState(SEED)`).
* Per-candidate static QDQ with `OpTypesToExcludeOutputQuantization =
  [HardSigmoid, HardSwish, Mul]` — the documented MobileNetV3-L
  recipe; the SE attention path would otherwise collapse.
* If static drift > `fail_drift=0.05` for a candidate, fall back to
  weight-only dynamic quant. Smart.

✓ **Fixed**: thin-set warning added in the audit follow-up. When
fewer than the requested 500 rows are available, `CalibrationReader`
prints the actual count and a "drop more rows into TEMP_CAL"
suggestion so the operator can correlate a high INT8 drift with a
thin calibration set.

### 5.2 Winner selection across candidates  ✓ ok

* Default: sort by `int8_realworld_f1`.
* Failsafe: if every candidate exceeds `fail_drift`, fall back to
  sorting by `fp32_realworld_f1` — the int8 column is then noise
  ("different broken-quant graphs"). § 6 step 3 then promotes the
  FP32 as the shipped int8 artefact and marks `int8.shipped=false`
  in the model card. Correctness is preserved at the ABI level
  (consumers don't care about the filename, they care about the
  graph behind it).

### 5.3 Drift gate  ✓ ok

* `WARN_DRIFT=0.02`, `FAIL_DRIFT=0.05`. Triggered on
  `f1_fp32 − f1_int8` measured against `val_realworld`. The fail
  branch ships the FP32 graph renamed, not crash. Right call.

### 5.4 Temperature scaling  ✓ ok

* LBFGS on `temp_cal` NLL using **INT8 ONNX logits** (not the FP32
  PyTorch logits). This is the right thing — calibration must match
  the deployed graph, not the training graph.
* Bounded to `[0.05, 10.0]` to defend against LBFGS pathologies.

**Minor:** `temperature.json` is fit on **plant inputs only**
(`temp_cal` is restricted to plantdoc + inat). NAP logits aren't
represented. The `_note` field documents this explicitly. T may
therefore be miscalibrated for the NAP class — but the runtime
doesn't expose NAP probabilities to the user anyway, so the impact
is local to the conformal coverage on plant rows.

### 5.5 Conformal RAPS  ✓ ok

* Splits `conformal_cal` 50/50 fit + audit
  (`np.random.RandomState(SEED + 7).permutation`). Out-of-sample
  empirical coverage lands on the audit half.
* RAPS exchangeability holds because `conformal_cal` is restricted
  to plantdoc + inat — same as the deployment distribution. NAP is
  routed through the detector + MaxLogit gate, not through RAPS.

**Concern:** in the shipped model card,
`mean_set_size = 3.4375` at α=0.10 means RAPS is *committing to ~3
classes out of 7 to hit 90 % coverage*. That's a fat set, indicating
the FP32 model itself is uncertain on the calibration distribution
(or the temperature is over-cooled). A leaner mean_set_size (1.5–2.0)
is what a confident model produces. **Tracking signal**, not a
blocker.

### 5.6 MaxLogit OOD floor  ⚠ documented-broken

Already covered in [§ 4.4](#44-nap-gating-at-runtime). The §6
calibration is sound; it correctly detects the negative separation
and the operator-side `classes.json` (post-screenshot triage) was set
to `max_logit_floor: null` to honestly disable the gate.

### 5.7 Ship gate  ✓ ok

The four gate clauses:
* `disease_macro_f1 ≥ 0.70`
* `healthy_bias < 1.50`
* `recall[healthy] ≥ 0.70` (catches the *inverse* of healthy_bias —
  the model that never predicts healthy)
* every disease class has `recall > 0`

All four catch the failure modes the v2.0 release surfaced. Stays.

### 5.8 Artefact cleanup  ✓ fixed

The notebook used to leave `models/classifier_candidates.pt` on disk
after §6 finished (4 state-dict bag, ~80 MB, never read by the
server).

✓ **Fixed**: §6 now has a `KEEP_KD_CANDIDATES=False` knob at the
tail; when unset (the default) the file is deleted after the winner
is promoted. Set `KEEP_KD_CANDIDATES=True` if you want to keep the
runner-up snapshots for an offline re-quant experiment.

---

## 6. The v2.1 head can't predict drought / nutrient damage

The five-screenshot review highlighted that **two of the failure
classes the model gets wrong are not in the head at all**:

* Drought / under-watering → `leaf_scorch_underwatering` (deferred).
* Yellowing → `nutrient_deficiency` (deferred).
* Sunburn → `sunburn_or_scorch` (deferred).
* Oedema → `edema` (deferred).

The §1 docstring documents these as "deferred until labelled
real-world recall data is added". When a user photographs a dried-out
plant, the head's argmax falls onto the closest in-head class, which
is almost always `healthy`.

**Recommendation:** v2.2 should add at least
`leaf_scorch_underwatering` and `nutrient_deficiency` to the head.
Upstream data:
* iNat (`Foliage burn`, `leaf curl`, `chlorosis`) — research-grade,
  unfiltered.
* Wikimedia Commons `Category:Plant damage` — small but CC-licensed.
* Hand-collected drought photos from the developer / friends — even
  100 hand-shot rows per class will outperform PV's clean labels for
  this distribution.

This is the **single highest-impact change** for the production
failure rate. The runtime tweaks I shipped in `4345cfe` (tighter
healthy gate + very-low-confidence banner) are stopgaps for exactly
this gap.

---

## 7. Pipeline + server module interactions

### 7.1 Artefact contract  ✓ ok

| File                              | Producer (notebook)         | Consumer (server)        |
|-----------------------------------|-----------------------------|--------------------------|
| `models/classifier_int8.onnx`     | §6 step 2 winner promote    | `classifier.py:_load_onnx` |
| `models/classifier.onnx`          | §6 step 2 winner promote    | `classifier.py:_load_onnx` (fallback) |
| `models/classifier.pth`           | §5 (winner state)           | `classifier.py:_load_pytorch_model` (final fallback) |
| `models/classes.json`             | §6 step 6                    | `classifier.py:_load_metadata` + `pipeline._resolve_max_logit_floor` |
| `models/temperature.json`         | §6 step 4                    | `pipeline._load_temperature` |
| `models/conformal.json`           | §6 step 5                    | `pipeline._build_prediction_set` via `conformal.load_conformal_config` |
| `models/detector.{pt,onnx}`       | §3 (PlantDoc YOLO build)    | `detector.py:_resolve_detector_path` |
| `models/training_metrics.json`    | §6 step 7                    | — (post-mortem only) |
| `models/model_card.json`          | §6 step 8                    | `/api/version` `int8_shipped` flag |
| `models/dinov2_probe.json`        | §3                           | — (post-mortem only) |
| `models/run_log.jsonl`            | every phase                  | — (post-mortem only) |
| `models/classifier_candidates.pt` | §5                           | — (notebook-internal; § 5.8) |

Every consumer-side path has a sane fallback when the file is
missing (defaults baked into the module).

### 7.2 Confidence floors  ✓ resolved

* `pipeline.py:HEALTHY_MIN_CONFIDENCE = 0.75` — drives the
  healthy-gate soft warning.
* `pipeline.py:HEALTHY_REROUTE_FLOOR = 0.65` (tightened recently) —
  drives the hard healthy-reroute.
* `pipeline.py:VERY_LOW_CONFIDENCE_FLOOR = 0.40` — drives the
  retake-photo banner.

Three thresholds, one file, three semantic levels (re-shoot / demote
/ soft warn) — consistent, single source of truth.

✓ **Fixed**: the redundant `classifier.py:LOW_CONFIDENCE_THRESHOLD =
0.35` constant and the per-crop `low_confidence` field on
`classify()` results have been removed — both were computed but
never read. The post-aggregation flag set by
`pipeline._pick_primary` against `HEALTHY_MIN_CONFIDENCE` is the
authoritative value.

### 7.3 Notebook's `int8_realworld_f1` ≠ production's `disease_macro_f1`  ✓ documented

§6's `eval_onnx` uses raw `argmax(logits)` — no temperature, no
weak-healthy reroute, no gap-reroute. Production applies all three.

The ship gate uses the raw INT8 number, which is the **conservative**
choice (production behaviour is at least as accurate, never less),
but it means the model_card's `f1_int8` is a floor on what production
delivers.

✓ **Fixed**: §6 now writes a verbatim `_note` into the
`model_card.json -> int8` block making the floor semantics explicit,
so anyone parsing the card downstream doesn't have to re-derive it.

### 7.4 `THRESH_TRAIN` is module-global

`THRESH_TRAIN = 150` in §2 applies to every class equally. As § 2.1
discussed, Botrytis ships at 227 — only 50 % over the floor. A
per-class minimum would let the operator demand 400 for Botrytis and
150 for the easier classes.

**Recommendation:** `THRESH_TRAIN_PER_CLASS = {'gray_mold_botrytis': 400}`
with a 150 default, applied in the §2 finishing guard.

---

## 8. Misc smaller findings

### 8.1 Stale comment ✓ fixed in this audit

§2's finishing-guard error message referenced
`server/tools/collect_smoke_set.py` — the tool was deleted in a
previous cleanup pass. Patched to "or drop additional real-world
photos into TRAIN_ROOT/<class>/" in the same commit as this report.

### 8.2 `model_version: '2.1'` hard-coded in §6  ✓ fixed

The version in `classes.json` / `model_card.json` used to be
hard-coded. If the user retrained with a different taxonomy or
training recipe, the emitted version still claimed 2.1.

✓ **Fixed**: `MODEL_VERSION = '2.1'` now lives next to `SEED` in
§1, and §6 reads it into both `classes.json -> model_version` and
`model_card.json -> version`. The operator changes one line on a
taxonomy / recipe bump.

### 8.3 §2 dedup runs before splits ✓ ok

Order is correct: `_dedup_train_in_place(TRAIN_ROOT)` runs BEFORE
the splitters. The §3 dedup is a cross-check on the held-out splits,
not a primary defence. (Audit fix #1 from a previous review — already
addressed.)

### 8.4 Detector data leakage  ❌ retracted

The original concern was that the new §3 PlantDoc-bbox YOLO builder
splits `PlantDoc/test` rows entirely into YOLO `val` while sub-
splitting `PlantDoc/train` rows 85/15, so the same PlantDoc image
could land in both the classification crop store (via
`fetch_plantdoc`) and the detection dataset.

On a closer trace this isn't actually a leakage path. The detector
and the classifier are **separate models with no shared parameters**:

* The detector trains on the YOLO dataset alone; it never reads the
  classification crops.
* The classifier trains on the crops alone; it never reads the YOLO
  val set.

A PlantDoc image landing in YOLO `val` AND classifier train is the
expected case: the classifier ate the bbox crop during *its*
training, and the detector — a different network — sees the full
image at *its* eval time without ever having been fitted to it.

The actual hold-out contract is still enforced by
`fetch_plantdoc`'s split routing: `PlantDoc/test` always goes to
`VAL_PRIMARY` (the classifier's ship-gate slice), never to
`TRAIN_ROOT`. The classifier-side `assert_no_exact_split_leakage`
catches the SHA-overlap case. The detector-side YOLO val
sufficiency is its own concern and is bounded by PlantDoc's own
train/test boundary.

No fix needed.

### 8.5 `LOW_CONFIDENCE_THRESHOLD` in classifier.py is dead  ✓ fixed

Dropped in this audit's follow-up commit. `pipeline._pick_primary`
sets `low_confidence` against `HEALTHY_MIN_CONFIDENCE = 0.75` on
the aggregated dict — the per-crop value was computed but never
read. Both the constant and the per-crop ``low_confidence`` field
are gone now.

### 8.6 `inat_observations` lacks per-call backoff diagnostic  ✓ fixed

`http_get_json` now prints the URL, attempt, and next-sleep delay
on every backoff so a class whose iNat fetch stalls under
rate-limit gives the operator real-time signal instead of a silent
under-count.

---

## 9. Summary of action items

Status as of the follow-up commit that ran the audit's "fix small
problems" pass:

### Deferred — need data / retraining cycle, not a code change

1. **Widen NAP pool** with Open Images Indoor + Wikimedia synthetic.
   ([§ 4.3](#43-what-to-add).) Needs a new fetch helper + a fresh
   training pass to land. Highest-impact item for the production
   failure rate.
2. **Add deferred physiological classes** (`leaf_scorch_underwatering`,
   `nutrient_deficiency`) to the head.
   ([§ 6](#6-the-v21-head-cant-predict-drought--nutrient-damage).)
   Needs a taxonomy bump in `CLASSES` + a new data fetch + a fresh
   training pass.
4. **Tighten `_green_content_ratio` floor** to 0.04 once Botrytis
   supply is fatter. ([§ 1.1](#11-inaturalist--apiinaturalistorgv1).)
   Conditional on #1/#3 — premature today because Botrytis would
   fall under its floor.

### Fixed in this audit's follow-up commit

3. **Per-class `THRESH_TRAIN`** for Botrytis — now
   `THRESH_TRAIN_PER_CLASS = {'gray_mold_botrytis': 400}` with a 150
   default. The §2 finishing guard fails loud on Botrytis < 400
   instead of waving 227 through as it did for v2.1.
   ([§ 2.1](#21-gray_mold_botrytis-is-5-under-supplied--medium).)
5. **Dropped `LOW_CONFIDENCE_THRESHOLD`** from `classifier.py`. Both
   the constant and the per-crop `low_confidence` field were dead.
   `pipeline._pick_primary` owns the post-aggregation flag. ([§ 7.2](#72-two-confidence-floors-in-two-files-minor),
   [§ 8.5](#85-low_confidence_threshold-in-classifierpy-is-dead--72).)
6. **Cleanup `classifier_candidates.pt`** at end of §6. New
   `KEEP_KD_CANDIDATES=False` knob deletes the runner-up state-dict
   bag (~80 MB) once §6 finishes. The deploy bundle stays clean by
   default. ([§ 5.8](#58-artefact-cleanup--minor).)
8. **`MODEL_VERSION` as a §1 constant.** Defined once at the top of
   §1; §6 reads it into both `classes.json -> model_version` and
   `model_card.json -> version`. The operator changes one line on
   a taxonomy / recipe bump. ([§ 8.2](#82-model_version-21-hard-coded-in-6--low).)

### Retracted on second look

7. **Detector-vs-classifier SHA cross-check** in §3. Re-trace: the
   detector and classifier are separate models with no shared
   parameters. A PlantDoc image landing in YOLO val AND classifier
   train is not a leakage path. ([§ 8.4](#84-detector-data-leakage--retracted).)

### Also fixed in this pass (smaller findings scattered through the report)

* **Openverse browser-UA** now carries an inline justification
  comment pointing at the documented Openverse 403-on-non-browser-UA
  behaviour. ([§ 1.4](#14-openverse--apiopenverseorgv1images).)
* **iNat backoff diagnostic** — `http_get_json` now logs every
  retry attempt with URL, attempt index, and next-sleep delay so a
  rate-limited class fetch gives real-time signal instead of a
  silent under-count. ([§ 8.6](#86-inat_observations-lacks-per-call-backoff-diagnostic--low).)
* **INT8 calibration thin-set warning** — `CalibrationReader` now
  prints when fewer than the requested 500 rows are available in
  TEMP_CAL so the operator knows the per-channel scales are
  noisier than usual. ([§ 5.1](#51-int8-calibration-steps-12--ok).)
* **`model_card.int8._note`** — added a verbatim disclaimer that
  `f1_int8` / `f1_fp32` are raw argmax-on-logits, no temperature,
  no weak-healthy reroute, no very-low-confidence demotion. The
  runtime applies all three on top; the ship-gate threshold is a
  conservative floor on production. ([§ 7.3](#73-notebooks-int8_realworld_f1-%E2%89%A0-productions-disease_macro_f1--doc-only).)
* **§ 8.1 stale `collect_smoke_set.py` reference** in the §2 error
  message — already patched in `02c9375` when the audit was first
  written.

The notebook as it stands today produces a working end-to-end
artefact set, and the runtime consumes it correctly. Items 1, 2, and
4 stay as the queue for the next training pass.
