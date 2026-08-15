# PlantVision — roadmap

Open work only; the code is the record. Companions: `DATASET_LABELING_PLAN.md`
(data loop), `../DEPLOYMENT.md` (deploy + Android signing/FCM),
`../server/models/MODEL_CARD.md` (**stale — see the model track**).

Every status here was verified against the tree. Two independently releasable
tracks: the **app** (`versionName` 1.8 / `versionCode` 18 already set, tag not
cut) and the **model** (bundle version, moves per training run).

---

## App 1.8

Everything else has landed: AI-doctor chat subtitle via `localizedSpeciesName`,
shared `ConnectivityObserver`, `GET /api/species-profile` returning all three
fields, flowering chart, native-range map, coach-mark onboarding, and the
species-info consolidation onto one screen behind one reusable card.

- [ ] **Cut the 1.8 tag and the RuStore build** — human-only.
- [ ] **First-launch intro video** — *externally blocked.* Server-hosted
      (~3 MB, background download, `/api/aqua`-style manifest), above the
      consent page, existing Lottie as offline fallback. Blocked on the
      user-generated Veo clip (vertical 9:16 macro monstera, scan-light sweep,
      `#16A34A`, ~8 s seamless loop, no text). Not started in the tree.

### Loose ends

- [ ] `ResultActivity.hasValidatedInternet()` duplicates
      `ConnectivityObserver.hasValidatedInternet` — the shared-observer work
      converted `CameraFragment` but not this second site.
- [ ] **Wikipedia attribution on the species card** — the card renders an
      extract with no source line; CC BY-SA makes this licensing, not design.
      Decide: source line on the card, or accept the link one level down.
- [ ] **No test drives the real map hit-test.** `1faddd8` moved hit-testing to
      `Region` + `Path.FillType.EVEN_ODD` and the old tests were deleted, not
      replaced, because `MapCanvasView` is private. Make it internal and test a
      dispatched `MotionEvent`.

---

## Security — open findings

August 2026 audit: 49 raised, 38 verified, 22 confirmed. Fixed items are in git
history. Theme is **cost and availability, not compromise** — an unauthenticated
caller is throttled only by a 2 req/s bucket, and staying under it is free.

- [ ] **`/api/feedback/image` unbounded** (`main.py:3043,3068`) — quota reads a
      counter this endpoint never increments so it can never fire; no
      magic-byte/decode validation; files never deleted. Disk fill **and**
      arbitrary bytes into the retraining corpus. *Highest value here.*
- [ ] **No inference admission control** (`main.py:2004`) — at `INFER_WORKERS=1`
      a client within the rate limit pins RAM and starves everyone. Wants a
      semaphore before decode → 503.
- [ ] **Cost caps** (`ai_services.py:1257`, `main.py:3865`) — caches key on
      client-chosen strings, so every distinct `name` is a guaranteed miss and a
      billable call. *OpenRouter has a monthly dollar cap, so bounded; stricter
      per-endpoint limits planned.* PlantNet has no server-side budget.
- [ ] **`/api/metrics`** (`main.py:1711`) — rate-limit exempt, full-table SQLite
      aggregates on the event loop.
- [ ] **`/api/plant-photo` amplifier** (`main.py:4041`, `plant_photos.py:184`) —
      unbounded WebP variants; LRU caps file *count*, not bytes, so any client
      can evict every warm photo.
- [ ] **Unbounded list on `/api/progression`** (`main.py:2491`), on the event
      loop; every comparable field is capped.
- [ ] **Prompt-cache poisoning** (`ai_services.py:1257`) — `common` reaches the
      prompt but not the cache key, poisoning the shared answer.
- [ ] **`/api/analyze` replays another user's `scan_id`** (`main.py:1972`) —
      keyed on upload-byte hash only, and `scan_id` authorises mutations.
- [ ] **`/api/chat` caps decorative** (`main.py:2226`) — client-chosen
      `session_id` resets turns and tokens.
- [ ] **Forgeable second opinions** (`community.py:636`) — client-supplied
      `author_pseudo_id`, so dedup does not bind.
- [ ] **EXIF GPS leaves the device** (`ScanRepository.kt:576,238`) — raw camera
      JPEG uploaded verbatim and forwarded to PlantNet, though
      `ImageUtils.prepareImageForUpload` exists and would strip it.
      `privacy.html:87` says this may be published as an open dataset.
      *Smallest change, largest privacy return.*
- [ ] **Pseudo-id in GET paths** (`ScanRepository.kt:775`) — a bearer capability
      landing in access logs.
- [ ] **Dependency constraints** — `starlette` unconstrained above 0.46.0
      (resolves clean today, range does not); `firebase-admin` pins
      `httpx==0.28.1`, colliding with `httpx>=0.28` once 0.29 ships; no lockfile.
