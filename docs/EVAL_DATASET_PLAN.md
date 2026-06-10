# Houseplant disease **eval-supplement** dataset — collection plan

**Status:** plan / awaiting collection
**Decided (with maintainer):** web-scraped collection · **eval/calibration-first** role · target classes
`nutrient_deficiency`, `leaf_scorch_underwatering`, `gray_mold_botrytis`.
**Model version this targets:** 2.6.x (PlantVision) · 9-class classifier.

---

## 1. Why this dataset exists (scope)

The classifier already scores **0.89–0.97 F1** on six classes. They are **done — do
not collect for them.** The confidence problem is concentrated in three thin classes,
and the binding limit is **how few real photos the ship gate measures them on**, not
training volume:

| class | train | **eval (val_realworld)** | recall | precision | problem |
|---|---|---|---|---|---|
| `nutrient_deficiency` | 647 | **25** | 0.80 | 0.74 | eval almost unmeasurable; 54 % of train is crop proxies (rice/maize) |
| `gray_mold_botrytis` | 454 | **53** | 0.81 | 0.75 | thinnest/hardest; no clean real source exists |
| `leaf_scorch_underwatering` | 787 | **45** | 0.76 | **0.52** | over-triggers (catch-all); needs clean, tightly-defined real scorch |

At n=25 the per-class recall CI spans ~30 points — the gate can't tell a real
regression from noise. Adding **real, verified, deployment-style photos** to the
held-out slices is the single highest-leverage move for "high-confidence."

### Do **NOT** collect for these (already strong — collecting would only add noise/imbalance)
`healthy`, `not_a_plant`, `leaf_spot`, `pests`, `viral_disease`, `powdery_mildew`.
If a scrape incidentally returns one of these, **discard it.**

---

## 2. Per-class targets (precise bounds)

The dataset is **eval-eligible**, so the notebook's group-split sends **~40 %** of each
class across the four held-out slices (`val_realworld` = 15 %, the ship gate) and keeps
**~60 %** for training. So ~600 collected ⇒ **~90 to `val_realworld`, ~240 held-out total,
~360 to train** (a clean-real-photo training bonus on top of the eval win).

| class | **collect (min – target – max)** | drives `val_realworld` to ≈ | notes |
|---|---|---|---|
| `nutrient_deficiency` | **500 – 600 – 700** | 25 → ~110 | top priority; also dilutes the crop-heavy train set |
| `leaf_scorch_underwatering` | **500 – 600 – 700** | 45 → ~130 | clean real scorch sharpens the over-triggering boundary |
| `gray_mold_botrytis` | **200 – 400 – 500** | 53 → ~95 | **best-effort** — hardest to source (see §6). Below 200 is not worth a noisy slice |

**Total: ~1,200 – 1,900 verified images.** Stop at the target; more does not help the
gate and risks re-inflating these classes' priors.

> If you realistically **cannot reach ~200 verified `gray_mold_botrytis`**, tell me and
> I'll leave gray_mold's eval as-is (iNat + DoctorP) rather than ship a noisy 50-image
> scraped slice. A small *bad* eval slice is worse than a small honest one.

---

## 3. The eval-first ⨉ web-scrape tension — mandatory gates

Web-scraped data is normally **train-only** here, because for **eval** two failures are
fatal:

1. **Leakage.** Much of the public web *is* our training data — iNaturalist, PlantDoc,
   PlantVillage and the wired Kaggle sets are all online. A scraped image that is
   secretly a training image, placed in eval, **inflates the metric**.
2. **Label noise.** A search term is not a diagnosis. An eval label that's wrong makes
   the gate lie.

This dataset can be eval-grade **only if every image clears these gates:**

**A. Source-disjointness (prevents leakage).** Do **NOT** scrape from, or re-upload images
that originate on: **iNaturalist, PlantDoc, PlantVillage, Kaggle**, or these already-wired
sets — `abdulahad0296/indoor-plant-disease-detection-dataset`,
`pappu54/indoor-plant-disease-dataset-23-classes`,
`usmanafzaal/strawberry-disease-detection-dataset`,
`guy007/nutrientdeficiencysymptomsinrice`,
`alexanderuzhinskiy/the-doctorp-project-dataset`,
`alexanderuzhinskiy/pepper-disease-classification-dataset-doctorp`.

