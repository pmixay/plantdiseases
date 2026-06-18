# Dataset & labeling plan — local collection + server review

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
2. **Server review of production signal.** The server records anonymized
   production scans + user feedback into `labeling.db` and serves them to a
   human admin queue (**feedback + scans only**). It does **not** fetch,
   ingest, route, or export any Kaggle dataset.

> **No server-side dataset loop — by design, not "later."** The Kaggle
> auto-fetch / version-gated ingestion, the model auto-router, the audit
> sampling, and the export-bundle builder were removed and are **not** a
> deferred server phase. Dataset collection and judging are done **locally**,
> where the maintainer has the tooling; the server's only data job is to
> surface real user feedback + uncertain scans for human review. Claude checks
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
        │                                        human admin queue (feedback + scans)
        │                                             │ verified verdicts stay in labeling.db
        └─────────────────────────────────────────────┘
   maintainer folds verified production rows into the next local dataset version
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

The v2.6.0 evidence sets the **v3 path** (precision- and supply-driven, not
eval-volume) — see the dedicated section below.

### Audited state of the live supplement — v3 (audited 2026-06-18)

`maxayyt5/houseplant-disease-eval-supplement` is now **v3** (live): **1825
images** (was 1647), pulled and counted straight from `manifest.csv`. v3 adds
`role` (positive | hard_negative), `hn_kind` (boundary | generic | texture) and a
fully-populated `subject_domain` column. Split: **1036 positives / 789 hard
negatives**.

| class | role | v3 n | v3 target | verdict |
|---|---|---|---|---|
| `gray_mold_botrytis` | positive | **228** | ≥250 | ⚠️ **22 short** (~91%) — the "#1 v3 job" not fully met |
| `leaf_scorch_underwatering` | positive | 406 | de-noise + negatives | positives trimmed (501→406); negatives moved to `healthy/boundary` |
| `nutrient_deficiency` | positive | 402 | de-crop | ✅ de-cropped (617→402; **no field/orchard rows remain**) |
| `healthy` | hard_negative | 414 | +140 boundary | ✅ boundary=135 (generic 273 / boundary 135 / other 6) |
| `not_a_plant` | hard_negative | 375 | +50 → 299 | ✅ exceeds (texture lookalikes) |

