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

## Corpus verification — the published v8, measured (2026-08-17)

Until now these criteria were asserted at build time and never checked against the
bytes that actually shipped. `tools/filter_supplement.py` checks them, on the corpus
downloaded from Kaggle rather than on the local build directory. Re-listed uncapped
against the live API, `maxayyt5/houseplant-disease-eval-supplement` holds 3,392 files
= 3,389 images + `manifest.csv` + `README.md` + `build_info.json`, per-class counts
matching disk exactly. **The published build is current and complete.**

| class | rows | distinct | infl | short-side p05 | <384 px | dropped |
|---|---|---|---|---|---|---|
| healthy | 1126 | 1125 | 1.00 | 480 | 21 | 1 blurry |
| leaf_scorch_underwatering | 663 | **662** | 1.00 | 422 | 9 | 1 blurry |
| nutrient_deficiency | 513 | 512 | 1.00 | 413 | 1 | 1 blurry |
| not_a_plant | 377 | 376 | 1.00 | 417 | 1 | 1 dup |
| gray_mold_botrytis | 259 | 259 | 1.00 | 415 | 1 | — |
| powdery_mildew | 217 | 217 | 1.00 | 426 | 7 | — |
| leaf_spot | 184 | 183 | 1.01 | 355 | 10 | 1 blurry |
| viral_disease | 29 | 29 | 1.00 | 652 | 0 | — |
| pests | 21 | 21 | 1.00 | 320 | 4 | — |

**Benchmark leakage: zero.** Every row pHashed against the 213-cluster OOD benchmark
at Hamming ≤ 6. Nothing matches. The eval set is outside the training corpus, now
measured rather than assumed — and the tool exits non-zero if that ever stops being
true, because a leak does not merely inflate a metric, it removes our ability to
notice the metric is inflated (see the `aditisalvi04` deny-list entry).

**Duplication: zero. Label contradictions: zero.** Inflation is 1.00 in every class,
so the v8 build's own pHash dedup held. This matters more than it looks:

> **The 663 leaf_scorch rows are 662 distinct photographs.** The scorch arm still
> scores 0.000 across three consecutive releases. So the collapse is not a volume
> problem, not duplicate padding, and not eval contamination — the three
> explanations that would have been cheap to fix. Whatever fails to transfer is a
> **property of the photographs**, and re-sourcing on count alone will not fix it.

**Collection floor: 98.4% compliant.** 54 of 3,384 rows fall below the declared
384 px short side. Reported, not enforced — those rows are already published and
already trained on, and dropping them retroactively would rewrite the corpus every
shipped bundle was fitted to. Worth acting on in the *next* round: `pests` is 19%
non-compliant (4 of 21, p05 = 320) and `leaf_spot` sits at p05 = 355, below the floor
at its own 5th percentile. `pests` is also the class Stage 1 misses 44.9% of the time
— it is the weakest class on every axis measured so far, not just on count.

**Five rows fail.** Four blur-gate failures (variance-of-Laplacian 26.5 / 30.5 / 43.4
/ 44.7 against the serving floor of 50) and one genuine near-duplicate pair in
`not_a_plant`. The blur check imports `pipeline._variance_of_laplacian` rather than
reimplementing it, so it cannot drift from what the server enforces at request time.
Two of the four carry `split_hint=eval_ok`, i.e. they currently grade models on frames
the server would short-circuit to `not_a_plant` before Stage 2 ever ran — a real
eval/serving mismatch, two rows wide.

**One caveat on the method.** The manifest-digest check first ran as exact equality
and condemned 131 rows (3.9%) as "not the dataset the manifest describes". Measuring
the distances: 128 at 2 bits, 3 at 4 bits, none worse — JPEG re-encoding during bundle
assembly, not different photographs. Every other gate treats digests inside the dedup
radius as the same image; that one demanded byte equality and so contradicted the
tool's own definition. It now uses the radius. A gate that is stricter than its own
semantics manufactures findings.

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
down-weight is arguing against the deployment distribution. Leave both alone until 2.7.0 gives
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

**⚠ Now permanently eval-only, not "not yet decided" (2026-08-15).** This set became
`server/tools/build_ood_benchmark.py`'s source — the only never-trained benchmark we
have — so training on it is no longer a judgement call, it is contamination. Two facts
seal it: its own official split is 100 % pHash-leaked (2,800 `scorch` files → **62
distinct clusters**, 61/61 test clusters also in train), and a *different* Kaggle
dataset re-uploads the same photographs.

