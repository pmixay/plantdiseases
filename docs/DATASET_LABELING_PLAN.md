# Dataset & labeling plan — local collection + offline export

The data loop is **local and offline**: the maintainer scrapes + vision-judges
images into a Kaggle supplement (`maxayyt5/houseplant-disease-eval-supplement`)
that `train_notebook.ipynb` pulls directly. The server only *records* anonymized
production scans + feedback for offline export — no server review queue, no
ingestion. Roadmap: `docs/ROADMAP.md`; per-run metrics: `server/models/MODEL_CARD.md`.

```
  LOCAL (maintainer, offline)                 SERVER (production)
  scrape → source-disjointness → pHash dedup  /api/analyze → anonymized scan record
  → per-creator cap → vision-agent judge      user feedback → correction (weak label)
       │ manifest.csv (verified_by=agent_vision)       │
       ▼                                               ▼
  Kaggle supplement  ◄──── fold judged ──────  tools/export_data.py (offline pull)
       │ pulled by train_notebook.ipynb → model bundle
```

## Supplement = supply + eval (one publish, no separate carve)

- **Supply (train):** every class folder (incl. the `healthy/` + `not_a_plant/`
  hard-negative folders) is train data.
- **Eval (held-out):** §2 holds out a deterministic hash fraction into
  `val_realworld`/`val_indomain` + `temperature_cal`/`conformal_cal`, group-aware
  (held-out source-disjoint from train).

Rows are agent-judged (`verified_by=agent_vision`), so the gate is a **proxy**
until verified production volume exists.

## Acceptance criteria (every version)

- **Layout:** `<class>/<image>` over the 9 classes + `manifest.csv`
  (`filename, class, role, subject_domain, hn_kind, confidence, source, phash, …`).
- **Source disjointness (anti-leakage):** no image from a training-corpus source
  (iNaturalist, PlantDoc, PlantVillage, the Kaggle training slugs, Wikimedia). A
  held-out training image inflates the gate.
- **Image gates:** real photos, short side ≥ 384 px, pHash near-dup removal
  (Hamming ≤ 6), per-creator cap.
- **Domain gate (v2.6.12, widened 2026-08):** subject is a **houseplant/indoor plant, an
  outdoor / garden ornamental** (potted, garden, landscape) **or a domestic kitchen-garden
  edible** (cucumber, tomato, pepper, berry bushes — a home plot, greenhouse or allotment) —
  still **no commercial field / plantation / greenhouse-monoculture imagery**. The line is
  *the grower*, not the species: a cucumber on a trellis in someone's garden is in-domain;
  a hectare of cucumbers under commercial glass is not.
- **Vision-agent judging:** kept/rejected vs exported reference exemplars; a
  boundary negative is kept only if the agent would *reject* it for the lookalike
  positive class (judged vs both confused-class exemplar sets).
- **Hard negatives role-tagged** (`role=hard_negative`, `hn_kind`) under `healthy/`
  or `not_a_plant/`; `hn_kind=boundary` is routed **train-only** by the notebook.

## Domain — houseplants → + garden ornamentals (v2.6.12) → + kitchen-garden edibles (2026-08)

**The domain is still widening.** After ornamentals, the next step admits **domestic edible
garden plants** — cucumber, tomato, pepper, courgette, berry bushes — grown by a person for
themselves. The exclusion that survives every widening is **commercial agriculture**
(plantations, monoculture fields, production greenhouses): that imagery is a different
photographic distribution, it is what the 2.6.7 gray_mold fix removed, and it is still ~39%
of gray_mold's eval slice today. Species is not the test; the grower is.

> **Assumption flagged.** "Cucumbers and similar" was read as *home-grown edibles, commercial
> agriculture still out*. If commercial rows are meant to be in-domain too, say so — it is a
> one-line change to the upstream judge prompt and **zero** notebook changes (below).

