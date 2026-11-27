# V9 minimum round: data collection task

**Status:** open, 2026-11-27. **Owner:** the collector, with an agent reviewer.
**Result:** Kaggle supplement `maxayyt5/houseplant-disease-eval-supplement`
revision 9.

This round supersedes three rules of `docs/DATASET_LABELING_PLAN.md` for its
own rows: only the minimum strata are targeted (P1), agent review replaces
human adjudication (P6), and up to 5 % AI-generated photos are allowed (P3).
Everything else in that plan still applies.

**Package contents** (the task archive; in the repo this file is
`docs/V9_MINIMUM_ROUND.md` and the harness is `server/tools/screen_candidates.py`):

| Path | What |
|---|---|
| `SPEC.md` | this document |
| `tools/screen_candidates.py`, `tools/requirements.txt` | the screening harness (section 8) |
| `templates/manifest_template.csv` | a filled manifest example: own photo, internet photo, AI-generated, disputed |
| `templates/notes_template.csv` | per-photo notes the harness reads with `--meta` |
| `inputs/batch1_review.csv` | per-photo verdicts for the first 87-photo batch (section 7) |
| `inputs/v8_relabel_queue.csv` | v8 healthy negatives to review for relabelling (section 6) |

## Why

The deployed model (2.7.5) misses early scorch. It reads a brown tip on a
green leaf as `healthy` or `leaf_spot`, because the supplement shows scorch at
end-stage browning and teaches the healthy side with brown-tipped negatives.
This round adds the missing early stage and its boundary partner. It also
gives the augmentation ablation (training with and without photometric
augmentation) enough new scorch in the holdout to be measurable. That
comparison runs on v9 once v9 is published (section 10), not before.

## 1. Targets

Counts are **capture families**. One family is one plant photographed in one
session; for an internet source, one page or post. More shots of the same
plant in the same session share the family and do not count again.

| # | Stratum | Class folder | Target | Have | Left |
|---|---|---|---|---|---|
| 1 | Early/dull scorch | `leaf_scorch_underwatering/` | 200 | 13 | **187** |
| 2 | Houseplant leaf spot | `leaf_spot/` | 100 | 0 | **100** |

"Have" is the 13 strong early families from batch 1 (section 7), counted once
they pass review. Medium and late scorch is welcome as training-only supply,
but does not count toward the target.

How the families get used: every agreed, non-synthetic family is published
`split_hint=eval_ok`. The notebook sends about 30 % of eval-eligible families
to the permanent holdout, about 25 % to validation and calibration, and trains
on the rest. So 200 early families put about 60 new early-scorch families in
the holdout, next to v8's 116 scorch photos there.

## 2. What each stratum must show

### Stratum 1: early/dull scorch

**Show:** dry, brown necrosis confined to the leaf tip or margin, on a blade
that is still green and firm. A thin yellow band between the brown and the
green is fine. As a guide, the brown is under about 5 % of the leaf and the
rest looks healthy.

- Typical plants: peace lily, calathea, maranta, dracaena, palms, ferns,
  ficus, avocado and citrus seedlings, but any species counts.
- Salt or fertiliser tip burn counts: the class card lists salt build-up as a
  cause of this condition, and it looks the same.
- Several brown tips on one plant are fine. Box every visible leaf (section 4).

**Do not include** (or file under the right class instead):

| Looks like | Why it is not stratum 1 |
|---|---|
| Large dry areas, whole-leaf browning, papery dead margins | end-stage scorch: training-only supply, not the target |
| Wilting or collapsed leaves, mushy or black tissue | rot, cold damage or overwatering |
| Torn or physically damaged edges | mechanical damage |
| Bleached, whitish or papery patches on the sun-facing side | sunburn (a separate, deferred class) |
| Red, orange or purple leaves | autumn colour or pigment, not necrosis |
| Yellowing as the main symptom, with little brown | `nutrient_deficiency` look-alike |
| Round brown spots inside the blade | `leaf_spot` |
| Lettuce tipburn | calcium disorder: `nutrient_deficiency` |
| Stippling, webbing or insects visible | `pests` |

