# Evaluation & calibration

Two ideas run through everything here, and they're the project's hard-won
discipline — apply them even when a task doesn't mention them:

1. **A point-estimate F1 gap is not evidence.** At n≈2k `val_realworld`, macro-F1
   sampling error is ≈±0.04–0.05. A 0.04 "improvement" is almost certainly
   noise. Use **paired bootstrap** confidence intervals before believing any A/B
   ordering. The repo has a famous cautionary case: a historical
   `f1_int8=0.73 > f1_fp32=0.69` inversion that was pure noise.
2. **Honest uncertainty beats confident wrongness.** Temperature scaling fixes
   *calibration*; conformal prediction sets express *"either of these two"*;
   OOD/blur gates refuse rather than fake a diagnosis. The UX is built to say
   "I'm not sure" rather than reassure on a sick plant.

## The evaluation contract (§7)

Three independent validation slices, **one ship gate**:

| Slice | Role | Pass criterion |
|---|---|---|
| `val_indomain` | drives early stopping in §4 | tracked; best head checkpoint restored |
| `val_realworld` | **primary ship/no-ship gate** | see below |
| `val_studio` | PlantVillage transfer monitor (covers only ~4 of 9 classes) | tracked, **never blocking**; read `supported_disease_macro_f1` (macro-F1 over the classes *present* in the slice — the plain macro-F1 averages in zeros for the absent classes, ~0.43 vs ~0.99) |
| `temperature_cal` | held-out, fits temperature T | — |
| `conformal_cal` | held-out, fits RAPS `q_hat` (80/20 fit/audit when n≥200) | — |

