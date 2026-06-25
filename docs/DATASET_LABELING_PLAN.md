# Dataset & labeling plan — local collection + offline export

> **Architecture change (2026-06-20): the human admin labeling queue was
> removed.** There is no `/api/label/*` review surface, no claim/verdict/auth,
> no in-app admin tool. The server still *records* anonymized production scans +
> feedback (with the model's prediction as a **weak label prior**), but it is no
> longer reviewed on the server. Instead the maintainer **exports** all three
> stores offline (`server/tools/export_data.py`) and judges the images with the
> same vision-agent that grades scraped images. App scans — friends scanning
> known houseplants, and internet images scanned from the gallery — are now a
> first-class image source feeding that offline judge, alongside scraping.
> The sections below that describe a server review queue are superseded by §5.


**Supersedes** the scrape-on-the-server plan (this file's earlier revisions)
and the old human-labels-everything plan (`EVAL_DATASET_PLAN.md`, deleted).
The roadmap context (which classes/defects this work must fix and why) lives
in `docs/ROADMAP.md`.

**Two independent halves, by design:**

1. **Local dataset collection & judging (maintainer, offline).** Scrape →
   source disjointness → pHash dedup → per-creator cap → vision-agent judge →
   publish a Kaggle supplement. `train_notebook.ipynb` pulls that supplement
   **directly** — that is how v2.6.0 was built. None of this runs on the
   server.
2. **Server-side production-signal collection (no review).** The server records
   anonymized production scans + user feedback into `labeling.db`, each carrying
   the model's prediction as a weak label prior. There is **no** server review
   queue; the maintainer exports the store offline (`tools/export_data.py`) and
   folds it into the same local collect→judge pipeline as scraped images.

> **No server-side dataset loop — by design, not "later."** The Kaggle
> auto-fetch / version-gated ingestion, the model auto-router, the audit
> sampling, and the export-bundle builder were removed and are **not** a
> deferred server phase. Dataset collection and judging are done **locally**,
> where the maintainer has the tooling; the server's only data job is to
> record real user feedback + uncertain/disease scans for offline export. Claude checks
> the locally collected dataset when asked and keeps this plan in sync — it
> does not re-introduce server ingestion.

## The shape of it

```
   LOCAL (maintainer, offline)                  SERVER (production)
   ───────────────────────────                  ───────────────────
   scrape → disjointness → phash dedup          /api/analyze
   → per-creator cap → vision-agent judge            │ anonymized scan recording
        │  manifest.csv (verified_by=agent_vision)   ▼
        ▼                                        route_scan(): confident → auto_yes,
   Kaggle supplement                             suspicious → pending
   maxayyt5/houseplant-disease-eval-supplement        │
        │  pulled directly by                         ▼
        ▼                                        user feedback → top-priority pending
   train_notebook.ipynb ──► model bundle              │
        ▲                                             ▼
        │                                        tools/export_data.py (offline pull)
        │                                             │ all 3 stores → one archive
        └─────────────────────────────────────────────┘
   maintainer judges exported scans offline (vision-agent) + folds them in
```

## (1) Local collection — acceptance criteria (the code stays external)

The maintainer's scraping/judging pipeline is NOT in this repo, and is NOT on
the server; both consume its published output. For dataset versions to stay
consistent, every published version must satisfy:

- **Folder layout:** `<class>/<image>` per the 9 canonical classes; a
  `manifest.csv` with `filename, class, source_url, verified_by` (the vision
  agent writes `verified_by=agent_vision`, confidence optional).
- **Source disjointness (anti-leakage, mandatory):** no image may originate
  from a training corpus source. Banned origins: iNaturalist (any host),
  PlantDoc, PlantVillage, the Kaggle training slugs
  (`abdulahad0296`, `pappu54`, `usmanafzaal`, `guy007`,
  `alexanderuzhinskiy`), and Wikimedia/Wikipedia (label quality). A held-out
  image that is secretly a training image inflates the ship gate.
- **Image gates:** real photos, short side ≥ 384 px, pHash near-dup removal
  (Hamming ≤ 6), per-creator cap to bound correlated content.
- **Vision-agent judging:** every image is kept/rejected by the agent with
  exported reference exemplars (see §6) as its calibration anchors.
- **Domain gate (v2 onward):** subject must be a **houseplant / indoor
  plant** in a plausible indoor context. v1 is accepted as-is and contains
  agricultural/field imagery (grape Botrytis, orchard fruit) — a known
  limitation and a likely contributor to weak-class errors on real phone
  photos.
- **Hard negatives (v2 onward, ROADMAP-driven):** lookalike NEGATIVES (healthy
  plants with dry brown tips/edges; mild-chlorosis / pale new growth; non-plant
  brown papery textures; fallen leaves) — as of 2.6.7 the shipped model's #1
  defect is the **over-prediction (low precision) of `nutrient_deficiency`
  (P 0.68) and `leaf_scorch` (P 0.75)**, so it needs disambiguation negatives,
  not more positives (see "v6"). Place them under `healthy/` / `not_a_plant/`
  as appropriate, role-tagged `hn_kind` ∈ {scorch_lookalike, nutrient_lookalike}.

## v5 — shipped (model 2.6.7, 2026-06-26)

> Supplement **v5** (2019 rows) trained on the A100. **Ship gate PASS:**
> `val_realworld` disease macro-F1 **0.862** (int8) / **0.868** (fp32); INT8
> reclaimed (paired bootstrap int8−fp32 CI [−0.018, +0.005], **not significant**);
> healthy recall 0.901, healthy-bias ~0.95; every disease class ≥ 0.81 recall.
> **The v5 thesis held — gray_mold is fixed:** recall **0.830**, precision **0.830**
> on clean foliage, after the *agricultural* strawberry Botrytis proxy was removed
> entirely (gray_mold had been the binding class for five versions). Aggregate F1 is
> flat vs v4's 0.861 only because two classes now cap it. Calibration: T=0.761,
> conformal coverage 0.991 @ mean set size 1.90.

### v5 mechanism — what shipped (wired into `train_notebook.ipynb`)

- **Stable, class-aware held-out test split (`make_test_split`, §2).** Before the
  random `make_*_split` val carves, a deterministic, **group-aware** partition is moved
  to `val/test`: each `(source, plant_id)` group's side is fixed by a content hash of
  the group id, so a plant is *always* test or *always* train **across dataset
  versions** — a reproducible benchmark, disjoint from train AND from the drifting
  `val_*` splits. The carve fraction is **class-aware** (`ratio_by_class`): supply-bound
  classes give up less so the benchmark stays populated without starving their scarce
  train side — `gray_mold_botrytis` 0.08, `nutrient_deficiency` / `leaf_scorch` /
  `pests` / `powdery_mildew` 0.10, everything else the 0.18 default. *(Shipped:
  "test split populated … class-aware" + the §6 `final test` slice confirmed on the
  2026-06-26 run.)*
- **gray_mold: clean in-domain supply, no crop proxy.** The Kaggle strawberry
  fetcher/slug/weights were deleted; gray_mold now draws from iNat foliage (606, with
  333 non-foliage filtered out), the supplement (255), and DoctorP (96). Result: 339
  clean train rows and the 0.83/0.83 above.
- **Weak-class top-up via `kaggle_ornamental per_class_cap 300→600`.** Pulled the
  unused ornamental headroom (rose/hibiscus/money-plant) into scorch/viral/powdery/
  nutrient train + the carved test slice.

### v5-run outcome (model 2.6.7) — what v6 must react to

- **gray_mold SOLVED (R 0.830 / P 0.830).** No longer a collection target — keep
  production/iNat rows flowing but stop chasing it, and never re-add a crop-domain
  Botrytis proxy. The OOD **MaxLogit floor auto-disabled** this run (not_a_plant is now
  a strong enough *trained* class that its MaxLogit overlaps real plants, sep=−0.257) —
  not a regression; the k-NN embedding OOD + Stage-1 detector still gate rejection.
  Watch that the embedding OOD bank stays fit as not_a_plant strengthens.
- **NEW #1 defect — the two physiological classes OVER-PREDICT (low precision):**
  - `nutrient_deficiency`: recall **0.878** but precision **0.679** (predicted 106 for
    82 actual).
  - `leaf_scorch_underwatering`: recall **0.814** but precision **0.745** (predicted
    106 for 97 actual).
  Their F1s (0.77, 0.78) are the *only* sub-0.86 disease classes; they alone hold
  macro-F1 at 0.862. **Lift both precisions to ~0.85 → macro-F1 ≈ 0.89.** They confuse
  with each other and with healthy (all are yellowing/browning abiotic stress).
- **viral precision recovered (0.899)** — the v4 drift watch-item is resolved.
- **INT8 nutrient recall drop did NOT recur** — the per-class INT8-drift check is clean.

## v6 — next dataset version (the precision fix)

**Thesis: v6 is a precision run, not a volume run.** gray_mold (a volume problem) is
solved; the binding constraint is now the *over-prediction* of `nutrient_deficiency`
(P 0.68) and `leaf_scorch` (P 0.75). **Adding more positives makes this worse.** The
fix is disambiguation negatives + boundary sharpening across the three confused classes
(nutrient ↔ leaf_scorch ↔ healthy).

### Improvements (what v6 adds)

1. **Confusion-pair boundary negatives — the core lever (~150–200 imgs).**
   - *scorch-lookalike healthy*: healthy plants with dry brown leaf tips/edges →
     `healthy/` (teaches "brown tip ≠ scorch").
   - *nutrient-lookalike healthy*: healthy plants with mild chlorosis / pale new growth
     / natural variegation → `healthy/` (teaches "yellowing ≠ deficiency").
   - *cross negatives*: clear nutrient that is NOT scorch and clear scorch that is NOT
     nutrient, each judged against BOTH exemplar sets so the agent rejects the
     ambiguous middle that drives the false fires.
2. **Trim, don't grow, the physiological positives.** Do NOT add generic nutrient/scorch
   positives — recall is already high (0.88 / 0.81). If anything, cull the
   lowest-confidence / most-ambiguous existing positives.
3. **gray_mold: maintain only.** Fold production rows, but it is not a target.
4. **Notebook lever (paired, not dataset).** The LA prior gives nutrient/leaf_scorch a
   large rare-class boost (0.058 / 0.095) that trades precision for recall. Consider a
   modest reduction of their boost (or a small precision-weighted head term) and test
   **paired vs 2.6.7** on the stable slice — this may move precision more cheaply than
   data does.

### Priorities

| class | v5 state | v6 target | lever | gap risk |
|---|---|---|---|---|
| `nutrient_deficiency` | R 0.878 / **P 0.679** (over-fires) | **P ≥ 0.80**, R ≥ 0.85 | nutrient-lookalike healthy negatives + LA-prior trim | **HIGH** |
| `leaf_scorch_underwatering` | R 0.814 / **P 0.745** (over-fires) | **P ≥ 0.82**, R ≥ 0.80 | scorch-lookalike healthy negatives; do NOT add positives | **HIGH** |
| `healthy` (boundary) | strong (R 0.901 / P 0.950) | hold | add ONLY brown-tip + mild-chlorosis boundary cases | MED |
| `gray_mold_botrytis` | **SOLVED** (0.83 / 0.83) | maintain | passive production only; no crop proxy | LOW |
| `powdery_mildew` | strong train, **thin eval (n=69)** | tighten CI | more real powdery scans for the benchmark, not train | MED |
| production `local` rows | healthy-skewed | weak-class-first | export/judge nutrient/scorch boundary scans first | MED |

### Requirements (every v6 publish must satisfy)

- All §1 acceptance criteria (9-class layout, `manifest.csv`, **source disjointness**,
  pHash dedup ≤6, per-creator cap, houseplant domain gate, vision-agent judge).
- New negatives are **role-tagged** (`role=hard_negative`, `hn_kind` ∈
  {`scorch_lookalike`, `nutrient_lookalike`}) so the notebook + audits can find them.
- A boundary negative is kept ONLY if the agent would *reject* it for the lookalike
  positive class — judged against **both** confused-class exemplar sets.
- Keep the supplement eval-eligible; the stable `make_test_split` carve means the new
  negatives also thicken the held-out benchmark for the precision metric that matters.
- Re-confirm on the **stable `final test` slice** with a **paired bootstrap vs 2.6.7**.
  The win to verify is **nutrient + leaf_scorch precision up with recall not falling
  below ~0.80** — NOT aggregate F1 (which is noise-flat at this margin).

### Expected gaps (what v6 likely will NOT fully solve)

- **The precision/recall seesaw is intrinsic** to nutrient ↔ scorch ↔ healthy — they
  are genuinely similar on a phone photo. v6 can over-correct (precision up, recall
  collapses). Tune negatives in small increments; always read BOTH P and R with CIs
  (n≈82/97 → ±0.08).
- **The production loop stays healthy-skewed** — it thickens `healthy` fastest; weak-class
  positives still need deliberate capture.
- **Labels stay a proxy** — agent-judged, not human ground-truth (by design).

### Unchanged from prior versions

9 classes stay (no structural add — see `CLASS_COVERAGE_RESEARCH.md`); hard houseplant
domain gate; hard-negatives split-tagged (`role`/`hn_kind`); no per-row license
requirement; `agent_vision` + offline judge only. INT8 ships, and the **per-class
paired INT8-vs-FP32 drift check is now wired into the §6 gate and ran clean** on 2.6.7
(the earlier `nutrient_deficiency` INT8 recall drop did not recur). The
`viral_disease`-precision drift watch-item is **resolved** (0.899 this run).

> **Training-time use:** the supplement is pulled directly by `train_notebook.ipynb`
> §2 (`fetch_kaggle_supplement`, source `supplement`) and is **eval-eligible** — a
> held-out fraction seeds `val_realworld` / `val_indomain` / the calibration splits,
> thickening the thin weak-class eval slices. It reads whatever canonical-class folders
> ship, so the `healthy/` + `not_a_plant/` hard-negative folders are picked up
> automatically. Because rows are agent-judged, the gate measured on them is a **proxy**
> until verified production volume exists. As of v5 the supplement (and the other
> real-photo sources) are *also* carved by `make_test_split` into the stable `val/test`
> slice *before* the val splits, so a fixed class-aware fraction is held out as the
> cross-version benchmark while the remainder feeds train + the drifting `val_*`.

## (2) No server ingestion — by design

There is **no** server-side dataset path: no Kaggle auto-fetch, no version
polling, no `server/ingest/`, no model auto-router (`classify_candidate`), no
audit sampling. The training notebook consumes the Kaggle supplement directly
(§1), and the server independently records production scans + feedback (§3–5).
This is the intended end state, not a stub awaiting a "server phase." If a
server-side ingestion loop is ever wanted again it would be a deliberate new
design, not a revival of the removed code.

## (3) Production scan recording — anonymized

Every `/api/analyze` result except `not_a_plant` (which also covers the blur
gate's synthetic NAP) is recorded AFTER the response is sent
(BackgroundTasks; zero hot-path latency), subject to consent + caps:

- **Anonymization by construction:** the decoded image is re-encoded
  (JPEG q88, no EXIF — GPS/metadata dropped), downscaled to ≤1024 px. No
  user id, IP, or filename is ever passed in. The ONLY linkage is the
  response `scan_id`; the user↔scan_id join lives solely in `feedback.db`
  and is swept by the GDPR delete cascade.
- **Routing:** `route_scan()` over the already-computed result — singleton
  conformal set == top-1 ⇒ `auto_yes`; otherwise `pending` (suspicious →
  human queue). `auto_no` is impossible for scans (the model's own
  prediction is the proposed class).
- **Value-driven caps:** model-uncertain (`pending`) scans **and every disease
  class** (the high training signal) are always kept; only the abundant easy
  class (`healthy`) is sampled at `scan_common_keep_rate` (0.1) by SHA;
  `not_a_plant` is dropped upstream. Per-class daily cap
  (`scan_daily_cap_per_class`) + global `scan_max_images` bound disk.
- **Retention / prune order (protects hard cases):** unreviewed `pending`
  scans die after `scan_retention_days` (90); over the global cap, redundant
  `auto_yes` common-class scans are pruned first, then weak-class
  `auto_yes`; `user_feedback` rows and human-verdict rows are NEVER pruned.
- **Consent:** disclosed via the blocking first-launch consent page
  (Android onboarding; upgrade installs get a one-time blocking dialog) and
  `website/privacy.html`.

## (4) Feedback → the collection store

- Negative feedback re-opens the recorded candidate (even an `auto_yes` one)
  as `pending` under `source='user_feedback'`, retargeted at the user's
  corrected class, carrying their note — the canonical false-positive signal
  and the one place a user supplies a ground-truth label. Exported for offline
  judging like every other row.
- Positive feedback stamps `user_correct=1` and promotes a still-`pending`
  scan to `auto_yes` when the user confirms the model's own prediction.
- If the scan was never recorded (old install / recording off / disputed
  NAP), the negative-feedback image upload builds the candidate from the
  uploaded bytes via the same anonymize helper. **Known gap:** negative
  feedback with no image and no recorded scan has no pixels — nothing can be
  recorded.
- **GDPR:** `/api/community/user/{id}` delete cascades community rows,
  feedback rows AND scan candidates keyed by that user's scan_ids.
  Plain `user_scan` rows are unreachable by design (no user linkage exists).

## (5) No human labeling queue — offline export + judge

The server-side review queue was removed (2026-06-20). The server only
**records** scans + feedback; the maintainer pulls everything off the box with
`server/tools/export_data.py` and judges the images offline with the same
vision-agent that grades scraped images. App scans are just another image
source for that judge — including friends scanning known houseplants and
internet images scanned from the gallery.

**Value-driven collection (no config, baked into `_record_scan_candidate`):**
model-uncertain (`pending`) scans **and every disease class** are always kept
(the high training signal); only confident `healthy` is sampled at
`scan_common_keep_rate`; `not_a_plant`/blur are dropped upstream. The weak
classes (gray_mold, nutrient, leaf_scorch) therefore land in full, but so do
all other disease scans — the export is a broad disease corpus, judged offline.

**Export contents** (`tools/export_data.py`): `labeling.db` (scans + feedback
corrections + images, model prediction as weak prior), `feedback.db` (raw
corrections + the user_pseudo_id↔scan_id join), `community.db` (community scans
+ opinions + thumbnails) → one timestamped `tar.gz` of JSONL + images + a
manifest. Read-only; no network.

## (6) Exported production rows → next dataset version (local / manual)

There is **no server HTTP export endpoint**; the maintainer runs
`tools/export_data.py` on the box to pull all three stores, then judges the
exported scans offline (vision-agent) before folding the kept images into the
local collection (§1). The model's prediction travels as a **weak prior** only
— the offline judge assigns the real label, exactly as for scraped images. The
rules below are what that local step must honour so production signal can be
reused without corrupting the eval:

- **What a verified row carries:** `class, source, source_url, subject_plant,
  sha256, phash`, plus the model prior (`model_pred`/`model_conf`/`status`).
  After the offline judge rules, `verified_by=agent_vision` (or `model+user`
  where positive feedback confirmed the model). No user data leaves the box
  beyond the anonymized image + the pseudo_id↔scan_id join needed for GDPR.
- **Eval-integrity rule (mandatory):** the model must never grade or calibrate
  itself on its own confident calls. Every `auto_yes` row (`verified_by` model
  or model+user) is **train-only**. Held-out splits draw ONLY from
  vision-agent-judged rows: a deterministic ~20% (by sha) of judged
  **production** scans is calibration (real phone photos are exactly what
  temperature scaling + RAPS need), the rest is eval. The training notebook's
  group-split keeps eval source-disjoint from train.
- **Near-dup sweep:** pHash Hamming ≤ 6 when merging into §1; strongest
  provenance wins (`agent_vision` > `model+user` > `model`).
- **Hard negatives:** negative-feedback rows (user-confirmed false positives)
  are kept as hard negatives for the vision-agent judge's reference *rejects*,
  not mixed into positive folders.
- **Closing the loop (local):** verified positives become reference exemplars
  for the §1 vision-agent judge and their `sha256`/`phash` become the scrape
  dedup index (never re-collect what's judged).

## Verification

- Server: `ruff check .` + `pytest tests -q` (scan recording, `route_scan`,
  the value-driven keep gate, feedback fold-back, the collection store and the
  offline export are covered — see `tests/test_scan_recording.py`,
  `tests/test_feedback_labeling_wiring.py`, `tests/test_labeling.py`,
  `tests/test_export_data.py`). The `/api/label/*` routes no longer exist
  (`tests/test_openapi_contract.py` pins the path set).
- Android: `./gradlew lintDebug testDebugUnitTest assembleRelease`; manual —
  fresh install ends onboarding on the consent page (Skip jumps to it,
  Agree required).
- Live: scan → row in `candidates` with `source='user_scan'` carrying
  `model_pred`; negative feedback → row flips to `source='user_feedback'`.
- Export: `python -m server.tools.export_data` → `tar.gz` of all three stores.

## Risks

- **Calibration drift** — trust rides on temperature/conformal staying
  calibrated; both are re-fit on every model ship (notebook §6), and
  calibration draws only from human-verified production items.
- **Model blind spots** — mitigated by negative feedback always reaching the
  queue at top priority and suspicious scans always being kept.
- **Consent/regulatory** — recording is consent-gated and anonymized;
  `website/privacy.html` and the in-app consent screen are the canonical
  disclosure.

## Maintainer actions (outside this repo)

- **v5 shipped as model 2.6.7 (2026-06-26) — gray_mold fixed; v6 is next.** The
  concrete **v6 path** is the "v6 — next dataset version" section above; in one line:
  it's a **precision** run — add ~150–200 confusion-pair boundary negatives
  (scorch-lookalike + nutrient-lookalike healthy) to cut the over-prediction of
  `nutrient_deficiency` (P 0.68) and `leaf_scorch` (P 0.75); do NOT add positives.
- Collect and judge **locally** (scrape → disjointness → pHash dedup →
  per-creator cap → vision-agent judge, §1), publish the supplement to Kaggle,
  and the **training notebook pulls it directly** on the next run. The server
  is never in this path.
- Fold human-verified production rows from `labeling.db` into the next local
  version under the §6 eval-integrity rules.