- [ ] **Host named publicly** — host IP + SSH user `root` in git history
      (`cd4bda2`, removed with the admin panel in #149). **No credentials
      leaked** — no key or password was ever committed. Action is ordinary
      hardening: `PermitRootLogin prohibit-password`, `PasswordAuthentication
      no`, SSH rate-limiting.

---

## Garden refactor — next major app work, its own release

> Today the Garden is a **flat list of saved plants**. The refactor turns it
> into a **care-management hub** — the screen a user opens daily, where every
> scan feeds an ongoing care story instead of a one-off verdict. Biggest
> remaining product lever, mostly new UI + aggregation over data Room already
> persists (`plants`, `scans`, `journals`, `reminders`, `chat`). Keep XML Views
> + Hilt; no Compose.

**Status:** DB is at `version = 6` (`Database.kt:452`). The `room` column on
`plants` **exists** (`Database.kt:29`, re-added by `MIGRATION_5_6`), so item 4
is partly done at the schema level. Still absent: checklist-step storage,
health derivation, aggregated queue.

1. **"Today" care queue** — aggregates due waterings, fertilising and open
   treatment steps into one dated queue, most urgent first; one-tap **Done**
   advances the schedule and logs to the journal. Promotes existing
   `PlantReminderWorker` data from notifications to a first-class list. Empty
   state is a genuine reward ("all caught up 🌿") — habit without a streak.
2. **Scan-driven care checklists** — materialize the care plan's treatment
   steps as a trackable checklist on the plant, with per-step completion and
   follow-up dates feeding the Today queue. `generate_care_plan → treatment`
   already produces the steps. Completion history feeds the health header and
   later the retraining loop ("did the treatment work?").
3. **Plant health header** — one healthy / recovering / declining verdict per
   plant from scan-history trend + care adherence; drives the Garden card
   accent. Thresholds in one tunable place.
4. **Rooms / locations** — group by room; care-relevant because light and
   humidity differ, so a room can seed default care and the light-meter
   baseline. Column exists; UI + grouping do not.
5. ~~Progression / photo-compare~~ — shipped (`DetailTimelineController`,
   `PhotoCompareActivity`).

| # | Task | Why here | Size |
|---|---|---|---|
| 1 | Design pass — schema + aggregation | Storage undecided | M |
| 2 | Today queue **+** checklists | The payoff; shared schedule/journal plumbing | XL |
| 3 | Health header | Needs adherence signal from #2 | M |
| 4 | Rooms UI | Independent polish; can slip | S |

**★ Human gate — schema approval, after #1.** Does checklist storage fit the
real care-plan `treatment` shape? Is the Today queue derivable from existing
tables? Stakes are lower than they look — `Database.kt` declares the schema
ephemeral pre-release — but everything builds on it.

**★ Human gate — health heuristic, after #3.** The header tells users a plant
is *declining*; a wrong verdict is worse than none. Sanity-check on real scan
histories.

---

## Model track

`MODEL_VERSION` is **2.7.0** (notebook §1). `server/models/` holds only
`.gitkeep` + `MODEL_CARD.md` — **nothing past 2.6.8 is deployed.**

Closed history: **2.6.9** gate-passed but lost a paired A/B vs 2.6.8 (kept
2.6.8). **2.6.10** recovered leaf_scorch but silently lost a 2.26k-image source
mid-run, so it cannot anchor an A/B. **2.6.11/v8** superseded by the 2.6.12
general-plant pivot, whose data-cleaning it folded in. The 2.6.12/2.6.13 work
never trained — it became 2.7.0. Details in `DATASET_LABELING_PLAN.md`.

**Version policy.** 2.7.x is the *stabilise-and-polish* line: the corpus is
finally clean, so every 2.7.x change is small, independently measurable, and
A/B-able against 2.7.0. The ambitious work moved to **2.8**, where it belongs —
each item there changes what the model *is*, and stacking them on an unproven
baseline is how 2.6.9 happened.

### Training modes (2.7.0)

One knob, `TRAIN_MODE` in §1 — `gamma` (server model alone, cheapest run),
`combined` (gamma + distilled aqua TFLite from one run; the default and what
ships), `frontier` (ViT-B/14, GPU). `frontier` is a **plain mode**: nothing
gates it, no probe go/no-go. `export_family` decides what lands in
`classes.json → model_family`, so a `combined` run still ships a `gamma` server
bundle — the mode describes the run, the family describes the artefact.

### 2.7.0 — the data-integrity release (train this)

Six notebook fixes: fail-fast on a truncated corpus (`REQUIRED_SOURCE_FLOORS` +
persisted `source_inventory.json`), re-sourced ornamental data, a revived
MaxLogit OOD floor over plant logits only, an unpinned conformal grid,
**`split_hint` routing**, and **`subject_domain` passthrough + per-split domain
reporting**. Plus the supplement fetcher no longer runs under a fail-soft
wrapper, and a `SUPPLEMENT_MIN_ROWS` guard rejects a stale upload.

Supplement **v8 is published and verified** (3,389 rows; 1,907 eval-eligible /
695 train-only / 787 boundary; 585 `garden_ornamental` of which 353 are
eval-eligible; `leaf_spot` 184; provenance present with 333 `new`). For the
first time `val_realworld` contains garden ornamentals, so the 2.6.12 pivot is
measurable rather than assumed.

- [ ] **Train 2.7.0 and paired-bootstrap vs 2.6.8.** Expect `val_indomain` to
      recover toward 0.86. Measured on the first full §2 with v8 loaded:
      `split_hint` held **695** rows out of the held-out splits that would
      otherwise have drained into them, and weak-class eval thickened anyway
      (nutrient 30→77, scorch 45→107, gray_mold 57→82, powdery 66→99 vs the
      supplement-less run). The slices grow, not shrink — the supplement adds
      more than the routing withholds. `viral_disease` is supply-starved (2
      eval-eligible supplement rows) — do **not** read a viral regression as a
      modelling failure.
      **The A/B cannot be corpus-verified**: the inventory gate ships *in*
      2.7.0, so 2.6.8 has none, and the corpora provably differ. Read the paired
      number **only** on supplement rows tagged `carried_from_v7 | v8 | v8b |
      new` (1,557 rows, 585 eval_ok) — the rest are inside 2.6.8's own training
      set and bias the comparison toward it. On-disk stems are
      `supp_<class>_<original stem>`, so eval images join straight to
      `manifest.csv`.
- [x] **Verdict: DO NOT SHIP 2.7.0. Keep 2.6.8.** (paired eval 2026-08-11, then a
      5-hypothesis / 20-agent adversarial investigation 2026-08-12. The verdict
      held; **several numbers in the first write-up did not** — corrected below.)

**⚠ Correction to the 2026-08-11 write-up.** The never-trained benchmark was
built from 2,000 files that hold only **501 SHA-unique images and 206 pHash
clusters** (Hamming ≤ 6) — it failed the near-dup gate `DATASET_LABELING_PLAN.md`
requires of any eval set, and the duplication inflated every count and p-value.
The scorch arm is **2 species, not 6**. Corrected, on pHash clusters:

| model | scorch recall (argmax, deduped) | served, full pipeline | healthy false-fire (deduped) |
|---|---|---|---|
| 2.6.0 | 0.597 | — | 0.078 |
| **2.6.8 (deployed)** | 0.565 | **71/200** | 0.022 |
| 2.7.0 | **0.048** | **0/200** | 0.047 |

**What survives:** the scorch collapse is real and decisive — McNemar **32–0,
p=4.7e-10** on n=62 clusters (not the reported 5e-122), and end-to-end the
production pipeline diagnoses **zero of 200** distinct ornamental scorch images
against the incumbent's 71 (p=8.5e-22). It is a **separability loss, not a
threshold shift**.

**What does not:** the healthy false-fire finding is **retracted**. Deduped it is
0.022 vs 0.047, McNemar 17–8, **p=0.108** (pHash: p=1.0) — not significant, and
2.7.0 (0.047) still beats 2.6.0 (0.078), so "undoes 2.6.8's false-fire
improvement" was false. Per species the picture is also narrower than reported:
hibiscus 1.00 / 1.00 / ~0.0 (n=31) but banana_bush 0.19 / 0.13 / 0.03 — **2.6.8
was already collapsed on banana_bush**, so the entire headline delta is 31
hibiscus clusters.

**Refuted mechanisms** (each re-derived from the bundles, then refuted): the 787
boundary hard-negatives; the `kaggle_ornamental` volume collapse (2,263 → 1,158)
as *the* cause; the OOD gate rejecting ornamentals; embedding-bank domain
coupling; `supplement_train_only` at 0.9. **⚠ The `kaggle_ornamental` line is
now half-retracted** — see 2.7.2 below. Volume was the wrong axis, but the
source is the right one: what changed is its *class content*.

**Surviving mechanism.** 2.7.0 reads ornamentals as out-of-distribution in a
quantity involving no bank and no detector: AUROC of `plant_max_logit` separating
an in-domain pool from the benchmark is 0.555/0.607 (2.6.8) vs **0.731/0.890**
(2.7.0). But the axis is **training-corpus acquisition style, not "ornamentals"**
— 2.7.0 *beats* 2.6.8 on held-out garden ornamentals from the supplement. The one
corpus fact that lines up: `kaggle_ornamental` supplies **zero `leaf_scorch`**
(rose/aloe: healthy, leaf_spot, powdery only) and was up-weighted 0.8 → 1.0 in
exactly the run that collapsed.

**Two defects found that are independent of 2.7.0:**

- [x] **The served path throws away ~19 pp of the DEPLOYED model's scorch
      recall.** The NAP AND-gate plus crop voting destroys 19 of 2.6.8's 56
      argmax-correct scorch calls (p=3.8e-6). Its argmax 0.565 is **0.355
      served**. This is the "report the production-faithful number" bullet, and
      it is worth more than the 2.7.1 work. **Cause found and fixed in 2.7.2
      item 6** — the gate never consulted the `not_a_plant` head. Served scorch
      is back to 0.565, equal to argmax, with zero cost on 890 paired images.
- [ ] **The OOD bank has no mass for damaged out-of-domain plants**, so its
      calibrated 5% plant-FPR is **75–88%** on a real out-of-domain plant and the
      AND-gate degrades to detector-only exactly where the model is weakest. A
      data-coverage bug, not a threshold one; present in both bundles. It also
      means any 2.7.1 ablation can move ornamental recall ~20 pp *through the
      gate alone* — so score end-to-end, never argmax.

### 2.7.1 — ordered, one change per run

- [x] **1. Benchmark rebuilt** — `server/tools/build_ood_benchmark.py` (2026-08-12).
      pHash-dedup at Hamming ≤ 6 in code, per-species manifest, and warnings when
      a class is too thin or spans < 3 species. **11,200 source files collapse to
      213 clusters** (healthy 151 across 6 species, scorch 62 across 2).
      `paired_eval` now records the image list so any preds.json can be broken
      down per species. Authoritative argmax numbers, 2.6.8 → 2.7.0:

| class / species | 2.6.8 | 2.7.0 | n |
|---|---|---|---|
| **healthy** | 0.967 | 0.960 | 151 |
| **leaf_scorch** | **0.565** | **0.065** | 62 |
| └ hibiscus | **1.000** | **0.097** | 31 |
| └ banana_bush | 0.129 | 0.032 | 31 |

      **Scored end-to-end too** (`paired_eval --served`: detector crop, crop
      voting, blur + OOD gates, weak-healthy reroute — what a user actually gets):

| | 2.6.8 argmax | 2.6.8 **served** | 2.7.0 argmax | 2.7.0 **served** |
|---|---|---|---|---|
| leaf_scorch (n=62) | 0.565 | **0.371** | 0.065 | **0.000** |
| └ hibiscus (31) | 1.000 | 0.645 | 0.097 | 0.000 |
| └ banana_bush (31) | 0.129 | 0.097 | 0.032 | 0.000 |
| healthy false-fire (151) | 0.033 | 0.046 | 0.040 | 0.046 |

      Three things this settles. (1) **The verdict holds end-to-end**: McNemar
      23–0, p=2.4e-07 served. 2.7.0 gets *zero* of 62 clusters. (2) **The served
      path costs the DEPLOYED model 0.565 → 0.371** — a 19.4 pp independent
      confirmation of the "served path destroys a third of 2.6.8's correct scorch
      calls" finding, now reproduced on clean deduped data. (3) **Healthy is a
      tie at 0.046 vs 0.046** — the false-fire retraction confirmed a third time,
      on clean data, end-to-end.

- [ ] **Widen the scorch arm past 2 species — BLOCKED, and the block is
      informative.** Searched public sources; the only never-trained candidate
      with an explicit scorch label is *Strawberry leaf scorch*
      (`mahin661/plant-disease-detection-bounding-box-dataset`, CC BY 4.0), which
      is the **fungal pathogen** *Diplocarpon earlianum* — not our abiotic
      `leaf_scorch_underwatering`. Mapping it would inject exactly the label
      guessing the builder refuses. `tarieretimitimi/houseplant-health-
      classification` has only coarse Healthy/Unhealthy (67/24). So public
      abiotic-scorch imagery is genuinely scarce — the same shape as the
      `gray_mold` supply ceiling, and independent corroboration that **"re-source
      ornamental `leaf_scorch`" is a real collection problem, not an oversight**.
      **⚠ Partly overturned 2026-08-15 — see "the scorch source that is the
      benchmark" in 2.7.2.** One never-trained dataset *does* carry an explicit
      Hibiscus scorch label; it turns out to be the benchmark's own imagery and
      is unusable. Other genera in the same dataset are clean and are real supply.
      Until it is widened, this benchmark measures **two species well and the
      domain not at all**: read it per species, and treat "hibiscus scorch
      recovers" as the 2.7.1 success criterion rather than a domain-level claim.
- [ ] **2. Zero-train precursor** (CPU/Colab, one afternoon). Run the §3
      cached-feature probe **with and without `kaggle_ornamental` rows** and score
      AUC(z_scorch) on the rebuilt benchmark. No LoRA training. If dropping the
      source recovers separability, the driver is its class incompleteness.
- [x] **3. `kaggle_ornamental` 1.0 → 0.35 — RUN 2026-08-14, FALSIFIED, REVERTED.**
      The ablation landed cleanly (draw 0.1107 → 0.0415, no other weight moved)
      and moved the target metric by **exactly nothing**, while costing healthy:

| never-trained benchmark | 2.6.8 | 2.7.0 | **2.7.1** |
|---|---|---|---|
| scorch recall, argmax | 0.565 | 0.065 | **0.065** |
| scorch recall, **served** | **0.371** | 0.000 | **0.000** |
| healthy recall, served | 0.954 | 0.947 | **0.861** |
| healthy false-fire, served | 0.046 | 0.046 | **0.119** |

      Scorch is identical to 2.7.0 to three decimals. Healthy false-fire is
      **2.6× worse than the incumbent** (McNemar 2–13, p=0.0074). In hindsight the
      source composition predicts this exactly: it supplies healthy / leaf_spot /
      powdery ornamentals and **zero scorch**, so cutting its weight removes
      ornamental *healthy* signal and cannot move a class it never contained.
      **2.7.1 is worse than 2.7.0. Do not ship. Keep 2.6.8.**
- [x] **Zero-train feature probe — RUN 2026-08-14. It corrects the diagnosis.**
      The shipped ONNX graphs export the 384-d DINOv2 CLS embedding, so this
      needed no torch and no GPU. Linear probes on those embeddings, scorch vs
      healthy:

| | in-domain CV | **transfer in-domain → OOD** | **OOD-oracle CV** |
|---|---|---|---|
| 2.6.8 | 0.994 | **0.925** | 0.994 |
| 2.7.0 | 0.995 | 0.811 | 0.986 |
| 2.7.1 | 0.995 | **0.790** | **0.988** |

      **The features are NOT the problem.** A linear probe on 2.7.1's *own*
      embeddings separates ornamental scorch from ornamental healthy at
      **AUROC 0.988** — the signal is fully present in the representation. What
      degraded is the *transfer of the direction the head learned* (0.925 →
      0.790). So "the surviving mechanism is representational" — written here on
      2026-08-12 — **was wrong**, and this measurement retires it.

      **But it is not post-hoc fixable either.** Sweeping a constant bias on
      2.7.1's scorch logit: to reach 2.6.8's recall (0.548) costs false-fire
      0.358 = **14× the incumbent's 0.026**; at bias 0 it is already 0.073 vs
      0.026. There is no operating point where 2.7.1 is simultaneously as
      sensitive and as safe. This independently confirms the investigation's
      refutation of the "re-fit temperature / logit bias" hypothesis (conf 0.9).

      **Where that leaves 2.8.** The fix is neither the backbone (features are
      fine, so DINOv3 / `frontier` will not address this) nor calibration. It is
      the **head's learned decision geometry**: it ties scorch to in-domain
      acquisition style. That points squarely at item 5 — ornamental scorch data
      the head can learn a style-invariant direction from — or a domain-robust
      training objective. It does **not** point at another sampler weight.
- [ ] **STOP TUNING SAMPLER WEIGHTS.** Four levers dead: `kaggle_ornamental`
      (by experiment), margin-crop/letterbox (conf 0.95), boundary
      hard-negatives, ornamental *volume* collapse. Each further guess costs an
      A100 run and 2.7.1 showed a wrong guess can regress healthy significantly.
- [ ] **Zero-train probe before any further run.** §3 cached-feature probe with
      and without the **supplement's own 663 scorch rows** (v6 → v8 grew that
      class sharply, and 2.7.x has far MORE in-domain scorch than 2.6.8 while
      being far worse out-of-domain — the classic more-data-narrows-the-
      representation shape). Score AUC(z_scorch) on the benchmark. No LoRA
      training, no A100.
