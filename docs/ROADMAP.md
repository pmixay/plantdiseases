# PlantVision — roadmap

Open work only — a checkbox to-do list. Completed items are dropped (the code is
the record). Companions: `DATASET_LABELING_PLAN.md` (data loop),
`ANDROID_RELEASE.md` (build/signing), `server/models/MODEL_CARD.md` (per-run metrics).

## Status

Shipped production model: **v2.6.7 `gamma`** — frozen DINOv2 ViT-S/14 + LoRA + MLP
head, 9 classes, two-stage pipeline (YOLO leaf-detect → DINOv2 classify) with
conformal uncertainty, OOD reject and a blur gate. On-device offline `aqua` model +
server `gamma`; bilingual EN/RU throughout.

9 classes: `gray_mold_botrytis`, `healthy`, `leaf_spot`, `not_a_plant`, `pests`,
`powdery_mildew`, `viral_disease`, `leaf_scorch_underwatering`, `nutrient_deficiency`.

## App / engineering — next

- [ ] **Decompose `PlantDetailActivity`** (~2300 lines) into controllers following the
      `ui/result/` template (history / care / progress / header). The Activity becomes
      a thin coordinator.
- [ ] **Finish the Koin DI rollout** — replace the remaining `application as
      PlantDiseasesApp` lookups with `by inject()` / `by viewModel()` on the large
      screens (Koin is already wired; the analyze path already runs through it).
- [ ] **Finish Retrofit + Gson → Ktor + kotlinx.serialization** for any endpoints that
      still use the Retrofit `ApiService` (the `/analyze` path is already on Ktor).
- [ ] **RuStore release config** — manifest / signing / release wiring so the app is
      publishable to RuStore. No secrets in git.

## Model / data — next

- [ ] **leaf_scorch regression (v4 run)** — F1 0.805→0.719 (P 0.774→0.703,
      R 0.840→0.736) at the same eval n; the v4 generic healthy hard-negatives made the
      model under-call scorch. **v5: add ONLY boundary brown-tip negatives, stop adding
      generic healthy.** Flagship fix — don't ship-regress it silently.
- [ ] **nutrient_deficiency precision** — still the laggard (P **0.750**, over-triggers
      ~1.19×). Confirmed boundary confusion, not crop domain → v5 scorch↔nutrient /
      healthy↔nutrient **boundary** negatives.
- [ ] **gray_mold** — precision improved **0.840→0.888** on flat supply (n≈94, wide CIs);
      production scans remain the real lever.
- [ ] **aqua student regression** — KD val_macroF1 0.838→**0.780**; teacher↔student gap
      tripled. Fix queued: feature distillation in §5 (`KD_BETA=0.5`, cosine-match the
      student to the teacher's DINOv2 CLS embedding) + clean teacher `eval()` targets at
      the same 30-epoch budget. Validate the gap closes next run.
- [ ] **Fold production data into the next dataset version** — export offline-judged
      production scans/feedback (`tools/export_data.py` → vision-agent judge), weak
      classes first.

## Later / maybe

- [ ] **Root rot / overwatering** — most common houseplant problem, but near-inseparable
      from nutrient/scorch on a leaf photo with no clean public data → handle as a
      symptom-triage question ("wet soil but wilting?"), not a vision class.
- [ ] **Edema + sunburn** class activation (content cards already in `diseases_data.py`).
- [ ] **Spider-mite split** from `pests` (deferred — `pests` P already 0.94, data is
      wrong-domain, only the webbing stage is separable).
- [ ] **More UI languages** (server content is already bilingual).
- [ ] **Expert-human consultation** (could be community-powered).

## Decisions

- **Target store: RuStore** (RU audience; the app is already bilingual). No Google Play
  for now — licensing / verification / health-claims overhead is out of scope.
- **No Compose rewrite** — keep XML Views, single-module; modernize the guts (Koin DI,
  Ktor networking) instead.
- **Offline doctor stays simple** — no on-device LLM; the online server doctor
  (OpenRouter `gpt-120b-oss`) is the smart path, offline shows the existing guide /
  symptom content.
- **No new disease classes** until the scorch/nutrient boundary is stable.
- **Stay free + honest** — calibrated, OOD-aware, free diagnosis; explicit out-of-domain
  reject; on-device offline; no account; privacy-first. Don't trade this edge for breadth.
- **Data loop is local** — collect/judge offline → Kaggle supplement → the notebook pulls
  it directly; the server only records production scans + feedback for offline export.
