# PlantVision — roadmap

Open work only — a checkbox to-do list (the code is the record). Companions:
`DATASET_LABELING_PLAN.md` (data loop), `../DEPLOYMENT.md` (deploy + Android
signing/FCM), `server/models/MODEL_CARD.md` (per-run metrics).

## Where we are (stage)

- **Shipped:** v2.6.7 `gamma` (frozen DINOv2 ViT-S/14 + LoRA + MLP) + on-device `aqua`,
  9 classes, two-stage pipeline with conformal / OOD / blur safety. Honest ceiling: the
  model is **data/precision-bound, not backbone-bound** — the §3 frozen probe already hits
  ~0.91, so more/better data beats a bigger net.
- **In progress:** v6 dataset collection (precision/safety boundary negatives).
- **Next (≈2 days):** train v6 → **2.6.8**, the precision/safety run.
- **Then:** **2.7** — the leap — decided on 2.6.8's per-class results; scaffolding prepped now.

## Now → 2.6.8  (precision / safety run, ≈2 days)

The user harm is a confident *wrong* diagnosis (disease on a healthy plant). v2.6.0 fired
disease on **31%** of hard-healthy inputs, v2.6.7 **~7%**; 2.6.8 drives it lower.

- [ ] **Finish v6 collection** — confusion-pair boundary negatives (scorch- + nutrient-
      lookalike healthy), judged vs both exemplar sets. **No new positives.** (`DATASET_LABELING_PLAN.md`)
- [ ] **Train v6 → 2.6.8** (`MODEL_VERSION` already bumped). Optionally trim the LA-prior
      boost for nutrient/leaf_scorch (paired test in the same run — may beat data).
- [ ] **Judge by precision/recall CIs + safety**, not headline F1 (no cross-version
      benchmark): nutrient/scorch precision ↑ with recall ≥ ~0.80, false-disease-fire rate ↓,
      conformal coverage ≥ target, OOD/blur intact.
- [x] **Exact aqua metrics, deployed** — the run now evaluates the shipped int8 TFLite on the
      val slices (reusing `evaluate()`) and ships per-class + the teacher→student drift in
      `model_card.json`. No more guessing the on-device numbers.
- [ ] **Watch aqua KD drift** — keep a weak-class volume floor for the data-hungry student.

## Unveiling 2.7 — the leap

> From "a good leaf classifier" to a **holistic, trustworthy plant doctor**. The revolution
> is in **scope + real-world reliability + data scale**, not a bigger backbone alone (the
> probe says features aren't the ceiling). Pick the exact levers from 2.6.8's results;
> scaffold now, in parallel with v6 collection + the 2.6.8 run.

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

## App / engineering

- [ ] **Decompose `PlantDetailActivity`** (~2300 lines) into `ui/result/`-style controllers.
- [ ] **RuStore release config** — manifest / signing / release wiring. No secrets in git.

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
- **No new disease classes in 2.6.8** — stabilize the scorch/nutrient boundary first; scope
  expansion (edema / sunburn / root rot) is a **2.7** goal.
- **Target store: RuStore** (RU audience; app already bilingual). No Google Play for now.
- **No Compose rewrite** — keep XML Views, single-module; modernize the guts (Koin, Ktor).
- **Stay free + honest** — calibrated, OOD-aware, on-device offline, no account, privacy-first.
- **Data loop is local** — collect/judge offline → Kaggle supplement → notebook pulls it.