- [ ] **4. Then, separately:** aspect-preserving preprocessing (letterbox vs
      centre-crop) and `supplement_boundary` 0.35 → 0.2 (draw 2.63% → 1.52%). The
      boundary premise was **refuted**, so this is a confirmatory ablation, not a
      lead.
- [ ] **5. The real fix is 2.8-era data:** re-source ornamental `leaf_scorch`
      into `KAGGLE_ORNAMENTAL_SLUGS` / the supplement. No sampler weight can
      supply a class the source does not contain.

**Honest scope.** "Beat 2.6.8 on every axis" is not yet evidenced as reachable:
2.7.0 is genuinely better in-domain and worse out-of-domain, and the out-of-domain
evidence rests on 31 hibiscus clusters. Item 1 is what turns that from an opinion
into a measurement.

### 2.7.2 — the archaeology release

A longitudinal sweep (2026-08-15) scored **every archived bundle** — 2.3.1
through 2.7.1, 14 distinct models — on the same 213-cluster never-trained
benchmark, argmax *and* served. Three of the findings below are reproduced
independently from the raw per-image predictions; the fourth is read straight
off the shipped `classes.json` / `model_card.json`.

**1. The collapse is a single cliff, not a drift.** Stage-2 argmax scorch recall
(n=62) across the series: 2.3.1 0.000–0.129 → **2.4.0 0.516–0.758** → 2.5.0
0.677 → 2.6.0 0.565–0.581 → 2.6.5 0.581 → 2.6.7 0.694 → 2.6.8 0.548 → **2.6.9
0.565** → **2.7.0 0.065** → 2.7.1 0.081. Adjacent-version McNemar: 2.6.8→2.6.9
gained 1 / lost 0 (p=1.0); **2.6.9→2.7.0 gained 0 / lost 31 (p=9.3e-10)**. No
other transition loses more than 15 clusters, and healthy recall is flat across
the same cliff (0.974 → 0.967). **2.6.9 is not "collapsed" — on this axis it is
marginally the best model on record.** Any hypothesis has to explain a step
change inside the 2026-07-03 → 2026-08-08 gap.