### Stratum 2: houseplant leaf spot

**Show:** an indoor houseplant leaf with brown, black or tan lesions **inside**
the blade, discrete or merging, often with a darker rim or a yellow halo. One
soft-lit, glossy leaf filling much of the frame is the ideal shot; fungal and
bacterial spot both belong here.

**Do not include:** browning confined to the tips or margin (that is
stratum 1), white powdery coating (`powdery_mildew`), grey fuzz
(`gray_mold_botrytis`), mosaic or ring patterns (`viral_disease`), garden or
field crops (this stratum is houseplants only).

### Domain

Houseplants, garden ornamentals and home-grown edibles. No commercial fields,
plantations or production greenhouses (stratum 2 is houseplants only).

## 3. Sources

Any way that yields the right photos:

- **Own photos.** The best source: you know the plant and the session.
- **Internet collection.** Forums, extension-service pages, blogs, Q&A sites,
  iNaturalist, Reddit, Flickr. Keep the landing URL and, where known, the
  creator and licence. Licences are recorded, not required.
- **Public datasets**, except the deny-listed ones below.
- **AI-generated photos**, at most **5 % of all kept photos** in the round
  (the harness stops the run above that). Rules:
  - mark each with `synthetic=1` in the manifest, or start its filename with `syn_`;
  - always `split_hint=train_only`: a synthetic photo never grades a model;
  - one family per generation template or prompt;
  - spread them over both strata, and over healthy too where possible, so a
    "generator look" never becomes a shortcut for one class;
  - they must meet the stratum definition and the image rules like any other photo.

**Never use** anything from the datasets the OOD benchmark was built from:
`aditisalvi04/indoor-plant-disease-dataset` (the whole `Hibiscus_*` branch)
and `shuvokumarbasak4004/flower-leaf-diseases-dataset-new-and-update` (every
folder). A copy there makes the benchmark meaningless.

Prefer sources the notebook does not already pull (PlantDoc, PlantVillage,
DoctorP, iNaturalist research grade, the Kaggle sources in notebook §2): a
copy of a photo the model already trains on adds nothing.

## 4. Rules for every photo

| Rule | Detail |
|---|---|
| File | JPEG, PNG or WebP; short side at least **384 px**; sharp enough for the server's blur floor (the harness checks both) |
| Content | no watermarks, drawn arrows or circles, collages or screenshots |
| Boxes | a YOLO label file beside each photo (`photo.jpg` + `photo.txt`), one row per box: `0 cx cy w h`, normalised 0 to 1, class 0 = `plant_leaf`, in the photo's displayed orientation. **Box every visible leaf, not only the damaged one**: the leaf detector trains on these, and an unboxed leaf teaches it to ignore leaves. Required for both strata |
| Family | `capture_family_id` = `fam_` + the first 16 hex of `sha1(batch_id + "|" + plant_ref + "|" + session_id)` for own photos; one id per source page or post for internet photos. All shots of one plant in one session share it. Prefer at most 3 photos per family |
| Layout | `<class>/<file>`; unique, ASCII-safe file names |
| Published rows | never rename or move a published file, and never add a family id to an already published row (both change the permanent holdout) |

**Manifest** (`manifest.csv`, plain UTF-8 **without** a byte-order mark; the
batch 1 CSVs had one and broke both the QA tool and the notebook):

| Column | Value |
|---|---|
| `filename` | `<class>/<file>` |
| `class` | the folder name |
| `split_hint` | `eval_ok` for agreed non-synthetic rows, `train_only` otherwise |
| `first_seen_model_version` | `never` for new rows |
| `capture_family_id` | as above |
| `stratum` | `scorch, early/dull` or `leaf_spot, houseplant`; blank for supply rows |
| `synthetic` | `1` for AI-generated, else `0` |
| `verified_by` | `agent:<model>` |
| `adjudication_status` | `agreed` or `disputed` |
| `adjudication_note` | one line: why |
| `confidence` | 0 to 1, the reviewer's; under 0.80 for disputed rows |
| `source_url`, `creator`, `license_id` | internet rows: landing page and whatever is known |
| `immutable_source_id` | internet: `<host>:<item>:<photo>`; own photos: `scan:<sha256 of the original file>` |
| `acquisition_batch_id` | your batch name, one per collection session or scrape |

