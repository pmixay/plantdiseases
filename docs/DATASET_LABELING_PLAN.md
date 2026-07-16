# Dataset & labeling plan — local collection + offline export

The data loop is **local and offline**: the maintainer scrapes + vision-judges
images into a Kaggle supplement (`maxayyt5/houseplant-disease-eval-supplement`)
that `train_notebook.ipynb` pulls directly. The server only *records* anonymized
production scans + feedback for offline export — no server review queue, no
ingestion. Roadmap: `docs/ROADMAP.md`; per-run metrics: `server/models/MODEL_CARD.md`.

```
  LOCAL (maintainer, offline)                 SERVER (production)
  scrape → source-disjointness → pHash dedup  /api/analyze → anonymized scan record
  → per-creator cap → vision-agent judge      user feedback → correction (weak label)
       │ manifest.csv (verified_by=agent_vision)       │
       ▼                                               ▼
  Kaggle supplement  ◄──── fold judged ──────  tools/export_data.py (offline pull)
       │ pulled by train_notebook.ipynb → model bundle
```

## Supplement = supply + eval (one publish, no separate carve)

- **Supply (train):** every class folder (incl. the `healthy/` + `not_a_plant/`
  hard-negative folders) is train data.
- **Eval (held-out):** §2 holds out a deterministic hash fraction into
  `val_realworld`/`val_indomain` + `temperature_cal`/`conformal_cal`, group-aware
  (held-out source-disjoint from train).

Rows are agent-judged (`verified_by=agent_vision`), so the gate is a **proxy**
until verified production volume exists.

## Acceptance criteria (every version)

- **Layout:** `<class>/<image>` over the 9 classes + `manifest.csv`
  (`filename, class, role, subject_domain, hn_kind, confidence, source, phash, …`).
- **Source disjointness (anti-leakage):** no image from a training-corpus source
  (iNaturalist, PlantDoc, PlantVillage, the Kaggle training slugs, Wikimedia). A
  held-out training image inflates the gate.
- **Image gates:** real photos, short side ≥ 384 px, pHash near-dup removal
  (Hamming ≤ 6), per-creator cap.
- **Domain gate:** subject is a **houseplant/indoor plant** — no agricultural/field
  imagery.
- **Vision-agent judging:** kept/rejected vs exported reference exemplars; a
  boundary negative is kept only if the agent would *reject* it for the lookalike
  positive class (judged vs both confused-class exemplar sets).
- **Hard negatives role-tagged** (`role=hard_negative`, `hn_kind`) under `healthy/`
  or `not_a_plant/`; `hn_kind=boundary` is routed **train-only** by the notebook.

## Version status

- **v5 / 2.6.7** (06-26): gray_mold fixed on clean foliage (R/P 0.83/0.83) after the
  agricultural strawberry-Botrytis proxy was removed. realworld disease macro-F1 0.862.
- **v6 / 2.6.8** (06-29): +boundary negatives; mixed — nutrient precision 0.679→0.747
  (still <0.80), recall slipped; healthy→disease false-fire 8.4% on hard boundary.
- **v7 / 2.6.9** (07-03): +boundary negatives + first powdery; the notebook
  aqua-collapse fix — §5 now ships the faithful **float32** TFLite (~6.1 MB): INT8
  collapsed MobileNetV3 to ≈0.01, float16 won't prepare on LiteRT (`CONV_2D`); aqua
  realworld 0.76. **Shippable over 2.6.8.**

**Leakage-aware A/B — 2.6.5↔2.6.9 on the v4 & v7 eval slices** (2026-07-16, boundary
excluded, paired bootstrap, `tools/eval_bootstrap.py`). Each model wins on its own era
(2.6.5 +0.039 on v4; 2.6.9 +0.036 on v7 — CIs exclude 0): a train/eval-contamination
crossover (71% of v7 is byte-identical to v4). Leakage-robust reads (consistent across
*both* sets):

- **powdery — clean win:** 2.6.9 recall 0.93 vs 2.6.5 0.59 (new class).
- **healthy — 2.6.9 more conservative:** recall 0.97–0.98 (↑) at lower precision
  0.76–0.79; healthy→disease false-fire ≤ 2.6.5.
- **nutrient/scorch — not improved:** recall *fell* (scorch 0.91→0.77, below floor;
  nutrient 0.94→0.89) at ~equal precision. The v7 boundary work bought healthy-side
  conservatism, not the targeted precision gain.

Net: **2.6.9 = safety + powdery *capability*, not a weak-class *accuracy* ship.** The
nutrient/scorch precision goal stays open → v8.

## v8 — next version (model 2.6.10)

Two problems block a real weak-class win and a trustworthy gate; v8 fixes the data,
**2.6.10 retrains** (`MODEL_VERSION` bumped to 2.6.10 in §1).

**Why v7 can't prove a weak-class gain:**
1. **Non-independent eval** — 71% recycled from v4 (scorch/nutrient positives 100%,
   gray_mold 91%); version-over-version reads on those classes are circular.
2. **Unfloored positive noise** — the ≥0.80 floor hit the negatives + powdery, not the
   physiological positives (~30% still <0.80, min 0.55, eval-eligible).