**2. ⚠ Correction to the 2026-08-12 refutation: `kaggle_ornamental` is the
mechanism, on content, not volume.** The source *enters* the corpus at
2.3.1→2.4.0 (0 → 1,233 images) and that is the single largest **gain** in the
series: **+39 / −0, p=3.6e-12**, scorch 0.129 → 0.758. It is *replaced* between
2.6.9 and 2.7.0 (pappu54 deleted upstream → `itselif/ornamental-plant-diseases`
+ `warcoder/rose-leaves-disease-detection`) and that is the single largest
**loss**. Nine pappu54-era runs sit at 0.516–0.758; the two replacement-era runs
at 0.065–0.081. The replacements supply healthy, `leaf_spot` and
`powdery_mildew` and **zero `leaf_scorch`** — while `KAGGLE_ORN_KEYWORDS` *does*
map scorch, so the mapper would have accepted it. `DATASET_LABELING_PLAN.md`
records pappu54 supplying **+67 scorch**.

This is a symmetric natural experiment, and it is consistent with every prior
refutation rather than contradicting them: those tested **volume** and
**weight**, never **content**. It also explains why the 2.7.1 ablation failed in
exactly the way it did — down-weighting a source that (post-replacement)
contains *zero* scorch cannot restore scorch, and can only remove the ornamental
*healthy* signal it does supply, which is precisely the healthy regression that
was measured. Confidence 0.72: the swap is confounded with four other corpus
changes in the same commit.

**3. The obvious fix — wire the benchmark's source in as training data — is
unavailable, and the reason is itself the finding.** All 2,800 `scorch` files in
`shuvokumarbasak4004/flower-leaf-diseases-dataset-new-and-update` collapse to
**62 pHash clusters** at Hamming ≤ 6; **61 of 61 test clusters also appear in
train**, so its official split is 100 % leaked. Its "1,400 scorch images" are 62
distinct photographs with ~22 augmented copies each. It cannot be training data
(it is our only eval set) and could not carry a class if it were.

- [ ] **4. Swap Stage 1 — real, but LARGELY SUBSUMED by item 6. Do not ship it
      on its own evidence; see the 2×2 at the end of this section.** Every run re-trains and
      re-exports its own `detector.onnx`, and **the ship gate has never scored
      Stage 1** — `model_card.json → metrics_by_split` records `eval_mode:
      classifier_only` for all three splits, and §6's detector-crop eval is a
      post-gate diagnostic that defaults **OFF** for `combined`, the mode that
      ships. Detector quality is therefore an unmeasured per-run lottery, and the
      deployed bundle drew badly. 2×2 crossover on the benchmark, served path:

| served, n=62 scorch | clf 2.6.8 | clf 2.6.9 |
|---|---|---|
| **det 2.6.8** | 0.371 (deployed) | 0.355 |
| **det 2.6.9** | **0.484** | 0.500 |

      Detector effect **+0.113** and +0.145; classifier effect −0.016 / +0.016.
      Paired bootstrap vs deployed: **+0.113, 95 % CI [+0.032, +0.210]**, McNemar
      **8–1, p=0.039**. Healthy is untouched — recall 0.954 → 0.954, NAP
      false-fire 0.046 → 0.046, no species loses more than 1 image. Falsifier
      **run** on 377 supplement `eval_ok` not_a_plant + 300 `eval_ok` plants: NAP
      recall 0.9894 → 0.9920 (0–1, p=1.0), plant accuracy 0.847 → 0.840 (13–11,
      **p=0.84**), plant→NAP false rejection 0.020 → 0.020. The hybrid bundle
      passes `check_bundle.py` clean.
      **Mechanism, and it converges with the served-path defect above:** Stage 1
      returns **zero regions on 14/62 (22.6 %) ornamental scorch vs 5/151 (3.3 %)
      healthy** (Fisher p=3.2e-05), the no-detection path forces a synthetic
      `det_conf = 0.0`, and 10 of those 14 are then routed to `not_a_plant`. All
      13 of 2.6.8's served NAP losses have `det_conf < 0.40`. Across nine
      versions, corr(zero-detection rate on scorch, served scorch recall) =
      **−0.757, p=0.018**. A better detector helps by *staying off* that path:
      NAP misses fall 13 → 7.
      **Before shipping:** re-run the real ship gate on `val_realworld` in
      **served** mode, and reproduce the gain on a second damaged-plant set — 62
      clusters and one falsifier at n=677 are not `val_realworld`.