**B. Label verification (the eval-critical step).** Every image's diagnosis must be
**confirmed by a human**, not assumed from the search query. For community posts, use
only ones with an expert/consensus diagnosis in the thread.

**C. Pre-upload de-duplication.** Remove exact and near-duplicates *within* your set
(perceptual hash, e.g. `imagehash.phash`, Hamming ≤ 6) before upload. (The notebook has a
cross-split leakage backstop, but it drops the **eval** side of any collision — so dupes
you leave in cost you eval images.)

**D. Provenance manifest.** Record the source URL per image (see §7) so overlapping
domains can be audited and excluded.

---

## 4. Image acceptance criteria (eval-grade)

Accept an image only if **all** hold:

- **Real photograph.** No illustrations, diagrams, infographics, charts, multi-panel
  collages, or screenshots with UI.
- **Single clear subject.** One plant/leaf; the symptom is visible and dominant.
- **Resolution.** Short side **≥ 384 px** (reject < 256). Much better than the current
  real-photo source (~128 px), so it strengthens, not dilutes, the slice.
- **Clean frame.** No watermark/logo/large text overlay; no lab measurement overlays.
- **Domain.** Houseplant / indoor ornamental preferred. For `gray_mold_botrytis`,
  ornamental / greenhouse / seedling shots are acceptable given scarcity.
- **Diversity.** ≤ ~3 images per unique plant/post; vary species, lighting, severity,
  background. Avoid 50 near-identical shots of one specimen.
- **Correct, verified label** (§3-B).

### Class definitions (label *to these*, keep classes pure)
- **`nutrient_deficiency`** — interveinal/general chlorosis (yellowing), iron/N/Mg
  patterns, marginal yellowing from deficiency, purpling (P). *Not* whole-plant wilt,
  *not* brown crispy edges (that's scorch), *not* spots (that's leaf_spot).
