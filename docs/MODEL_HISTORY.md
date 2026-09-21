# PlantVision — model history ledger

**What this is.** Every archived gamma/aqua bundle the maintainer still holds, in
training order, with the numbers read straight out of each archive's own
`model_card.json` / `training_metrics.json` / `classes.json` / `conformal.json` /
`temperature.json` / `run_log.jsonl` (extracted 2026-09-21; nothing here was
re-scored). It exists so a version-over-version question can be answered from
one table instead of fifteen downloads. `docs/ROADMAP.md` stays the home of
*open* work; this file is the record of what each run measured and why the
numbers moved.

**Read the numbers with these three rules or not at all.**

1. **`val_realworld` is re-drawn every run.** It is a seeded ratio split over
   whatever corpus that run had, so its composition drifts with every data
   change (support per class below moves by ±30 % between adjacent runs). At
   n≈2k the sampling noise on disease macro-F1 is ≈±0.04–0.05, and two runs of
   an identical config at `SEED=0` differed by 2 pp on `val_indomain`. A
   cross-version delta smaller than that is eval-set drift plus run noise, not
   a model change. Only per-class moves larger than ~0.1, and moves that repeat
   across precision *and* recall, are read as real below.
2. **Only 2.7.2 carries a never-trained slice.** The permanent holdout
   (`HOLDOUT_PERMILLE = 300`, sha1 bucket over `(source, plant_id)` of the
   supplement's `eval_ok` rows) first exists in 2.7.2. Every earlier bundle
   trained on those rows, so the holdout number is comparable *forward* to
   2.8.x, never backward. See "How to compare two releases".
3. **The out-of-domain sentinel numbers quoted in older docs are not in the
   archives.** None of the fifteen `model_card.json` files carries an
   `ood_benchmark` block; those scores were produced outside the notebook and
   the 2026-08-23 audit found them non-independent (one species, one session,
   pseudo-replicates, mixed served/argmax instruments). They are cited here only
   as the *reason a release was rejected*, never as a measurement.

None of the fifteen archives carries `bundle_manifest.json`, `data_manifest.json`
or `run_manifest.json`, so **none boots on the current strict server** (branch
`model-fix-test`, `MODEL_VERSION = 2.8.0`). They remain valid as
`tools/paired_eval.py --argmax` diagnostic inputs.

## Ledger

| # | trained (UTC) | version | archive | train n | val_rw n | rw disease mF1 | indomain mF1 (best ep) | holdout mF1 | shipped graph | T | RAPS set size | aqua rw mF1 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 2026-06-03 12:12 | 2.3.1 (8-class, no LoRA) | [upgrmodels.tar](https://drive.google.com/file/d/1s10Lo_-xLiHLCIzYZqKAsprC78F-xJgh/view?usp=sharing) | 7304 | 1408 | 0.767 | 0.804 (7) | — | FP32 | 0.71 | 2.63 | — |
| 2 | 2026-06-04 08:46 | 2.3.1 | [modelsvnew.tar](https://drive.google.com/file/d/1LOZxxTMPi0JcTm3aGv8I9TMs7HZ9Lfjq/view?usp=sharing) | 8432 | 1662 | 0.815 | 0.812 (7) | — | FP32 | 0.75 | 2.56 | — |
| 3 | 2026-06-04 15:03 | 2.3.1 | [trmodels.tar](https://drive.google.com/file/d/1txV9lZM6WoLJ-9LfdHM5vItuvPdCBgvS/view?usp=sharing) | 9236 | 1644 | 0.792 | 0.791 (8) | — | INT8 | 0.73 | 1.93 | — |
| 4 | 2026-06-07 19:23 | 2.4.0 | [models-070626.tar](https://drive.google.com/file/d/1CuLXayMf8ROCvMpw0vd-72AmawTkm4Yl/view?usp=sharing) | 11546 | 1880 | 0.819 | 0.826 (14) | — | INT8 | 0.81 | 1.88 | — |
| 5 | 2026-06-08 14:30 | 2.4.0 | [models-080626.tar](https://drive.google.com/file/d/1RmpfLhynWhPkIxsKFlKknLG76xiaYT5S/view?usp=sharing) | 12340 | 1937 | 0.774 | 0.861 (5) | — | INT8 | 0.79 | 1.86 | — |
| 6 | 2026-06-09 19:19 | 2.5.0 | [models090626.zip](https://drive.google.com/file/d/1bDxWgG2c6wzJIONJ-ra1ypL3byF-U2-t/view?usp=sharing) | 12362 | 1938 | 0.835 | 0.862 (9) | — | INT8 | 0.71 | 1.87 | — |
| 7 | 2026-06-15 15:50 | 2.6.0 | [models-150626.zip](https://drive.google.com/file/d/1UKw9Bsop8d0TO9oxBLBivrgUzHHali7L/view?usp=sharing) | 13311 | 2213 | 0.865 | 0.858 (12) | — | INT8 | 0.78 | 1.89 | — |
| 8 | 2026-06-19 12:26 | 2.6.0 | [models-190626.tar](https://drive.google.com/file/d/1PYAgxnpRTkXNTf7YZ6TUIlNKILumkODy/view?usp=sharing) | 13304 | 2334 | 0.865 | 0.876 (15) | — | FP32 | 0.73 | 1.84 | — |
| 9 | 2026-06-21 15:25 | 2.6.5 | [models-210626.tar](https://drive.google.com/file/d/1DTtnoNQZE5u_e-Lg7n97PL4WBc04UorC/view?usp=sharing) | 12811 | 2150 | 0.861 | 0.877 (22) | — | INT8 | 0.84 | 1.89 | — |
| 10 | 2026-06-25 14:44 | 2.6.7 | [models-250626.tar](https://drive.google.com/file/d/1PHjRyB9uUEKZt6sB-A6vxVPzhyhC-9uY/view?usp=sharing) | 12084 | 1854 | 0.862 | 0.848 (11) | — | INT8 | 0.76 | 1.90 | — |
| 11 | 2026-06-29 10:42 | 2.6.8 | [models-290626.tar](https://drive.google.com/file/d/1aA2aIL8juLOFK3xzQQTtO8TjYcyAZxfX/view?usp=sharing) | 13827 | 2183 | 0.858 | 0.890 (19) | — | INT8 | 0.85 | 1.87 | 0.011 (degenerate INT8) |
| 12 | 2026-07-03 20:02 | 2.6.9 | [models-030726.tar](https://drive.google.com/file/d/1BmYiUDzypSQk8ENFon4XIXsuXUfl65Jh/view?usp=sharing) | 14315 | 2151 | 0.834 | 0.865 (5) | — | INT8 | 0.74 | 1.88 | 0.761 |
| 13 | 2026-08-08 17:41 | 2.7.0 | [models-080826.tar](https://drive.google.com/file/d/1N_f4JSsCS68xUHLwA_c6MEFx3Zc5m-s6/view?usp=sharing) | 13948 | 2201 | 0.860 | 0.884 (9) | — | INT8 | 0.81 | 1.54 | 0.788 |
| 14 | 2026-08-14 11:01 | 2.7.1 | [models-140826.tar](https://drive.google.com/file/d/1Z3VkyGSsIgFOPxoKCrRBLO_glz9tqeGu/view?usp=sharing) | 14000 | 2184 | 0.870 | 0.877 (8) | — | INT8 | 0.76 | 1.52 | 0.791 |
| 15 | 2026-08-17 11:14 | 2.7.2 | [models-170826.tar](https://drive.google.com/file/d/14YuB8HdVtAb6z2OgDmMvUscjRoXyURIt/view?usp=sharing) | 13660 | 2116 | 0.841 | 0.870 (19) | 0.863 | INT8 | 0.82 | 1.48 | 0.761 |

## Per-class recall on `val_realworld` (classifier-only argmax, hflip TTA)

| version (date) | gray_mold | healthy | scorch | leaf_spot | nap | nutrient | pests | powdery | viral |
|---|---|---|---|---|---|---|---|---|---|
| 2.3.1 (06-03) | 0.86 | 0.90 | 0.68 | 0.82 | 0.98 | 0.53 | — | 0.94 | 0.90 |
| 2.3.1 (06-04) | 0.80 | 0.93 | 0.66 | 0.89 | 0.98 | 0.52 | 0.98 | 0.97 | 0.87 |
| 2.3.1 (06-04) | 0.80 | 0.89 | 0.60 | 0.93 | 0.97 | 0.70 | 0.96 | 1.00 | 0.86 |
| 2.4.0 (06-07) | 0.79 | 0.88 | 0.68 | 0.87 | 0.98 | 0.60 | 0.96 | 0.93 | 0.93 |
| 2.4.0 (06-08) | 0.85 | 0.87 | 0.64 | 0.85 | 0.97 | 0.76 | 0.95 | 0.96 | 0.87 |
| 2.5.0 (06-09) | 0.81 | 0.92 | 0.76 | 0.89 | 0.96 | 0.80 | 0.96 | 0.91 | 0.90 |
| 2.6.0 (06-15) | 0.86 | 0.87 | 0.86 | 0.84 | 0.96 | 0.92 | 0.97 | 0.93 | 0.92 |
| 2.6.0 (06-19) | 0.87 | 0.92 | 0.84 | 0.88 | 0.97 | 0.90 | 0.95 | 0.89 | 0.85 |
| 2.6.5 (06-21) | 0.84 | 0.90 | 0.74 | 0.86 | 0.97 | 0.89 | 0.95 | 0.94 | 0.93 |
| 2.6.7 (06-25) | 0.83 | 0.90 | 0.81 | 0.85 | 0.97 | 0.88 | 0.97 | 0.93 | 0.90 |
| 2.6.8 (06-29) | 0.89 | 0.91 | 0.79 | 0.83 | 0.98 | 0.78 | 0.96 | 0.95 | 0.90 |
| 2.6.9 (07-03) | 0.87 | 0.86 | 0.61 | 0.88 | 0.97 | 0.87 | 0.93 | 0.94 | 0.90 |
| 2.7.0 (08-08) | 0.90 | 0.91 | 0.72 | 0.87 | 0.96 | 0.91 | 0.94 | 0.94 | 0.85 |
| 2.7.1 (08-14) | 0.84 | 0.93 | 0.74 | 0.86 | 0.97 | 0.92 | 0.96 | 0.96 | 0.86 |
| 2.7.2 (08-17) | 0.86 | 0.93 | 0.71 | 0.90 | 0.95 | 0.83 | 0.92 | 0.96 | 0.88 |

## Per-class precision on `val_realworld`

| version (date) | gray_mold | healthy | scorch | leaf_spot | nap | nutrient | pests | powdery | viral |
|---|---|---|---|---|---|---|---|---|---|
| 2.3.1 (06-03) | — | — | — | — | — | — | — | — | — |
| 2.3.1 (06-04) | — | — | — | — | — | — | — | — | — |
| 2.3.1 (06-04) | — | — | — | — | — | — | — | — | — |
| 2.4.0 (06-07) | — | — | — | — | — | — | — | — | — |
| 2.4.0 (06-08) | — | — | — | — | — | — | — | — | — |
| 2.5.0 (06-09) | 0.75 | 0.94 | 0.52 | 0.90 | 0.99 | 0.74 | 0.94 | 0.97 | 0.89 |
| 2.6.0 (06-15) | 0.84 | 0.97 | 0.71 | 0.91 | 0.99 | 0.72 | 0.90 | 0.95 | 0.85 |
| 2.6.0 (06-19) | 0.84 | 0.94 | 0.77 | 0.85 | 0.99 | 0.74 | 0.92 | 0.88 | 0.95 |
| 2.6.5 (06-21) | 0.89 | 0.95 | 0.70 | 0.89 | 0.98 | 0.75 | 0.95 | 0.94 | 0.81 |
| 2.6.7 (06-25) | 0.83 | 0.95 | 0.75 | 0.88 | 0.97 | 0.68 | 0.97 | 0.93 | 0.90 |
| 2.6.8 (06-29) | 0.82 | 0.94 | 0.75 | 0.91 | 0.98 | 0.75 | 0.91 | 0.91 | 0.87 |
| 2.6.9 (07-03) | 0.88 | 0.92 | 0.66 | 0.88 | 0.97 | 0.57 | 0.94 | 0.93 | 0.90 |
| 2.7.0 (08-08) | 0.86 | 0.93 | 0.76 | 0.85 | 0.98 | 0.72 | 0.94 | 0.90 | 0.90 |
| 2.7.1 (08-14) | 0.89 | 0.92 | 0.74 | 0.85 | 0.99 | 0.75 | 0.96 | 0.95 | 0.92 |
| 2.7.2 (08-17) | 0.84 | 0.93 | 0.70 | 0.88 | 0.99 | 0.62 | 0.98 | 0.87 | 0.88 |

## Eval-slice support on `val_realworld` (rows per class)

| version (date) | gray_mold | healthy | scorch | leaf_spot | nap | nutrient | pests | powdery | viral |
|---|---|---|---|---|---|---|---|---|---|
| 2.3.1 (06-03) | 22 | 366 | 28 | 194 | 560 | 19 | 0 | 48 | 171 |
| 2.3.1 (06-04) | 20 | 338 | 62 | 209 | 565 | 33 | 239 | 29 | 167 |
| 2.3.1 (06-04) | 20 | 338 | 62 | 209 | 569 | 33 | 239 | 37 | 137 |
| 2.4.0 (06-07) | 53 | 386 | 117 | 231 | 548 | 62 | 265 | 67 | 151 |
| 2.4.0 (06-08) | 53 | 576 | 45 | 216 | 552 | 25 | 265 | 67 | 138 |
| 2.5.0 (06-09) | 53 | 535 | 45 | 234 | 556 | 25 | 265 | 77 | 148 |
| 2.6.0 (06-15) | 71 | 554 | 121 | 233 | 599 | 122 | 265 | 80 | 168 |
| 2.6.0 (06-19) | 91 | 631 | 106 | 236 | 625 | 90 | 265 | 71 | 219 |
| 2.6.5 (06-21) | 94 | 626 | 106 | 251 | 501 | 91 | 265 | 66 | 150 |
| 2.6.7 (06-25) | 94 | 505 | 97 | 199 | 409 | 82 | 240 | 69 | 159 |
| 2.6.8 (06-29) | 88 | 634 | 104 | 218 | 508 | 87 | 264 | 87 | 193 |
| 2.6.9 (07-03) | 91 | 600 | 103 | 209 | 507 | 87 | 265 | 105 | 184 |
| 2.7.0 (08-08) | 82 | 621 | 107 | 236 | 516 | 77 | 266 | 112 | 184 |
| 2.7.1 (08-14) | 89 | 620 | 107 | 229 | 519 | 77 | 266 | 102 | 175 |
| 2.7.2 (08-17) | 69 | 615 | 90 | 232 | 495 | 65 | 265 | 93 | 192 |

## Training rows per class

| version (date) | gray_mold | healthy | scorch | leaf_spot | nap | nutrient | pests | powdery | viral |
|---|---|---|---|---|---|---|---|---|---|
| 2.3.1 (06-03) | 86 | 1185 | 113 | 1169 | 2149 | 407 | 0 | 1022 | 1173 |
| 2.3.1 (06-04) | 78 | 1185 | 242 | 1179 | 2141 | 460 | 915 | 1046 | 1186 |
| 2.3.1 (06-04) | 78 | 1435 | 490 | 1429 | 2136 | 279 | 1163 | 1057 | 1169 |
| 2.4.0 (06-07) | 455 | 1818 | 691 | 1819 | 2139 | 664 | 1012 | 1415 | 1533 |
| 2.4.0 (06-08) | 455 | 2431 | 913 | 1836 | 2122 | 645 | 1012 | 1408 | 1518 |
| 2.5.0 (06-09) | 454 | 2456 | 914 | 1810 | 2141 | 646 | 1012 | 1412 | 1517 |
| 2.6.0 (06-15) | 530 | 2554 | 1075 | 1861 | 2261 | 1020 | 1012 | 1419 | 1579 |
| 2.6.0 (06-19) | 600 | 2685 | 1021 | 1860 | 2360 | 896 | 1012 | 1409 | 1461 |
| 2.6.5 (06-21) | 593 | 2733 | 1023 | 1853 | 1930 | 700 | 1013 | 1414 | 1552 |
| 2.6.7 (06-25) | 339 | 2585 | 921 | 1955 | 1568 | 636 | 918 | 1573 | 1589 |
| 2.6.8 (06-29) | 356 | 3237 | 1013 | 2136 | 1927 | 682 | 1014 | 1695 | 1767 |
| 2.6.9 (07-03) | 366 | 3621 | 1013 | 2151 | 1938 | 682 | 1014 | 1716 | 1814 |
| 2.7.0 (08-08) | 403 | 3737 | 1253 | 2155 | 1951 | 768 | 1032 | 1391 | 1258 |
| 2.7.1 (08-14) | 399 | 3732 | 1254 | 2177 | 1945 | 768 | 1032 | 1420 | 1273 |
| 2.7.2 (08-17) | 371 | 3663 | 1187 | 2133 | 1888 | 723 | 1030 | 1378 | 1287 |

## Calibration / quantisation / OOD sidecars

| version (date) | INT8 drift (int8−fp32 mF1) | paired-bootstrap significant | T | RAPS α chosen | audit coverage | mean set size | k-NN threshold | MaxLogit floor | NAP head veto |
|---|---|---|---|---|---|---|---|---|---|
| 2.3.1 (06-03) | -0.024 | False | 0.71 | 0.1 | 0.993 | 2.63 | — | 1.800 | — |
| 2.3.1 (06-04) | 0.000 | False | 0.75 | 0.1 | 0.994 | 2.56 | — | — | — |
| 2.3.1 (06-04) | -0.011 | False | 0.73 | 0.15 | 0.973 | 1.93 | 0.650 | — | — |
| 2.4.0 (06-07) | -0.002 | False | 0.81 | 0.2 | 0.976 | 1.88 | 0.655 | — | — |
| 2.4.0 (06-08) | 0.002 | False | 0.79 | 0.2 | 0.966 | 1.86 | 0.610 | — | — |
| 2.5.0 (06-09) | 0.003 | False | 0.71 | 0.2 | 0.959 | 1.87 | 0.595 | — | — |
| 2.6.0 (06-15) | 0.001 | False | 0.78 | 0.2 | 0.969 | 1.89 | 0.588 | — | — |
| 2.6.0 (06-19) | -0.014 | True | 0.73 | 0.2 | 0.981 | 1.84 | 0.623 | — | — |
| 2.6.5 (06-21) | 0.006 | False | 0.84 | 0.2 | 0.984 | 1.89 | 0.580 | — | — |
| 2.6.7 (06-25) | -0.006 | False | 0.76 | 0.2 | 0.991 | 1.90 | 0.606 | — | — |
| 2.6.8 (06-29) | 0.004 | False | 0.85 | 0.2 | 0.988 | 1.87 | 0.615 | — | — |
| 2.6.9 (07-03) | 0.010 | True | 0.74 | 0.2 | 0.954 | 1.88 | 0.594 | — | — |
| 2.7.0 (08-08) | 0.001 | False | 0.81 | 0.6 | 0.980 | 1.54 | 0.594 | 2.013 | — |
| 2.7.1 (08-14) | -0.004 | False | 0.76 | 0.6 | 0.976 | 1.52 | 0.607 | 1.894 | — |
| 2.7.2 (08-17) | 0.001 | False | 0.82 | 0.6 | 0.987 | 1.48 | 0.630 | 1.750 | 0.05 |

## Release notes — what changed, and why the numbers moved

Dates are `trained_at` from the card. "Knob" lines are the diff of
`model_card.json → hyperparams` against the previous archive; "data" lines are
the diff of `train_counts` / `source_inventory`.

### 2.3.1 ×3 (06-03, 06-04 ×2) — bootstrap era
- 06-03: 8-class head (no `pests`), frozen DINOv2 + MLP only, FP32 shipped
  (INT8 drift 0.024 > warn 0.02).
- 06-04 a: LoRA added (rank 8, α 16, last 6 blocks), `pests` class added from
  iNat (239 eval rows). Disease mF1 0.767 → 0.815 — a real gain, but also a
  different eval set (viral 171 → 167, `pests` appears).
- 06-04 b: `kaggle_houseplant` supplement added (weight 0.7). First INT8 ship.
  Nutrient recall 0.52 → 0.70 on a 33-row slice, i.e. inside noise.

### 2.4.0 ×2 (06-07, 06-08) — DoctorP + LoRA r16
- Knobs: LoRA rank 8 → 16, α 16 → 32, last 6 → 8 blocks; `doctorp` (real app
  photos, weight 1.0), `kaggle_ornamental` 0.8, `kaggle_nutrient` 0.5,
  `kaggle_strawberry` 0.5, `kaggle_species` 0.7 added; `kaggle_houseplant`
  0.7 → 0.35.
- Data: train 9.2k → 11.5k; gray_mold 78 → 455, scorch 490 → 691, nutrient
  279 → 664. Eval slices for the weak classes finally reach n>50.
- The 06-08 re-run (identical knobs, healthy train +613) stopped at epoch 5 with
  rw mF1 0.774 vs 0.819 the day before: the first recorded instance of the
  2 pp-plus run-to-run band, on the *same* config.

### 2.5.0 (06-09) — head selection
- Knob: `select_weakclass_precision_weight` introduced (0.0 — the head-select
  phase is logged from here on). Per-class precision starts being recorded.
- rw mF1 0.835; nutrient recall 0.80 / precision 0.74; scorch precision 0.52 is
  the worst physiological-class precision on record.

### 2.6.0 ×2 (06-15, 06-19) — the eval supplement enters
- Knobs: `supplement` (weight 0.9) and `doctorp_pepper` (0.8) added.
- Data: nutrient train 646 → 1020, scorch 914 → 1075; val nutrient 25 → 122.
- rw mF1 0.865 on both runs; scorch recall 0.76 → 0.86, nutrient 0.80 → 0.92 —
  the largest single-release weak-class gain in the ledger and the one most
  plausibly real (both classes, both P and R, on 5× larger slices).
- 06-19 differs from 06-15 by seven training images and shipped **FP32**
  because INT8 was significantly worse (paired bootstrap, drift 0.014). The two
  runs are the "replicate band" pair cited in §6 of the notebook
  (`REPLICATE_BAND`).

### 2.6.5 (06-21) — `coleaf` removed
- Data: `coleaf` source dropped; NAP train 2360 → 1930. rw 0.861 (flat).
  Scorch recall 0.84 → 0.74 on 106 rows and viral precision 0.95 → 0.81 are
  inside noise for those slice sizes.

### 2.6.7 (06-25) — agricultural proxy purge
- Data: `kaggle_strawberry` (the strawberry-Botrytis proxy) removed; gray_mold
  train 593 → 339, NAP 1930 → 1568. gray_mold R/P 0.84/0.89 → 0.83/0.83 on
  clean foliage. Last run to carry the old stable `test` split (0.866).

### 2.6.8 (06-29) — **deployed, the incumbent**
- No knob change. Data: healthy train +652, powdery +122, viral +178, gray_mold
  +17; boundary hard-negatives sit in the eval-eligible `supplement`.
- rw mF1 0.858, best in-domain epoch 19 (0.890); healthy R/P 0.91/0.94;
  nutrient P 0.75, scorch P 0.75, R 0.79.
- **The 2.6.8 archive's aqua TFLite is degenerate** (`aqua_int8_tflite`,
  disease mF1 0.011 — predicts gray_mold for everything). The server bundle is
  fine; the on-device model in this archive must not be served. Fixed in 2.6.9
  by shipping float32.

### 2.6.9 (07-03) — the boundary-negative regression (not shipped)
- Knob: `supplement_boundary` created as a train-only source at weight **0.9**.
- Effect: scorch R 0.79 → 0.61 (103 rows), nutrient P 0.75 → 0.57, healthy R
  0.91 → 0.86; head best epoch 19 → 5; rw mF1 0.834. A paired bootstrap vs 2.6.8
  on common rows excluded 0 (2.6.8 +0.021…+0.046), so this one is real. Cause:
  697 boundary negatives at 5.8 % of every batch taught "browning = healthy".
- Aqua fixed: float32 TFLite, rw mF1 0.761.

### (no archive) 2.6.10 (07-20) and 2.6.11–2.6.13 — see `DATASET_LABELING_PLAN.md`
- 2.6.10 re-ran v7 with `supplement_boundary` 0.9 → 0.35 and recovered scorch
  recall (0.61 → 0.75); it lost a 2.26k-image source mid-run and anchors no A/B.
  2.6.11–2.6.13 never trained; their code (`split_hint` routing,
  `supplement_train_only`) became 2.7.0.

### 2.7.0 (08-08) — dataset v8, first run
- Knobs: `supplement_boundary` 0.35 (the 2.6.10 fix), `supplement_train_only`
  0.9 (new: sub-0.80-confidence positives train but never grade or calibrate),
  `kaggle_ornamental` 0.8 → 1.0, `kaggle_nutrient` removed.
- Data (v8 routing 1,900 eval-eligible / 695 train-only / 787 boundary):
  scorch train 1013 → 1253, nutrient 682 → 768, gray_mold 366 → 403; viral
  1814 → 1258 and powdery 1716 → 1391 (the ornamental source swap and the
  field-crop purge). `source_inventory` is persisted from here on.
- rw mF1 0.860, nutrient R 0.91 / P 0.72, scorch R 0.72 / P 0.76, gray_mold R
  0.90: in-domain fully recovered from 2.6.9 and level with 2.6.8.
- Calibration: the RAPS grid was widened (α up to 0.6, λ up to 1.0) and now
  picks **α = 0.6** with audit coverage 0.980 and mean set size 1.54 (was 1.87).
  See "Sidecar findings" — the server reports `1 − α` as `coverage`.
- Rejected on the out-of-domain sentinel (the "2.6.9 → 2.7.0 cliff"); that
  measurement is not in the archive and is exploratory after the audit.

### 2.7.1 (08-14) — single-knob ablation, reverted
- Knob: `kaggle_ornamental` 1.0 → 0.35, nothing else. rw mF1 0.870 (+0.010,
  noise); no per-class move outside its slice's CI. Sentinel: no change on the
  scorch arm, healthy false-fire up — the reason it was reverted.

### 2.7.2 (08-17) — holdout carve + NAP head veto (not shipped)
- Knobs: `kaggle_ornamental` back to 1.0. Serving: `nap_head_mass_floor = 0.05`
  (three-of-three OOD gate), `max_logit_scope = plant_only` (MaxLogit floor
  re-enabled at 1.75 after auto-disabling since 2.3.1), `z1_bias_sweep` and
  `stage1_audit` recorded.
- Data: the permanent holdout takes 535 supplement `eval_ok` rows out of
  *both* train and the ratio splits (scorch 116, NAP 112, healthy 99, nutrient
  80, powdery 55, gray_mold 54, leaf_spot 16, pests 3, viral 0). Train drops
  ~340 rows; val_realworld thins (nutrient 77 → 65, scorch 107 → 90, gray_mold
  89 → 69).
- rw mF1 0.841 (−0.029 vs 2.7.1: within the band, and consistent with losing
  ~550 in-domain rows from training); nutrient P 0.62 is the lowest since
  2.6.9; healthy R/P 0.93/0.94 unchanged.
- **Holdout (never trained): supported disease mF1 0.863 over 6 classes, acc
  0.918**; scorch R 0.84 / P 0.95, nutrient R 0.86 / P 0.99, gray_mold 0.93 /
  0.94, powdery 0.95 / 0.95, healthy R 0.98 / P 0.85. leaf_spot (16), pests (3)
  and viral (0) are unreadable there.
- Stage 1 audit on val_realworld: no-box rate 18.9 % damaged vs 13.3 % healthy
  (ratio 1.42), 84 % on not_a_plant (correct behaviour).
- The private-sentinel scorch number (0.097) that rejected it is not in the
  archive.

### (no archive) 2.7.3 (08-22) — regime-aware sampling + regime conformal
- Stopped by the then-blocking OOD gate before writing a bundle; both levers
  falsified and removed. Its in-domain numbers exist only in the Colab log.

## Where the dataset rounds show up

| round | first run | what it changed in the mix | what moved |
|---|---|---|---|
| v2–v3 supplement (06-15) | 2.6.0 | `supplement` + `doctorp_pepper`; nutrient/scorch supply ×1.5 | scorch R 0.76 → 0.86, nutrient R 0.80 → 0.92 — the biggest defensible gain |
| ag-proxy purge (06-21/25) | 2.6.5, 2.6.7 | `coleaf`, `kaggle_strawberry` out; gray_mold train −43 % | gray_mold precision held on clean foliage; NAP supply −800 |
| v6 boundary negatives (06-29) | 2.6.8 | boundary rows in the eval-eligible pool | healthy precision 0.94; nutrient P 0.68 → 0.75 |
| v7 boundary → train-only @0.9 (07-03) | 2.6.9 | 697 boundary rows pinned to train at 5.8 % of draws | scorch R −0.18, nutrient P −0.18, healthy R −0.05 (real) |
| **v8** (08-08) | 2.7.0 | `split_hint` routing (1,900 / 695 / 787), boundary weight 0.35, field-crop purge, ornamental source swap | in-domain back to the 2.6.8 level; viral/powdery train −556/−325; sentinel scorch arm collapsed (exploratory) |
| holdout carve (08-17) | 2.7.2 | 535 `eval_ok` rows never trained, drawn before every ratio split | first forward-comparable number: holdout mF1 0.863 |
| user-scan fold-in (08-06 export) | none yet | 2,727 exported files, 80 feedback-positive head-class rows | see `DATASET_LABELING_PLAN.md` — the locked test set T must be carved from these *before* any of them enter a supplement |

The honest summary of v8: it fixed the integrity of the in-domain gate (sub-floor
positives no longer grade or calibrate the model; boundary negatives no longer
swamp the physiological classes) and it did not move in-domain capability —
2.6.8 / 2.7.0 / 2.7.1 / 2.7.2 are 0.858 / 0.860 / 0.870 / 0.841, one noise band.
What it could not do is supply the missing photographic regime (early, dull,
edge-confined scorch on a green blade; picked leaf on plain ground), which is
why the collection target moved to first-party v9.

## Sidecar findings (things the archives say that the docs do not)

- **Conformal `alpha` has been 0.6 since 2.7.0.** The §6 grid search minimises
  mean set size subject to audit coverage ≥ 0.9 + 2 SE, and the 2.7.0 grid
  admits α up to 0.6. Because the model is right ~92 % of the time and the set
  always holds top-1, α = 0.6 still audits at 0.98 coverage with sets of 1.5 —
  but `server/conformal.py` reports `coverage = 1 − alpha`, so any 2.7.x bundle
  makes `/api/analyze` advertise **`coverage: 0.4`** while `conformal.json`
  carries `target_coverage: 0.9`. Either clamp the grid to α ≤ 1 − target or
  export and serve `target_coverage`. Not yet fixed on `model-fix-test`.
- **INT8 has never been significantly worse except once** (2.6.0 on 06-19,
  drift 0.014, CI excluded 0 → FP32 shipped) and was significantly *better*
  once (2.6.9). Every other run: |drift| ≤ 0.011, CI straddles 0. The drift
  gate is doing its job and is not a bottleneck.
- **Temperature sits at 0.71–0.85 on every run** (the head is under-confident
  after label smoothing 0.05 + logit adjustment τ 0.5); it never approaches the
  [0.05, 10] validity clamp.
- **The k-NN OOD threshold is stable (0.58–0.66)**; the MaxLogit floor
  auto-disabled on every export from 06-04 to 2.6.9 and only returned with the
  `plant_only` scope in 2.7.0 (2.01) → 2.7.2 (1.75).
- **The DINOv2 linear-probe diagnostic reads 0.84 top-1 on val_realworld** in
  both 2.6.8 (0.839) and 2.7.2 (0.844) after 8 epochs, which is why "features
  are fine, the head and the data are the levers" has held.
- **Classifier latency**: cpu single-thread with hflip TTA ≈ 183 ms p50 since
  2.6.5 (299 ms on the 06-19 FP32 build).

## How to compare two releases (the evaluation-dataset contract)

Five slices are drawn per run in §2, in this order, and the order is the
contract:

1. **`holdout`** (2.7.2+): every `(source, plant_id)` group of the supplement's
   `eval_ok` rows whose `sha1(source|plant_id)` bucket is < 300/1000. Drawn
   *first*, so no ratio split can drain it; leakage against it is resolved on the
   *train* side in §3 (pHash ≤ 6, same-class and cross-class), so the instrument
   is never mutated. Membership is a property of the row, so it stays fixed as
   the corpus grows and a row that was ever held out is never re-admitted.
2. **`val_realworld`** (ratio 0.15, seed 2), **`val_indomain`** (0.10, seed 0),
   **`temperature_cal`** (0.12, seed 3), **`conformal_cal`** (0.15, seed 1):
   group-aware ratio splits over the allowed sources (PlantDoc, iNat, COCO,
   Open Images, DoctorP, `supplement`; never `supplement_train_only`,
   `supplement_boundary`, Kaggle third-party or PlantVillage). Same-plant leakage
   is impossible by construction; same-*regime* overfitting is not, which is the
   whole lesson of 2.7.0–2.7.2.
3. **`val_studio`** = PlantVillage, transfer monitor only.

So: **`val_indomain` stops training, `val_realworld` gates shipping, `holdout`
measures improvement** — and the first two are different rows in every run.
To compare release A with release B on data *both* never saw:

- **Holdout** — valid for any pair where both trained with the carve (2.7.2 and
  later). For 2.6.8 or 2.7.0/2.7.1 vs a 2.8.x candidate it is one-sided: the
  old model trained on ~70 % of it.
- **Locked test set T** (`DATASET_LABELING_PLAN.md`, "Locked
  deployment-representative test set") — production scans adjudicated by two
  humans, `first_seen_model_version = never`, private, never in any supplement.
  This is the only slice that can be fair to 2.6.8 *and* to a new candidate,
  because neither trained on it. It is specified and not yet carved.
- **Supplement `eval_ok` rows with `first_seen_model_version` later than both
  candidates** — §2 on this branch routes any row first seen at or before the
  candidate's version to `supplement_train_only`, so v9 rows stamped `never`
  or `> both` are eval-fair to both.
- **OOD sentinel v2** — never trained by any model, but one species, one
  session, ≥ 11 pseudo-replicates and no non-plant arm; a regression alarm,
  not a comparison instrument, until v3 exists.

The mechanics: score both bundles served (`tools/paired_eval.py --served
--manifest`), then `tools/eval_bootstrap.py` with the capture family as the
resampling unit; read the *difference* CI, not the two point estimates. A CI
that straddles 0 is not evidence, whatever the headline gap.
