# Dataset & labeling plan — local collection + offline export

The data loop is **local and offline**. The maintainer scrapes + judges images
into a Kaggle supplement; `train_notebook.ipynb` pulls it directly. The server
only *records* anonymized production scans + feedback for offline export — there
is **no server review queue, no server ingestion** (removed 2026-06-20).
Roadmap context (which classes to fix and why) lives in `docs/ROADMAP.md`;
per-run metrics in `server/models/MODEL_CARD.md`.

```
  LOCAL (maintainer, offline)                 SERVER (production)
  ───────────────────────────                 ───────────────────
  scrape → source-disjointness → pHash dedup  /api/analyze → anonymized scan record
  → per-creator cap → vision-agent judge      user feedback → correction (weak label)
       │ manifest.csv (verified_by=agent_vision)       │
       ▼                                               ▼
  Kaggle supplement  ◄──── fold judged ──────  tools/export_data.py (offline pull)
  (maxayyt5/houseplant-disease-eval-supplement)
       │ pulled directly by
       ▼
  train_notebook.ipynb → model bundle
```

## The supplement is BOTH supply and eval

The supplement feeds two roles in one publish (no separate test carve):

- **Supply (train):** every canonical-class folder it ships becomes train data,
  including the `healthy/` + `not_a_plant/` hard-negative folders.
- **Eval (held-out):** `train_notebook.ipynb` §2 holds out a deterministic
  fraction (by content hash) into `val_realworld` / `val_indomain` and the
  `temperature_cal` / `conformal_cal` splits, thickening the thin weak-class
  eval slices. Group-aware splitting keeps held-out source-disjoint from train.

Because rows are agent-judged (`verified_by=agent_vision`), the gate measured on
them is a **proxy** until verified production volume exists.

## Acceptance criteria (every published version)

- **Layout:** `<class>/<image>` over the 9 canonical classes + a `manifest.csv`
  (`filename, class, source_url, verified_by`).
- **Source disjointness (mandatory anti-leakage):** no image from a training
  corpus source. Banned: iNaturalist, PlantDoc, PlantVillage, the Kaggle
  training slugs (`abdulahad0296`, `pappu54`, `usmanafzaal`, `guy007`,
  `alexanderuzhinskiy`), Wikimedia/Wikipedia. A held-out image that is secretly
  a training image inflates the gate.
- **Image gates:** real photos, short side ≥ 384 px, pHash near-dup removal
  (Hamming ≤ 6), per-creator cap to bound correlated content.
- **Domain gate:** subject is a **houseplant / indoor plant** in a plausible
  indoor context (no agricultural/field imagery).
- **Vision-agent judging:** kept/rejected by the agent against exported
  reference exemplars; a boundary negative is kept ONLY if the agent would
  *reject* it for the lookalike positive class (judged vs **both** confused-class
  exemplar sets).
- **Hard negatives, role-tagged** (`role=hard_negative`, `hn_kind` ∈
  {`scorch_lookalike`, `nutrient_lookalike`}), filed under `healthy/` or
  `not_a_plant/` so the notebook + audits can find them.

## v5 — shipped (model 2.6.7, 2026-06-26)

`gray_mold_botrytis` **fixed** on clean foliage (R/P **0.83/0.83**) after the
agricultural strawberry-Botrytis proxy was removed — it had been the binding
class for five versions. `val_realworld` disease macro-F1 **0.862** (int8),
healthy recall 0.901, every disease class ≥ 0.81 recall. Aggregate is flat vs v4
only because two classes now cap it (see v6). Full numbers: `MODEL_CARD.md`.

## v6 — next version (a precision run, not a volume run)

The binding constraint is the **over-prediction (low precision)** of the two
physiological classes; adding more positives makes it worse.

| class | v5 state | v6 target | lever |
|---|---|---|---|
| `nutrient_deficiency` | R 0.878 / **P 0.679** | **P ≥ 0.80**, R ≥ 0.85 | nutrient-lookalike healthy negatives (+ LA-prior trim) |
| `leaf_scorch_underwatering` | R 0.814 / **P 0.745** | **P ≥ 0.82**, R ≥ 0.80 | scorch-lookalike healthy negatives; **no new positives** |
| `healthy` (boundary) | R 0.901 / P 0.950 | hold | add ONLY brown-tip + mild-chlorosis boundary cases |
| `powdery_mildew` | strong train, **thin eval (n=69)** | tighten CI | more real powdery houseplant scans for the benchmark |
| `gray_mold_botrytis` | **solved** (0.83/0.83) | maintain | passive production only; never re-add a crop proxy |

**What v6 adds:**

1. **Confusion-pair boundary negatives — the core lever (~150–200 imgs).**
   scorch-lookalike healthy (dry brown tips/edges → `healthy/`), nutrient-
   lookalike healthy (mild chlorosis / pale new growth / natural variegation →
   `healthy/`), and cross negatives (clear nutrient that is NOT scorch, and vice
   versa) judged against **both** exemplar sets to reject the ambiguous middle.
2. **Trim, don't grow, the physiological positives** — recall is already high;
   if anything, cull the lowest-confidence existing positives.
3. **Notebook lever (paired, not data):** the LA prior gives nutrient/leaf_scorch
   a large rare-class boost (0.058 / 0.095) that trades precision for recall.
   Test a modest reduction **paired vs 2.6.7** on `val_realworld` — it may move
   precision more cheaply than data.