Code-side this costs nothing, by design. As of **2.6.13** the notebook **records and reports**
`subject_domain` verbatim and **never gates on it** — no enum, no allowlist, no mapping table.
A new bucket (`garden_edible`, whatever you name it) appears in the per-split domain report and
in `model_card.json → source_inventory.per_split_subject_domain` the first run after it is
collected. Domain policy lives entirely in the offline labeling pipeline; the runtime gate stays
bundle-derived (a rebuilt OOD bank on the broader in-distribution).

Two weight comments in §4 now lag the policy and should be revisited **with evidence, not
reflex**: `doctorp_pepper` is down-weighted 0.8 (prior 0.5) because "pepper is a container
CROP", and `kaggle_ornamental` sits at 1.0. If domestic edibles are in-domain, pepper's
down-weight is arguing against the deployment distribution. Leave both alone until 2.6.13 gives
a baseline — changing sampler weights and the corpus in the same run makes the A/B unreadable.

### Original v2.6.12 pivot (kept for the record)

The target domain broadens to **houseplants + outdoor / garden ORNAMENTALS** (potted, patio,
garden, landscape); commercial **field / agricultural crops stay excluded**. The v8
data-quality work below still applies — it now targets **field-crop** leakage (not "outdoor"),
and outdoor ornamentals are welcome. In-code the pivot is just the notebook up-weighting the one
ornamental source (`kaggle_ornamental` 0.8→1.0, §4). The real broadening is **upstream**: loosen
the supplement's external `field_crop`/`orchard_fruit` gate to admit ornamentals + collect
garden-ornamental scans. The runtime gate is **bundle-derived** (0 server code changes; a rebuilt
OOD bank on the broader in-distribution is what accepts ornamentals and still rejects field crops
+ non-plants).

## Version status

- **v5 / 2.6.7** (06-26): gray_mold fixed on clean foliage (R/P 0.83/0.83) after the
  agricultural strawberry-Botrytis proxy was removed. realworld disease macro-F1 0.862.
- **v6 / 2.6.8** (06-29): +boundary negatives; mixed — nutrient precision 0.679→0.747
  (still <0.80), recall slipped; healthy→disease false-fire 8.4% on hard boundary.
- **v7 / 2.6.9** (07-03): +boundary negatives + first powdery; the notebook
  aqua-collapse fix — §5 now ships the faithful **float32** TFLite (~6.1 MB): INT8
  collapsed MobileNetV3 to ≈0.01, float16 won't prepare on LiteRT (`CONV_2D`); aqua
  realworld 0.76. **A paired A/B says the v7 changes net-hurt gamma — keep 2.6.8 (below).**

**Leakage-aware A/B — 2.6.5, 2.6.8 (shipped), 2.6.9 (candidate)** on the v4 & v7 eval
slices (2026-07-16, boundary excluded, paired bootstrap, `tools/eval_bootstrap.py`).
Supported disease macro-F1 (the 4 disease classes present — the supplement has no
leaf_spot/pests/viral) + healthy false-fire:

| model | v4 | v7 | false-fire v4 / v7 |
|---|---|---|---|
| 2.6.5 | 0.940 | 0.872 | 0.033 / 0.033 |
| **2.6.8 (shipped)** | **0.947** | **0.929** | **0.006 / 0.010** |
| 2.6.9 (candidate) | 0.901 | 0.908 | 0.022 / 0.030 |

**Paired 2.6.8 vs 2.6.9 → 2.6.8 wins both:** v4 **+0.046** [+0.029,+0.063], v7 **+0.021**
[+0.003,+0.038] (CIs exclude 0). It wins even on v7 — 2.6.9's *own* training era, where
2.6.9 carries more train leakage — so it is not a leakage artifact. Per-class (v7),
2.6.8→2.6.9 **regressed**: leaf_scorch recall 0.89→0.77 (below the 0.80 floor), nutrient
0.92→0.89 (prec 0.98→0.93), healthy precision 0.87→0.76, false-fire ↑; it gained only
powdery recall (0.84→0.93). The v7 dataset iteration **net-hurt gamma**.

