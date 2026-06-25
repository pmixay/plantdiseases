# PlantVision — roadmap

Short status + next steps. **No app-store release planning** — licensing,
verification and health-claims overhead put a public Play launch out of scope for
now; this stays an internal/beta build. Companions: `DATASET_LABELING_PLAN.md`
(data loop), `CLASS_COVERAGE_RESEARCH.md` (class add/remove research),
`ANDROID_RELEASE.md` (build/signing mechanics).

## Where we are

Last shipped: **v2.6.7 `gamma`** (frozen DINOv2 ViT-S/14 + LoRA + MLP head,
supplement v5) — ship gate **PASSED** (`val_realworld` n=1854, disease macro-F1
**0.862** int8 / 0.868 fp32; **stable held-out test** n=2329 F1 **0.866**) and
**INT8 ships** (reclaimed: paired bootstrap int8−fp32 CI [−0.018, +0.005], n.s.)
→ **130 MB bundle / 22.5 MB classifier**. The **gray_mold fix landed**: recall /
precision **0.83 / 0.83** on clean foliage after the *agricultural* strawberry
Botrytis proxy was removed — it had been the binding class for five versions, now
corroborated at **0.913** recall on the stable slice. Aggregate F1 is flat vs
v2.6.5/v2.6.6 (~0.86) because two classes now cap it: **nutrient_deficiency
(P 0.679) and leaf_scorch (P 0.745) over-predict** — the v6 target. YOLOv8n
detector mAP50 **0.819**. Calibration: T=0.761, RAPS α=0.2 → coverage 0.991,
set 1.90 (fit on the shipped int8 path). aqua student recovered (KD val-macroF1
0.780→**0.798**).

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

- [x] **Trained v3 → v2.6.5**, **v4 → v2.6.6**, **v5 → v2.6.7** (`val_realworld`
      n=1854, F1 0.862; stable test n=2329, F1 0.866) — **shipped**
- [x] **INT8 reclaimed** — v2.6.7's INT8 is statistically tied with FP32 (paired
      bootstrap CI [−0.018, +0.005], n.s.); ships 22.5 MB classifier / 130 MB bundle
      (v2.6.0 had to ship FP32 87 MB). Calibration now fit on the int8 path (no fallback).
- [x] **gray_mold FIXED** — removed the *agricultural* strawberry Botrytis proxy and
      fed clean foliage (iNat 606 + supplement 255 + DoctorP 96 → 339 train). Result:
      R/P **0.83/0.83** on val_realworld, **0.913** recall on the stable slice. The
      binding class for five versions is solved; production scans now a maintain-only lever.
- [x] **leaf_scorch — v4 regression did NOT persist** (v2.6.7 F1 ≈0.78, R 0.814);
      it is now **precision-bound** (P **0.745**, over-triggers) rather than recall-bound.
- [x] **rice/coleaf de-crop verdict: NEUTRAL** (kept removed; were wrong-domain dead weight).
- [x] **aqua student recovered** — the §5 feature-distillation fix landed: KD val-macroF1
      0.780→**0.798**, smooth curve, no collapse (still below the 0.861 teacher, expected).
- [x] **stable cross-version benchmark shipped** — `make_test_split` (§2) carves a
      class-aware, content-hash, group-aware held-out slice; `final test` F1 0.866 graded
      next to `val_realworld`. Future runs are now comparable across dataset versions.
- [ ] **v6 = the precision run** — `nutrient_deficiency` (P **0.679**) and `leaf_scorch`
      (P **0.745**) over-predict and alone cap macro-F1 at 0.86. Fix with confusion-pair
      boundary negatives (scorch-lookalike + nutrient-lookalike healthy); do NOT add
      positives. Optionally trim the LA-prior rare-class boost. See `DATASET_LABELING_PLAN.md`.
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