- [ ] **5. Gate Stage 1 in the notebook (§6).** Measure leaf-detection recall
      **separately on damaged vs healthy foliage** and fail loud on the ratio, so
      the lottery is visible per run instead of after 15 versions. Report served
      numbers next to `classifier_only` in `MODEL_CARD.md`. Longer term: stop
      re-training Stage 1 every run, or checkpoint-select it against that gate.
- [x] **6. The NAP gate ignored the one head trained to answer its question —
      FIXED AND MEASURED, and it closes the 19 pp served-path defect above.**
      `_decide_nap_route` was two-of-two (detector unsure AND embedding k-NN says
      OOD) and never consulted the classifier's own `not_a_plant` probability. On
      the 13 benchmark scorch clusters 2.6.8 re-routes to NAP, the head had put
      **~99 % on `leaf_scorch` and a median 0.0018 on `not_a_plant`** — overruled
      by a k-NN distance that is mostly measuring *unfamiliar*. Instrumenting the
      gate over 890 images shows `P(not_a_plant)` is by far the best separator in
      the bundle, and the gate was the only signal not using it:

| population | n | P(not_a_plant) |
|---|---|---|
| real `not_a_plant` (supplement `eval_ok`) | 373 | **p05 = 0.815**, p50 = 0.978 |
| out-of-domain ornamentals (benchmark) | 213 | p50 = 0.0043, **p95 = 0.024** |
| in-domain plants (supplement `eval_ok`) | 300 | p50 = 0.0048, **p95 = 0.040** |

      So the gate now takes a third condition — a **veto** at
      `NAP_HEAD_MASS_FLOOR = 0.05`: it refuses to fire when the classifier is
      confident the frame *is* a plant. Re-scored end-to-end on the same 890
      images, paired, deployed 2.6.8 bundle, **no retraining**:

| paired, served | n | before | after | lost / gained |
|---|---|---|---|---|
| benchmark `leaf_scorch` | 62 | 0.371 | **0.565** | 0 / 12, **p=0.0005** |
| benchmark healthy | 151 | 0.954 | 0.954 | 0 / 0 |
| **real `not_a_plant`** | 377 | **0.9894** | **0.9894** | **0 / 0** |
| in-domain plants | 300 | 0.847 | 0.853 | 0 / 2 |

      **Zero regressions anywhere in 890 images.** Served scorch 0.565 now equals
      2.6.8's *argmax* 0.565 — the veto recovers essentially the whole
      served-vs-argmax gap that this roadmap has carried as an open defect since
      2026-08-11. Plant→NAP false rejection falls 0.020 → 0.013 in-domain and
      0.210 → 0.000 out-of-domain.
      **Honest limit on the safety side.** The gate turns out to contribute
      almost nothing to NAP recall to begin with: it fires on **1 of 373** real
      `not_a_plant` images (the head alone already catches the other 372), and
      that one had `P(nap)=0.233`, comfortably above the floor. But those 373 are
      *in-corpus-style* non-plants. The veto is conservative by construction — it
      can only remove gate firings, never add them, and it cannot touch the head's
      own NAP verdicts, which are the primary mechanism — yet a genuinely novel
      non-plant that the head misreads as a plant is exactly the case this set
      cannot speak to. Bundles can restore the historical gate with
      `ood.nap_head_mass_floor: null`.

**The two no-training fixes are not additive — ship only the veto.** Items 4 and
6 were found independently and turned out to be two ways of stopping the *same*
failure (Stage 1 finds no box → synthetic `det_conf = 0` → NAP). Crossed on the
same 890 images:

| | benchmark scorch (62) | benchmark healthy (151) | real `not_a_plant` (377) | in-domain plants (300) |
|---|---|---|---|---|
| **det 2.6.8 + old gate (deployed)** | 0.371 | 0.954 | 0.9894 | 0.847 |
| det 2.6.9 + old gate | 0.484 | 0.954 | 0.9920 | 0.840 |
| **det 2.6.8 + veto** | **0.565** | 0.954 | 0.9894 | **0.853** |
| det 2.6.9 + veto | 0.581 | 0.954 | 0.9920 | 0.847 |

On top of the veto the detector swap buys **+1 scorch cluster and costs 2
in-domain plants**; against the deployed bundle the pair together is 0.371 →
0.581 (13–0, p=0.0002) but in-domain plants land back at 0.847 with **12 lost and
12 gained** — churn, not improvement. The veto alone is the whole win, and it is
a code change rather than a bundle change, so it needs no re-validation of
weights. **Conclusion: ship item 6, hold item 4.** Item 5 (gating Stage 1 per
run) survives either way — the detector really is an unmeasured lottery, and this
2×2 only shows that a *better* detector is worth little once the gate stops
punishing a missing box.

### 2.7.2 — the training plan

Designed 2026-08-15 by a 13-agent design/adversarial-verify pass over the
archaeology. **All nine design proposals were refuted at the verify step** — the
plan below is what survived that, plus what the refutations themselves taught.
The load-bearing numbers are re-derived here independently.

#### Verdict on "~10 % global improvement"

Three axes, three different answers. Do not quote one number.

| Axis | Instrument | 2.6.8 today | +10 % relative | Verdict |
|---|---|---|---|---|
| In-domain macro-F1 | `val_realworld` | 0.858 | 0.944 | **Dead.** The whole 2.6.8/2.6.9/2.7.0/2.7.1 spread is 0.834–0.870. |
| Out-of-domain, served | 213-cluster benchmark | 0.784 → **0.840** | 0.862 | **~70 % already closed** — by the veto, a serving change. |
| Never-trained in-domain | 585 post-2.6.8 supplement rows | 0.807 | 0.887 | **Open, and the only credible route.** |

The third row is where the headroom actually is, and it is a *data* number.
Scoring the deployed 2.6.8 over all 1,907 `eval_ok` supplement rows, split by
provenance:

| | n | accuracy |
|---|---|---|
| rows in 2.6.8's own corpus (`carried_from_v5`) | 1,322 | **0.964** |
| rows it never saw (`v7`/`v8`/`v8b`/`new`) | 585 | **0.807** |

A **15.8 pp generalisation gap** on real in-situ photography — not the
benchmark's detached-leaf regime. The 113 errors are concentrated:

| class, never-trained rows | n | recall | goes to |
|---|---|---|---|
| `leaf_spot` | 84 | **0.512** | pests 12, scorch 12, healthy 11 |
| `leaf_scorch_underwatering` | 138 | **0.739** | **healthy 25**, not_a_plant 5 |
| `powdery_mildew` | 180 | 0.889 | gray_mold 6, pests 5 |
| `nutrient_deficiency` | 82 | 0.902 | healthy 6 |
| `healthy` | 61 | 0.934 | pests 2 |

`leaf_spot` splits **0.311 on houseplants (n=45) vs 0.744 on garden ornamentals
(n=39)**, Fisher p=8.8e-5. Fixing `leaf_spot` to 0.85 and halving the
scorch→healthy misses is +43 images = 0.807 → 0.880, **+9.1 % relative** — the
only place in the record where "~10 % global" is arithmetically available. Sized
honestly: ~300–400 judged in-situ houseplant `leaf_spot` positives and ~150–250
*mild/early* scorch positives (marginal browning short of necrosis — the 25 that
read as healthy), with depth on ≤8 named genera rather than another long tail.
That is a collection round, not a training run.