**Deploy call: keep 2.6.8; do NOT ship 2.6.9.** Fix the data (v8) → train 2.6.11.
(Caveats: this eval can't see leaf_spot↔viral precision, where the boundary work was
also aimed, but the classes that regressed are the ones v7 targeted; the older
2.6.5↔2.6.9 gap is contamination — each model wins on its own era, 71% of v7 is
byte-identical to v4.)

## Source loss — `kaggle_ornamental` died mid-run (found 2026-07-24)

`pappu54/indoor-plant-disease-dataset-23-classes` — the **only** outdoor/garden
ornamental source, and the one the v2.6.12 pivot up-weighted — was **deleted or
unlisted by its owner**. The Kaggle API returns `403 {"code":403,"message":"Permission
'datasets.get' was denied"}` on a token that reads every other slug we use, and
`datasets/list?user=pappu54` returns `[]`. It is not coming back.

It stopped loading on the **2026-07-20** run (2.6.10). `_kaggle_download` is fail-soft,
so the loss was one `skipping` line in a 900-line log:

| | 07-03 (2.6.9) | 07-20 (2.6.10) |
|---|---|---|
| `kaggle_ornamental` images | 2,264 | **0** |
| train features | 14,315 | **12,025** |
| val_indomain disease macro-F1 | 0.865 | **0.809** |
| val_indomain leaf_spot recall | 0.852 | **0.695** |
| val_indomain viral_disease recall | 0.952 | **0.792** |
| head best epoch | 5 | **3** |

The source supplied 600 images each of `leaf_spot` / `viral_disease` /
`powdery_mildew` / `healthy` (+77 nutrient, +67 scorch) — exactly the classes that fell.

**Why nothing caught it.** `kaggle_ornamental` is in **no** `make_*_split`
`allowed_sources`, i.e. train-only, so `val_realworld` — the ship gate — contains none
of it and **passed**. Two lessons, both now enforced in code:

1. **A train-only source's disappearance is invisible to the ship gate.** §2 now has
   `REQUIRED_SOURCE_FLOORS`, which raises before training when any load-bearing source
   is missing or under floor (`STRICT_SOURCE_FLOORS=True`). This generalises the
   `REQUIRE_SUPPLEMENT` gate, whose own comment had already described this exact
   failure mode for the supplement.
2. **Version-over-version A/B assumes a shared corpus, and nothing verified that.**
   The per-source inventory is now persisted and shipped in
   `model_card.json → source_inventory`. **Diff it before believing any paired
   bootstrap** — two models trained on different corpora are not comparable, however
   tight the CI.

**Replacement (2.6.13).** `KAGGLE_ORNAMENTAL_SLUGS` pools two live sets, both
folder-inventoried via the Kaggle API before wiring:

| slug | licence | maps to (after DROP) |
|---|---|---|
| `itselif/ornamental-plant-diseases-master-dataset` | CC0 | healthy 389, leaf_spot 287, powdery_mildew 133 (rust dropped) |
| `warcoder/rose-leaves-disease-detection` (FlowerNet) | CC BY 4.0 | healthy 404, leaf_spot 313 (downy mildew dropped) |

At the shipped `per_class_cap=600` that is **healthy 600, leaf_spot 600,
powdery_mildew 133 ≈ 1,333 of 2,544**. **Unreplaced: `viral_disease` −600 and
`powdery_mildew` −467.** No live ornamental mosaic-virus set surfaced in the search;
closing viral needs collection or a new source, so treat viral as supply-starved rather
than reading a regression as a modelling problem.

