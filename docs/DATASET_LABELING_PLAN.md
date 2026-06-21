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
- **Hard negatives (v2 onward, ROADMAP-driven):** ~300 scorch-lookalike
  NEGATIVES (healthy plants with dry brown tips/edges; non-plant brown
  papery textures; fallen leaves) — the shipped model's #1 defect is
  leaf_scorch PRECISION (0.52), so it needs lookalike negatives, not more
  positives. Place them under `healthy/` / `not_a_plant/` as appropriate.

## v5 — next dataset version (plan + expected gaps)

> The live supplement is **v4** (1951 rows); it **trained as v2.6.6** — gate passed
> (`val_realworld` F1 0.861, flat vs v2.6.5) and **INT8 reclaimed**, but it was a
> lateral move with one clear data lesson (below). Trajectory: v2 fixed eval-volume
> → v3 hardened leaf_scorch precision → v4 added nutrient negatives + first
> production signal **but over-corrected leaf_scorch**. v5 fixes leaf_scorch,
> finishes nutrient, and leans on the production loop for gray_mold.

### v4-run outcome (model v2.6.6) — what v5 must react to

- **leaf_scorch regressed (F1 0.805→0.719; P 0.774→0.703, R 0.840→0.736).** v4 grew
  healthy hard-negatives +114 but only +31 *boundary* — the +57 *generic* lush-green
  negatives pushed the model to under-call scorch. **v5: add ONLY boundary brown-tip
  negatives; do NOT add generic healthy, and consider trimming some of v4's.**
- **nutrient precision flat (0.743→0.750).** Removing the rice `kaggle_nutrient` +
  `coleaf` was net-neutral — they were wrong-domain dead weight, not the cause.
  Nutrient is a *boundary* problem → boundary negatives stay the priority.
- **gray_mold precision rose (0.840→0.888)** despite flat supply, but CIs stay wide
  (n=94, 31% conf<0.80) → production scans remain the lever.
- **powdery recovered (P 0.875→0.939)** — that drift watch-item is resolved.
- **viral precision dipped (0.954→0.814)** — partly eval-slice noise (n 219→150); watch.
- **aqua student regressed (0.838→0.780)** — a distillation issue, not data; out of v5 scope.

### Priorities

| class | v4 state | v5 target | lever | gap risk |
|---|---|---|---|---|
| `gray_mold_botrytis` | 232 positives, **31% conf<0.80** | **250 clean** (≥0.80) | **production scans, not web** — open-web is at its ceiling; deliberately scan gray-mold cases and fold the high-conf `local` rows in | **HIGH** |
| `nutrient_deficiency` | only **+31 boundary** negatives in v4 | **+~100 boundary** healthy↔nutrient + scorch↔nutrient | targeted web collection of brown-tipped / chlorotic-lookalike healthy (boundary, not generic) | LOW |
| `leaf_scorch_underwatering` | 406; **regressed in v4 run** (P→0.703, R→0.736) | **recover** P≥0.75 + R | *fewer* generic healthy negatives — the v4 over-correction is the cause | **HIGH** |
| `healthy` / `not_a_plant` (HN) | 528 / 376 (only 166 boundary) | **trim generic, keep boundary** | drop some of v4's +57 generic lush-green negatives; add only brown-tip boundary | MED |
| production `local` rows | 73 @ median conf 0.90, 66/73 healthy | grow **weak-class-first** | export/judge prioritising gray_mold/nutrient/scorch scans over the healthy majority | MED |

### Expected gaps (what v5 likely will NOT fully solve)

- **gray_mold supply is the structural gap.** Open-web scraping hit its ceiling at
  ~232; production accrual is slow (1 gray_mold `local` row so far). Without
  *deliberate* gray-mold scanning by the maintainer + friends, v5 gray_mold stays
  short of 250 and keeps its wide CIs. This is the #1 unsolved item.
- **The production loop is healthy-skewed.** Real scans are mostly healthy plants,
  so the loop thickens `healthy` far faster than the disease classes. It only
  feeds the weak classes if disease cases are deliberately captured.
- **Labels stay a proxy.** Production scans are model-pseudo-labelled then
  agent-judged offline — still not human ground-truth (by design, no in-app
  truth capture). Acceptable, but the ship gate measured on agent-judged rows
  remains a proxy.
- **leaf_scorch is now a balance problem, not a volume one** — v5 can over-correct the
  other way (too few healthy negatives → scorch over-triggers again). Tune the boundary
  negatives carefully; this is the class most likely to seesaw between runs.

### Unchanged from prior versions

9 classes stay (no structural add — see `CLASS_COVERAGE_RESEARCH.md`); hard
houseplant domain gate; hard-negatives split-tagged (`role`/`hn_kind`); no per-row
license requirement; `agent_vision` + offline judge only. INT8 is resolved (the v4
run shipped INT8). Remaining drift watch-item: `viral_disease` precision (0.814,
eval-slice-confounded) — confirm on the next run before acting.

> **Training-time use:** the supplement is pulled directly by
> `train_notebook.ipynb` §2 (`fetch_kaggle_supplement`, source `supplement`) and
> is **eval-eligible** — a held-out fraction seeds `val_realworld` /
> `val_indomain` / the calibration splits, thickening the thin weak-class eval
> slices. It reads whatever canonical-class folders ship, so the `healthy/` +
> `not_a_plant/` hard-negative folders are picked up automatically. Because rows
> are agent-judged, the gate measured on them is a **proxy** until verified
> production volume exists.

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

- **v4 is published (1951 rows, 2026-06-21) but not yet trained — train it next.**
  The concrete **v5 path** is the "v5 — next dataset version" section above; in one
  line: feed gray_mold from *production scans* (open-web is at its ceiling) and add
  ~100 boundary healthy↔nutrient / scorch↔nutrient negatives.
- Collect and judge **locally** (scrape → disjointness → pHash dedup →
  per-creator cap → vision-agent judge, §1), publish the supplement to Kaggle,
  and the **training notebook pulls it directly** on the next run. The server
  is never in this path.
- Fold human-verified production rows from `labeling.db` into the next local
  version under the §6 eval-integrity rules.