#### ⚠ The blocker nobody had priced: run-to-run variance

Two archived pairs share a version **and a byte-identical `hyperparams` dict**
(md5-matched). Scored served on the benchmark:

| pair | corpus Δ | scorch | healthy |
|---|---|---|---|
| 2.6.0 `x5`→`x6` | **7 images** | 28/62 → 29/62 (3–4, p=1.0) | 149/151 → **141/151 (8–0, p=0.0078)** |
| 2.4.0 `x8`→`x9` | 794 images | 25/62 → **38/62 (3–16, p=0.0044)** | 139/151 → 130/151 (9–0, p=0.0039) |

**Two runs of 2.6.0 that differ by seven corpus images swing eight healthy
clusters at p=0.0078** — a delta the ship gate would call significant. The
2.4.0 pair's 13-cluster scorch swing is confounded by 794 images, so it is an
*upper* bound on seed noise, not a clean estimate; no lower bound exists,
because **no seed replicate has ever been run** — all 15 versions moved the
corpus and the seed together. Practical consequence: every version-to-version
delta smaller than ~8 healthy clusters is inside the band, which covers
2.6.7→2.6.8 (−9 scorch), 2.6.8→2.6.9 (+1) and most of the 2.4.0–2.6.9 plateau.
It does **not** cover the 2.7.0 cliff (0 gained / 31 lost).

#### Order of work — nothing on the A100 until R0 and Z1 are done

- [ ] **R0 — build the instruments (notebook, no GPU). Blocking.**
      **(a)** A permanent never-trained in-domain **holdout**: a third
      `split_hint` value or a stable `phash` bucket, ~500–600 rows stratified
      over class × `subject_domain`, excluded from train / temperature_cal /
      conformal_cal / every `val_*` from 2.7.2 forward and never re-admitted.
      Why: on the 1,322 rows *both* eras trained, 2.6.8 vs 2.7.1 is 35–32,
      **p=0.807** — dead flat; on the 585 only 2.7.x trained, 2.7.1 leads by
      8–24 pp. As routed today the supplement is a training-set-membership
      detector, not an accuracy instrument. (Control that it is not merely
      insensitive: the same subset does catch 2.6.9's real regression, 31–67,
      p=2e-4.)
      **(b)** Split the benchmark's scorch arm **by taxonomy**. 2.6.8 + veto
      scores hibiscus **31/31** and banana_bush **4/31**; a brown-pixel measure
      puts banana_bush "scorch" at median 0.036 vs hibiscus 0.238 — the healthy
      `dwarf_white_bauhinia` arm (0.177) is browner than banana_bush scorch. So
      report hibiscus as the *necrosis regression* arm (saturated — it can only
      measure loss now) and banana_bush separately as a turgor-loss / healthy-
      precision probe. **(c)** Publish the replicate band above in §6 as the
      noise floor any benchmark claim must clear.
- [ ] **Z1 — per-class decision bias, cached logits, no GPU.** *Not* the refuted
      lever (that was a constant bias on **2.7.1** chasing 2.6.8's OOD recall at
      14× the false-fire). This is 2.6.8, small, measured on the never-trained
      585 only: `leaf_scorch` bias +1.0 → scorch 0.739 → **0.841**, healthy
      **unchanged at 0.934**, other diseases 0.810 → 0.795, overall 0.807 →
      0.821. On the benchmark the same bias holds hibiscus 31/31 and lifts
      banana_bush 3→9/31 at a cost of one healthy cluster. **Falsified if** the
      `val_realworld` sweep costs > 1 pp healthy recall or > 2 pp on any other
      disease class. If it survives it is a `classes.json` field plus a
      `pipeline.py` read — not a retrain.
- [ ] **R1 — the seed replicate. FIRST A100 run.** Re-run 2.7.1's exact config,
      new seed, everything else byte-frozen, `TRAIN_MODE='gamma'`. *Predicted:*
      `val_realworld` within ±0.02 of 0.870, benchmark scorch stays ~0/62.
      **Falsified if** it lands at benchmark scorch ≥ 15/62 — in which case the
      2.7.0 cliff is a run lottery, the corpus archaeology is void, and 2.7.2
      becomes "run it three times, ship the best on the holdout". It is the only
      run whose result cannot be wasted: every later A/B is uninterpretable until
      the band is known.
- [ ] **R2 — the LoRA control.** `USE_LORA = True → False` (§4). In 15 versions
      no bundle has ever shipped without LoRA, so there is **no frozen-backbone
      control anywhere in the record** — and LoRA is what gives corpus
      composition write access to the embedding. Transfer AUROC (linear probe,
      in-domain → benchmark) falls 2.6.8 **0.916** → 2.6.9 0.901 → 2.7.0 0.809 →
      2.7.1 **0.747** while in-domain CV AUROC *rises* (0.982 → 0.990); rank
      correlation between the two across nine bundles is **−0.65 (p=0.058, n=9)**
      — underpowered, and pointing the wrong way for anything that optimises
      in-domain. *Predicted (wide estimate):* in-domain −0.01 to −0.03, transfer
      0.82–0.92. **Falsified if** transfer ≤ 0.78 — close the branch, do not
      spend a follow-up on rank or block count.
- [ ] **R3 — label smoothing off.** `LABEL_SMOOTHING = 0.05 → 0.0` (§4).
      Corroborated only circumstantially (fitted temperature < 1 in all 12
      archived bundles; 2.6.8 = 0.849). *Predicted:* transfer +0.00 to +0.04,
      in-domain flat. **Falsified if** transfer moves < 0.03 with in-domain flat
      — a coin-flip-sized lever, do not repeat it at another value.

**Deliberately not proposed:** any sampler-weight or `kaggle_ornamental` change,
head-objective/margin redesign, bank rebuild or threshold retune, checkpoint
selection against the benchmark, TTA, crop voting, `FOLD_WHOLE_FRAME_VOTE`, blur
gate, `_MAX_RECLASSIFY`, the detector swap (held), and any new capacity (DINOv3,
ViT-B). The features are not the bottleneck; adding capacity does not address a
transfer failure.

#### Run protocol

- **Baseline is 2.6.8 + the veto**, never bare 2.6.8 — the veto ships
  independently and 2.7.2 must not be credited with it.
- **Four populations, every run, reported together:** (1) `val_realworld`
  macro-F1, `classifier_only` *and* served — publish both, every headline to date
  is whole-image; (2) the R0 holdout, overall + per class + per `subject_domain`
  — this is the improvement metric; (3) benchmark **hibiscus** arm served with
  the veto on — a pure regression metric, floor 31/31; (4) benchmark healthy
  (floor 0.954) + supplement `not_a_plant` (floor 0.9894).
- **Statistics:** 20k paired bootstrap for the interval, exact McNemar for the
  sign, on the same images — and a benchmark claim must additionally clear the
  **replicate band** or be reported as "inside run noise".
- **Power, so nobody over-reads:** benchmark scorch at n=62 has a 95 % CI
  half-width of **±0.123**; resolving +5 pp needs ~352 clusters, +10 pp needs 86.
  `val_realworld` per-class n is 87–104, i.e. ±0.08–0.09 on recall — a 10 %
  relative move on those classes sits at the edge of what the split can see.