**Candidate, NOT wired in** — `shuvokumarbasak4004/flower-leaf-diseases-dataset-new-and-update`
(MIT, ~6k+ files, `flower_data/<split>/<species>/<condition>`) carries `healthy` 2,200
and **`scorch` 1,400** — attractive because `leaf_scorch_underwatering` is our weakest
class (realworld R 0.75 / P 0.76; the §3 DINOv2 probe recalls only 0.44). It is
deliberately left out: 1,400 unverified positives into the most precision-sensitive,
boundary-sensitive class is the same bet that produced the 2.6.9 regression. **Judge a
sample against the reference exemplars first**, then decide.

## 2.6.10 — the boundary fix on v7: recovered (07-20) — see the caveat above

Re-ran v7 with the fix (`supplement_boundary` 0.9→0.35, §4) — boundary draw 5.8%→2.8%.
**leaf_scorch recall 0.61→0.75**, disease macro-F1 **0.834→0.845**, **ship gate passed**,
aqua healthy (float32). The 2.6.9 *recall* regression is undone and 2.6.10 ≈ 2.6.8 on gamma.
Still open: weak-class **precision** (nutrient P **0.67**, leaf_scorch P 0.76) — the boundary
fix recovers recall, not precision → that's the v8 job.

## v8 — next version (model 2.6.11)

The 2.6.10 fix recovered *recall* but not *precision*, and the eval is still untrustworthy
(71% recycled, ag-leak, sub-0.80 noise). v8 fixes the data; **2.6.11 retrains** on it (the
boundary fix already carried forward; `MODEL_VERSION` bumped to 2.6.11 in §1).

**Root cause of the 2.6.9 regression (found 2026-07-16; notebook fix applied).** Between
2.6.8 and 2.6.9, commit `12623c4` began routing **all** `hn_kind=boundary` hard-negatives
(scorch/nutrient-lookalike healthy) to **train-only** as a new `supplement_boundary`
source at sampler weight **0.9** (and dropped the per-class cap). In 2.6.8 those negs sat
in the eval-eligible `supplement`, so ~½ went to *val*; in 2.6.9 all **697** are pinned to
TRAIN and drawn at ~5.8% of every batch — on par with the real physiological-positive
source (5.5%) — while scorch/nutrient TRAIN supply stayed frozen (healthy TRAIN +384 →
25.3% of the set). The head over-learned "browning/chlorosis = healthy": leaf_scorch
recall 0.89→0.77, nutrient down, healthy precision 0.87→0.76, and it plateaus earlier
(head best epoch 19→5). It is the precision/recall seesaw over-rotated; §1 constants, loss,
early-stop, and checkpoint logic were byte-identical, so they are **not** the cause.

**Fix applied + validated (§4):** `SOURCE_WEIGHTS['supplement_boundary']` **0.9 → 0.35** —
boundary hard-negatives now *nudge* precision without swamping the physiological classes,
train-only (eval stays clean). Validated on the **2.6.10 run** (07-20): boundary draw
5.8%→2.8%, leaf_scorch recall 0.61→0.75, disease macro-F1 0.834→0.845, gate passed — the
recall regression is undone. Kept for 2.6.11. Don't over-tune the weight on the contaminated
v7 eval; let v8's clean benchmark guide any further move.

**Why v7 can't prove a weak-class gain (the dataset amplifiers):**
1. **Non-independent eval** — 71% recycled from v4 (scorch/nutrient positives 100%,
   gray_mold 91%); version-over-version reads on those classes are circular.
2. **Unfloored positive noise** — the ≥0.80 floor hit the negatives + powdery, not the
   physiological positives (~30% still <0.80, min 0.55, eval-eligible).
3. **Agricultural-domain leak (bigger than first estimated).** Crop/outdoor imagery is
   **~8–22% of disease positives** (strict query-only → broad query+notes match),
   concentrated in **gray_mold ~39%** (grape/strawberry Botrytis — the exact proxy the
   2.6.7 removal fought) and **nutrient ~31%** (tomato/rice/citrus/potato); leaf_scorch
   ~10%, powdery ~0%.

