# PlantVision — roadmap

Open work only — a checkbox to-do list (the code is the record). Companions:
`DATASET_LABELING_PLAN.md` (data loop), `../DEPLOYMENT.md` (deploy + Android
signing/FCM), `server/models/MODEL_CARD.md` (per-run metrics).

## 2.6.9 — next train (dataset v7, not yet trained)

`MODEL_VERSION` is bumped to 2.6.9 in the notebook (§1); **2.6.8 stays the shipped bundle
until 2.6.9 trains and clears the gate.** The aqua-collapse fix already landed in the notebook
(§5 ships the near-lossless **float16** TFLite, ~2.9 MB, not the dynamic-range INT8 that
collapsed MobileNetV3 to ≈0.01 macro-F1; §6 degeneracy guard) — it deploys on-device once
2.6.9 trains and the guard is confirmed silent.

- [ ] **Train 2.6.9 on dataset v7** — keep collecting confusion-pair negatives **and**
      weak-class positives (recall floor); re-test the LA-prior trim for nutrient/scorch
      (paired vs 2.6.8). Plan + per-class targets in `DATASET_LABELING_PLAN.md` (scorch/nutrient
      boundary still binding — data/precision-bound, not backbone-bound; §3 frozen probe ~0.91).

## Unveiling 2.7 — the leap

> From "a good leaf classifier" to a **holistic, trustworthy plant doctor**. The revolution
> is in **scope + real-world reliability + data scale**, not a bigger backbone alone (the
> probe says features aren't the ceiling). 2.6.8's per-class results are now in hand —
> pick the exact levers from them; scaffold in parallel with continued v7 collection.

**Monster ingredients (priority order):**

1. **Real-distribution data flywheel at scale** — fold *thousands* of offline-judged
   production scans (`tools/export_data.py` → vision-agent judge) into the next dataset.
   Real phone photos are the single biggest reliability lever — bigger than any backbone.
2. **Scope expansion (the revolution in *scope*)** — activate `edema` + `sunburn` (content
   cards exist) and add **root rot via image + symptom-triage fusion** (the #1 houseplant
   problem, not separable on a leaf photo alone). Diagnose *conditions*, not leaf classes.
3. **Multimodal holistic diagnosis** — fuse the vision verdict with species ID
   (PlantNet / BioCLIP — BioCLIP fits *here*, for species, not disease) and the symptom Q&A,
   so species + context condition the disease priors.
4. **Capacity jump — `frontier` (DINOv2 ViT-B/14, GPU)** — already scaffolded; A/B vs gamma
   **only if** the §3 probe shows real headroom on the new data, then distill back to
   gamma/aqua so the win ships cheaply on CPU + device.
5. **Better localization** — evolve Stage 1 toward lesion **segmentation** (per-leaf,
   multi-disease, severity from lesion area) instead of a single box.
6. **Safety at scale** — per-class conformal, an explicit **abstain → expert/community
   escalation** path, calibrated severity that never over-treats.

**Prep now (parallel tracks):**

- [ ] Wire `frontier` end-to-end in the notebook + a probe-gate (cheap go/no-go before a full run).
- [ ] Stand up the production-scan export→judge loop at volume (the flywheel).
- [ ] Spike multimodal fusion (vision + species + symptom) behind a flag.
- [ ] Design the segmentation-detector path (data + head) — research spike.

## Later / maybe

- [ ] **Spider-mite split** from `pests` (deferred — `pests` P already 0.94).
- [ ] **More UI languages** (server content already bilingual).
- [ ] **Expert-human consultation** (community-powered) — ties into the 2.7 safety escalation.

## Decisions

- **Reliability & safety first** — optimize against a confident wrong diagnosis (precision +
  honest uncertainty + OOD/blur refusal) over headline recall/F1.
- **Data > architecture** — the ceiling is data/precision; spend on the flywheel + scope
  before backbone size.
- **No dedicated eval benchmark** — the stable test split was removed (it stole scarce
  weak-class data); judge versions by per-class precision/recall CIs + runtime safety,
  never a small cross-version F1 delta.
- **No new disease classes until the scorch/nutrient boundary is stable** — 2.6.8 held the
  9-class set and it still isn't solved; scope expansion (edema / sunburn / root rot) waits
  for **2.7**.
- **Target store: RuStore** (RU audience; app already bilingual). No Google Play for now.
- **No Compose rewrite** — keep XML Views, single-module; modernize the guts (Koin, Ktor).
- **Stay free + honest** — calibrated, OOD-aware, on-device offline, no account, privacy-first.
- **Data loop is local** — collect/judge offline → Kaggle supplement → notebook pulls it.