- **One change per run.** 2.6.9 and 2.7.0 moved several things at once and are
  uninterpretable; 2.7.1 moved one thing and is the only failure that taught
  something.

#### The scorch source that IS the benchmark

An adversarial verifier refuted the "abiotic scorch supply is exhausted" entry
above by finding `aditisalvi04/indoor-plant-disease-dataset` — never inventoried,
never trained, carrying an explicit **`Hibiscus_Scorch` (1,200 files)** folder,
missed because the inventory lister capped pagination and returned silently. The
find is real. **Its usefulness is not — and the gate that killed it is the one
the verifier itself specified.** Sampled 60 `Hibiscus_Scorch` files evenly and
pHashed them against the benchmark:

| folder | files | benchmark hits at Hamming ≤ 6 | distinct in sample |
|---|---|---|---|
| **`Hibiscus_Scorch`** | 1,200 | **60 / 60** (median Hamming **0**) | 24/60 |
| **`Hibiscus_Healthy`** | 1,200 | **20 / 20** (median **0**) | 8/20 |
| `Hibiscus_Necrosis` | 1,200 | 0 / 30 (min 12) | 19/30 |
| `Aloe_Sunburn` | 3,343 | 0 / 30 (min 14) | **30/30** |
| `Money_Plant_Healthy` | 2,760 | 0 / 20 (min 10) | 20/20 |
| `Rose_Black_Spot` | 3,000 | 0 / 6 (min 12) | 6/6 |
| `Turmeric_Blotch` | 1,904 | 0 / 15 (min 14) | 15/15 |

**Its Hibiscus arms ARE the benchmark** — same photographs, not similar ones.
Training on them destroys the only never-trained eval arm we have (and the only
one 2.6.8 + veto scores 31/31 on). So `Hibiscus_Scorch` and `Hibiscus_Healthy`
are permanently eval-poison: add them to an explicit deny-list, do not merely
leave them un-wired.

Everything else sampled is **clean of the benchmark and not
augmentation-collapsed** (30/30 distinct for `Aloe_Sunburn` vs 24/60 for the
contaminated scorch folder). That makes the dataset real supply on the *other*
genera — most interestingly **`Aloe_Sunburn`, 3,343 files on a true houseplant
genus**, which maps to the deferred `sunburn_or_scorch` feedback label the head
does not yet predict. `Hibiscus_Necrosis` (1,200, clean) is the same genus as
half the benchmark's scorch arm without being the same pictures.

Two process lessons, both worth more than the dataset:

- **The inventory lister lies by omission.** A full uncapped listing returns
  **49,878 files across 20+ folders**; the original inventory saw 24,000 and the
  verifier's "uncapped" re-run also stopped short. Every negative result in that
  lane — 207 datasets' worth of "nothing here" — is untrustworthy until re-run
  with a lister that *raises* when `nextPageToken` is still live at exit.
- **pHash against the benchmark before anything else.** The find and the
  refutation of the find were both correct at their own step; only the
  contamination gate separated them. Any new source goes through it first.

#### Making a domain-level claim

The benchmark cannot support one. Its scorch arm is 62 clusters across **2
species that disagree completely** (hibiscus 31/31, banana_bush 4/31);
between-species SD 0.616 puts a domain-level estimate from k=2 at **±0.854** —
the interval covers the unit range. Reaching ±0.15 needs k≈65 species at that
SD, or k≈11 if the off-taxonomy banana_bush arm is dropped. The healthy arm is
fine (k=6, SD 0.061, ±0.049). So: split the arm by taxonomy; build a **second
never-trained arm from production scans** in the in-situ regime the product
actually sees (benchmark subject fraction 0.085–0.31 = detached leaf on a sheet;
supplement scorch 0.74–0.85 = frame-filling in situ) and hold it eval-only
forever; target 8–12 scorch species × ≥15 clusters; **report per species, never
pooled** — "0.371 → 0.565" was 11/12 one species.

Run-log checks, each proving one fix landed:

| Look for | Expect | Means |
|---|---|---|
| `Kaggle supplement manifest:` | **3389 rows**, `split_hint` in the column list | **Read this first.** A stale upload otherwise trains and grades silently. |
| `Kaggle supplement: eval/train routing basis =` | `split_hint` | `confidence>=0.8` = the fallback; `none` = every row is grading the model. |
| `Kaggle supplement routing:` | ≈ `1907 / 695 / 787` | The offline verdict is being honoured. |
| `per-split subject_domain:` → `val_realworld` | a real `garden_ornamental` count | **The ship gate can finally see the broadened domain.** |
| `source floors OK: …` | present | §2 gate ran. If it *raised*, a source is down — read it, don't bypass. |
| `MaxLogit OOD floor calibrated [plant_only]` | `sep=` **positive** | Fallback alive for the first time since v2.3. Still negative ⇒ flag it. |
| `conformal: selected alpha=… lambda_reg=…` | **not** the last grid value | Search converged inside the grid. |
| `head early stop @ epN (best ep=M)` | `M` well above 3 | Best-epoch 3 was the corpus-loss symptom. |
| `[final val_indomain] disease_macro_f1` | toward **0.86** | Headline regression undone. |

### 2.7.x — polish, one measurable change at a time

Each is small, cheap, and A/B-able against 2.7.0 on the same corpus. Do them
one per run, not in a batch — a batch is unreadable.

- [ ] **Report the production-faithful number.** §6's detector-crop eval now
      defaults ON for `gamma`/`frontier` (OFF for `combined`, where it OOMs).
      Every headline metric to date scores the classifier on a **whole val
      image** while production classifies a **detector crop** — publish both in
      `MODEL_CARD.md` and treat the gap as a first-class finding.
- [ ] **Aspect-preserving preprocessing (highest-value single experiment).** The
      eval transform is `Resize(224×1.05) → CenterCrop(224)`, which trims the
      leaf **margin** — exactly where `leaf_scorch` lives, and its §3 probe
      recall is 0.39–0.52 across all four runs on record, by far the worst of
      any class. That says *features*, not supply. Test resize-and-pad, and
      `IMG` 224 → 308, **on the §3 probe first** (cheap, no full train).
- [ ] **`USE_REGISTERS`** — `dinov2_vits14_reg` has cleaner features; off today
      only because the validated 0.91 probe used non-register weights. One flag,
      one run, one paired bootstrap.
- [ ] **Revisit `doctorp_pepper` (0.8 / prior 0.5) and `kaggle_ornamental`
      (1.0)** now that domestic edibles are in-domain — pepper is being
      down-weighted for being a "crop" the domain now includes.
- [ ] **Build the comparison set** from the 2,593 exportable production scans —
      see `DATASET_LABELING_PLAN.md`. `pending` rows + the 111 feedback rows are
      the eval-eligible half (`auto_yes` is train-only forever). ~150/class gives
      ±0.06 per-class recall and ±0.023 macro-F1; healthy wants 400 to resolve a
      2 pp false-fire change. Note it cannot measure NAP or blur — recording
      excludes both by design.
- [ ] **Refresh `MODEL_CARD.md`** — still says "v2.6.9 (in training — NOT YET
      TRAINED)" and contradicts this file on aqua export precision.
