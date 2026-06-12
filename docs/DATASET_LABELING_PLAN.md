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

## The core idea (and why it stays honest)

Each candidate carries a **proposed class** (the folder/scrape query it came from). We run it
through the gamma classifier and combine three calibrated signals — `confidence`, the conformal
`prediction_set`, and the OOD gate — to route it:

| Model vs. proposed class | Route | Status | Becomes |
|---|---|---|---|
| **Agrees, confident** (proposed == top-1 and `prediction_set == {proposed}` or `conf ≥ τ_high`), not OOD | auto-label | `auto_yes` | **TRAIN** supply (model-trusted, no human) |
| **Confidently disagrees** (proposed ∉ `prediction_set`; top-1 is a different in-domain class at `conf ≥ τ_high`), or OOD/not-a-plant for a disease class | auto-reject | `auto_no` | discarded |
| **Uncertain / confusing** (proposed ∈ a multi-class `prediction_set`, or `conf ∈ [τ_low, τ_high]`, or top-1 ≠ proposed but proposed still plausible) | **human** | `pending` → `yes/no/not_sure` | **EVAL** supply (human-verified) |

**This routing is the key to trusting the model without circularity.** A naïve "auto-accept
everything the model agrees with, then evaluate the model on it" would bias the eval set toward
what the model already gets right — inflating the gate. Here:

- **Train** gets the cheap, high-volume, model-confident bulk (self-training / weak supervision —
  standard and safe for *training* data).
- **Eval** gets **only the cases the model was NOT confident on** — the decision-boundary images
  where human verification actually matters and where the model cannot self-certify. An eval
  slice built from the model's *own* hard cases is the **honest** way to measure it, not a
  circular one.

Net effect: humans see a small fraction (the confusing tail), and every human minute lands on
the most valuable, eval-bound examples.

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
   (JSON conformal set), `route TEXT` (`train` | `eval`); extend `status` to
   `{pending, auto_yes, auto_no, yes, no, not_sure}`.
2. **Auto-label step** — new `server/labeling_auto.py` `classify_candidate(image) -> decision`
   that calls the already-loaded `pipeline.classifier` (gamma) and applies the routing table.
   Runs in the ingestion worker thread (`run_in_executor`), right after the image gates, before
   the row is committed. Each candidate is inserted with its computed status + model provenance.
   `auto_yes` → `route='train'`; `pending` (later human `yes`) → `route='eval'`.
3. **Human queue is the confusing set, automatically.** `claim_next` already serves only
   `status='pending'` → no change needed; humans now only ever see uncertain cards.
4. **`stats`** — per class report `{target, train_auto (auto_yes), eval_human (yes),
   pending (confusing, awaiting human), auto_no, satisfied}` and the train/eval split.
5. **Export** — split-aware bundle: `train/<class>/…` (from `auto_yes`) and `eval/<class>/…`
   (from human `yes`), each with a `manifest.csv` carrying `verified_by` = `model` (train) vs
   `human` (eval) and the model's `pred/conf/set` for audit. The notebook §9 then wires the
   train folder as a **train-only** source and the eval folder as an **eval-eligible** source.
6. **Re-score on model update** — when a new model ships, optionally re-run `classify_candidate`
   over `pending`/`auto_*` to re-route (a candidate the new model is now sure about leaves the
   human queue). Admin-triggered endpoint; cheap (cached images).
7. **Config (`config.example.json` security.*):** `labeling_auto_enabled` (true),
   `labeling_conf_high` (τ_high, 0.85), `labeling_conf_low` (τ_low, 0.55),
   `labeling_trust_conformal` (true — prefer the conformal set over raw τ),
   `labeling_auto_to_train` (true — auto_yes feeds train; flip to send auto_yes to a human
   spot-check sample instead).

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