3. **Agricultural-domain leak** — ≥8% of disease positives are crop imagery
   (nutrient 15%: tomato/rice/citrus), reviving the proxy the 2.6.7 removal fought.

**v8 plan (priority):**
1. **Purge agricultural/outdoor from disease positives** — crop-term blocklist + an
   "is it a potted/indoor houseplant?" re-judge; populate `subject_domain`/`subject_plant`.
   A net trim (on-plan for the precision-bound classes).
2. **Refresh recycled weak-class eval** — ~50–100 *new* houseplant positives each for
   nutrient + scorch, ~40 gray_mold, from new creators/genera (Stack Exchange/Reddit;
   folded production scans). Restores independence + targets the fallen recall.
3. **Extend the ≥0.80 floor to physiological positives** — route sub-0.80 disease
   positives **train-only** (don't delete: protects aqua's data-hungry volume).
4. **Thicken thin eval** — +50–100 powdery past its 14-query/54%-one-engine monoculture;
   broaden gray_mold via new multilingual sources. Don't grow nutrient/scorch count.
5. **Boundary negatives: hold ~700, don't grow**; keep `hn_kind=boundary` train-only;
   hold ~300 generic-healthy in *eval* (the pool shrank 362→305).
6. **Variegated-healthy boundary negatives** (Calathea/Maranta/Croton/Aglaonema/Begonia
   rex → `healthy/boundary`) vs the viral over-trigger; add a small clean-houseplant
   `leaf_spot` eval slice (leaf_spot/pests/viral have no leakage-free held-out today).
7. **Hygiene** — reject drawn-annotation/watermark/AI-looking images; add a cross-version
   pHash tag (`carried_from_vN`/`new`); fix the v7 folder README (still titled "v6").

**2.6.10 verify (paired, not aggregate):** paired bootstrap 2.6.9 vs 2.6.10 on the
*new* (non-recycled) v8 eval rows — nutrient/scorch precision up with recall back
≥ 0.80, healthy false-fire not up, powdery held. Keep aqua KD val-macro-F1 a watch-item.

## Standing disciplines

**Safety asymmetry (the priority).** The harm is a *confident wrong diagnosis* — calling
a healthy plant diseased, or diagnosing a non-plant/blur. Prefer precision + "I'm not
sure" over recall; a false disease alarm is worse UX than a missed mild disease. Keep +
tune the runtime guards (`pipeline.py`: blur gate, OOD AND-gate, conformal sets,
weak-healthy reroute, low-confidence demotion). Judge a version by false-disease-fire
rate on healthy/boundary inputs ↓, healthy P/R high, conformal coverage ≥ target,
OOD/blur still reject junk.

**Cross-version A/B — never trust a raw disease-F1 delta.** Each version's `val_realworld`
drifts in composition and is partly drawn from its own supplement, so a small headline
gap is eval-set drift, not a model change (the v2.6.0↔v2.6.7 "regression" and the
2.6.5↔2.6.9 crossover above were both this). There is **no fixed never-trained benchmark**
by choice — it would steal scarce weak-class data. Judge by within-run per-class P/R CIs
(`model_card.json`) + safety behaviour; for a head-to-head, run a one-off paired bootstrap
on whatever common held-out exists (`tools/eval_bootstrap.py`).

**Protect the on-device student (aqua).** `aqua` (MobileNetV3-Small, distilled from
scratch) is **data-hungry**, unlike the near-data-free frozen-DINOv2 `gamma`. The same
hardening that helps gamma precision (trimming positives, adding hard negatives) starves
the student. Keep a weak-class volume floor for the student (or distill it on a superset
that retains the trimmed positives, train-only); treat aqua KD val-macro-F1 as a release
watch-item.

**val_studio gap.** PlantVillage transfer monitor, **non-blocking**; covers only 4 of 9
classes. Read `supported_disease_macro_f1` (over present classes, ≈0.99), not the
all-class number (≈0.43 from absent-class zeros). Don't backfill with agricultural studio.

**Eval-integrity (mandatory).** The model never grades/calibrates on its own confident
calls: every `auto_yes` row is **train-only**; held-out splits draw only from
vision-agent-judged rows (deterministic ~20% by sha → calibration, rest eval). Near-dup
sweep on fold-in (pHash ≤ 6; provenance `agent_vision` > `model+user` > `model`).

## Production signal loop (server, no review)

Recorded post-response (consent-gated, anonymized: JPEG q88, EXIF/GPS stripped, ≤1024 px;
only linkage `scan_id`, GDPR-swept):
- **Scans:** every `/api/analyze` except `not_a_plant`/blur; uncertain (`pending`) + all
  disease always kept, confident healthy sampled at 0.1; `pending` expires 90 days.
- **Feedback:** negative → re-opens `pending` under `user_feedback` at the corrected
  class; positive → promotes to `auto_yes`.
- **Export:** `tools/export_data.py` → one `tar.gz` of `labeling.db`/`feedback.db`/
  `community.db` (read-only); maintainer judges offline + folds in.

## Risks

- **Calibration drift** — re-fit every ship (§6), from judged production items only.
- **Labels are a proxy** — agent-judged, not human ground-truth (by design).
- **Consent/regulatory** — recording is consent-gated + anonymized; `privacy.html` +
  the in-app consent screen are the canonical disclosure.
