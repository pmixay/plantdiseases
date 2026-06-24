# PlantVision — roadmap

Short status + next steps. **No app-store release planning** — licensing,
verification and health-claims overhead put a public Play launch out of scope for
now; this stays an internal/beta build. Companions: `DATASET_LABELING_PLAN.md`
(data loop), `ANDROID_RELEASE.md` (build/signing mechanics).

## Where we are

> **Version scheme.** The **public/production** version is the one the live API
> reports — currently **2.6.0** (`GET /api/health` → `version: 2.6.0`), the
> deployed `gamma` bundle. The `v2.6.x` labels below are **internal
> training-run identifiers** for successive dataset supplements, not separate
> public releases. "Shipped" = what production serves = **2.6.0**.

Production (public) version: **2.6.0 `gamma`** (the deployed bundle, supplement v3).
Internal run identifiers below track successive dataset supplements.

Last training run shipped to the bundle: **v2.6.5 `gamma`** (frozen DINOv2
ViT-S/14 + LoRA + MLP head, supplement v3). **Latest run: v2.6.6** (supplement v4) — ship gate passed
(`val_realworld` n=2150, disease macro-F1 **0.861** vs v2.6.5's 0.865, flat
within noise) and **INT8 now ships** (it beat FP32 this run: +0.0057, n.s.) →
**130 MB bundle / 22.5 MB classifier**, reclaiming the 4× size win v2.6.5 lost.
But it's a **lateral move**: powdery F1 0.881→**0.939** and gray_mold precision
0.840→**0.888**, offset by a **leaf_scorch regression** (F1 0.805→0.719) and a
viral-precision dip (eval-slice-confounded). YOLOv8n detector mAP50 0.817.
**Ship decision pending** — see "Model / data — next".

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

- [x] **Trained v3 → v2.6.5**, then **v4 → v2.6.6** (`val_realworld` n=2150, F1 0.861)
- [x] **INT8 reclaimed** — the v4 run's INT8 beat FP32 (+0.0057, n.s.); ships 22.5 MB
      classifier / 130 MB bundle (v2.6.5 had to ship FP32 87 MB)
- [x] **rice/coleaf de-crop verdict: NEUTRAL** — removing the 200 rice `kaggle_nutrient`
      images (and `coleaf` entirely) left nutrient precision flat (0.743→0.750): they
      were wrong-domain dead weight, not the cause. Keep removed; don't re-add.
- [ ] **leaf_scorch regression (NEW, v4 run)** — F1 0.805→0.719 (P 0.774→0.703,
      R 0.840→0.736) at the same eval n. Likely the v4 healthy hard-negatives (+114,
      mostly *generic*; only +31 boundary) made the model under-call scorch → **v5: add
      ONLY boundary brown-tip negatives, stop adding generic healthy.** Flagship fix,
      don't ship-regress it silently.
- [ ] **nutrient_deficiency precision** — still the laggard (P **0.750**, over-triggers
      ~1.19×). Confirmed it's boundary confusion, not crop domain → v5 scorch↔nutrient /
      healthy↔nutrient **boundary** negatives.
- [ ] **gray_mold** — precision improved **0.840→0.888** despite flat supply (232,
      31% conf<0.80, wide CIs at n=94) → production scans remain the real lever.
- [ ] **aqua student regression (NEW)** — KD val_macroF1 0.838→**0.780**; teacher↔student
      gap tripled (0.043→0.106); on hard real web photos it collapses toward
      healthy/not_a_plant. **Fix queued for the next run:** feature distillation
      added to §5 (`KD_BETA=0.5` — cosine-match the student's representation to the
      teacher's DINOv2 CLS embedding) + clean teacher `eval()` targets, at the same
      30-epoch budget (no added runtime). Validate the gap closes on the next run.
- [ ] Fold offline-judged production scans/feedback into the next dataset version
      (export via `tools/export_data.py` → vision-agent judge; weak classes first)

## Differentiators — keep 🛡️

Free diagnosis, calibrated "I'm not sure" (conformal sets), explicit out-of-domain
reject, on-device offline, no account, privacy-first. Every major competitor
paywalls diagnosis and answers overconfidently on poor photos — don't trade this
edge for breadth.

## Later / maybe 💡

Class-expansion stance: keep the 9 trainable classes, add nothing structural yet.

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