**Verify the right thing:** nutrient + leaf_scorch **precision up with recall
not below ~0.80**, via a **paired bootstrap vs 2.6.7** on `val_realworld`
(`tools/eval_bootstrap.py`) — NOT aggregate F1 (noise-flat at this margin;
n≈82/97 → ±0.08). The precision/recall seesaw across nutrient ↔ scorch ↔ healthy
is intrinsic; tune negatives in small increments and read BOTH P and R with CIs.

## Improve & extend the supplement (advice)

The supplement is the main lever for the weak classes — the per-class train
supply is small (v5: gray_mold 339, nutrient 636, scorch 921; eval slices are
thinner still: powdery 69, nutrient 82). Concrete extensions, in priority order:

1. **Boundary negatives first (v6 core).** Biggest precision win per image; cheap
   to scrape (healthy plants with cosmetic browning/yellowing are common).
2. **Thicken thin EVAL classes** (`powdery_mildew`, `nutrient_deficiency`,
   `gray_mold_botrytis`) with real **houseplant** scans so the held-out CIs
   tighten — eval coverage, not just train volume.
3. **Fold offline-judged production scans, weak-class first** (`tools/export_data.py`
   → vision-agent judge). Real phone photos are the best calibration + eval data;
   the production loop is healthy-skewed, so weak-class positives still need
   deliberate capture.
4. **Keep extending source diversity, not just count** — new creators/genera
   under the per-creator cap beat more frames of the same plant (correlated
   content is dedup'd by pHash anyway). Respect source disjointness.
5. **Don't chase** more `not_a_plant` (P 0.97) or `pests` (P 0.97) — already
   strong; and never re-add agricultural/crop-domain proxies.

## val_studio coverage gap — accounted for

`val_studio` is the PlantVillage transfer monitor (**non-blocking**, never gates
ship). PlantVillage maps to only **4** of the 9 classes
(`healthy`, `leaf_spot`, `powdery_mildew`, `viral_disease`), so `gray_mold`,
`leaf_scorch`, `nutrient_deficiency`, `pests`, `not_a_plant` have **zero studio
supply** — structural to that agricultural dataset, not a collection failure.

- **Reporting fix (shipped in the notebook):** the plain `disease_macro_f1`
  averages those absent classes in as hard zeros (≈0.43, misleading). The
  notebook now also reports **`supported_disease_macro_f1`** — macro-F1 over only
  the classes present in the slice — which is the honest studio number (≈0.99 on
  2.6.7). Use the *supported* number for studio; ignore the all-class one.
- **Do NOT** try to backfill studio coverage for the missing classes with more
  agricultural studio data — wrong domain, and studio never blocks. If a clean
  studio-style **houseplant** source ever appears it could widen coverage, but
  it is low priority.

## Production signal loop (server, no review)

Recorded after the response (BackgroundTasks; zero hot-path cost), consent-gated
and anonymized by construction (re-encoded JPEG q88, EXIF/GPS stripped, ≤1024 px;
the only linkage is `scan_id`, swept by the GDPR delete cascade):

- **Scans:** every `/api/analyze` result except `not_a_plant`/blur. Value-driven
  caps — model-uncertain (`pending`) scans **and every disease class** are always
  kept; confident `healthy` is sampled at `scan_common_keep_rate` (0.1). Per-class
  daily + global caps bound disk; unreviewed `pending` die after 90 days;
  `user_feedback` + human-verdict rows are never pruned.
- **Feedback:** negative feedback re-opens the candidate as `pending` under
  `source='user_feedback'` retargeted at the corrected class (the canonical
  false-positive signal + the one user-supplied ground truth); positive feedback
  promotes a pending scan to `auto_yes`. No image + no recorded scan ⇒ nothing to
  record (known gap).
- **Export:** `tools/export_data.py` pulls all three stores (`labeling.db`,
  `feedback.db`, `community.db`) into one timestamped `tar.gz` (read-only, no
  network). The maintainer judges them offline and folds the kept images into the
  local collection.

**Eval-integrity rule (mandatory):** the model must never grade or calibrate
itself on its own confident calls. Every `auto_yes` row (model / model+user) is
**train-only**; held-out splits draw ONLY from vision-agent-judged rows (a
deterministic ~20% by sha of judged **production** scans → calibration, the rest
eval). Near-dup sweep on fold-in (pHash ≤ 6, strongest provenance wins:
`agent_vision` > `model+user` > `model`).

## Verification

- Server: `ruff check .` + `pytest tests -q` (scan recording, `route_scan`, the
  value-driven keep gate, feedback fold-back, export — `tests/test_scan_recording.py`,
  `tests/test_feedback_labeling_wiring.py`, `tests/test_labeling.py`,
  `tests/test_export_data.py`; `tests/test_openapi_contract.py` pins that
  `/api/label/*` no longer exists).
- Android: `./gradlew lintDebug testDebugUnitTest assembleRelease`; fresh install
  ends onboarding on the consent page.
- Export: `python -m server.tools.export_data` → `tar.gz` of all three stores.

## Risks

- **Calibration drift** — re-fit every ship (notebook §6); calibration draws only
  from judged production items.
- **Labels stay a proxy** — agent-judged, not human ground-truth (by design).
- **Consent/regulatory** — recording is consent-gated + anonymized;
  `website/privacy.html` + the in-app consent screen are the canonical disclosure.