- **`leaf_scorch_underwatering`** — crispy **brown** dead leaf **tips/margins**,
  desiccation, drought/heat scorch. *Not* sunburn bleaching, *not* wilting/curl (turgor),
  *not* generic yellowing (that's nutrient).
- **`gray_mold_botrytis`** — fuzzy **grey/brown** mould sporulation on leaves, stems,
  cuttings, flowers (Botrytis). *Not* white powdery coating (that's powdery_mildew),
  *not* dry necrosis (that's scorch).

---

## 5. Sources to search

**Avoid Wikipedia / Wikimedia as a label authority** — a large share is mislabeled or
generic. A Commons image is acceptable *only* if you independently confirm the diagnosis;
never trust the caption/category alone.

**Authoritative (best labels):** university **extension / IPM** plant-clinic galleries
(land-grant universities, e.g. PennState, Clemson, UMN, Missouri Botanical Garden plant
finder/pest pages), **RHS** (rhs.org.uk) disease pages, reputable houseplant-care sites
with expert diagnoses.

**Community (verify each):** Reddit `r/plantclinic`, `r/houseplants`, `r/IndoorGarden`
(use posts with a confirmed diagnosis in comments); houseplant-app community galleries
(Greg, Planta); Houzz/GardenWeb forums.

**Image search (verify each result):** Google/Bing image search on the precise queries
below — then apply §3/§4 to every hit.

### Per-class query seeds
- **nutrient_deficiency:** `interveinal chlorosis houseplant`, `iron chlorosis [pothos|
  monstera|calathea|ficus]`, `nitrogen deficiency leaf indoor plant`, `magnesium
  deficiency houseplant leaf`, `potassium deficiency marginal yellowing`, `phosphorus
  deficiency purple leaves`.
- **leaf_scorch_underwatering:** `underwatered [calathea|fern|spider plant] crispy
  leaves`, `leaf scorch houseplant`, `brown crispy leaf tips indoor plant`, `drought
  stress leaf margin necrosis`, `fern frond scorch`.
- **gray_mold_botrytis:** `botrytis [cyclamen|begonia|geranium|African violet]`, `gray
  mold houseplant leaf`, `botrytis blight ornamental`, `grey mould seedling`, `botrytis
  cutting propagation`. (Expect low yield — this is the hard one.)

---

## 6. Reality check on `gray_mold_botrytis`

Botrytis on **houseplant foliage** is genuinely rare to photograph — it shows mostly on
soft fruit, spent flowers, dense seedlings/cuttings, and in humid greenhouses. Expect to
work hardest here and to lean on **ornamental/greenhouse** shots (cyclamen, begonia,
geranium, African violet are classic Botrytis hosts). This is why the bound is lower
(200–500) and **best-effort**. Do not pad it with fruit-rot close-ups that don't look like
foliar mould — that would teach the wrong thing.

---

## 7. Kaggle dataset preparation

**Folder layout — folder names are the *exact* class names** (so the notebook maps them
directly, no keyword guessing):

```
houseplant-disease-eval-supplement/
├── nutrient_deficiency/
│   ├── nd_0001.jpg
│   └── ...
├── leaf_scorch_underwatering/
│   ├── ls_0001.jpg
│   └── ...
├── gray_mold_botrytis/
│   ├── gm_0001.jpg
│   └── ...
└── manifest.csv
```

- **Images:** JPG or PNG, RGB, short side ≥ 384 px. Flat files inside each class folder.
- **`manifest.csv`** (provenance + verification audit trail):

  ```csv
  filename,class,source_url,subject_plant,verified_by,notes
  nutrient_deficiency/nd_0001.jpg,nutrient_deficiency,https://…,pothos,me,interveinal chlorosis
  ```

  `verified_by` non-empty = the label was human-checked (gate §3-B).

---

## 8. How to upload to Kaggle

**CLI (recommended for re-runnable workflows):**

1. `pip install kaggle`; put your token at `~/.kaggle/kaggle.json` (or
   `~/.kaggle/access_token` for the new-format token) and `chmod 600`.
2. In the dataset folder, create `dataset-metadata.json`:
   ```json
   {
     "title": "Houseplant Disease Eval Supplement",
     "id": "<your-kaggle-username>/houseplant-disease-eval-supplement",
     "licenses": [{ "name": "CC0-1.0" }]
   }
   ```
3. First publish: `kaggle datasets create -p . -r zip --dir-mode zip`
4. Later updates: `kaggle datasets version -p . -m "add gray_mold batch" -r zip`

**Web UI:** kaggle.com → *Datasets* → *New Dataset* → drag the folder → set title/slug →
*Create*. Keep it **public** (educational/open — license is not a concern for this
project, CC0 is simplest).

Then **send me the dataset slug** (`<username>/houseplant-disease-eval-supplement`).

---

## 9. Notebook integration — *what I'll do once the slug exists* (the engineering work)

This is the "what needs work" on the code side; I implement it after you upload:

1. **`fetch_web_houseplant_supplement(slug, source='web_houseplant')`** in §2 — reads the
   three `<class>/` folders **directly** (folder name == class; no keyword resolver),
   validates (`min_side ≥ 256`), SHA + pHash de-dups against every other source, writes
   sidecars with a high (verified) but < 1.0 `label_confidence`.
2. **Make it eval-eligible** — add `'web_houseplant'` to all four `make_*_split`
   `allowed_sources` (so it seeds `val_realworld` / `val_indomain` / `temp_cal` /
   `conformal_cal`).
3. **Weights** — `SOURCE_WEIGHTS['web_houseplant'] = 1.0` (real, in-domain); decouple the
   LA prior only if a class's count would inflate it.
4. **Order** — fetch **before** the pre-split dedup so the pHash cross-split leakage
   resolver runs as a backstop.
5. **Guardrails** — a contract test asserting the three folders map 1:1 and the source is
   eval-eligible; bump `MODEL_VERSION`.
6. **Validate** — download via the token, confirm per-class counts + that no eval image
   collides with a training image, run the test suites.

---

## 10. Actions needed from you

1. **Pick the Kaggle slug** (`<username>/houseplant-disease-eval-supplement`).
2. **Collect + verify** per §3–§6. The **label-verification pass (§3-B) is the part that
   makes this eval-grade** — please don't skip it; an unverified eval slice is worse than
   none.
3. **Exclude the training-source domains** (§3-A) while scraping.
4. **Upload** (§8) and **send me the slug** → I wire it in (§9) and validate.

## 11. Open question for you
- **gray_mold feasibility:** if your workflow can't realistically reach ~200 verified
  gray-mold images, say so and I'll keep that class's eval on the current iNat/DoctorP
  supply instead of integrating a noisy slice. Everything else can proceed regardless.
