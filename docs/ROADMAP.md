# PlantVision — roadmap

Short status + next steps. **No app-store release planning** — licensing,
verification and health-claims overhead put a public Play launch out of scope for
now; this stays an internal/beta build. Companions: `DATASET_LABELING_PLAN.md`
(data loop), `CLASS_COVERAGE_RESEARCH.md` (class add/remove research),
`ANDROID_RELEASE.md` (build/signing mechanics).

## Where we are

Model **v2.6.5 `gamma`** shipped (frozen DINOv2 ViT-S/14 + LoRA + MLP head),
trained on **dataset supplement v3**; on-device **`aqua`** TFLite student
(1.71 MB), YOLOv8n leaf detector (mAP50 0.814). `val_realworld` (n=2334): disease
macro-F1 **0.865**, overall accuracy **91.9%**, healthy-bias 0.97 — ship gate
passed. Shipped **FP32** (INT8 regressed this run). The v3 precision-hardening
landed: **leaf_scorch precision 0.71→0.774**, gray_mold eval n 71→91.

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
- [x] Anonymized scan recording + feedback collection (consent-gated); value-driven
      keep gate (uncertain + all disease classes kept) + offline data export
      (`server/tools/export_data.py`) — the human admin review queue was removed
- [x] GDPR export/delete; privacy page
- [x] Per-run model card: per-class CIs, int8 paired-bootstrap, latency/size benchmark

## Model / data — next ⏳

- [x] **Train on supplement v3** → shipped as **v2.6.5** (`val_realworld` n=2334)
- [x] **leaf_scorch precision** — v3 boundary negatives held: **0.71 → 0.774**
      (≥ 0.75 target met; over-trigger 1.21×→1.08×)
- [ ] **nutrient_deficiency precision** — still the laggard (P **0.743**, over-triggers
      1.21×) → more scorch↔nutrient / healthy↔nutrient disambiguating negatives. Also a
      **train-source fix landed** (2026-06-19 audit): the 200 rice field-crop
      `kaggle_nutrient` images that were diluting this class are now gated off and
      COCO not_a_plant halved 1500→750 (`coleaf`/`mpld` stay — guarded crop proxies,
      not dead config) — see `DATASET_LABELING_PLAN.md` "v4 audit addendum".
- [ ] **gray_mold supply** — top off positives 228 → ≥ 250 (v4 carry-over; point
      P/R flat, CIs tighter at eval n 91). Now a **quality** target too: 32% of the
      supplement's gray_mold positives are agent-conf < 0.80 → collect clean (≥0.80)
      houseplant positives, don't just hit the count.
- [ ] **INT8 regressed on v3** (paired-bootstrap significantly worse → shipped FP32
      87.3 MB) — investigate static-quant / per-channel before reclaiming the 4× size win
- [ ] Fold offline-judged production scans/feedback into the next dataset version
      (export via `tools/export_data.py` → vision-agent judge; weak classes first)

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
  pulls it directly; the server only records production scans + feedback for offline
  export + judging (`DATASET_LABELING_PLAN.md`).