The harness pre-fills `filename`, `class`, `split_hint`,
`first_seen_model_version`, `capture_family_id` and `synthetic`, and suggests
a stratum in `stratum_candidate` (section 8). The review adds the
`adjudication_*`, `verified_by` and `confidence` columns. Copy the rest from
your collection notes (`templates/notes_template.csv`). A filled example is in
`templates/manifest_template.csv`.

## 5. Review

No human review is required. Every kept photo gets one review pass by an
**Opus agent** looking at the image:

1. **For its class:** does it match the stratum definition in section 2?
2. **Against the confused class:** stratum 1 must also be rejected as
   `leaf_spot` and as `healthy`; stratum 2 must be rejected as scorch.
3. Both yes → `agreed`. Otherwise → `disputed`, published `train_only` with
   `confidence` under 0.80. Disputed rows are kept as boundary information,
   but they never grade a model.

Work through the harness's buckets and contact sheets (section 8):
`early_fast` and `spots_fast` first, then `review`; `late` is mostly supply.

## 6. Relabel v8's brown-tip negatives

v8 teaches the healthy side of the scorch boundary with 787 "boundary"
negatives, and many show exactly the early scorch this round collects. Four
are the very same photos or sessions as batch 1 candidates:
`healthy/hl_0588.jpg` (PD008), `hl_0790` (PD110), `hl_0451` (PD009) and
`hl_0914` (PD141).

**Queue:** `inputs/v8_relabel_queue.csv`, 518 rows. 133 are high priority:
both v8's own notes describe brown tips and the harness flags a scorch
pattern. 385 are medium: one signal only. Each row carries v8's notes and the
harness measures.

**Task:** an Opus agent reviews each image (from the v8 supplement) against
the stratum 1 definition.

- **Fits** → relabel:
  - move `healthy/<file>` to `leaf_scorch_underwatering/<file>`;
  - set `class=leaf_scorch_underwatering`, `role=positive`, `hn_kind` blank;
  - keep `split_hint=train_only` (these rows have been trained on);
  - add `relabeled_from=healthy` and a one-line note;
  - fill `review_decision` / `review_note` in the queue.
- **Does not fit** → keep the row exactly as it is.

Moving these files is a deliberate exception to "never move a published file":
boundary rows are training-only and never in the holdout, so its membership
cannot change. At least one boundary row must remain, or notebook §2 stops.

## 7. Fix batch 1 (the 87-photo archive)

`inputs/batch1_review.csv` has a verdict per photo. In short:

- **Drop 12** already in v8: PD100, PD118, PD124, PD135, PD154, PD159, PD161,
  PD162, PD163 (as scorch), and PD008, PD009, PD110 (filed in v8 as healthy:
  decide them with the relabel queue).
- **Keep 3 as training-only** siblings of v8 sessions: PD121, PD152, PD160.
  Decide PD141 together with `hl_0914`.
- **Re-box every photo**, every visible leaf.
- **Review the 14 label doubts** listed in the CSV (sunburn, nutrient,
  leaf-spot and autumn-colour look-alikes).
- **Salt references:** PD144 goes in as scorch; PD149 needs a domain check
  (nursery bench); PD168 stays out (a studio composite).
- **Re-save the CSVs** without a byte-order mark.

## 8. The screening harness

`tools/screen_candidates.py` (in the repo: `server/tools/screen_candidates.py`).
Install with `pip install -r tools/requirements.txt` (Python 3.11+).

It **drops only objective defects**, with the verdict `drop:<reason>`:

