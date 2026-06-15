# Dataset & labeling plan — the closed data loop

**Supersedes** the scrape-on-the-server plan (this file's previous revision)
and the old human-labels-everything plan (`EVAL_DATASET_PLAN.md`, deleted).
**Goal:** a self-reinforcing loop — offline collection → Kaggle dataset →
server ingestion → production scan recording → user feedback → human
verification of the uncertain minority → export → next dataset version →
retrain — with hard eval-integrity guarantees and near-zero wasted human
effort. The roadmap context (which classes/defects this loop must fix and
why) lives in `docs/ROADMAP.md`.

## The loop, end to end

```
            (1) OFFLINE COLLECTION (maintainer, external)
   scrape → disjointness → phash dedup → per-creator cap → vision-agent judge
                         │  manifest.csv, verified_by=agent_vision
                         ▼
            (2) KAGGLE DATASET  maxayyt5/houseplant-disease-eval-supplement
                         │  server polls version; ingests on change only
                         ▼
            (3) SERVER INGESTION  (Kaggle-only; model auto-routes)
                auto_yes (confident) / pending (human) / auto_no (kept!)
                         ▼
            (4) PRODUCTION SCANS  /api/analyze → anonymized recording
                route_scan(): confident → auto_yes, suspicious → pending
                         ▼
            (5) USER FEEDBACK  → top-priority pending cards
                         ▼
            (6) HUMAN LABELING  (3-source queue + audit samples)
                         ▼
            (7) EXPORT BUNDLE  (split-aware manifest, hard negatives)
                 │ admin downloads, uploads to Kaggle manually
                 └────────► feeds (1): dedup index + judge exemplars
```

## (1) Offline collection — acceptance criteria (the code stays external)

The maintainer's scraping/judging pipeline is NOT in this repo; the server
only consumes its published output. For dataset versions to stay consistent,
every published version must satisfy:

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
  the exported reference exemplars (see §7) as its calibration anchors.
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

### Audited state of the live supplement — v2 (audited 2026-06-15)

`maxayyt5/houseplant-disease-eval-supplement` **v2**: 1647 images, every row
`verified_by=agent_vision`, manifest now also carries a `subject_domain` column.
Measured against the §1 gates:

| class | n | ≥384px | domain (subject_domain / sniff) | verdict |
|---|---|---|---|---|
| `nutrient_deficiency` | 617 | 100% | 402 houseplant / 110 crop / 30 orchard / rest | ✅ eval volume met; ~25% crop residue |
| `leaf_scorch_underwatering` | 501 | 100% | ~90% houseplant (boundary positives, e.g. calathea/dracaena crispy tips) | ✅ good positives; ~20 crop |
| `not_a_plant` | 249 | 100% | brown straw/hay/burlap/cardboard/fallen-leaf textures | ✅ **excellent scorch-lookalike negatives** |
| `healthy` | 160 | 100% | houseplant — but ~150 generic lush-green, only ~10 brown-tipped | ⚠️ **generic healthy, not the boundary negative** |
| `gray_mold_botrytis` | 120 | 100% | 120 tagged houseplant (de-cropped) | ⚠️ clean now but **thin (was 239)** |

pHash dedup clean (0 near-dups/class); resolution gate 100%.

**Resolved since v1:** banned-origin rows removed (0, was 18); `gray_mold`
de-cropped (was 105/239 grape/strawberry); `subject_domain` column added;
`not_a_plant` brown-texture negatives added; `nutrient_deficiency` eval volume
met and now ~65% houseplant.

### v2.6.0 run outcome — supplement v2's first training use (2026-06-15)

The aqua run (which also exports the DINOv2 teacher as the production
`gamma` bundle) trained on supplement v2 and **PASSED the ship gate** —
disease macro-F1 **0.865** on `val_realworld` (n=2213), INT8 drift
−0.0014 (paired bootstrap not significant). The supplement worked as
designed: it grew the weak-class eval 3–5× and recovered the two acute
v2.5.0 defects.

| class | precision v2.5.0 → v2.6.0 | recall v2.5.0 → v2.6.0 | eval n 2.5.0 → 2.6.0 |
|---|---|---|---|
| `leaf_scorch_underwatering` | 0.52 → **0.71** | 0.76 → 0.86 | 45 → 121 |
| `nutrient_deficiency` | 0.74 → 0.72 | 0.80 → **0.92** | 25 → 122 |
| `gray_mold_botrytis` | 0.75 → 0.84 | 0.81 → 0.86 | 53 → 71 |

The not_a_plant brown-texture + scorch boundary negatives paid off
(leaf_scorch precision +0.19); nutrient eval volume target is met and its
recall jumped to 0.92.

**Remaining gaps for v3 (precision- and supply-driven now, not eval-volume):**
1. **gray_mold supply — the top v3 data priority.** Thinnest eval (n=71)
   and thinnest supplement (120, below v1's 239), so its CIs stay the
   widest of any class. Backfill foliar houseplant Botrytis toward ≥250
   (genuinely scarce; iNat genus + DoctorP are the realistic supply).
2. **Residual over-triggering on the two recovered classes.** Both still
   predict more than their support (leaf_scorch 146 predicted vs 121
   actual; nutrient 156 vs 122 → P ≈ 0.72). The fix is *disambiguating
   negatives*, not more positives: healthy-with-dry-brown-tips for scorch,
   and scorch↔nutrient / healthy↔nutrient boundary cases for nutrient.
3. **Minor data hygiene:** ~25% of `nutrient_deficiency` and a few
   `leaf_scorch` rows are crop, not houseplant; `subject_domain` is blank
   on all `leaf_scorch` rows; `license` is empty on ~98% of rows while the
   export hard-codes `CC0-1.0` (`labeling.py`).

> **Training-time use (v2.6):** the same supplement is pulled directly by
> `train_notebook.ipynb` §2 (`fetch_kaggle_supplement`, source `supplement`) and
> is **eval-eligible** — a held-out fraction seeds `val_realworld` /
> `val_indomain` / the calibration splits, thickening the thin weak-class eval
> slices (realized in the v2.6.0 run: nutrient_deficiency 25 → 122,
> leaf_scorch 45 → 121, gray_mold 53 → 71). It reads whatever
> canonical-class folders ship, so v2's `healthy/` + `not_a_plant/` folders are
> already picked up. Because rows are agent-judged, the gate measured on them is
> a **proxy** until verified production volume exists.

## (2)→(3) Server ingestion — Kaggle-only, version-gated

`server/ingest/` pulls ONLY from the Kaggle supplement (no in-server
scraping; `openverse`/HTTP probes and the runtime disjointness gate were
removed — disjointness is now an offline criterion above).

- **Version gate:** each cycle polls the dataset's `currentVersionNumber`
  (cheap metadata call, download credentials only); a full ingestion runs
  only when the version changed (or first run). State lives in the
  `labeling.db` `meta` table (`last_ingested_dataset_version`).
- **Gates kept:** decode + min-side 384, SHA dedup, pHash near-dup.
- **Model auto-routing** (`labeling_auto.classify_candidate`): conformal-set
  primary, `labeling_conf_high` fallback —
  `auto_yes` (confident agree) / `auto_no` (confident disagree) /
  `pending` (uncertain → human).
- **`auto_no` rows are KEPT** (row + image): confident disagreements are
  exactly where model false positives hide. They don't count toward targets
  but stay auditable and exportable as hard negatives after human review.
- **Audit sampling (`labeling_audit_rate`, default 0.05):** a deterministic
  (by SHA) fraction of confident calls is re-routed to the human queue with
  `audit=1` — humans spot-check the model's confident calls, so auto
  pre-filtering can never silently bury its blind spots.

## (4) Production scan recording — anonymized

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
- **Class-balanced caps:** suspicious scans and weak-class (target>0) scans
  are always kept; confident common-class scans are sampled at
  `scan_common_keep_rate` (0.1) decided deterministically by SHA; per-class
  daily cap (`scan_daily_cap_per_class`) + global `scan_max_images` bound
  disk.
- **Retention / prune order (protects hard cases):** unreviewed `pending`
  scans die after `scan_retention_days` (90); over the global cap, redundant
  `auto_yes` common-class scans are pruned first, then weak-class
  `auto_yes`; `user_feedback` rows and human-verdict rows are NEVER pruned.
- **Consent:** disclosed via the blocking first-launch consent page
  (Android onboarding; upgrade installs get a one-time blocking dialog) and
  `website/privacy.html`.

## (5) Feedback → the labeling queue

- Negative feedback re-opens the recorded candidate (even an `auto_yes` one)
  as `pending` under `source='user_feedback'`, retargeted at the user's
  corrected class, carrying their note — the canonical false-positive
  signal, served at top queue priority. It never overrides a human verdict.
- Positive feedback stamps `user_correct=1` and promotes a still-`pending`
  scan to `auto_yes` when the user confirms the model's own prediction.
- If the scan was never recorded (old install / recording off / disputed
  NAP), the negative-feedback image upload builds the candidate from the
  uploaded bytes via the same anonymize helper. **Known gap:** negative
  feedback with no image and no recorded scan has no pixels — nothing can be
  enqueued.
- **GDPR:** `/api/community/user/{id}` delete cascades community rows,
  feedback rows AND labeling candidates keyed by that user's scan_ids.
  Plain `user_scan` rows are unreachable by design (no user linkage exists).

## (6) Human labeling — 3 prioritised sources

`/api/label/next` serves `pending` cards in priority order **feedback →
dataset → scans** (filterable via `?source=`). Target gating (skip
satisfied/weight-0 classes) applies ONLY to the dataset group — a feedback
correction for a satisfied class must still surface. Audit cards show the
model's verdict so the human knows they're auditing. `/api/label/stats`
adds a per-source block.

### Per-class targets (`labeling_targets`) — retargeted on model v2.6.0 evidence

After the v2.6.0 run (above) the targets are precision-/supply-driven,
not eval-volume-driven: the eval-volume crisis on nutrient/scorch is over,
so the remaining work is hardening precision and backfilling the one class
that is still genuinely supply-limited.

| class | target | rationale (real-world eval, n=2213) |
|---|---|---|
| gray_mold_botrytis | 400 | **top data-supply priority** — eval n only 71, supplement just 120; P 0.84 / R 0.86 but the widest CIs of any class. Scarce (iNat genus + DoctorP). |
| nutrient_deficiency | 300 | eval-volume bottleneck **resolved** (25 → 122); now precision-limited (P 0.72, predicts 156 vs 122) — fix = scorch↔nutrient + healthy↔nutrient disambiguating negatives, not more positives |
| leaf_scorch_underwatering | 300 | precision recovered **0.52 → 0.71** (R 0.86); residual over-trigger (predicts 146 vs 121) → keep healthy-with-brown-tips boundary negatives at lower volume |
| powdery_mildew | **0** | solved: precision 0.95 / recall 0.93 |
| others | 0 | not collected |

**Human verification priority:** gray_mold scans first (thinnest eval,
n=71, widest CIs), then nutrient_deficiency and leaf_scorch precision FPs
(both still over-predict) — these are the trust bottleneck for tightening
the next ship gate.

## (7) Export → next dataset version (and back into §1)

`POST /api/label/export` builds a Kaggle-ready zip of verified images; the
admin downloads it and updates the Kaggle dataset **manually** (`kaggle
datasets version …`). The server has download-only Kaggle access and NEVER
uploads.

- **Manifest columns:** `filename, class, source, source_url, subject_plant,
  verified_by, split, sha256, phash, notes`. `verified_by` ∈ `model`
  (auto_yes), `model+user` (auto_yes + positive feedback), `human` (yes).
  **No scan_id, no feedback text, no user data.**
- **Eval-integrity rule (mechanical in the `split` column):** the model must
  never grade or calibrate itself on its own confident calls. Every
  `auto_yes` row (`verified_by` model or model+user) is `split=train`,
  always. Held-out splits draw ONLY from human-verified rows: a
  deterministic ~20% (by sha) of human-verified **production** scans gets
  `split=calibration` (real phone photos are exactly what temperature
  scaling + RAPS need), all other human-verified rows get `split=eval`. The
  training notebook routes the manifest `split` accordingly; its group-split
  keeps eval source-disjoint from train.
- **Near-dup sweep at build time:** pHash Hamming ≤ 6 across bundle items;
  the strongest provenance wins (`human` > `model+user` > `model`), dropped
  rows are listed in `skipped_dups.csv` (DB rows untouched).
- **Hard negatives:** `?include=hard_negatives` adds a
  `hard_negatives/<proposed_class>/` tree of human-`no` rows (confirmed
  false positives), `split=hard_negative`. Excluded by default so training
  bundles stay positives-only.
- **Closing the loop:** once per-class targets are admin-verified, the
  bundle feeds the next offline collection run — `sha256`/`phash` columns
  are the scrape dedup index (never re-collect what's verified), verified
  positives are reference exemplars and the hard-negative tree gives the
  vision-agent judge reference *rejects*.

## Verification

- Server: `ruff check .` + `pytest tests -q` (routing, recording,
  feedback wiring, source queues, export splits, versioned ingestion are all
  covered — see `tests/test_scan_recording.py`,
  `tests/test_feedback_labeling_wiring.py`, `tests/test_label_sources.py`).
- Android: `./gradlew lintDebug testDebugUnitTest assembleRelease`; manual —
  fresh install ends onboarding on the consent page (Skip jumps to it,
  Agree required), labeling source chips filter/badge correctly.
- Live: scan → row in `candidates` with `source='user_scan'`; negative
  feedback → card at top of `/api/label/next`; export manifest carries
  `split` per the rule above and no scan_ids.

## Risks

- **Calibration drift** — trust rides on temperature/conformal staying
  calibrated; both are re-fit on every model ship (notebook §6), and
  calibration draws only from human-verified production items.
- **Model blind spots** — mitigated three ways now: audit sampling of
  confident calls, kept `auto_no` rows, and negative feedback always
  reaching the queue at top priority.
- **Consent/regulatory** — recording is consent-gated and anonymized;
  `website/privacy.html` and the in-app consent screen are the canonical
  disclosure.

## Maintainer actions (outside this repo)

- **v2 shipped and trained cleanly (v2.6.0, gate passed).** For dataset
  **v3**, shift from eval-volume to precision + the one supply-limited class:
  backfill gray_mold foliar houseplant Botrytis toward ≥250 (now the binding
  constraint), and add scorch↔nutrient / healthy↔nutrient and
  healthy-with-brown-tips disambiguating negatives. The server ingests v3
  automatically on the version bump.
- Upload export bundles to Kaggle when notified; wire the manifest `split`
  column in the training notebook (auto-labeled → train only).
- Feed each export bundle back into the offline pipeline (dedup index +
  judge exemplars).
