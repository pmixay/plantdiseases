# Inference pipeline & feedback loop

This covers the **serving** side (`server/pipeline.py` and friends) and the
**data flywheel** that feeds the next training run. The pipeline's job is to turn
an uploaded photo into an *honest* diagnosis; the feedback loop's job is to turn
production usage into labelled training data — **the repo deliberately contains
no training code on the server**; retraining happens offline via the notebook.

## Two-stage pipeline (`pipeline.py`)

```
image → Stage 1 (YOLOv8n detector) → leaf bbox crops → Stage 2 (classifier) → vote → guards → verdict
```

Stage 1 (`detector.py`, `DETECTOR_IMG_SIZE=640`, single-class `plant_leaf`,
recall-biased `MIN_CONFIDENCE=0.15`) **only localises** — every "is it diseased /
which disease" decision is 100% Stage 2 (`classifier.py`, DINOv2 @224). Letting
the detector crop first means Stage 2 sees a tight leaf, not a whole room.

### Crop handling & voting
- **Size filter** drops tiny detections before classification:
  `MIN_CROP_SIZE=32` px and `MIN_BBOX_AREA_FRAC=0.005` (0.5% of frame).
  `CROP_PADDING_RATIO=0.10` pads each bbox.
- **Mean voting** across valid crops — one noisy edge crop can't win the room.
- **Whole-frame arbitration** (`FOLD_WHOLE_FRAME_VOTE`, default on, settable
  via `limits.fold_whole_frame_vote`) folds the full image into the vote to
  recover detector-crop loss on leaf-centric inputs. Turning it off is the
  cheapest latency lever — it drops one full ViT forward per scan.
- `TOP_K_ALTERNATIVES=3` — the response always carries the top-3.

### Guards (the "honest uncertainty" machinery)
- **Blur gate** — variance-of-Laplacian `< BLUR_LAPLACIAN_FLOOR` (≈50,
  downscaled to `BLUR_DOWNSCALE=320`) → short-circuit to `not_a_plant`.
- **OOD AND-gate** — fires only when detector confidence `<
  NAP_DETECTOR_CONF_FLOOR` (0.40) **and** the classifier-side OOD signal agree
  (embedding k-NN distance where a bank ships, else MaxLogit `<
  NAP_MAX_LOGIT_FLOOR_DEFAULT`=4.0). Calibration in notebook §6.
- **Temperature scaling** — applied before voting when `temperature.json` is
  present (else T=1.0).
- **Weak-healthy reroute** — when top-1 is `healthy` but **below a majority**
  (`HEALTHY_REROUTE_FLOOR=0.50`) while a disease is competitive
  (`HEALTHY_REROUTE_DISEASE_FLOOR=0.25`), swap to the disease so the UI doesn't
  reassure on an obviously-sick plant. A **confident-plurality `healthy` is
  never force-converted** — instead the suspicion surfaces via `top_k` + soft
  warnings (`uncertain_healthy`, `disease_suspected`). The symmetric
  "weak-disease → healthy" downgrade was **removed** in favour of an honest
  `low_confidence` flag — see `_pick_primary`.

> *Design rule:* the pipeline never manufactures a disease false-positive on a
> healthy plant, and never reassures on a weak-healthy one. When unsure it flags
> (`uncertain_healthy`, `low_confidence`, `blurry_image`) rather than commits.

### Severity scoring
Each verdict carries `severity ∈ {low, medium, high}` from (a) diagnosed-disease
**bbox area** (`SEVERITY_AREA_LOW=0.06`, `SEVERITY_AREA_HIGH=0.18`), (b)
**confidence margin** over `healthy` (`SEVERITY_MARGIN_*`), and (c) whether the
class is **degenerative** — `viral_disease` always escalates one level
(incurable, `_DEGENERATIVE_DISEASES`).

## Content & companion services
- `diseases_data.py` — the bilingual (EN/RU) disease database: per-class
  description, treatment checklist, prevention, watering/feeding/humidity
  schedule, red-flag escalation. A new class needs a card here.
- `conformal.py` / `ood.py` — the calibration/OOD runtime (see
  `eval-and-calibration.md`).
- `ai_services.py` — optional PlantNet species ID + OpenRouter "plant doctor" +
  care-plan/progression. All **graceful when unconfigured** (`available:false`),
  never a hard dependency.

## Feedback loop → next training run

The flywheel that the notebook §2 ultimately consumes:

1. **`POST /api/feedback`** (`feedback.py`) — append-only training signal: was
   the diagnosis right? Stores `predicted_class`, `corrected_class`, `top3`,
   `prediction_set`, `image_path`, `out_of_head`, `model_version`, locale,
   platform. It **accepts deferred labels** (`edema`, `sunburn_or_scorch`) the
   head can't yet predict, so the retraining loop knows what data to prioritise.
   Append-only — never mutate or delete.
2. **Recorded scans as weak labels** — every scan carries the model's own
   prediction (`model_pred`/`model_conf`/`model_set`/`status`) as a **weak label
   prior**. There is no in-app human review queue; the maintainer judges images
   offline with a vision-agent (same one that grades scraped images).
3. **`tools/export_data.py`** — bundles the three production stores
   (`labeling.db`+images, `feedback.db`+images, `community.db`+thumbs) into one
   timestamped archive to pull off the box. **Read-only, offline, never decodes
   images** — judging is the offline agent's job.
4. **`tools/analyse_feedback.py`** — read-only deep analysis over the feedback
   corpus: where the model is right, wrong, and **overconfident** (predicted
   confidently but corrected). This is how you find the next weak class to target
   in §2. Read-only; never retrains; never touches the network.
5. Folded back into the §2 dataset → retrain → §6 ship gate → deploy.

*Portable principle:* close the loop with **append-only** capture + **offline**
judging + a **read-only** analysis pass that surfaces overconfident errors. Keep
all training/judging off the serving box; the server only emits signal.

## Tests guard the contract (`server/tests/`, 45 files, pytest)

The suite encodes the invariants above — run it before trusting a change:
- `test_pipeline_helpers.py`, `test_ood.py`, `test_conformal.py` — gate logic,
  OOD, conformal sets.
- `test_data_invariants.py`, `test_classifier_config.py`,
  `test_model_families.py` — `classes.json` ↔ taxonomy ↔ family consistency.
- `test_eval_bootstrap.py` — the paired-bootstrap estimators.
- `test_feedback_endpoint.py`, `test_feedback_labeling_wiring.py`,
  `test_scan_recording.py`, `test_analyse_feedback.py` — the feedback flywheel.
- `test_openapi_contract.py`, `test_endpoints.py`, `test_validation.py` — the
  HTTP contract.

Conventions: `ruff` (line-length 110, target py314, rules E/F/I/B; `E501`/`B008`
ignored; notebook excluded), `pytest>=9`, `httpx` `TestClient`. The runtime
requirements are deliberately lean (no `scikit-learn` / `torch` in
`requirements.txt` — they're dev/eval-only) so the production image stays small.
Run: `cd server && ruff check . && pytest -q`.
