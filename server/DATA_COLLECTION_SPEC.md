# PlantVision — Data Collection Spec

**Purpose:** one-pass collection of the real-world data the model is starved
of, sized so it (a) fixes the thin classes, (b) breaks the specific
confusions the last run showed, (c) gives eval slices big enough to
*certify* results, and (d) future-proofs the 3 tiers (Aqua / Gamma /
Frontier) and the deferred classes — so you collect once.

> **The deployment domain is INDOOR HOUSEPLANTS photographed on PHONES by
> non-experts.** That is the distribution we're short on. Current training
> data leans agricultural (PlantDoc/PlantVillage) + iNaturalist + crop
> proxies (coffee/maize/rice/strawberry). **Collect real indoor-houseplant
> phone photos, with messy real backgrounds — NOT lab/studio/uniform
> backgrounds** (those bias the model and we already have them).

---

## 0. TL;DR — what to collect

| Class (folder) | Priority | Have now (train/eval, recall) | **Collect (train)** | **Collect (held-out eval)** |
|---|---|---|---|---|
| `gray_mold_botrytis` | 🔴 critical | 78 / 20 · 0.80 | **1,200** | 200 |
| `nutrient_deficiency` | 🔴 critical | 279 / 33 · 0.70 | **1,200** | 200 |
| `leaf_scorch_underwatering` | 🔴 high | 490 / 62 · 0.60 | **1,000** | 200 |
| `pests` | 🟠 high (eval gap) | 1163 / 239 · 0.96 | **1,000** | 200 |
| `powdery_mildew` | 🟠 medium | 1057 / 37 · 1.00 | **800** | 200 |
| `viral_disease` | 🟠 medium | 1169 / 137 · 0.86 | **800** | 200 |
| `leaf_spot` | 🟡 medium | 1429 / 209 · 0.93 | **800** | 200 |
| `healthy` | 🟠 high (diversity) | 1435 / 338 · 0.89 | **2,000** | 250 |
| `not_a_plant` | 🟠 high | 2136 / 569 · 0.97 | **3,000** | 300 |
| *future classes (§5)* | optional | — | ~400 each | 100 each |

Totals ≈ **13–15k train + ~2k eval** for the 9 production classes, plus
optional future classes. With 20–50 agents this is a short job. Recall
numbers are from the last run's `val_realworld` — the 🔴 classes are the
ones dragging quality.

**Two rules that matter most:**
1. **Real houseplant phone photos, real indoor backgrounds.** Reject
   lab/studio/uniform-background shots for the disease classes.
2. **Provide a separate, held-out `eval/` split** (last column) collected
   from *different* plants/sessions/sources than `train/`. Today's eval
   slices are 20–62 images (CIs ±0.12–0.18) — too small to prove an
   improvement. ~200 clean real eval images per class fixes that.

---

## 1. Folder structure for the Kaggle upload

Organize by **fine-grained label folder** + a `metadata.csv`. Fine labels
let us merge to the 9 production classes now and split later (e.g. per-pest,
per-nutrient) **without re-collecting**.

```
plantvision_houseplants/
  train/
    leaf_spot_fungal/            img0001.jpg ...
    leaf_spot_bacterial/
    powdery_mildew/
    gray_mold_botrytis/
    nutrient_deficiency_nitrogen/    nutrient_deficiency_iron/   ... (per element)
    leaf_scorch_underwatering/
    sunburn/                     # collected separately; merged into scorch for now
    pests_mealybug/   pests_scale/   pests_spidermites/  pests_aphids/ ...
    viral_disease/
    healthy/
    not_a_plant_indoor_scene/  not_a_plant_object/  not_a_plant_fake_plant/  ...
    overwatering_root_rot/  edema/  cold_damage/  physical_damage/   # future (§5)
  eval/                         # SAME folder names, DIFFERENT plants/sources
    ...
  metadata.csv
```

`metadata.csv` columns (one row per image):
```
filepath, fine_label, coarse_class, species, framing, severity,
lighting, license, source_or_credit, notes
```
- `coarse_class` ∈ the 9 production classes (your mapping of the fine label).
- `framing` ∈ {`closeup`, `whole_plant`, `wide_scene`}.
- `severity` ∈ {`mild`, `moderate`, `severe`} (best-effort).
- `license` ∈ {`own`, `cc0`, `cc-by`, `cc-by-sa`, …} — **see §7**.
- `notes` — any co-occurring condition (e.g. "also slight chlorosis").