**Deny-list — never route these into training, at any weight:**

| ref | folders | why |
|---|---|---|
| `shuvokumarbasak4004/flower-leaf-diseases-dataset-new-and-update` | all | is the OOD benchmark |
| `aditisalvi04/indoor-plant-disease-dataset` | **the whole `Hibiscus_*` branch** | re-upload of the row above — `Scorch` 60/60 and `Healthy` 20/20 match the benchmark at Hamming 0; `Necrosis` 12/12 matches its *source* at Hamming 0 |

### The deny-list widened: a clean pHash gate is not proof of independence (2026-08-17)

This entry used to name only `Hibiscus_Scorch` and `Hibiscus_Healthy`, and the paragraph
that followed recommended the rest of the slug — **`Hibiscus_Necrosis` explicitly** — as
"genuine candidate supply, clean of the benchmark on a 0/101 sample". That reading was
wrong, and it was wrong in a way worth understanding rather than just patching.

`Hibiscus_Necrosis` really does read 0/30 against our 213 benchmark clusters. It is also
`flower_data/*/hibiscus/death_leaf` from the deny-listed `shuvokumarbasak4004` dataset,
re-uploaded: an independent fresh sample of 12 files, drawn under a different seed and
downloaded from Kaggle rather than reused from any cache, matches that folder at pHash
Hamming **0**, 12 of 12. The candidate's four folders are one-to-one with the upstream's
four hibiscus subclasses (`blight / death_leaf / healthy / scorch`).

It passed because **the gate can only detect the frames we happened to sample into the
benchmark.** We built the benchmark from that source's `healthy` and `scorch` folders and
left `death_leaf` out, so `death_leaf` is invisible to a 213-cluster comparison while
carrying the same leaves, the same session, the same sheet and the same lighting. Those
are exactly the properties that make the eval set independent. Wiring it in would have
been the `aditisalvi04` incident a second time, through a gate built to prevent it.

Two rules follow, and both are now enforced in §2 of the notebook:

1. **Gate against the benchmark's upstream datasets, not only against its clusters.** A
   re-upload of the *source* passes a cluster-level gate.
2. **Deny at the branch, not the folder.** If one folder of a genus/collection is the
   benchmark, treat the whole branch as contaminated until proven otherwise — the
   deny-list is substring-matched, so the entry is `('hibiscus',)`.

The rest of the slug (49,878 files — Aloe, Rose, Money Plant, Turmeric) remains clean on
sampling and is genuine candidate supply; `Aloe_Sunburn` (3,343 files, a real houseplant
genus) maps to the deferred `sunburn_or_scorch` label. The same rule as ever applies
before wiring any of it: judge a sample against the reference exemplars, count
**originals** rather than folder size, re-run the pHash gate on the specific folders you
intend to use — and now also check the folder's provenance against every dataset the
benchmark was built from.

**Fix the lister before trusting any "nothing here".** The inventory tooling caps
pagination and returns silently: a full listing of the dataset above is 49,878 files
where the inventory recorded ~24,000 and a follow-up "uncapped" re-run still stopped
short. A negative result from a capped lister is not evidence of absence — make it
raise when `nextPageToken` is still live at exit, then re-run the band.

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

## ✅ RESOLVED — the stale-upload incident (2026-08-05 → 2026-08-07)

**Version 8 is published and verified: 3,389 rows, `split_hint` + `provenance` present,
585 `garden_ornamental` (353 eval-eligible), `leaf_spot` 184.** Routing measured on the
real manifest: **1,907 eval-eligible / 695 train-only / 787 boundary**. Kept below
because the failure mode is worth remembering.

For two days the published dataset — `maxayyt5/houseplant-disease-eval-supplement`
**version 7**, what the notebook actually downloaded — was a **v7-era build, not v8b**. The v8 and v8b rounds were finalized locally
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

**The lesson, now enforced.** An offline round only reaches training once
`kaggle datasets version` has actually run, and a stale build is invisible: it loads
fine and passes every gate. Guard added in §2: `SUPPLEMENT_MIN_ROWS = 3000` fails the run loud rather than letting a previous era's
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

## v9 — what to collect, and the finding that changes the target (2026-08-17)

