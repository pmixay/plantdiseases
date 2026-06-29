# PlantVision — roadmap

Open work only — a checkbox to-do list (the code is the record). Companions:
`DATASET_LABELING_PLAN.md` (data loop), `../DEPLOYMENT.md` (deploy + Android
signing/FCM), `server/models/MODEL_CARD.md` (per-run metrics).

## Where we are (stage)

- **Shipped:** v2.6.8 `gamma` (frozen DINOv2 ViT-S/14 + LoRA + MLP), 9 classes, two-stage
  pipeline with conformal / OOD / blur safety. Trained on the **early-v6 precision/safety
  supplement** (+1.7k rows over v2.6.7, mostly hard-healthy + `not_a_plant` boundary
  negatives). Ship gate passed: `val_realworld` (n=2183, harder/boundary-heavy) disease
  macro-F1 **0.858** int8 / 0.854 fp32, in-domain **0.890**, overall acc **91.3%**,
  healthy recall 0.912; T=0.849, conformal coverage 0.988 / set 1.87; detector mAP50 0.814.
  Honest ceiling unchanged: **data/precision-bound, not backbone-bound** (§3 frozen probe
  ~0.91) — more/better data beats a bigger net.
- **⚠ On-device `aqua` is NOT shippable from this bundle** — the §6 exact-metrics eval
  caught the dynamic-range INT8 TFLite collapsing MobileNetV3 to near-constant output
  (realworld disease macro-F1 ≈0.01 vs ≈0.82 for the healthy float student). The notebook
  §5 selector + §6 guard are fixed to ship the **float16** build (~2.9 MB); regenerate on
  the next `aqua` run before any on-device release.
- **In progress:** continue v6 collection (more boundary negatives + first production scans).
- **Next:** pick **2.7** levers from 2.6.8's per-class results (now in hand); scaffolding prepped.

## 2.6.8 — shipped (precision / safety run)

The user harm is a confident *wrong* diagnosis (disease on a healthy plant). The early-v6
boundary negatives went in; results are **mixed**, which sets the v6-full agenda:

- [x] **Trained on early-v6 + shipped `gamma`** — ship gate passed, calibration re-fit on
      the int8 path, bundle validated (`check_bundle.py`).
- [x] **Test-split discipline** — removed the stable `make_test_split` carve (it stole scarce
      weak-class data; cross-version F1 deltas are eval-set noise). Gate is now an absolute
      `val_realworld` floor + per-class P/R CIs + false-fire rate.
- [x] **Exact aqua metrics, deployed** — the run evaluates the shipped TFLite on the val
      slices (reusing `evaluate()`) and ships per-class + teacher→student drift in
      `model_card.json`. This is what caught the int8 collapse; no more guessing on-device numbers.
- [~] **Precision goal partially met** — `nutrient_deficiency` precision **0.679 → 0.747**
      (good direction, still < 0.80) but recall slipped to **0.782**; `leaf_scorch` precision
      **~flat 0.752** (recall 0.788). On the harder boundary val set, healthy→disease
      false-fire is **8.4%**. The scorch/nutrient boundary is still the binding weakness.
- [ ] **Carry into v6-full** — keep collecting confusion-pair negatives **and** weak-class
      positives (recall floor); re-test the LA-prior trim for nutrient/scorch.
- [ ] **Ship the fixed float16 aqua** — re-run `aqua` mode, confirm the §6 guard is silent,
      deploy the float16 student on-device.

## Unveiling 2.7 — the leap

> From "a good leaf classifier" to a **holistic, trustworthy plant doctor**. The revolution
> is in **scope + real-world reliability + data scale**, not a bigger backbone alone (the
> probe says features aren't the ceiling). 2.6.8's per-class results are now in hand —
> pick the exact levers from them; scaffold in parallel with continued v6 collection.

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
- **No new disease classes until the scorch/nutrient boundary is stable** — 2.6.8 held the
  9-class set and it still isn't solved; scope expansion (edema / sunburn / root rot) waits
  for **2.7**.
- **Target store: RuStore** (RU audience; app already bilingual). No Google Play for now.
- **No Compose rewrite** — keep XML Views, single-module; modernize the guts (Koin, Ktor).
- **Stay free + honest** — calibrated, OOD-aware, on-device offline, no account, privacy-first.
- **Data loop is local** — collect/judge offline → Kaggle supplement → notebook pulls it.