If a `metadata.csv` is too much for the agents, the **folder name is the
minimum required signal** (the fetcher maps folders → classes by keyword),
but the CSV is what makes the data reusable for future tiers/classes.

---

## 2. The 9 production classes — definitions & collection rules

For every class: **real houseplant, phone photo, one dominant condition,
in focus, real background.**

### 🔴 `gray_mold_botrytis` — *Botrytis cinerea* (gray mold) — TARGET 1,200 + 200 eval
- **Include:** fuzzy grey/tan mould on leaves, stems, flowers, soft rotting
  tissue with a grey velvety coating; common on begonia, cyclamen, African
  violet, succulents, geranium, lettuce-like foliage in humid indoor air.
- **Exclude:** powdery *white* coating (that's powdery_mildew), black sooty
  mould (that's a pest by-product → `pests`), dry brown crisp (scorch),
  white *fluffy* mealybugs (pests).
- **Search:** "botrytis houseplant", "gray mold indoor plant", "grey mould
  leaf", "botrytis blight begonia/cyclamen/succulent".
- **Why critical:** only 78 real images today; the binding constraint on
  macro-F1. **4/20 are currently misread as `pests`** — collect clear grey
  *fuzz* (distinct from waxy/cottony pests).

### 🔴 `nutrient_deficiency` — TARGET 1,200 + 200 eval (sub-label by element)
- **Include, sub-labeled by element folder where possible:**
  - `nitrogen` — uniform yellowing of *older* leaves first.
  - `iron` / `manganese` — interveinal chlorosis (yellow between green
    veins) on *young* leaves.
  - `magnesium` — interveinal chlorosis on *older* leaves.
  - `potassium` — yellow/brown leaf *margins* on older leaves.
  - `phosphorus` — dark/purple-tinged dull leaves.
- **Exclude:** whole-leaf brown crisp edges from drought (scorch),
  pest stippling, natural variegation, **nutrient *toxicity*** (excess, e.g.
  manganese toxicity spotting — opposite signal, do not include).
- **Search:** "iron chlorosis houseplant", "nitrogen deficiency leaf",
  "interveinal chlorosis indoor plant", "potassium deficiency leaf margin".
- **Why critical:** 279 today and mostly *crop* proxies (coffee/maize/rice).
  **15/62 scorch images are misread as nutrient and 5 back** — this is the
  #1 confusion. Collect clean, unambiguous chlorosis (clearly *not* dry/crisp).

### 🔴 `leaf_scorch_underwatering` — abiotic desiccation — TARGET 1,000 + 200 eval
- **Include:** dry, crispy, **brown leaf tips and margins** working inward;
  curling; limp/wilted from chronic under-watering, low humidity, or salt
  build-up. Common on calathea, maranta, fern, peace lily, spider plant,
  dracaena.
- **Collect `sunburn` SEPARATELY (own folder):** bleached/scorched *patches*
  on the sun-facing side from excess light. We merge it into scorch for now
  but want it splittable later.
- **Exclude:** interveinal *yellowing* (nutrient), spots with halos
  (leaf_spot), whole-plant mushy yellowing (that's `overwatering_root_rot`,
  §5 — do NOT put it here; mixing over/under-watering is what hurt this class).
- **Search:** "underwatered houseplant crispy leaves", "leaf tip burn brown
  edges", "calathea crispy edges", "salt burn houseplant", "sunburn leaf
  scorch indoor".
- **Why high:** weakest class (0.60), heterogeneous. Clean, single-cause
  examples are what it needs.

### 🟠 `pests` — sap-sucking insects (merged) — TARGET 1,000 + 200 eval (sub-labeled)
- **Include, sub-labeled:**
  - `mealybug` — white cottony tufts in leaf axils / along veins.
  - `scale` — brown/tan/waxy immobile bumps on stems & leaf undersides.
  - also collect `spidermites` (fine webbing + stippling), `aphids`,
    `thrips`, `whitefly` into their own folders (future pest classes; merged
    into `pests` for now).
- **Exclude:** chew holes / caterpillars (not sap-suckers), grey fuzzy mould
  (gray_mold), healthy variegation.
- **Search:** "mealybug houseplant", "scale insect leaf", "spider mites
  webbing houseplant", "aphids indoor plant", "thrips damage leaf".
- **Why (eval gap):** train recall looks great (0.96) but it's mostly
  iNaturalist + cactus cochineal — **we have almost no real mealybug/scale on
  common houseplants in the eval slice**, so 0.96 is an upper bound. Real
  phone shots on pothos/ficus/succulents are the priority here.

### 🟠 `powdery_mildew` — TARGET 800 + 200 eval
- **Include:** white/grey powdery coating on leaf surfaces (begonia,
  African violet, ivy, jade, kalanchoe, rosemary, indoor herbs).
- **Exclude:** grey *fuzzy* mould (gray_mold), dust, leaf hairs/variegation.
- **Search:** "powdery mildew houseplant", "white powder leaf indoor plant".
- **Why:** strong recall but eval is only 37 — needs a real eval slice.

### 🟠 `viral_disease` — TARGET 800 + 200 eval
- **Include:** mosaic/mottled light-dark patterns, ring spots, leaf
  distortion/puckering, vein clearing, color-break streaking (orchid,
  hoya, canna, dieffenbachia, peace lily).
- **Exclude:** uniform yellowing (nutrient), natural variegation (collect a
  few *as healthy* to teach the difference), spots with halos (leaf_spot).
- **Search:** "mosaic virus houseplant", "ringspot virus leaf", "orchid
  virus mosaic", "leaf distortion virus indoor".
- **Why:** **10/137 viral are misread as healthy** — collect clear
  mosaic/mottle, and a handful of *healthy variegated* leaves as `healthy`.

### 🟡 `leaf_spot` — fungal + bacterial (merged) — TARGET 800 + 200 eval (sub-labeled)
- **Include, sub-labeled `_fungal` / `_bacterial`:** discrete spots/lesions —
  brown/black spots, concentric rings, yellow halos (fungal); small angular
  water-soaked spots between veins (bacterial). Anthracnose (dark sunken
  lesions) counts as fungal leaf spot.
- **Exclude:** **rust** (orange/brown pustules — different morphology, do not
  include), whole-leaf scorch, powdery coating, mosaic.
- **Search:** "leaf spot houseplant", "bacterial leaf spot indoor", "fungal
  leaf spot brown rings", "anthracnose leaf".
- **Why:** strong; just shift the domain toward real houseplants.

### 🟠 `healthy` — TARGET 2,000 + 250 eval
- **Include:** healthy foliage across **many species** (see §6) and real
  indoor settings — different lighting, backgrounds, angles, distances.
  **Deliberately include healthy *variegated* plants** (monstera, calathea,
  pothos 'marble', aglaonema) so variegation isn't read as disease.
- **Exclude:** any visible disease/pest/damage, fake plants (→ not_a_plant).
- **Why:** the prior most-confused-with class; diversity prevents
  false positives (14 healthy were misread as scorch — collect healthy with
  benign leaf-edge aging too).

### 🟠 `not_a_plant` (OOD / rejection) — TARGET 3,000 + 300 eval (sub-labeled)
This is what the OOD gate must reject. Collect indoor *non-plant* and
**hard negatives**:
- `indoor_scene` — rooms, furniture, walls, windows, floors, kitchens.
- `object` — electronics, mugs, books, clothes, food, toys.
- `person` / `pet` — hands, faces, cats/dogs (users point cameras at these).
- `fake_plant` — **plastic/silk artificial plants** (critical hard negative).
- `plant_print` — plants on wallpaper, fabric, mugs, paintings, screens.
- `partial_blurry_green` — extreme close-ups of green textures, blurry
  green, grass/moss/lawns (non-houseplant greenery).
- **Search:** "living room", "office desk", "kitchen counter", "fake plastic
  plant", "plant wallpaper", "blurry green", "lawn grass close up".

---

## 3. Image quality & diversity requirements (all classes)

- **Phone camera, real indoor scenes.** Varied phones, lighting (daylight /
  warm bulb / low light), backgrounds, angles, hand-held + on-shelf.
- **Resolution:** short side **≥ 600 px** (ideally ≥ 1000). Reject < 400 px /
  thumbnails. (Detector runs at 640, classifier crops at 224.)
- **Framing mix per class:** ~60 % **leaf-level close-ups** (the main
  classifier input), ~30 % **whole-plant**, ~10 % **wide scene / multiple
  plants** (feeds the detector + matches real "what's wrong with my plant"
  shots). Record `framing` in metadata.
- **One dominant condition per image** (clean labels). Note co-occurring
  issues in `notes`.
- **In focus.** A small *labeled* set of blurry shots is OK (helps calibrate
  the blur gate) but keep them out of the main class folders or mark them.
- **NO heavy augmentation / near-duplicates.** We want *real* diversity —
  rotated/flipped copies of one photo add little (the last Kaggle set was
  ~75 % near-duplicate Aloe and got deduped away). Distinct plants/sessions.
- **Avoid studio/lab uniform backgrounds** for the disease classes.

---

## 4. Detector (Stage 1) data — wide-frame + boxes

Separate, currently-unmeasured need: the YOLO leaf detector is trained on
agricultural bboxes and we can't measure its value indoors.
- **Collect ~1,000–2,000 wide/whole-plant indoor shots** (the `whole_plant`
  + `wide_scene` framings above already help) and, if the agents can,
  **leaf bounding boxes** (YOLO/COCO format) on a subset (~500–1,000).
- Even without boxes, flagging `framing=wide_scene/whole_plant` lets us build
  a wide-frame **eval** set to finally measure the detector + the
  whole-image-vs-crop arbitration on real indoor compositions.

---

## 5. Future / deferred classes — collect NOW (sub-labeled), activate later

Collect ~**400 train + 100 eval each** so we can turn them on without
re-collecting. Keep them in their own folders; they do **not** dilute the 9
production classes.
- **`overwatering_root_rot`** — *currently missing and common*: yellowing +
  wilting despite wet soil, mushy/black stems, black mushy roots. (Today
  "withering" cases get misfiled into scorch — this class fixes that.)
- **`sunburn`** — bleached patches on the light-facing side (split from
  scorch later; different care = move out of sun).
- **`edema`** — corky/blistered bumps on leaf undersides from overwatering
  (a content card already exists for it).
- **`spider_mites`, `thrips`, `aphids`, `whitefly`** — split out of `pests`
  later (different treatment).
- **`cold_damage`** (translucent/blackened patches from chill) and
  **`physical_damage`** (tears, sunscald, handling) — common benign
  look-alikes worth modeling.

---

## 6. Species to spread across (avoid single-species bias)

Stratify every class across common houseplants so no one species dominates
(the last Kaggle set was Aloe-heavy and hurt generalization):

pothos, monstera, snake plant (sansevieria), ZZ plant, peace lily, philodendron,
calathea / maranta (prayer plants), ficus (fiddle-leaf, rubber plant),
spider plant, dracaena, aglaonema, anthurium, begonia, african violet,
croton, hoya, alocasia, succulents (aloe, echeveria, jade, haworthia),
cacti, orchids (phalaenopsis), ferns (boston, maidenhair), palms (areca, parlor),
english ivy, herbs (basil, rosemary, mint).

Aim for **no single species > ~15 %** of any class.

---

## 7. Licensing & provenance (required for a shipped product)

- Only collect images you can legally use: **your own photos, CC0 / CC-BY /
  CC-BY-SA, or explicitly licensed.** Record `license` + `source_or_credit`
  per image. Avoid un-licensed web scraping for anything that ships.
- Prefer sources with clear licenses: Wikimedia Commons, iNaturalist
  (CC-licensed observations), Flickr CC, university extension pages with
  reuse terms, your own captures.

---

## 8. How this plugs into training

- Upload as **one Kaggle dataset**; send me the slug. I'll wire a dedicated
  fetcher (it already authenticates via your Colab `KAGGLE_API_TOKEN`) that
  maps the fine-label folders → the 9 classes, keeps your `eval/` split as a
  **real held-out `val_realworld`** (no augmentation, no leakage), and routes
  `train/` into training. Because it's real in-domain data, it gets **full
  sampler weight** (unlike the down-weighted crop proxies CoLeaf/MPLD/rice).
- The held-out real `eval/` (~200/class) is the single biggest unlock: it
  turns today's ±0.15 CIs into ±0.04–0.06, so we can finally *certify* the
  thin-class gains and run the Frontier capacity probe on a trustworthy slice.

---

## 9. Priority order (if collecting in waves)

1. **`gray_mold_botrytis`, `nutrient_deficiency`, `leaf_scorch_underwatering`**
   — the three starved/weakest classes (biggest quality lever).
2. **Held-out `eval/` for all 9** (~200 each) — unlocks certification.
3. **`pests` real mealybug/scale** + **`not_a_plant` hard negatives**
   (fake plants, plant prints).
4. `powdery_mildew`, `viral_disease`, `leaf_spot`, `healthy` real-domain top-up.
5. Future classes (§5) — especially `overwatering_root_rot`.