Four sourcing lanes (Kaggle uncapped, HuggingFace/Zenodo/Mendeley, iNaturalist/GBIF
live, CC web) plus two zero-train characterisation probes on the shipped embeddings.
**Eight candidates were pHash-gated; none survived.** That is the honest headline, and
the reasons are more useful than a source list would have been.

### 1. The scorch collapse is a decision-layer failure, not a data shortage

Measured on the shipped ONNX graphs (384-d DINOv2 CLS, `output[1]`), both bundles:

| | 2.6.8 | 2.7.2 |
|---|---|---|
| within-benchmark 5-fold CV probe AUROC | 0.997 ± 0.002 | **0.993 ± 0.004** |
| corpus-trained probe, transferred to benchmark | 0.924 | 0.824 |
| shipped head, P(leaf_scorch) AUROC on benchmark | 0.935 | 0.744 |
| shipped head, argmax recall on 62 scorch clusters | 36/62 | **5/62** |
| served (full pipeline) | 0.565 | 0.000 |
| **in-domain** supplement scorch recall | 0.784 | **0.941** |

2.7.2's representation has **not** lost the distinction — a linear probe on its own
frozen features separates the benchmark's arms at 0.993. The head allocates that
signal elsewhere: on the 62 scorch clusters the argmax moves scorch 36→5, healthy
18→32, leaf_spot 8→20. Meanwhile in-domain recall went **up** 15.7 points, which is
precisely why the ship gate passed three collapsed releases in a row.

**Ceiling on any data-side fix.** Oracle-perfect reselection of the existing 663 rows
— using benchmark labels to choose, which no real collector can do — moves the probe
from 0.812 to 0.921 AUROC. The best label-free rule reaches 0.859. The gap that has to
close is a head ranking at 0.744 and deciding at 0.000. **Data selection is
second-order here; the head and the operating point are first-order.**

### 2. The target is 31 clusters, not 62 — and 0.565 was one arm

Served recall decomposes: 2.6.8 scored **hibiscus 31/31 and banana_bush 4/31**. On the
benchmark the 31 banana_bush/turgor clusters are statistically indistinguishable from
the 151 healthy ones on nearly every measured property (nongreen-in-subject AUC 0.605,
marginality 0.581, lesion count 0.492, tan-necrosis 0.529); the largest separation is
an exposure difference. No release has ever exceeded 4/31 there.

> Sizing a collection round against "restore 0.565" is really "restore hibiscus", and
> sizing against 62 clusters systematically over-promises. Report the arms separately
> and enforce that in the ship gate.

### 3. Framing: two probes disagreed, and the disagreement is the finding

The transfer probe found photographic regime carries **no** transfer signal (|Δ| ≤
0.031 AUROC across border-plainness, subject fraction, saturation, blur, resolution —
with signs that flip between bundles). The causal ablation found framing is the
**single largest** axis: shrinking each of the 663 corpus scorch images onto a plain
light canvas at the benchmark's subject fraction drops recall 0.950 → 0.630 (McNemar
221 lost / 9 gained, p=5.1e-54), and the errors land on `healthy` (16 → 205).

Both are correct, and reconciling them is the point:

- The probe asked **"can I select better rows from what I have, by framing?"** No —
  because only **4.4%** of corpus scorch is in that regime. There is almost no
  variance to select on, so nothing correlates.
- The ablation asked **"does the model fail when framing changes?"** Yes,
  catastrophically — it had to *create* the variance synthetically to see it.

So framing is a **coverage** deficit, not a **selection** criterion. The probe's
conclusion ("restaging collection to imitate the plain-sheet regime is unsupported")
is right about re-weighting existing rows and wrong as collection guidance. A control
rules out the transform simply being destructive: the same operation on 300 healthy
hard-negatives costs 4.6× less (0.967 → 0.897).

**Mechanism.** In the corpus a plain foliage-free ground is a 16× `not_a_plant` cue:
237/377 `not_a_plant` rows sit inside the benchmark's own plainness/subject-fraction
window versus 117/3,012 across all eight plant classes; P(not_a_plant | plain ground)
= 0.669. This is a live product risk, not a benchmark quirk — **a user who photographs
a picked leaf on a worktop is in the regime we reject.**

### 4. A second, independent axis: morphology, and it is disjoint

Re-framing the benchmark does **not** recover scorch — it converts it to `leaf_spot`.
Tight-cropping the 31 hibiscus clusters to corpus-like framing moves leaf_spot 11 → 14;
adding a 3× chroma boost moves it to 23 (p=4.9e-4) while scorch never rises above 2.