**v8 plan (priority):**
1. **Purge field/agricultural crops from disease positives** — crop-term blocklist + an
   "is it a houseplant or garden ornamental (not a field crop)?" re-judge; populate
   `subject_domain`/`subject_plant`. (v2.6.12: outdoor ornamentals are now **kept**.)
   **gray_mold (~39%) and nutrient (~31%) are the most contaminated — start there**
   (gray_mold's grape/strawberry Botrytis is the retired proxy). A net trim (on-plan for
   the precision-bound classes); pair with item 2 so gray_mold/nutrient don't lose supply.
2. **Refresh recycled weak-class eval** — ~50–100 *new* houseplant positives each for
   nutrient + scorch, ~40 gray_mold, from new creators/genera (Stack Exchange/Reddit;
   folded production scans). Restores independence + targets the fallen recall.
3. **Extend the ≥0.80 floor to physiological positives** — route sub-0.80 disease
   positives **train-only** (don't delete: protects aqua's data-hungry volume).
   ✅ **Landed both sides as of 2.6.13.** The manifest stamps `split_hint`
   (`eval_ok` / `train_only`); `fetch_kaggle_supplement` now honours it and routes
   `train_only` rows to a new **`supplement_train_only`** source — TRAIN-ONLY (in no
   `make_*_split allowed_sources`) at the **full 0.9 `supplement` sampler weight**, so the
   student keeps every image. Until this landed the notebook read *only* `hn_kind`, so of
   v8b's 1,226 `train_only` rows just the ~700 boundary negatives were held back and the
   rest — the sub-floor positives — drained into `val_realworld` / `val_indomain` /
   `temperature_cal` / `conformal_cal` at ~52%. **They were not only grading the ship gate,
   they were fitting the shipped temperature and conformal q̂.** Routing precedence:
   boundary first, then `eval_ok`; anything else (blank, unrecognised, absent from the
   manifest) routes train-only — an unknown verdict must never *promote* a row into a
   held-out slice. Expect eval/cal slices to **shrink** and train to grow on this run;
   that is the fix working, not a regression.
   **Fallback:** a build that predates `split_hint` but carries `confidence` routes on
   `confidence >= 0.80` instead — the same floor the hint is derived from, so the fix
   works without waiting on a re-upload. Only a build with *neither* column raises.
   Measured on the currently-published manifest: **1,502 eval-eligible / 340 train-only
   / 697 boundary**, and the 340 are exactly the sub-floor positives the item asks for
   (gray_mold 75, leaf_scorch 112, nutrient 113, plus 40 hard-negatives).

## ⚠ The published Kaggle supplement is STALE (found 2026-08-05)

`maxayyt5/houseplant-disease-eval-supplement` **version 7** — what the notebook actually
downloads — is a **v7-era build, not v8b**. The v8 and v8b rounds were finalized locally
but the `kaggle datasets version` publish step never landed, so none of that work has
ever reached a training run:

| class | v8b (round notes) | **published v7** |
|---|---|---|
| nutrient_deficiency | 493 | **378** |
| leaf_scorch_underwatering | 560 | **387** |
| gray_mold_botrytis | 282 | **249** |
| powdery_mildew | 188 | **146** |
| healthy | 1126 | **1002** |
| leaf_spot | 99 | **0 — absent entirely** |
| not_a_plant | 377 | 377 |
| **total** | **3125** | **2539** |

The manifest also has **no `split_hint` and no provenance column**, and `subject_domain`
holds only `houseplant` / `not_a_plant` — **zero `garden_ornamental` rows**. So the
v2.6.12 domain pivot has never been in a corpus either, and the non-recycled provenance
subset that the vs-2.6.8 A/B depends on does not exist upstream.