- `corrupt`, `too_small` (short side under 384 px), `blurry` (under the server's blur floor);
- `dup_in_batch`: the first copy is kept;
- `dup_other_class`: the same photo filed under two classes, a label conflict;
  the kept copy is flagged `dup_in_other_class`;
- `dup_of_reference`: already in a corpus you pass with `--against`;
- `bench_leak`: a copy of the OOD benchmark; the run exits 2.

Nothing is dropped for how it looks. Everything kept gets a **bucket** that
orders the review queue:

| Class | Buckets |
|---|---|
| scorch | `early_fast`, `late`, `review`, `no_visible_damage` |
| leaf spot | `spots_fast`, `edge_damage_check` (may be scorch), `review`, `no_visible_damage` |
| healthy | `scorch_candidate`, `clean`, `review` |

Buckets come from two colour measures inside the photo's boxes: `brown_frac`
(brown share of leaf pixels) and `edge_share` (brown share touching the leaf
outline). Measured on batch 1 against manual verdicts, about 7 in 10
`early_fast` photos were truly early and about 9 in 10 `late` photos were truly
late. Treat buckets as an order, never as a decision.

It also flags photos without required boxes (`needs_boxes`) and broken label
rows (`bad_box_rows`), counts families against the targets, and stops the run
(exit 3) when AI-generated photos exceed 5 % of the kept ones.

```bash
# A new batch: against the published supplement (and earlier batches) and the benchmark.
# --sift also catches crops and letterboxed copies, which pHash misses (7 of 16 in batch 1).
python tools/screen_candidates.py --root batch2/ --out report/batch2 \
    --against supplement_v8/ --against batch1/ --bench oodbench/ --sift --sheets report/batch2_sheets

# Optional per-photo notes (synthetic, capture_family_id, source_url ...), keyed by filename:
python tools/screen_candidates.py --root batch2/ --meta batch2/notes.csv --out report/batch2

# Rebuild the v8 relabel queue's harness signal:
python tools/screen_candidates.py --root supplement_v8/ --meta supplement_v8/manifest.csv \
    --where hn_kind=boundary --only-classes healthy --out report/v8_relabel --sheets report/relabel_sheets
```

Outputs: `<out>.csv` (one row per photo, manifest columns first), `<out>.json`
(verdict, bucket and target counts) and, with `--sheets`, contact sheets per
class and bucket with boxes drawn, for the reviewer.

## 9. Publish revision 9 (maintainer, from the repo)

1. Build the v9 tree: v8, plus the accepted new rows, plus the relabels; one
   `manifest.csv` with the columns of section 4.
2. `python -m server.tools.filter_supplement --root <v9> --bench <oodbench> --out <report>`.
   It exits non-zero on a benchmark leak. Publish its `<report>.csv` as `manifest.csv`.
3. Check at least 3,000 rows (`SUPPLEMENT_MIN_ROWS`) and at least one boundary row.
4. `kaggle datasets version -p <v9> -m "v9 minimum round"`, then confirm the
   revision with `python -m server.tools.kaggle_inventory --list maxayyt5/houseplant-disease-eval-supplement`.

## 10. Done when

- At least 200 early-scorch families (stratum 1, including batch 1's 13) and
  100 houseplant leaf-spot families are `agreed`, boxed, at least 384 px, and
  free of duplicates against v8 and the benchmark.
- AI-generated photos are at most 5 % of kept photos, all `train_only`.
- Every row of the relabel queue has a decision, and the relabels are applied.
- Revision 9 is published; the harness and `filter_supplement` reports are kept
  with it.

**Then, outside this task:** two Colab runs on v9 with `MODEL_VERSION='2.7.6'`,
`TRAIN_MODE='gamma'`, `SEED=0`. One is the default; the other sets
`TRAIN_AUGMENTATION='off'` in §4. They are compared on the v9 holdout with
`tools/paired_eval.py` and `tools/eval_bootstrap.py`.
