# Dataset labeling plan — model-trust active learning

**Supersedes** the old human-labels-everything plan (`EVAL_DATASET_PLAN.md`, deleted).
**Goal:** supply the thin classes with enough trustworthy data to make a **high-confidence
production model**, while cutting human effort to the **uncertain minority only**.

## Context

The thin classes (`nutrient_deficiency`, `leaf_scorch_underwatering`, `gray_mold_botrytis`,
and the lighter `powdery_mildew`) are eval-supply-bound. The first approach asked a human to
verify **every** scraped/Kaggle candidate — thousands of cards, most of them obvious. That
doesn't scale and wastes humans on images the model already understands.

The model is **calibrated**: temperature scaling + inductive conformal prediction (RAPS) +
an embedding/MaxLogit OOD gate (`server/pipeline.py` `analyze()` → `class_name`, calibrated
`confidence`, `all_probs`, `prediction_set`, OOD). So it can be **trusted to label the easy
majority itself** and to **flag what it's unsure about**. This is textbook **active learning
(uncertainty sampling)**: the model auto-labels confident cases; humans adjudicate only the
confusing ones.

## The core idea

Each candidate carries a **proposed class** (the folder/scrape query it came from). We run it
through the calibrated gamma classifier and use the **conformal prediction set** (primary) plus
the temperature-calibrated `confidence` and OOD gate to route it:

| Model vs. proposed class | Status | Becomes |
|---|---|---|
| **Agrees, confident** — conformal set is the singleton `{proposed}` (or proposed == top-1 with `conf ≥ τ_high`), not OOD | `auto_yes` | **approved** (model-trusted, no human) |
| **Confidently disagrees** — proposed ∉ conformal set and top-1 is a different in-domain class at `conf ≥ τ_high`; or OOD/not-a-plant for a disease class | `auto_no` | discarded |
| **Uncertain / confusing** — proposed ∈ a multi-class conformal set, or `conf ∈ [τ_low, τ_high]`, or top-1 ≠ proposed but proposed still plausible | `pending` → human `yes/no/not_sure` | **approved** iff human `yes` |