**Publish the v8b build before training 2.6.13.** Guard added in §2:
`SUPPLEMENT_MIN_ROWS = 3000` fails the run loud rather than letting a previous era's
corpus train + grade silently — which is the same failure class as the `kaggle_ornamental`
source loss, just upstream of the notebook. Set it to 0 to train on an older build on
purpose.
4. **Thicken thin eval** — +50–100 powdery past its 14-query/54%-one-engine monoculture;
   broaden gray_mold via new multilingual sources. Don't grow nutrient/scorch count.
5. **Boundary negatives: hold ~700, don't grow**; keep `hn_kind=boundary` train-only;
   hold ~300 generic-healthy in *eval* (the pool shrank 362→305).
6. **Variegated-healthy boundary negatives** (Calathea/Maranta/Croton/Aglaonema/Begonia
   rex → `healthy/boundary`) vs the viral over-trigger; add a small clean-houseplant
   `leaf_spot` eval slice (leaf_spot/pests/viral have no leakage-free held-out today).
7. **Hygiene** — reject drawn-annotation/watermark/AI-looking images; add a cross-version
   pHash tag (`carried_from_vN`/`new`); fix the v7 folder README (still titled "v6").

**Added 2026-07-24 after the `kaggle_ornamental` source loss (items 8–10).** The plan above
was written when that source was still supplying 2,264 train images. Three of its
assumptions no longer hold.

8. **NEW — an ornamental slice that is EVAL-ELIGIBLE (highest-value new item).** Every
   ornamental source we have — the dead `pappu54` and both 2.6.13 replacements — is
   **train-only**. So the `val_realworld` ship gate contains **zero** garden ornamentals
   and *cannot measure the v2.6.12 pivot at all*. That is also the precise reason a 2.26k
   image loss passed the gate unnoticed. Collect garden-ornamental positives into the
   **supplement** (`source=supplement`, which every `make_*_split` allows) so a held-out
   fraction seeds the gate. Until this exists, no number in this repo says whether the
   pivot worked.
9. **NEW — `viral_disease` and `powdery_mildew` lost TRAIN supply, not eval supply.**
   The dead source carried 600 viral + 600 powdery; the replacements restore powdery to
   133 and viral to **0**. This is a different ask from items 2/4 (which target *eval*
   independence): these two classes now need **train-side volume**. Expect
   `val_indomain` viral recall to stay depressed (it fell 0.952 → 0.792 on the loss) —
   **do not read that as a modelling regression in the 2.6.13 A/B.** Ornamental
   mosaic-virus imagery is the specific gap; no live public set was found.
10. **NEW — re-weight the effort between `gray_mold` and `nutrient`.** They are failing
    for *different* reasons and the v8b round treats them as one job:
    - `nutrient_deficiency` has a **real precision defect** — P **0.670**, 109 predicted
      vs 87 actual (1.25× over-fire). This is the genuine model problem. Prioritise.
    - `gray_mold_botrytis` **measures fine** (R 0.846 / P 0.856) — but its eval slice is
      ~39% agricultural leak, so that number is partly *untrustworthy rather than good*.
      Its job is item 1 (purge + re-judge for a believable benchmark), **not** volume.
      Don't spend collection effort chasing a recall number that isn't broken.

**Careful with `leaf_scorch` — more data may be the wrong lever.** The §3 DINOv2 linear
probe recalls scorch at only 0.39–0.52 across all four runs on record, by far the lowest
of any class. That says the **frozen features** barely separate it, not that positives are
scarce. Scorch is a leaf-**margin** phenomenon and the eval transform is
`Resize(224×1.05) → CenterCrop(224)`, which trims exactly the margin carrying the signal.
Test resize-and-pad (or a larger `IMG`) *before* funding a large scorch collection round.
Same reasoning is why `shuvokumarbasak4004/flower-leaf-diseases-dataset-new-and-update`
(1,400 `scorch` images, MIT) is **not** wired in: judge a sample against the reference
exemplars first — 1,400 unverified positives into our most boundary-sensitive class is
the bet that produced the 2.6.9 regression.

