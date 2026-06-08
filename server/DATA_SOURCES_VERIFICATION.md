# Data-source verification (live, 2026-06)

Each `§2` data source was hit **live** and checked for (a) real availability vs the
notebook's target and (b) quality. Method per source noted. This is ground truth at
verification time; iNaturalist counts drift over time.

> Counts are the **research-grade + has-photo** pool the notebook draws from (the
> upper bound before the green-content/leaf filters and SHA/pHash dedup, which only
> *reduce* yield). "Target" is the notebook's requested cap.

## iNaturalist API

| Source (taxa/queries) | Target | Live availability | Verdict |
|---|---|---|---|
| healthy — 8 genera (Ficus, Monstera, Epipremnum, Calathea, Spathiphyllum, Dracaena, Sansevieria, Philodendron) | 180/taxon (~1440) | **~195k pool** (smallest Spathiphyllum 680) | ✅ abundant |
| pests — 4 families (Pseudococcidae 1.5k, Diaspididae 5.7k, Coccidae 8.3k, Eriococcidae 4.7k) | 900 | **~20k pool** | ✅ abundant |
| not_a_plant hard-neg — Poaceae/Bryophyta/Marchantiophyta/Lecanoromycetes/Chlorophyta | 900 | **0.05–1.9M each** | ✅ abundant |
| gray_mold_botrytis — genus *Botrytis* | 2400 | **~295 obs total** (notebook-measured ~138 foliage post-filter) | ⚠️ **supply-limited at the source** |
| leaf_scorch_underwatering — 22 free-text | 1500 | **~925 pool; 6/22 queries return 0**; concentrated in broad queries (`leaf edge browning`=476) | ⚠️ supply-limited + noise-prone |
| nutrient_deficiency — 21 free-text | 1500 | **1453 pool but 92% from ONE generic query** (`marginal leaf yellowing`=1331); the specific-deficiency queries (potassium/phosphorus/zinc/manganese/calcium/micronutrient…) return **~0** | ⚠️ **noisy**; real signal must come from MPLD/Kaggle |
| gray_mold_botrytis — 12 free-text | — | **~4 total, 9/12 return 0** | ❌ **effectively dead weight** |
| not_a_plant — Animalia kingdom | 500 | abundant (kingdom-level) | ✅ |

**Quality notes (iNat):** all queried taxa resolve to valid IDs; pool is research-grade
with photos. The free-text supplements are the weak spot — several queries are dead, and
the two physiological classes lean on broad, label-noise-prone phrases. This is consistent
with (and explains) the notebook's reliance on curated Kaggle/MPLD supplements for the
starved classes, and its earlier removal of `yellowing leaves plant`.

## HuggingFace datasets (row counts via datasets-server)

| Dataset | Target | Live rows | Verdict |
|---|---|---|---|
| `agyaatcoder/PlantDoc` | 500/cls train, 120/cls val | **train 2342 / test 236** | ✅ |
| `BrandonFors/Plant-Diseases-PlantVillage-Dataset` (primary) | 900/cls train, 300 studio | **train 43,456 / test 10,849** (38 classes) | ✅ |
| `mohanty/PlantVillage` (color, fallback) | — | **train 129,783 / test 33,133** | ✅ |

PlantVillage is studio/crop domain — correctly capped, down-weighted, and excluded from
every eval/calibration split (`allowed_sources`).

## Kaggle datasets (authenticated; metadata for all, full verify for the small/critical)

| Dataset | Size | Target | Live finding | Verdict |
|---|---|---|---|---|
| `alexanderuzhinskiy/the-doctorp-project-dataset` (DoctorP, **eval-eligible**) | 32 MB | 400/cls | **4000 imgs → 8 classes** (leaf_scorch 351, powdery 202, healthy 191, nutrient 174, pests 173, leaf_spot 165, **gray_mold 96**, viral 76); 53 out-of-taxonomy folders correctly dropped; 0 undecodable; **uniformly 128×128 (low-res, as documented)** | ✅ verified |
| `usmanafzaal/strawberry-disease-detection-dataset` (gray_mold) | 618 MB | 250 | **621 "Gray Mold" LabelMe instances** (cap 250); 0 undecodable; **419×419** | ✅ key gray_mold source |
| `abdulahad0296/indoor-plant-disease-detection-dataset` | 3.7 GB | 250/cls | exists, substantial | ✅ exists |
| `kacpergregorowicz/house-plant-species` | 5.3 GB | 400 | exists, substantial | ✅ exists |
| `pappu54/indoor-plant-disease-dataset-23-classes` | 386 MB | 300/cls | exists | ✅ exists |
| `guy007/nutrientdeficiencysymptomsinrice` | 1.0 GB | 200 | exists | ✅ exists |

**gray_mold_botrytis — full real supply** (the most-starved class): strawberry 250 +
DoctorP 96 + iNat-taxon ~138 + free-text ~4 ≈ **~488 pre-dedup**. The free-text path
contributes almost nothing; the Kaggle supplements carry the class.

## HTTP sources (reachability)

| Source | URL | Live |
|---|---|---|
| COCO val2017 + annotations | HF mirror `pcuenq/coco-2017-mirror` | ✅ has `val2017.zip` + `annotations_trainval2017.zip` |
| COCO direct | `images.cocodataset.org/zips/val2017.zip` | ⚠️ **503** — validates the HF-mirror-first design |
| Open Images v7 (bbox CSV, class CSV, S3 imgs) | `storage.googleapis.com` / `s3.amazonaws.com` | ✅ reachable |
| MPLD (maize P-deficiency) | `zenodo.org/.../MPLD_databse.zip` | ✅ reachable |

COCO val2017 = 5000 imgs → not_a_plant targets (1500 + 600) easily met.

## Actions taken

1. **APPLIED — pruned the dead iNat free-text queries** (re-confirmed 0-yield on two
   live checks): **6** removed from `leaf_scorch_underwatering` (22→16), **10** from
   `nutrient_deficiency` (21→11), **9** from `GRAY_MOLD_FREETEXT_QUERIES` (12→3). These
   returned zero research-grade+photo observations, so removal loses **no images** — it
   only drops wasted API calls. The `test_stage0_weakclass_supply_productive` invariant was
   updated to count productive (non-comment) terms with a >=10 floor.
2. **NOT applied (recall-sensitive) — `marginal leaf yellowing`** remains: it's ~92% of
   `nutrient_deficiency`'s free-text yield and is generic-yellowing-noisy, but removing it
   would gut the class's iNat supply. It's documented inline as a "reassess against recall
   on a real run" item; the deficiency *signal* still comes from MPLD + the Kaggle nutrient
   sets, and the green/leaf filter + LA-prior decoupling suppress the noise.

## Standing note

**gray_mold + the physiological classes are structurally supply-limited at iNat** — the
Kaggle (strawberry, DoctorP) + MPLD supplements are load-bearing, not optional. The leakage
guard + per-class count floors protect against silent starvation, and DoctorP (eval-eligible)
supplies the real-photo eval slices for these classes.
