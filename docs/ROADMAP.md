# PlantVision — roadmap

Open work only — a checkbox to-do list. Completed items are dropped (the code is
the record). Companions: `DATASET_LABELING_PLAN.md` (data loop),
`ANDROID_RELEASE.md` (build/signing), `server/models/MODEL_CARD.md` (per-run metrics).

## Status

Shipped production model: **v2.6.7 `gamma`** — frozen DINOv2 ViT-S/14 + LoRA + MLP
head, 9 classes, two-stage pipeline (YOLO leaf-detect → DINOv2 classify) with
conformal uncertainty, OOD reject and a blur gate. On-device offline `aqua` model +
server `gamma`; bilingual EN/RU throughout. The v5 run fixed `gray_mold_botrytis`
(the binding class for five versions) on clean foliage and ships INT8 (reclaimed,
22.5 MB classifier); the open weakness is `nutrient_deficiency` / `leaf_scorch`
over-prediction (the v6 target). Per-run metrics live in `server/models/MODEL_CARD.md`.

9 classes: `gray_mold_botrytis`, `healthy`, `leaf_spot`, `not_a_plant`, `pests`,
`powdery_mildew`, `viral_disease`, `leaf_scorch_underwatering`, `nutrient_deficiency`.

## App / engineering — next

- [ ] **Decompose `PlantDetailActivity`** (~2300 lines) into controllers following the
      `ui/result/` template (history / care / progress / header). The Activity becomes
      a thin coordinator.
- [x] **Koin DI rollout — done.** All `application as PlantDiseasesApp` lookups now use
      direct injection (`by inject()` in Activities/Fragments, `KoinComponent` + `by inject()`
      in controllers/workers/push); the Application's repository accessors were removed.
- [x] **Retrofit + Gson → Ktor + kotlinx.serialization — done.** All endpoints run on the
      Ktor `HttpClient` (over the shared OkHttp engine); the Retrofit `ApiService` interface
      and `converter-gson` were removed. Gson stays only for Room field + response-cache JSON.
- [ ] **RuStore release config** — manifest / signing / release wiring so the app is
      publishable to RuStore. No secrets in git.

## Model / data — next

- [ ] **v6 = the reliability/safety run (precision over recall).** A confident *wrong*
      diagnosis is the user harm: v2.6.0 fired disease on **31%** of hard-healthy inputs,
      v2.6.7 ~7% — drive it lower. `nutrient_deficiency` (P **0.679**) and `leaf_scorch`
      (P **0.745**) over-predict; fix with confusion-pair boundary negatives (scorch- +
      nutrient-lookalike healthy), do NOT add positives, optionally trim the LA-prior
      boost. Keep tuning the runtime safety guards (OOD/blur/conformal/weak-healthy
      reroute). See `DATASET_LABELING_PLAN.md`.
- [ ] **Fold production data into the next dataset version** — export offline-judged
      production scans/feedback (`tools/export_data.py` → vision-agent judge), weak
      classes first.
- [ ] **Judge v6 without a cross-version benchmark.** The stable test split
      (`make_test_split`) was **removed** (it stole scarce weak-class data) and no frozen
      benchmark is maintained, so headline cross-version disease-F1 is NOT comparable (the
      v2.6.0↔v2.6.7 "0.865 vs 0.862 regression" was an eval-set artifact). Judge v6 by
      per-class precision/recall CIs + runtime safety signals (false-disease-fire rate,
      OOD/blur, conformal coverage), not the macro-F1 mean. See `DATASET_LABELING_PLAN.md`.
- [ ] **aqua drift watch.** The on-device student regressed as the data hardened (KD
      val-macroF1 0.838→0.780→0.798 while gamma held ~0.86) — it is data-hungry and
      overfits the smaller/harder set. Keep a weak-class volume floor for the student +
      monitor KD drift each ship. See `DATASET_LABELING_PLAN.md`.

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
- **Reliability & safety first.** Optimize for not giving a confident wrong diagnosis
  (precision + honest uncertainty + OOD/blur refusal) over headline recall/F1.
- **No dedicated eval benchmark — judge by precision/recall + safety.** The stable test
  split was removed (carving it stole scarce weak-class data), so cross-version macro-F1
  is approximate; compare per-class precision/recall CIs + runtime safety behaviour, and
  never read a small disease-F1 delta across versions as real.
- **Stay free + honest** — calibrated, OOD-aware, free diagnosis; explicit out-of-domain
  reject; on-device offline; no account; privacy-first. Don't trade this edge for breadth.
- **Data loop is local** — collect/judge offline → Kaggle supplement → the notebook pulls
  it directly; the server only records production scans + feedback for offline export.