**2.6.11 verify (paired, not aggregate):** paired bootstrap 2.6.11 vs 2.6.8 (and vs 2.6.10)
on the *new* (non-recycled) v8 eval rows — nutrient/scorch **precision** up with recall
staying ≥ 0.80, healthy false-fire not up, powdery held. Keep aqua KD val-macro-F1 a watch-item.
**Diff `model_card.json → source_inventory` between the two runs first** — a paired
bootstrap across two different corpora is not a comparison, however tight the CI. That is
the lesson of 2.6.10, and the inventory now exists precisely so this check is possible.

## Standing disciplines

**Safety asymmetry (the priority).** The harm is a *confident wrong diagnosis* — calling
a healthy plant diseased, or diagnosing a non-plant/blur. Prefer precision + "I'm not
sure" over recall; a false disease alarm is worse UX than a missed mild disease. Keep +
tune the runtime guards (`pipeline.py`: blur gate, OOD AND-gate, conformal sets,
weak-healthy reroute, low-confidence demotion). Judge a version by false-disease-fire
rate on healthy/boundary inputs ↓, healthy P/R high, conformal coverage ≥ target,
OOD/blur still reject junk.

**Cross-version A/B — never trust a raw disease-F1 delta.** Each version's `val_realworld`
drifts in composition and is partly drawn from its own supplement, so a small headline
gap is eval-set drift, not a model change (the v2.6.0↔v2.6.7 "regression" and the
2.6.5↔2.6.9 crossover above were both this). There is **no fixed never-trained benchmark**
by choice — it would steal scarce weak-class data. Judge by within-run per-class P/R CIs
(`model_card.json`) + safety behaviour; for a head-to-head, run a one-off paired bootstrap
on whatever common held-out exists (`tools/eval_bootstrap.py`).

**Protect the on-device student (aqua).** `aqua` (MobileNetV3-Small, distilled from
scratch) is **data-hungry**, unlike the near-data-free frozen-DINOv2 `gamma`. The same
hardening that helps gamma precision (trimming positives, adding hard negatives) starves
the student. Keep a weak-class volume floor for the student (or distill it on a superset
that retains the trimmed positives, train-only); treat aqua KD val-macro-F1 as a release
watch-item.

**val_studio gap.** PlantVillage transfer monitor, **non-blocking**; covers only 4 of 9
classes. Read `supported_disease_macro_f1` (over present classes, ≈0.99), not the
all-class number (≈0.43 from absent-class zeros). Don't backfill with agricultural studio.

**Eval-integrity (mandatory).** The model never grades/calibrates on its own confident
calls: every `auto_yes` row is **train-only**; held-out splits draw only from
vision-agent-judged rows (deterministic ~20% by sha → calibration, rest eval). Near-dup
sweep on fold-in (pHash ≤ 6; provenance `agent_vision` > `model+user` > `model`).

## Production signal loop (server, no review)

Recorded post-response (consent-gated, anonymized: JPEG q88, EXIF/GPS stripped, ≤1024 px;
only linkage `scan_id`):
- **Scans:** every `/api/analyze` except `not_a_plant`/blur; uncertain (`pending`) + all
  disease always kept, confident healthy sampled at 0.1; `pending` expires 90 days.
- **Feedback:** negative → re-opens `pending` under `user_feedback` at the corrected
  class; positive → promotes to `auto_yes`.
- **Export:** `tools/export_data.py` → one `tar.gz` of `labeling.db`/`feedback.db`/
  `community.db` (read-only); maintainer judges offline + folds in.

## Risks

- **Calibration drift** — re-fit every ship (§6), from judged production items only.
- **Labels are a proxy** — agent-judged, not human ground-truth (by design).
- **Consent/regulatory** — recording is consent-gated + anonymized; `privacy.html` +
  the in-app consent screen are the canonical disclosure.
