# Class-coverage research — houseplant diseases vs. our taxonomy & competitors

> **Research report only — no class/dataset changes were made.** Scope: indoor
> houseplants, phone photos. Companions: `ROADMAP.md` (project status + next
> steps), `DATASET_LABELING_PLAN.md` (the data loop). Requested 2026-06-18 to
> decide what (if anything) to add/remove for the next dataset version.
>
> Grounding: repo state is read from `server/diseases_data.py`,
> `server/train_notebook.ipynb` §1, `ROADMAP.md`, `DATASET_LABELING_PLAN.md`.
> External claims are cited inline to horticulture/extension sources. Low-
> confidence claims are flagged.

## TL;DR

**The 9 classes are essentially right; add nothing structural yet.** The highest-
value work is precision-hardening the two abiotic classes
(`leaf_scorch_underwatering`, `nutrient_deficiency`) and closing the `gray_mold`
supply gap — exactly what the data plan already targets. The biggest *prevalence*
gap is **overwatering / root rot**, but it is also the least diagnosable from a
leaf photo and has no clean public houseplant data — so it should be handled as a
**symptom-triage path first, not a vision class**. Chasing breadth against
Blossom/PictureThis would trade our real differentiator (calibrated, OOD-aware,
free diagnosis) for labels the model cannot separate on the available data.

## 1. Most common houseplant conditions (prevalence × phone-photo separability)

Indoor plants differ sharply from field agriculture: fewer foliar fungal
epidemics, far more water-management disorders and sap-sucking pests.

