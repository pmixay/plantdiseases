# PlantVision — roadmap

Short status + next steps. **No app-store release planning** — licensing,
verification and health-claims overhead put a public Play launch out of scope for
now; this stays an internal/beta build. Companions: `DATASET_LABELING_PLAN.md`
(data loop), `CLASS_COVERAGE_RESEARCH.md` (class add/remove research),
`ANDROID_RELEASE.md` (build/signing mechanics).

## Where we are

Model **v2.6.0 `gamma`** shipped (frozen DINOv2 ViT-S/14 + MLP head), on-device
**`aqua`** TFLite student, YOLOv8n leaf detector. Disease macro-F1 **0.865** on
`val_realworld` (n=2213); passes the ship gate. Dataset **supplement v3** is
collected (1825 imgs, hard houseplant-domain gate) but **not yet trained**.

9 classes: `gray_mold_botrytis`, `healthy`, `leaf_spot`, `not_a_plant`, `pests`,
`powdery_mildew`, `viral_disease`, `leaf_scorch_underwatering`,
`nutrient_deficiency`.

## Shipped ✅

- [x] Photo diagnosis (9 classes) with calibrated conformal uncertainty + severity
- [x] Two-stage pipeline (YOLO leaf-detect → DINOv2 classify), OOD reject, blur gate
- [x] On-device offline `aqua` model + server `gamma`; `frontier` tier recipe
- [x] Bilingual (EN/RU) care plans, AI chat doctor, no-photo symptom triage
- [x] Light meter, watering calculator, garden tracker + care reminders
- [x] Community nearby-scans feed + second opinions, progression tracking
- [x] Species ID (PlantNet) as a Home headline feature
- [x] Disease guide with example photos, pet-toxicity warnings
- [x] Anonymized scan recording + feedback → human labeling queue (consent-gated)
- [x] GDPR export/delete; privacy page
- [x] Per-run model card: per-class CIs, int8 paired-bootstrap, latency/size benchmark

## Model / data — next ⏳

- [ ] **Train on supplement v3** → read per-class `val_realworld` metrics (this is
      the gate that decides everything below)
- [ ] **leaf_scorch precision** — confirm v3 boundary negatives held the gain
      (0.52 → 0.71); push toward ≥ 0.75
- [ ] **nutrient_deficiency precision** — still over-triggers (P 0.72) → disambiguating
      negatives, not more positives
- [ ] **gray_mold supply** — top off positives 228 → ≥ 250 (the v3 carry-over)
- [ ] Fold human-verified production feedback/scans into the next dataset version
      (the only non-proxy rows; weak classes first)

## Differentiators — keep 🛡️

Free diagnosis, calibrated "I'm not sure" (conformal sets), explicit out-of-domain
reject, on-device offline, no account, privacy-first. Every major competitor
paywalls diagnosis and answers overconfidently on poor photos — don't trade this
edge for breadth.

## Later / maybe 💡

Class-expansion research lives in `CLASS_COVERAGE_RESEARCH.md` (TL;DR: keep the 9,
add nothing structural yet).

- [ ] **Root rot / overwatering** — the most common houseplant problem, but nearly
      inseparable from nutrient/scorch on a leaf photo and no clean public data →
      handle as a **symptom-triage question** ("wet soil but wilting?"), not a
      vision class
- [ ] Edema + sunburn class activation (content cards already exist in `diseases_data.py`)
- [ ] Spider-mite split from `pests` (deferred — `pests` P already 0.94, data is
      wrong-domain, only the webbing stage is separable)
- [ ] More UI languages (server content is already bilingual)
- [ ] Expert-human consultation (could be community-powered)

## Decisions

- **No public app-store release for now** — out of scope; internal/beta only.
- **No new disease classes** until the scorch/nutrient boundary is stable.
- **Stay free + honest** — the product *is* calibrated, OOD-aware, free diagnosis.
- **Data loop is local** — collect/judge offline → Kaggle supplement → the notebook
  pulls it directly; the server only records production scans + feedback for human
  review (`DATASET_LABELING_PLAN.md`).