The cause is what the two populations mean by "scorch". Chromatic tan/brown fraction,
corpus scorch deciles [10,25,50,75,90] = [0.044, 0.087, 0.170, 0.267, 0.409]; benchmark
hibiscus = [0.0029, 0.0051, 0.0079, 0.0098, 0.0110], **max 0.0176**. Only 9 of 663
corpus scorch images (1.4%) fall below the benchmark's brownest cluster. Most damning:
corpus **healthy** hard-negatives have brown fraction 0.122 — **15.4× the benchmark's
scorch median.**

> In our corpus's own colour statistics, the benchmark's scorched leaves are *less
> brown than our healthy images*. A classifier that learned "scorch = large tan area"
> **must** call them healthy. The corpus defines the class at end-stage desiccation;
> the benchmark shows it early, dull and achromatic, edge-concentrated on a green
> blade — which is the model's `leaf_spot` prototype.

Roughly 22 of 62 clusters are lost to framing and 24 to morphology — different fixes.

### 5. Public supply does not exist. This needs first-party collection.

Every lane searched exhaustively, with the truncation bug fixed in each (see below):

| lane | redistributable abiotic-scorch supply found |
|---|---|
| HuggingFace / Zenodo / Mendeley / Figshare | **0** — HF full-text "leaf scorch" = 2 hits, both PlantVillage strawberry *Diplocarpon* (rule 4); "tip burn"/"tipburn" = 0; Zenodo title queries all 0 |
| iNaturalist + GBIF | ~20–39 usable, and the gate rejected 41% of a hand-audit as mislabelled |
| Wikimedia / Flickr CC / Openverse | ~50–95 distinct, best single block ~30 CC0 images |
| Kaggle (1,684 datasets across 100 query formulations) | 0 clean, correctly-labelled ornamental abiotic scorch |
| Bugwood / IPM Images / extension services | ~47, **licence-blocked** (non-commercial / all-rights-reserved) |

Total redistributable: **~50–95 distinct photographs**, or 1–2% of what the corpus
already holds — and none of it in the missing stratum. Measured on the project's own
statistics, every candidate pool sits with the corpus, not the benchmark:

```
                corpus  bench   Bugwood  ScotNelson  Openverse  Commons
bg_plainness     0.491  0.960    0.636      0.545      0.628     0.605
subj_frac_bg     0.541  0.233    0.388      0.457      0.424     0.392
sat_mean         0.371  0.101    0.397      0.369      0.354     0.222
colorfulness     44.2    9.3     37.9       41.8       40.7      28.4
```

**Conclusion: the scorch gap is not closeable from public sources.** Not because the
search was shallow — it was exhaustive and the negatives are measured — but because
the missing thing is a photographic regime and a disease stage that nobody publishes.

### 6. What to collect first-party, sized against measured deficits

Per-stratum acceptance predicates, not volume targets. Current coverage in brackets.

| stratum | target | acceptance predicate | why |
|---|---|---|---|
| **scorch, plain ground** | 250 | `bg_plainness ≥ 0.636`, `subj_frac_bg ≤ 0.406`, `border_green_frac ≤ 0.030` | 4.4% coverage; −31.9 pp causal; also removes the 16× `not_a_plant` cue |
| **scorch, early/dull** | 250 | `brown_frac < 0.05`, marginal necrosis on a **still-green** blade | 1.4% coverage; the morphology axis re-framing cannot fix |
| **scorch, low chroma** | 150 | `sat_mean ≤ 0.15`, dull ambient light | 12.2% coverage; −10.6 pp, largely subsumed by framing |
| **healthy, plain ground** | 250 | same framing predicate, healthy subject | breaks "plain ground ⇒ not_a_plant" |
| **healthy, ND regime** | 250 | `det_union_frac ≥ 0.40`, `sat_mean ≥ 0.38`, `achro_bright ≤ 0.05` | ND precision 0.621 is a 6.4× prior distortion, not a label problem — the fix is **negatives**, not positives |
| **leaf_spot, houseplant** | 200 | one soft-lit glossy leaf indoors, confluent lesions | 98 rows / ~10 genera; recall 0.311 vs 0.744 garden |
| **pests + gray_mold, with boxes** | 200 | must carry bounding boxes | Stage 1 returns no box on 44.9% / 43.5%; `pests` is 21 rows and 19% under the 384 px floor |