- [ ] **Collect kitchen-garden edibles into the supplement** (not into a
      train-only slug — that routing is why the 2.26k-image loss was invisible).

### 2.8 — the leap

> From "a good leaf classifier" to a **holistic, trustworthy plant doctor**.
> Ordered by expected effect per unit of risk, with the evidence for each. Not
> a wishlist: items 1–3 are the ones that change what the model *is*.

**1. Real-distribution data at scale — still the biggest lever.** Every source
today is scraped or curated; production scans are what users actually shoot.
2,593 already exportable and growing. The evidence is already in the corpus:
DoctorP (real app photos) is the source that supplies the starved classes, and
`val_realworld` is seeded from real-photo sources. Nothing else on this list
moves reliability as much per unit of effort.

**2. Multi-label instead of single-label — the biggest *modelling* change
available.** A softmax over 9 classes cannot say "underwatered **and**
nutrient-deficient", which is the single most common real co-occurrence and
plausibly a chunk of why `nutrient` over-fires (P 0.670, 1.25× over-prediction)
and why scorch/nutrient sit on a contested boundary. Sigmoid head + per-class
thresholds + per-class conformal. Cost is real: multi-label annotations on at
least a subset, a new loss, and a server contract change. Do it *after* the
boundary is stable, never during.

**3. Segmentation, not a box.** Stage 1 → lesion **area**, so severity is
measured instead of heuristic ("12% of leaf area affected" beats "moderate"),
and a single diseased leaf stops being diluted in a whole frame. Also the
honest fix for the crop-vs-whole gap item 2.7.x measures.

**4. Capacity — `frontier`, and possibly a newer backbone.** `frontier` is now
a plain mode, so it costs one run.
**Trigger: only after 2.7.0 actually beats 2.6.8** — without a trustworthy
incumbent there is nothing to A/B against, and that is precisely how 2.6.9 got
shipped-then-reverted. Honest expectation: with a ~0.91 frozen-probe ceiling on
ViT-S and precision-bound weak classes, a bigger backbone historically buys a
few points at best; its real value is as a **distillation teacher**, so if it
wins, distill back into gamma/aqua and ship the win on CPU. Worth checking in
the same breath: **DINOv3** (2025) reports substantially better dense features
than DINOv2 and would be a drop-in swap in this exact frozen-backbone recipe —
**verify its licence terms before committing**, they are not DINOv2's Apache-2.0.
- [ ] Probe DINOv2-S / DINOv2-B / DINOv3-S under §3's cached-feature probe —
      one afternoon, no training, tells you whether capacity is the ceiling.

**5. Species-conditioned priors.** Species ID and symptom triage already exist
and are already called on the same scan. Conditioning the disease posterior on
species is nearly free (a prior table × logits) and rules out combinations that
cannot occur on a given genus. Cheapest real accuracy win on this list.

**6. Scope expansion.** Activate `edema` + `sunburn` (content cards and deferred
feedback labels already exist); add root rot via image + symptom fusion.
Diagnose *conditions*, not leaf classes. Gated on the scorch/nutrient boundary
being stable — see Decisions.

**7. Safety at scale.** Per-class conformal, an explicit **abstain →
expert/community escalation** path, calibrated severity that never over-treats.

---

## Documentation debt

None of it blocks a release.

- [ ] **`privacy.html` — third-party processors undisclosed. Highest-priority
      doc item**, as the canonical disclosure surface. `/api/identify` sends
      photos to PlantNet; `/api/chat`, `/api/care-plan`, `/api/species-diseases`
      and `/api/species-profile` send text to OpenRouter. Neither is named. It
      also omits the stored FCM token and that error reports carry a hashed
      client IP.
- [ ] **`README.ru.md` has diverged from `README.md`** — missing 12 of 24 API
      rows, the project-tree line and two Documentation bullets; carries a
      project-website block the English one never gained.
- [ ] **`DEPLOYMENT.md`** — tells operators to set the server URL "through
      Profile → Server URL" (compile-time only, no such setting); lists an
      onboarding entry point the coach-mark rework removed; config table misses
      the three `TELEMETRY_*` keys.
- [ ] **`THIRD_PARTY.md`** — lists `android-image-cropper` (not a dependency;
      cropping is first-party) and **Koin** (replaced by Hilt); misses osmdroid
      and the RuStore SDK. `NOTICE` still lists Retrofit, removed some time ago.
- [ ] **Both READMEs link to `server/models/classes.json`** as the canonical
      class list — gitignored and absent, so the link 404s.
- [ ] **Consolidate repetition** — the "model artefacts are not committed /
      fail-fast" paragraph appears in five places and optional-integration setup
      in four. `DEPLOYMENT.md` should own both; the rest link.

---

## Later / maybe

- [ ] **Spider-mite split** from `pests` (deferred — `pests` P already 0.94).
- [ ] **More UI languages** (server content already bilingual).
- [ ] **Expert-human consultation** — ties into 2.7 safety escalation, builds
      on the shipped second-opinion loop.

---

## Decisions

- **Reliability & safety first** — optimize against a confident wrong diagnosis
  (precision + honest uncertainty + OOD/blur refusal) over headline recall/F1.
- **Data > architecture** — the ceiling is data/precision; spend on the flywheel
  and scope before backbone size.
- **One species surface** — species info lives on the species screen alone;
  every other screen links via `SpeciesInfoCardView`.
- **New knowledge cards are best-effort, never blocking** — LLM + cache with a
  graceful `available:false`.
- **No new disease classes until the scorch/nutrient boundary is stable** —
  scope expansion waits for 2.7.
- **Target store: RuStore** (RU audience; app already bilingual). No Google Play.
- **No Compose rewrite** — keep XML Views, single-module; modernize the guts
  (Hilt, Ktor).
- **Stay free + honest** — calibrated, OOD-aware, on-device offline, no account,
  privacy-first.
- **Data loop is local** — collect/judge offline → Kaggle supplement → notebook
  pulls it.

---

## Working agreements

**Model choice for delegated work.** *Is there already a pattern in this repo to
follow?* Yes → **Sonnet 5** (follows instructions literally, so spell out
scope). New architecture across many files → **Opus 5** (`effort: xhigh`,
generous `max_tokens`; it verifies its own work, so "double-check your answer"
makes output *worse*, and it expands scope readily, so state the boundary).
Nobody has decided what it should be → **Fable 5**, once, to decide it (omit
`thinking` entirely; it degrades when you enumerate steps — give intent and
constraints, then get out of the way).

**RU string review is a standing gate.** Machine-written Russian UI copy reads
wrong in ways tests never catch. Review the `values-ru/strings.xml` diff on
every merge.

**CI** (`.github/workflows/ci.yml`) is push-only, on `main`, `claude/**`,
`work/**` and `codex/**`, with a `paths-filter` running the **android** and
**server** jobs only when their trees change. Deliberately no `pull_request`
trigger: every branch that reaches a PR was pushed here first, so a PR trigger
only duplicates runs (they land under a different `github.ref`, so `concurrency`
does not cancel them). Android:
`ktlintCheck` + `detekt` + `testDebugUnitTest` + R8 `assembleRelease`. Server:
`ruff` + `pytest`. Dependency updates arrive via `.github/dependabot.yml`.