**Trust-the-model routing (maintainer's choice).** Approved = `auto_yes` (model) + human `yes`
(the confusing tail). The whole approved pool is exported as **one eval-eligible dataset** and
the notebook's existing group-split seeds both train and eval from it — exactly how `doctorp` is
handled today. Humans only ever touch the uncertain minority.

> **Caveat (accepted):** because model-auto-labeled images feed the eval slice too, the ship gate
> partly measures the model on its own confident calls — a mild optimistic bias. This is the
> explicit "trust the model" trade for near-zero human effort. Two cheap honesty guards remain:
> the conformal set is *calibrated* (a singleton set genuinely means low error at the chosen α),
> and humans still supply the decision-boundary (eval-hardest) cases. If the gate later looks
> inflated, flip `labeling_auto_to_eval=false` to make `auto_yes` train-only again.

The signal is the **conformal prediction set** (`labeling_trust_conformal=true`): auto-accept only
when the calibrated set is the singleton `{proposed}`. Raw `τ_high`/`τ_low` are the fallback when
conformal is disabled or unavailable.

## Per-class targets (unchanged, config-driven `LABELING_TARGETS`)

| class | weight (target approved) | notes |
|---|---|---|
| nutrient_deficiency | 600 | thinnest eval |
| leaf_scorch_underwatering | 600 | over-triggers; needs clean boundary cases |
| gray_mold_botrytis | 400 | hardest to source (best-effort) |
| powdery_mildew | 150 | modest top-up; needs its own source (not in the Kaggle set) |
| others | 0 | not collected |

Targets now count **approved = `auto_yes` (train) + human `yes` (eval)**, reported split so we
can see "train auto-labeled" vs "eval human-verified" progress separately.

## Sources & gates (carried over, condensed)

Ingestion (`server/ingest/`) is unchanged in spirit:

- **Sources:** the maintainer's Kaggle eval-supplement (`kaggle_supplement`, primary) + Openverse
  (fallback). De-prioritize Wikimedia (often mislabeled).
- **Image gates:** real photo, short side ≥ 384 px, low-green floor 0.04 (keeps brown scorch /
  yellow chlorosis), SHA + perceptual-hash dedup.
- **Source-disjointness (anti-leakage, mandatory):** reject any candidate whose origin is a
  training source (iNaturalist / PlantDoc / PlantVillage / Kaggle training slugs). Without it a
  held-out image could secretly be a training image.
- **Deficit-driven:** only scrape classes under target; idle otherwise; log every source's
  yield (no silent failures).

## Server refactor (`server/labeling.py` + `server/ingest/` + `main.py`)

1. **`candidates` schema** — add `model_pred TEXT`, `model_conf REAL`, `model_set TEXT`
   (JSON conformal set) for provenance/audit; extend `status` to
   `{pending, auto_yes, auto_no, yes, no, not_sure}`. (Idempotent `ALTER TABLE ADD COLUMN`,
   mirroring `feedback.py`.)
2. **Auto-label step** — new `server/labeling_auto.py` `classify_candidate(image, proposed) ->
   (status, pred, conf, set)` that calls the already-loaded `pipeline.classifier` (gamma) and
   applies the routing table (conformal set first, τ fallback, OOD guard). Runs in the ingestion
   worker thread, right after the image gates, before the row is committed. Each candidate is
   inserted with its computed status + model provenance. Fail-soft: if the pipeline is
   unavailable, fall back to `status='pending'` (human) so nothing is lost.
3. **Human queue = the confusing set, automatically.** `claim_next` already serves only
   `status='pending'` → no change; humans now only ever see uncertain cards.
4. **`stats`** — per class report `{target, auto_yes, human_yes (yes), pending (confusing),
   auto_no, validated = auto_yes+yes, satisfied: validated ≥ target}`.
5. **Export** — one **approved** bundle (`status IN ('auto_yes','yes')`) organised `<class>/…`
   with `manifest.csv` carrying `verified_by` = `model` (auto_yes) vs `human` (yes) and the
   model `pred/conf/set` for audit. The notebook §9 wires it as an **eval-eligible** source
   (like `doctorp`); the group-split seeds both train and eval.
6. **Re-score on model update (optional)** — admin endpoint to re-run `classify_candidate` over
   existing rows when a new model ships (a candidate the new model is now sure about leaves the
   human queue). Cheap (cached images).
7. **Config (`config.example.json` security.*):** `labeling_auto_enabled` (true),
   `labeling_trust_conformal` (true — conformal set primary), `labeling_conf_high` (τ_high 0.85),
   `labeling_conf_low` (τ_low 0.55), `labeling_auto_to_eval` (true — `auto_yes` counts toward the
   approved/eval pool; flip to make auto-labels train-only and require human `yes` for eval).

## App refactor — minimum human contribution

The app already serves only `pending` (confusing) cards, so the change is **reframing + speed**,
not new screens:

- **Banner:** "The model auto-labeled the easy ones. You only see the cases it's unsure about."
- **Show the model's read on each card:** its top guess + confidence, and the conformal
  alternatives as chips (e.g. *"model: leaf_spot 0.46 · nutrient_deficiency 0.39"*). The human
  instantly sees *why* it's ambiguous.
- **One-tap resolution:** keep Yes / No / Not-sure, but add the conformal-set classes as
  quick-pick chips so the human can pick the *correct* class in one tap when the proposed one is
  wrong (captured as a correction for future routing; the verdict itself stays yes/no/not_sure
  against the proposed class for the eval slice).
- **Stats:** surface "auto-labeled N (train) · you resolved M ambiguous (eval) · K left" so the
  human sees their effort is small and targeted.
- Keep the prior fixes (window insets, class switcher, satisfied-gating, exit).

## Verification

1. Server unit tests: routing table (`classify_candidate` → auto_yes/auto_no/pending for
   confident-agree / confident-disagree / uncertain, using a stub classifier returning crafted
   `prediction_set`/`confidence`); export produces split `train/` + `eval/` bundles; stats split;
   disjointness + dedup unchanged. `ruff` + full suite green; regenerate `openapi.json`.
2. Live dry-run: ingest a batch → confirm most land `auto_yes` (train) and only the genuinely
   ambiguous land `pending`; `/api/label/next` returns only confusing cards; `/stats` shows the
   train/eval split; export yields two manifests.
3. Android: `lintDebug testDebugUnitTest assembleRelease` green; manual — banner + model chips
   render, one-tap resolution works, stats show the auto/human split.
4. Anti-circularity check: confirm `auto_yes` rows are train-routed and never enter the eval
   bundle; eval bundle is human-only.

## Risks

- **Circularity** — addressed by the train/eval routing (eval = human-verified uncertain cases
  only; the model never self-certifies its own eval slice).
- **Calibration drift** — trust rides on temperature/conformal being calibrated; re-fit on each
  model ship (already done in notebook §6). If `prediction_set` looks miscalibrated, fall back to
  raw `τ_high`/`τ_low` (config switch).
- **Model blind spots** — a class the model systematically mislabels would auto-route its hard
  cases to humans (good) but might auto-`no` true positives it's confident-wrong about. Mitigate
  with a periodic human **audit sample** of `auto_yes`/`auto_no` (small random %), surfaced in the
  same app queue.
- **powdery_mildew** has no Kaggle source and Openverse is sparse — still needs a dedicated
  dataset, or it stays at 0 regardless of the labeling method.

## Actions needed from you

- Confirm the routing thresholds (τ_high 0.85 / τ_low 0.55) and that **auto-labeled → train,
  human → eval** is the split you want (vs. requiring a human audit % of auto-labels).
- A source for `powdery_mildew` if you want that class filled.
- On approval I implement the server routing + schema, the split-aware export, and the app
  reframe, then verify end-to-end against the live model.