~1,550 clusters. The first two strata have no substitute; the rest are supporting.

**A words-and-thresholds spec cannot capture all of it.** A cross-validated oracle split
of the existing scorch shows a real 0.33-AUROC transfer axis — 4–8× larger than any
measurable property — that correlates with *nothing* we can measure (|ρ| ≤ 0.16 across
15 properties, R² ≤ 2.6%; medians of the top and bottom quartiles are indistinguishable
on brown fraction, plant fraction, subject fraction, saturation, resolution). Visual
inspection names it only loosely: high-transfer = whole-plant collapse and dieback;
low-transfer = textbook marginal tip-burn on a turgid leaf. **Final selection should be
done in embedding space against an anchor set, not from a photographic checklist.**

### 6b. The out-of-domain ship gate — published and validated (2026-08-18)

The benchmark now lives at `maxayyt5/plantvision-ood-benchmark` (**private**, 213
images + both manifests, 1.6 MB) and §6 pulls it through the same
`_kaggle_download` choke point as every other source, then **blocks**. A missing
benchmark is a hard failure unless `REQUIRE_OOD_BENCHMARK` is turned off as a
deliberate edit — absence is how a check like this quietly stops existing.

Validated against both bundles on the published data, through the real
kagglehub download path, with the gate's own preprocessing (resize ×1.05,
centre-crop 224, hflip TTA):

| | hibiscus (31) | banana_bush (31) | healthy false-fire (151) | verdict |
|---|---|---|---|---|
| **2.6.8** (deployed) | 1.000 | 0.129 | 0.026 | **PASSED** |
| **2.7.2** (rejected) | 0.097 | 0.097 | 0.040 | **FAILED** — hibiscus 0.097 < 0.70 |

The gate accepts the model we kept and rejects the model we rejected. Both
in-domain gates passed 2.7.2.

`banana_bush` is reported but deliberately **unfloored**: no release has exceeded
0.16 and on this benchmark its clusters are statistically indistinguishable from
the healthy ones, so a floor there would block every run for a reason no data can
fix. The healthy ceiling sits on the same never-trained clusters so cost and
benefit share one population — a recall floor alone is satisfiable by firing
`leaf_scorch` more often, which the bias sweep in §7 shows explicitly.

**Keep the dataset private.** Its whole value is being outside every training
corpus; a public copy gets scraped into someone's "houseplant disease dataset"
and then into ours, which is exactly how the `aditisalvi04` contamination
happened.

### 7. Two cheap levers to measure before the next A100 run

Both testable on existing data, neither replacing the collection round:

1. **Re-weight the in-regime slice we already own.** The low-saturation quartile
   (n=166) transfers at 0.677 benchmark recall versus 0.161 for the other three
   quartiles — currently diluted 1:3.
2. **Fit the decision threshold on a regime-stratified calibration set.** Across seven
   stratifications probe AUC barely moves (0.921–0.965) while recall moves 7× (0.097 →
   0.677). The ranking is fine; the operating point is not.

Also: Stage 1 misses **14/62** benchmark scorch clusters versus 5/151 healthy (Fisher
OR 8.5, p=3.2e-05). A shrivelled, curled leaf is not leaf-shaped to a PlantDoc-trained
detector — the same structural cause as pests and gray_mold. New plain-ground scorch
should ship with boxes.

### 8. Method corrections found while searching

- **Kaggle's *search* endpoint truncates too**, not just the file lister: it caps at 20
  results per page regardless of `pageSize`. Paginating to an empty page returned
  **1,684** datasets versus 329 — 5.1×. Any negative result from a short-page break
  condition is worthless.
- **The iNat harvest capped at 600/term**, silently truncating 5 of 9 broad terms.
- **pHash alone does not count distinct images.** Roboflow exports augmented by
  *rotation* survive pHash as distinct. Count by original stem where the source
  exposes one.
- **`kaggle_inventory.gate()` reports `augmentation_ratio` as sampled/distinct**, which
  understates collapse when the sample is small relative to the folder.

### 9. What remains unknown

- Whether the 0.33-AUROC latent transfer axis is *collectable* by instruction at all,
  or only selectable in embedding space after the fact.
- Whether the banana_bush/turgor arm is learnable from any imagery, or should be
  retired from the benchmark as degenerate.
- Whether fixing the head/operating point alone recovers hibiscus without new data —
  untested, and the cheapest experiment available.

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