| Condition | Category | Prevalence | Phone-photo separability |
|---|---|---|---|
| **Overwatering → root rot** | abiotic→biotic | **#1 owner search topic** ([Horticulture.co.uk](https://horticulture.co.uk/blogs/growing/houseplants-statistics), [Penn State](https://extension.psu.edu/pest-and-disease-problems-of-indoor-plants)) | **POOR** — foliar yellowing/wilt mimics under-water & nutrient; the clue is roots + wet soil ([RHS](https://www.rhs.org.uk/disease/phytophthora-root-rot)) |
| Fungus gnats | pest (soil) | "most common indoor pest" ([Iowa State](https://yardandgarden.extension.iastate.edu/how-to/diagnosing-houseplant-problems-caused-insect-pests), [UMN](https://extension.umn.edu/news/houseplant-pests)) | POOR — flying adults / soil larvae, not a leaf symptom |
| Spider mites | pest (mite) | very common; dry indoor air ([Clemson](https://hgic.clemson.edu/factsheet/common-houseplant-insects-related-pests/)) | MIXED — webbing distinctive; **early stippling overlaps nutrient/scorch** |
| Mealybugs | pest | very common | GOOD — cottony masses distinctive |
| Scale | pest | common | MODERATE — looks like plant bumps |
| Underwatering / scorch | abiotic | very common | MODERATE — brown tips overlap nutrient burn / salt / root rot |
| Nutrient deficiency | abiotic | common | POOR — broad chlorosis overlaps overwater/age/light |
| **Powdery mildew** | fungal | common (African violet, begonia, ivy) | **EXCELLENT** — white floury coating, unmistakable |
| Aphids | pest | common on new growth | MODERATE |
| Fungal/bacterial leaf spot | fungal/bact. | common | MODERATE — spots clear; fungal vs bacterial not eye-separable |
| Thrips | pest | rising (aroids/Monstera) | POOR — silver streaks + frass, confused with mites |
| Whitefly | pest | common | POOR on a still photo |
| Gray mold (Botrytis) | fungal | common in cool/humid/crowded | MODERATE — fuzzy gray sporulation when present |
| Edema | abiotic | succulents/pelargonium/peperomia | MODERATE — corky undersides ([UC IPM](http://ipm.ucanr.edu/PMG/GARDEN/ENVIRON/edema.html)) |
| Sunburn | abiotic | common after a light move | MODERATE — bleached patches, overlaps scorch |
| Viral | viral | less common indoors | POOR — mottling overlaps nutrient/mite |
| Anthracnose / sooty mold / downy mildew / rust | fungal | **low on indoor foliage** (mostly crop/outdoor) | varies |

**Takeaways:** the biggest real-world cluster is **water management**, and it is
precisely the hardest to read from a leaf photo. The most *photogenic/separable*
conditions are powdery mildew (best), mealybugs, and webbing-stage spider mites.
The "diseases" that agricultural datasets are full of (anthracnose, downy mildew,
rust, sooty mold) are **low-prevalence on indoor plants** — their abundant public
data is a domain trap, not an opportunity (see §4).

## 2. What we have vs. don't

Canonical classes (`server/diseases_data.py` / notebook §1): `gray_mold_botrytis`,
`healthy`, `leaf_spot` (fungal+bacterial merged), `not_a_plant` (OOD reject),
`pests` (mealybugs+scale; **spider mites excluded**), `powdery_mildew`,
`viral_disease`, `leaf_scorch_underwatering`, `nutrient_deficiency`.

- **Well-covered:** `powdery_mildew` (P 0.97/R 0.91, "solved"), `leaf_spot`
  (0.90/0.89), `healthy` (0.94), `not_a_plant` (0.99), `pests` (0.94/0.96).
  Metrics from the v2.5.0 model card (per-class `val_realworld` eval).
- **Covered but coarse/weak:** `pests` lumps mealybugs+scale and **drops spider
  mites** (the #2/#3 indoor pests); `leaf_spot` merges fungal+bacterial (defensible);
  `nutrient_deficiency` over-triggers (P 0.72); `leaf_scorch_underwatering` is the
  project's **#1 defect** (P 0.52→0.71 across versions) and silently absorbs
  root-rot foliar symptoms and sunburn; `gray_mold_botrytis` is correct but
  supply-limited (widest CIs).
- **Not covered:** **overwatering/root rot** (absent), fungus gnats, spider
  mites/thrips/aphids/whitefly (none individually addressable), edema & sunburn
  (content cards exist, classes inactive), anthracnose/sooty mold/downy/rust
  (acceptably absent — low indoor prevalence).

**The defining gap is overwatering/root rot** — most prevalent and the top owner
search topic, yet unnamed, and currently misrouted into the two weakest classes.
It is roadmap-deferred for a good reason: it is also the least diagnosable from a
leaf photo (§1, §4).

## 3. Competitor coverage

Sources: app marketing/store/help pages (cited inline). Marketing
overstates ("100 diseases") — treat counts skeptically; unenumerated cells are
low-confidence.

| Condition | **Us** | PictureThis | NatureID/Blossom | PlantIn | Planta | Plantix/Agrio |
|---|---|---|---|---|---|---|
| Powdery mildew | ✅ | ~ | ✅ | ~ | ~ | ✅ crop |
| Gray mold / botrytis | ✅ | ~ | ~ | ~ | ~ | ✅ crop |
| Leaf spot | ✅ | ~ | ~ | ~ | ~ | ✅ crop |
| Viral | ✅ | ~ | ~ | ~ | ~ | ✅ crop |
| Nutrient deficiency | ✅ | ~ | ~ | ~ | ~ | ✅ crop |
| Underwater / scorch | ✅ | ~ | ~ | ~ | ~ | ~ |
| **Root rot / overwatering** | ❌ | ✅ | ✅ | ~ | ~ | ~ |
| Mealybugs | ✅ lumped | ~ | ✅ | ~ | ~ | ✅ |
| Scale | ✅ lumped | ~ | ~ | ~ | ~ | ✅ |
| **Spider mites** | ❌ | ~ | ✅ | ~ | ~ | ✅ |
| Aphids / thrips / whitefly | ❌ | ~ | ✅ (aphids/thrips) | ~ | ~ | ✅ |
| Sunburn / edema | ❌ card | ~ | ~ | ~ | ~ | ❌ |
| **Out-of-domain reject** | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Calibrated "not sure"** | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Free diagnosis** | ✅ | ❌ | ❌ | ✅ | ❌ | B2B |

Per-app: **PictureThis** ([site](https://www.picturethisai.com/)) and
**NatureID/Blossom** ([features](https://blossomplant.com/features/plant-disease-identification))
explicitly name **root rot** and individual pests (Blossom: thrips, mites, aphids,
mealybugs) — the clearest parity gaps. **PlantIn** offers free diagnosis but
doesn't enumerate pests; **Planta** has "Dr. Planta" + human review; **Greg** is
light/community; **Plantix/Agrio** are **agricultural** (different domain);
**Pl@ntNet/Flora Incognita** are species-only.

**They beat us on breadth** (root rot, granular pests). **We beat them on trust:**
explicit OOD reject, calibrated uncertainty/conformal sets (counters the category's
#1 complaint — overconfident wrong diagnoses), on-device offline, free, no account.
Adding labels we can't separate would erode that edge.

## 4. Public data availability & safety to add

Core finding: **clean, permissive, indoor-houseplant, phone-photo disease imagery
barely exists.** The big corpora are agricultural/lab, and field imagery is a known
accuracy-killer for phone photos (the plan is actively *de-cropping*
`nutrient_deficiency` for this reason). The one genuinely houseplant CC-BY phone
set found — the Money-Plant/Pothos Disease Atlas (~1,872 imgs,
[PMC11729688](https://pmc.ncbi.nlm.nih.gov/articles/PMC11729688/)) — covers the
*wrong* classes (bacterial wilt, Mn toxicity).

**Anti-leakage:** iNaturalist, PlantDoc, PlantVillage, Wikimedia and the listed
Kaggle training slugs are already **training** sources — fine to train on, but
**banned as eval-supplement origins** (`DATASET_LABELING_PLAN.md`). Eval must stay
source-disjoint.

| Candidate | Houseplant data? | License | Domain | Separability risk | Verdict |
|---|---|---|---|---|---|
| **root_rot/overwatering** | none dedicated | — | — | **Severe** — ≈ nutrient + scorch | **NO (yet)** — must self-collect |
| **spider_mites** split | PlantVillage tomato, Roboflow, PLOS (100s–1000s) | PV research / mixed | lab tomato + macros | **High** — early stipple overlaps nutrient/scorch (the documented exclusion reason) | **RISKY** — only webbing stage is clean |
| anthracnose | mango/crop (Roboflow) | mixed | field/orchard | overlaps leaf_spot | **NO** — low indoor prevalence |
| sooty_mold | ~14 imgs (mango) | unstated | crop | distinct but rare | **NO** — too sparse |
| downy_mildew | corn/cucurbit | mixed | field | conflated w/ powdery; rare indoors | **NO** |
| thrips | IP102 / sticky traps | **IP102 academic-only** | macros/traps | overlaps mites | **RISKY/NO** |
| aphids | abundant (IP102, APHID-4K) | IP102 academic-only / unstated | field/macro | clusters transfer moderately | **RISKY** — license + domain |
| whitefly | trap/field | unstated | traps/greenhouse | hard on still leaf | **NO** for foliar |
| edema | none labeled | — | — | distinct blistering | **NO (yet)** — self-collect |
| sunburn (abiotic) | mostly mislabeled fungal "scorch" | research | field/crop | **Severe** — overlaps leaf_scorch | **RISKY** — concept-mismatched |

**License flags:** IP102 is **academic-use-only** (unsafe for a commercial app);
Roboflow Universe licenses are heterogeneous/often unstated (verify per-set).

## 5. Recommendation (add / split / keep / remove — not implemented)

**KEEP as-is:** `powdery_mildew`, `leaf_spot` (keep fungal+bacterial **merged** —
eye-separation unreliable, advice overlaps), `healthy`, `not_a_plant`,
`viral_disease`.

**FIX before expanding (precision, not new classes):** `leaf_scorch_underwatering`
and `nutrient_deficiency` — the plan's negatives-not-positives approach is correct.
**Do not add root rot or sunburn until these are stable**, since both new classes
would dump look-alike foliar imagery into the two weakest boundaries.
`gray_mold_botrytis`: close the *supply* gap, not a structural change.

**DEFER (correctly):**
- **Root rot/overwatering** — highest prevalence and clearest competitor gap, but
  no public houseplant data + near-inseparable foliar symptoms. Defer until
  (i) the scorch/nutrient boundary is hardened and (ii) the production loop yields
  verified root-rot positives. **Interim:** add a **symptom-triage question**
  ("wet soil but wilting?") to the existing no-photo triage — it routes root rot
  far more reliably than a leaf classifier ever will.
- **Sunburn / edema activation** — cards exist; data is mismatched/sparse. Defer.

**SPLIT (only one candidate, and it's risky):** **spider-mite split from `pests`** —
prevalence justifies it and Blossom labels it, but `pests` precision is already
0.94 (low urgency), available data is wrong-domain, and early stippling overlaps
nutrient/scorch. If done, scope tightly to the **webbing-positive** presentation and
source verified houseplant images via the production loop — not PlantVillage tomato.
Lower priority than the scorch/nutrient/gray_mold fixes.

**REMOVE:** nothing — all 9 earn their place by prevalence and have non-trivial
support.

**If you add only ONE thing:** address root rot/overwatering as a **symptom-triage
path, not a vision class** — capture the most-searched houseplant problem and close
the headline competitor gap **without** feeding ambiguous foliar imagery into the
two weakest classifiers. Promote it to a real class only after the scorch↔nutrient
boundary is fixed and the data loop has verified houseplant root-rot positives.

### Confidence
- **High:** prevalence ranking, separability, data-availability findings, repo
  metrics, the OOD/calibration differentiator.
- **Medium:** competitor matrix cells marked `~` (marketing rarely enumerates pest
  lists); PictureThis spider-mite labeling specifically.
- **Lower:** precise dataset image counts and some Roboflow license states (verify
  per-set before any commercial training use).