**Acceptance criteria (§C) status:**
- **Hard domain gate — MET.** `subject_domain ∈ {houseplant: 1450, not_a_plant:
  375}`, 100% populated (v2's 501 blank `leaf_scorch` rows are backfilled); **no
  `field_crop`/`orchard_fruit` rows ship** (dropped, not down-weighted).
- **Hard negatives first-class + split-tagged — MET** (`role`/`hn_kind`).
- **pHash dedup — MET** (100% populated; 0 near-dups at Hamming ≤ 6 within class).
- **Resolution ≥384px — MET** (sampled clean). Source-disjointness asserted in the
  dataset README (banned origins swept).
- **Per-row `license` — NOT MET.** **1676/1825 (91.8%) blank**; only 149 carry a
  license. The README is honest (dataset license = `other`, "do not relabel
  CC0"), but §C's "per-row license required" is the one acceptance criterion still
  unsatisfied.
- **`source_url`/`image_url` dropped from the manifest** (README: intentional). v3
  keeps a `source` host + `engine`/`query`; this weakens manifest-level
  disjointness *re-auditability* (the README says it was enforced at collect time).
- **Human-verified rows — none yet** (all `verified_by=agent_vision`), so the gate
  on this supplement stays a **proxy** (expected per §D).

**Notebook alignment — OK, no code change needed.** `fetch_kaggle_supplement`
reads the 5 canonical-class folders generically; the `healthy/` + `not_a_plant/`
hard-negative rows correctly load as their own class (a brown-tipped healthy plant
*is* `healthy`; a texture lookalike *is* `not_a_plant`), so the boundary negatives
do their job implicitly. The `role`/`hn_kind` columns are informational for the
maintainer. (The stale v2 caveat in §2 about `gray_mold` carrying field/orchard
imagery was refreshed — the v3 hard gate removed it.)

**Verdict:** v3 substantially delivers the precision-hardening intent (hard domain
gate, boundary negatives, de-crop, dedup, split-tagging). Two residual gaps for a
v3.x top-up: (1) **`gray_mold` 228 < 250** positives; (2) **per-row `license`**
(92% blank).

> **Training-time use (v2.6):** the supplement is pulled directly by
> `train_notebook.ipynb` §2 (`fetch_kaggle_supplement`, source `supplement`) and
> is **eval-eligible** — a held-out fraction seeds `val_realworld` /
> `val_indomain` / the calibration splits, thickening the thin weak-class eval
> slices (realized in the v2.6.0 run: nutrient_deficiency 25 → 122,
> leaf_scorch 45 → 121, gray_mold 53 → 71). It reads whatever
> canonical-class folders ship, so v2's `healthy/` + `not_a_plant/` folders are
> already picked up. Because rows are agent-judged, the gate measured on them is
> a **proxy** until verified production volume exists.

## v3 path — what to collect, what to change

> **Grounded in two checks (2026-06-16).** (a) The live Kaggle supplement is
> still **v2** — 1647 rows, re-counted straight from `manifest.csv` below.
> (b) The shipped **v2.6.0 `gamma` bundle** was validated end-to-end: ZIP CRCs
> intact, all four ONNX graphs load and run in onnxruntime (gamma →
> `logits[9]` + DINOv2 `embedding[384]`, YOLOv8n detector → `[1,5,8400]`, aqua
> student → `[9]`), the reported metrics **recompute exactly**, and it **passes
> every `val_realworld` ship-gate criterion** (disease macro-F1 0.865, min
> disease recall 0.84, healthy-bias 0.90, healthy recall 0.87). So v3 is **not**
> a rescue release — it is precision-hardening plus one supply-limited class.

### Current supplement (v2, re-counted from `manifest.csv`)

| class | n | subject_domain split | v2.6.0 realworld eval | the v3 problem |
|---|---|---|---|---|
| `nutrient_deficiency` | 617 | 402 houseplant / 110 field_crop / 30 orchard / 51 other / 12 n-a-p / 12 blank | P **0.72** / R 0.92 — predicts 156 vs 122 | over-triggers → disambiguating negatives + de-crop |
| `leaf_scorch_underwatering` | 501 | **100% blank** | P **0.71** / R 0.86 — predicts 146 vs 121 | over-triggers → brown-tip negatives; backfill domain |
| `not_a_plant` | 249 | 249 not_a_plant | (negatives) | keep; extend brown-texture lookalikes |
| `healthy` | 160 | 160 houseplant (~150 generic, ~10 brown-tipped) | (negatives) | needs boundary healthy, not generic lush-green |
| `gray_mold_botrytis` | 120 | 120 houseplant | P 0.84 / R 0.86 — eval n **71**, widest CIs | **top supply gap** — thin positives |

All 1647 rows are `verified_by=agent_vision` (no human-verified rows yet);
`license` is populated on only **30 / 1647**.

### (A) Per-class collection targets for v3

| class | now | v3 target | add | kind / where to get it |
|---|---|---|---|---|
| `gray_mold_botrytis` | 120 | **≥250** | +130 positives | foliar **houseplant** Botrytis — genuinely scarce (iNat genus + DoctorP). The #1 v3 job. |
| `healthy` (boundary) | ~10 brown-tipped | **+140** | boundary negatives | healthy houseplants with **dry brown tips/edges** — the scorch-precision negative the set lacks |
| `leaf_scorch` (negatives) | — | **+100** | disambiguating negatives | scorch↔nutrient boundary + non-scorch brown (not more positives) |
| `nutrient_deficiency` (negatives) | — | **+100** | disambiguating negatives | scorch↔nutrient + healthy↔nutrient boundary (not more positives) |
| `not_a_plant` | 249 | **+50** | lookalike negatives | brown straw / paper / cardboard / fallen-leaf textures |

Volume is no longer the constraint — **precision is**. Apart from gray_mold, the
bulk of v3 is *negatives* that tighten the two over-triggering classes.

### (B) What needs changing in the existing rows (manifest hygiene)

Mechanical fixes that block a clean v3:

1. **Backfill `subject_domain` on all 501 `leaf_scorch` rows** (today 100%
   blank) — the domain gate can't be enforced on a blank column.
2. **De-crop `nutrient_deficiency`:** ~140 rows are `field_crop`/`orchard_fruit`
   (110 + 30). Drop them (or hard down-weight) to honour the houseplant domain
   gate — they dilute the houseplant signal real phone photos need.
3. **Populate `license`** (only 30/1647 today). v3 should require a real
   per-row license; the blank default is a hygiene/compliance gap.
4. **Promote ~140 generic `healthy` rows** toward the brown-tipped boundary
   variant (see A) — generic lush-green doesn't serve scorch precision.

### (C) Acceptance-criteria deltas (tighten §1 for v3)

- **Domain gate becomes hard, not advisory:** every row carries a non-blank
  `subject_domain ∈ {houseplant, not_a_plant}`; `field_crop`/`orchard_fruit`
  are rejected (the v1/v2 grandfathering of agricultural imagery ends).
- **Per-row `license` required.**
- **Hard-negative folders are first-class:** the brown-tipped `healthy/` and the
  `not_a_plant/` lookalikes are split-tagged so they never inflate positive
  recall (they exist to raise precision).

### (D) Fold in human-verified production rows

v3 is the first version that can use real production signal: human-verified
`user_feedback` + `user_scan` verdicts from `labeling.db` (§6) merge in under
the eval-integrity split rules. They are the **only** non-proxy rows — until
they exist the supplement gate stays a proxy (every supplement row is
agent-judged). Verification priority mirrors the model's weak spots: gray_mold
first, then leaf_scorch / nutrient precision false-positives.

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

## (4) Feedback → the labeling queue

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

## (5) Human labeling — 2 prioritised sources

`/api/label/next` serves `pending` cards in priority order **feedback →
scans** (filterable via `?source=`). These are the only two sources: there is
no `dataset` source (`?source=dataset` returns 422). `/api/label/stats` adds a
per-source block.

### Per-class targets (`labeling_targets`) — retargeted on model v2.6.0 evidence

`target>0` marks weak classes whose recorded scans are **always retained and
prioritised for human review** (scan recording + review, not dataset
collection). After the v2.6.0 run the targets are precision-/supply-driven,
not eval-volume-driven: the eval-volume crisis on nutrient/scorch is over, so
the remaining work is hardening precision and backfilling the one class that
is still genuinely supply-limited.

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

## (6) Verified production rows → next dataset version (local / manual)

There is **no server export endpoint**. Human-verified feedback/scan verdicts
simply stay in `labeling.db`; when the maintainer builds the next local
dataset version they extract the verified rows directly from the DB and fold
them into the offline collection (§1). The rules below are what that local
step must honour so production signal can be reused without corrupting the
eval:

- **What a verified row carries:** `class, source, source_url, subject_plant,
  verified_by, sha256, phash`, plus the verdict provenance. `verified_by` ∈
  `model` (auto_yes), `model+user` (auto_yes + positive feedback), `human`
  (admin yes). **No scan_id, no feedback text, no user data leaves the DB.**
- **Eval-integrity rule (mandatory):** the model must never grade or calibrate
  itself on its own confident calls. Every `auto_yes` row (`verified_by` model
  or model+user) is **train-only**. Held-out splits draw ONLY from
  human-verified rows: a deterministic ~20% (by sha) of human-verified
  **production** scans is calibration (real phone photos are exactly what
  temperature scaling + RAPS need), the rest is eval. The training notebook's
  group-split keeps eval source-disjoint from train.
- **Near-dup sweep:** pHash Hamming ≤ 6 when merging into §1; strongest
  provenance wins (`human` > `model+user` > `model`).
- **Hard negatives:** human-`no` rows (confirmed false positives) are kept as
  hard negatives for the vision-agent judge's reference *rejects*, not mixed
  into positive folders.
- **Closing the loop (local):** verified positives become reference exemplars
  for the §1 vision-agent judge and their `sha256`/`phash` become the scrape
  dedup index (never re-collect what's verified).

## Verification

- Server: `ruff check .` + `pytest tests -q` (scan recording, `route_scan`,
  feedback wiring, and the feedback+scans queue are covered — see
  `tests/test_scan_recording.py`, `tests/test_feedback_labeling_wiring.py`,
  `tests/test_label_sources.py`, `tests/test_labeling.py`).
  `tests/test_label_sources.py` also asserts the removal: `dataset` is not a
  valid source group (422) and never appears in `/api/label/stats`.
- Android: `./gradlew lintDebug testDebugUnitTest assembleRelease`; manual —
  fresh install ends onboarding on the consent page (Skip jumps to it,
  Agree required), labeling source chips show **Feedback / Scans**.
- Live: scan → row in `candidates` with `source='user_scan'`; negative
  feedback → card at top of `/api/label/next`.

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

- **v2 shipped and trained cleanly (v2.6.0, gate passed — bundle validated
  2026-06-16).** The concrete **v3 path** (per-class targets, the manifest
  hygiene fixes, and the tightened acceptance criteria) is the
  "v3 path — what to collect, what to change" section above; in one line:
  precision-harden the two over-triggering classes with disambiguating
  negatives and backfill gray_mold positives to ≥250.
- Collect and judge **locally** (scrape → disjointness → pHash dedup →
  per-creator cap → vision-agent judge, §1), publish the supplement to Kaggle,
  and the **training notebook pulls it directly** on the next run. The server
  is never in this path.
- Fold human-verified production rows from `labeling.db` into the next local
  version under the §6 eval-integrity rules.