**Ship gate on `val_realworld`** (§6, `MIN_DISEASE_F1, MAX_HEALTHY_BIAS,
MIN_HEALTHY_RECALL = 0.70, 1.50, 0.70`):
- disease **macro-F1 ≥ 0.70**
- **no disease class with recall = 0**
- **healthy-bias < 1.5** (model isn't just predicting "healthy" everywhere)
- **healthy recall ≥ 0.70** (controls false-positive disease alarms)

Nothing writes to `models/` until this passes. *Portable principle:* separate
the metric that **stops training** (in-domain) from the metric that **gates
shipping** (real-world), and make the gate a small set of floors, not a single mean.

### No cross-version benchmark — judge by per-class P/R + safety (2026-06)

There is **no fixed held-out benchmark** (the stable `make_test_split` carve was
removed — it stole scarce weak-class data, and each version's `val_realworld`
drifts in composition, so a small cross-version macro-F1 delta is eval-set noise,
not a model change — the v2.6.0↔v2.6.7 "regression" was exactly this artifact).
The gate is therefore an **absolute** floor, not a "beat the live model" check.
Judge a new version by signals that need no benchmark:
- **per-class precision AND recall with CIs** (`model_card.json` ships both) — the
  current binding weakness is *precision* (`nutrient_deficiency` / `leaf_scorch`
  over-predict), not recall;
- the **false-disease-fire rate** on healthy/boundary inputs — the user-facing
  reliability headline (a confident wrong diagnosis on a healthy plant is the harm);
- conformal coverage ≥ target; OOD/blur still reject junk.
For a real A/B, run a **one-off** `paired_bootstrap` of candidate vs live model on
an identical set (`tools/eval_bootstrap.py`) — a manual check, not a maintained gate.

## "Disease" macro-F1 — the exact definition

Macro-F1 is computed over **disease classes only** — `healthy` and
`not_a_plant` are excluded (they're not diseases and would inflate the mean).
The canonical implementation is `tools/eval_bootstrap.py:disease_macro_f1`;
`assets/analysis_boilerplate.py` mirrors it so scratch analysis stays
comparable. Macro (not micro) is deliberate: it's dominated by the smallest
classes, which is exactly where this model is weak and where regressions hide.

## Paired bootstrap — the trustworthy A/B (`tools/eval_bootstrap.py`)

Run it from `server/`. Public estimators:

- `bootstrap_macro_f1_ci(y_true, y_pred, class_names, …)` — percentile CI for
  one model's disease macro-F1 (resample test set, recompute, take 2.5/97.5).
- `paired_bootstrap(y_true, pred_a, pred_b, class_names, …)` — the **paired**
  test for A vs B on the *same* test set: one resampled index vector applied to
  **both** models, then the distribution of the difference. Controls for "which
  images are hard" and is far more sensitive than comparing two independent CIs.
  Reports `p_a_gt_b` (or similar) — read it as "P(A beats B)".
- `bootstrap_per_class_recall_ci` / `paired_per_class_recall` — same two
  estimators on **per-class recall**, because macro-F1 hides which weak class
  moved.
- `bootstrap_per_class_precision_ci` — for classes that over-trigger
  (precision-bound), so precision ships beside recall.

The CLI `main()` runs the paired bootstrap on both macro-F1 and per-class
recall — the honest gate to clear before trusting any Stage-2 improvement.
`scripts/` does **not** re-implement this; point at the repo tool.

> **Workflow:** for an out-of-notebook A/B (e.g. a weak-class run vs current
> model on a shared cleaned test set), get both models' predictions on identical
> indices, then `paired_bootstrap`. If the CI for the difference straddles 0,
> the change is **not** shippable evidence regardless of the point estimate.

## Temperature scaling (`conformal.py` consumes `temperature.json`)

A single scalar T (fit by LBFGS on `temperature_cal`, clamped to ~[0.05, 10]).
Logits are divided by T before softmax so a 0.85 probability means 85%. T is fit
on **the numerics path that ships** — `temperature.json.source ∈ {int8,
fp32_fallback}` records which, and `conformal.json.fit_source` must match. The
runtime applies T before voting; absent the file, T=1.0 (no-op).

*Why T isn't enough:* it fixes global miscalibration but not the top-1 boundary
between two look-alike classes (`leaf_spot ↔ viral_disease`), where the model
still emits one confident answer when the honest answer is "either". That's what
conformal sets are for.

## Conformal prediction sets — RAPS (`conformal.py`)

Inductive conformal (Vovk; Angelopoulos & Bates 2021): a held-out calibration
split yields `q_hat` per `alpha`, and online inference emits a **prediction set**
that contains the true label with probability ≥ `1 - alpha` under
exchangeability. Two schemes: **APS** (accumulate sorted softmax mass to
`q_hat`) and **RAPS** (APS + a size penalty `lambda*max(0, k - k_reg)` that
curbs huge sets on hard examples). §6 grid-searches `alpha` and `lambda_reg`
against a target coverage and ships the choice in `conformal.json`
(`{method, alpha, q_hat, lambda_reg, k_reg, calibration_n, empirical_coverage}`).

Clients treat the set as a **hint** — never gate UX on its presence; a
single-element set means "confident", a two-element set means "between these".

## OOD & blur gates (calibrated in §6, enforced in `pipeline.py`)

The pipeline refuses rather than guesses on inputs that aren't a focused leaf:

- **Blur gate** — variance-of-Laplacian below `BLUR_LAPLACIAN_FLOOR` (≈50) →
  short-circuit to `not_a_plant` so the user re-takes instead of getting fake
  confidence.
- **OOD AND-gate** — fires only when detector confidence **and** a
  classifier-side OOD signal agree. The classifier signal is, preferably, a
  **deep k-NN distance** in DINOv2 CLS embedding space (Sun et al. ICML 2022):
  score = `1 - mean cosine similarity to k nearest in-distribution vectors`;
  `score > threshold ⇒ OOD`. The bank (`ood_embedding.npz`) + config
  (`classes.json → ood.embedding`, `k=5`, cosine, `embed_dim=384`) are built in
  §6 at a target in-distribution FPR (`TARGET_PLANT_FPR=0.05`). Legacy bundles
  fall back to a **MaxLogit** floor (Hendrycks 2019) — robust to temperature +
  label smoothing because it reads raw logits, unlike plain MSP. On the
  frozen-DINOv2 head the `not_a_plant` logit got confident enough that the
  MaxLogit separation collapsed, which is *why* the embedding k-NN signal was
  added; the export auto-disables a non-separating MaxLogit floor.

*Portable principle:* gate on the input manifold (is this even a leaf?)
separately from the class decision, and require **agreement** between an
upstream detector and a backbone-side signal before rejecting — one weak signal
shouldn't veto.

## What to inspect first when a metric moves

1. `per_class_recall` — did a *disease* class hit 0 (auto-fail)? Which weak class
   moved?
2. Paired bootstrap CI of the difference — does it exclude 0?
3. `healthy_bias` and `healthy recall` — did the model drift toward "healthy"?
4. Calibration: did T or coverage shift? A coverage drop means conformal sets
   are now under-covering.
5. `model_card.json` — the per-class recall bootstrap CIs (`per_class_recall_ci_
   realworld`) ship with every run; compare against the previous run's card.
