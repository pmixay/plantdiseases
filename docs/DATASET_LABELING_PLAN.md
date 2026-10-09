# Dataset & labeling plan — local collection + offline export

> **2026-08-23 audit correction.** The v8 supplement and private benchmark do
> not satisfy the independence, rights, identity, or label-verification claims
> assumed by older sections of this research log. In particular, the supplement
> is mostly missing auditable acquisition/consent records and human label
> verification, and the 182-image “OOD” artifact contains
> only healthy/scorch images from one studio source with augmentation families.
> Authenticated Kaggle metadata confirms the configured supplement is still
> revision 8 (not v9) and the OOD artifact is revision 2 as of this date.
> Historical scores and “falsified lever” conclusions below are exploratory, not
> release evidence. The verified counts are in [`docs/ROADMAP.md`](ROADMAP.md);
> the v9 collection protocol, the benchmark-v3 spec and the locked test-set
> carve rule are in this file, directly after the routing summary, and the
> status of every older section is tabulated in "Status of the numbers in
> this file".

The data loop is **local and offline**: the maintainer scrapes + vision-judges
images into a Kaggle supplement (`maxayyt5/houseplant-disease-eval-supplement`)
that `train_notebook.ipynb` pulls directly. The server only *records* anonymized
production scans + feedback for offline export — no server review queue, no
ingestion. Roadmap: `docs/ROADMAP.md`; per-run metrics: the run's own
`model_card.json`, shipped inside the bundle — `server/models/MODEL_CARD.md` is
version-agnostic and carries no run-specific numbers by design.

```
  LOCAL (maintainer, offline)                 SERVER (production)
  scrape → source-disjointness → pHash dedup  /api/analyze → anonymized scan record
  → per-creator cap → vision-agent judge      user feedback → correction (weak label)
       │ manifest.csv (verified_by=agent_vision)       │
       ▼                                               ▼
  Kaggle supplement  ◄──── fold judged ──────  tools/export_data.py (offline pull)
       │ pulled by train_notebook.ipynb → model bundle
```

## Study-project policy (2026-09-21): licences are provenance, not a gate

Everything below that says a licence, a landing URL, an immutable source id, a
capture family or a first-seen version is *required* now reads: **recorded when
present, defaulted with a warning when absent, never a reason to drop a row or
refuse a run.** The `STRICT_SUPPLEMENT_*` knobs in notebook §2 default to
`False`; the per-source licence filters (iNat `photo_license`, COCO licence
ids, Open Images licence keys, the DoctorP opt-out) are gone and every fetcher
writes what it saw into the sidecar. `model_card.json →
source_inventory.license_policy` tallies the result per run. What still gates
is *quality*: the benchmark deny-list, `bench_leak` / `phash_mismatch`, the
blur floor, pHash and SHA dedup, the domain judge, `REQUIRED_SOURCE_FLOORS`
and `SUPPLEMENT_MIN_ROWS`.

### Sources returned under the 2026-09-21 policy, and their eligibility

Eligibility here means data quality and independence, which is what the
notebook still enforces; the licence was the only reason each of these was
switched off on 2026-08-25.

| source | rows it contributed (2.7.2 inventory) | what the licence filter would have cost | eligibility (quality) | verdict |
|---|---|---|---|---|
| **DoctorP** + **DoctorP-pepper** (`doctorp`, `doctorp_pepper`) | 1,403 + 155 train rows; eval-eligible in every split | all of it (CC BY-NC-SA data cards) | The only public source of real plant-doctor *app* photos: the gardener's framing, no augmentation, the source that supplies `nutrient_deficiency` and `gray_mold` in deployment framing. Uniformly ~128 px, so `fetch_doctorp_disease` runs with a lowered `min_side`; labels are the dataset's folder labels (no per-row confidence); not on the benchmark deny-list; SHA/pHash-deduped against every other source; `REQUIRED_SOURCE_FLOORS` 1000 / 80 restored so a silent upstream loss fails the run | **eligible, restored at its prior weights** (`doctorp` 1.0, `doctorp_pepper` 0.8) |
| **iNaturalist** research-grade photos outside CC0 / CC-BY / CC-BY-SA (`inat`, `inat_pest`, `inat_physiological`, `inat_hard_greentexture`, `inat_animalia`) | 2,505 + 1,597 + 504 + 927 + 495 rows | ~87 % of the supply: 83.0M research-grade Plantae observations with photos, 10.8M under the three permitted licences (live API, 2026-09-21) | The licence is the uploader's account default and is uncorrelated with focus, framing or identification quality; research grade is the quality bar and is unchanged. Every other gate applies as before (green-content check, `min_side`, dedup, per-taxon caps, benchmark deny-list) | **eligible; the `photo_license` API filter is removed**, `license_code` recorded per photo (`unlicensed` when iNat reports none) |
| **COCO val2017** images with licence ids 1–3 (`coco`, `coco_hard_partial_plant`) | part of 750 + 223 NAP negatives | ids 1–3 are the NC variants of the same Flickr pool; a minority of the split | Indoor-clutter and partial-plant *negatives* for the `not_a_plant` head; their licence has no bearing on whether they are non-plants. Plant-category exclusion and the partial-plant area rule are unchanged | **eligible**; licence id and name recorded |
| **Open Images** validation rows without a metadata row or with a non-CC-BY-2.0 key (`openimages_indoor`) | part of 640 NAP negatives | small: Open Images photos are CC BY 2.0 by construction, so the filter mostly dropped rows with no metadata line | Indoor negatives; same reasoning as COCO | **eligible**; licence key recorded (`unknown` when the metadata row is missing) |
| **Supplement v8 rows** (`supplement*`) | 1,900 eval-eligible / 695 train-only / 787 boundary | all 3,389 rows: v8 carries no `license_id`, `immutable_source_id`, `capture_family_id` or `first_seen_model_version` column, so the strict loader raised on line 2 | The corpus every shipped bundle was fitted to. QA'd on bytes on 2026-08-17 ("Corpus verification" below): zero benchmark leakage, zero duplication, 4 blur failures, 54 rows under the 384 px floor. Fallbacks: `sha256:` of the bytes as source id, no family (each row its own group, as v8 always was), `split_hint` routing, the permanent `HOLDOUT` carve for the never-trained slice | **eligible under the soft contract**; the one WARN per absent column is expected on v8 |

Not returned, because they never left for licence reasons: the benchmark
source (`aditisalvi04`, deny-listed for contamination), `coleaf` and
`kaggle_strawberry` (agricultural proxies, removed for domain), `kaggle_nutrient`
(dropped in 2.7.0 for content). The deny-list rule ("deny at the branch")
stands.

## Supplement routing — version-aware and fail-closed

- **Supply (train):** every row with a passed filter verdict (or none — a
  manifest without the column is treated as all-`keep` with a warning) is
  eligible. Rights, immutable source identity, capture-family identity and
  human verification are recorded when present and defaulted when absent
  (study-project policy above); they are not admission criteria.
- **Candidate evaluation:** a row/family is eligible only when its
  `first_seen_model_version` is later than the candidate model's complete
  training lineage and no capture/augmentation sibling was seen. An `eval_ok`
  bit is not permanent evidence. Evaluation/calibration families never re-enter
  training for that candidate.
- **Locked release evidence:** must be a separately versioned, never-trained,
  deployment-representative v9 test plus a true OOD set containing non-plants
  and difficult plant negatives. The current private artifacts are diagnostic
  regression sentinels only.

Grouping is by stable capture/plant/session family first, with robust geometric
matching as a backstop. pHash uniqueness and `(source, plant_id)` alone do not
establish independence.

## Status of the numbers in this file (2026-09-16)

This file is the research log and is kept whole; finished work lives in git
history, the reasoning stays here. After the 2026-08-23 audit the sections below
carry one of three statuses. Each stale section also carries an inline
`Superseded` note at its head so a reader landing mid-file sees it.

| section | status | why |
|---|---|---|
| Supplement routing — version-aware and fail-closed | **current** | matches notebook §2 (`STRICT_SUPPLEMENT_*`, `SUPPLEMENT_EVAL_THROUGH_MODEL_VERSION`) |
| v9 collection protocol · OOD benchmark v3 · Locked test set · Sequencing (below) | **current** | written against the code on this branch; **P0 (2.7.5) is the minimum** — P1–P8 are the full protocol for `eval_ok` rows |
| Acceptance criteria (every version) | **baseline, extended** | still true; the per-row contract is the light one in P0 (2.7.5), with P4 as the full record for eval rows |
| Corpus verification — the published v8 (2026-08-17) | **bytes stand, reading superseded** | the counts (3,389 rows, 662 distinct scorch, 54 under 384 px, 5 failures) were measured on bytes and are not disputed; "the eval set is outside the training corpus" and "scorch scores 0.000 across three releases" rest on the v2 sentinel, which the audit found non-independent, and on archive predictions that contradict the headline scores |
| Domain — houseplants → ornamentals → edibles | **policy current, numbers exploratory** | the domain line stands; the `kaggle_ornamental` ablation numbers are exploratory |
| Version status · Source loss · 2.6.10 · v8 · stale-upload incident | **historical** | metrics are pre-audit and exploratory; the deny-list, the `REQUIRED_SOURCE_FLOORS` lesson and the `SUPPLEMENT_MIN_ROWS` guard stand in code; v8 rows carry no per-row licence/family/first-seen columns; since 2026-09-21 §2 loads them with documented fallbacks (see the study-project policy section) |
| v9 — the finding that changes the target, §1–§5, §7, §9 | **exploratory** | probes and ablations scored on the v2 sentinel (one species, one session, ≥11 SIFT+RANSAC pseudo-replicates among the nominal 31) and on classifier-only argmax; the *direction* (early/dull scorch is confused with `leaf_spot`; plain ground reads as `not_a_plant`) is the collection hypothesis, the numbers are not release evidence |
| v9 §6 stratum table | **targets stand** | the per-stratum targets and predicates are carried into P1 unchanged; the coverage brackets are exploratory |
| v9 §6b — the out-of-domain ship gate | **superseded** | §6 now has `REQUIRE_OOD_BENCHMARK = False`, `OOD_ARM_FLOORS = {'hibiscus': 0.70}` and `OOD_MAX_HEALTHY_FALSE_FIRE = 0.12` marked *advisory heuristic*; the notebook records `diagnostic_only: True, release_blocking: False`. `REQUIRE_OOD_DIAGNOSTIC_RUN = True` only makes a run that cannot download and score the sentinel fail — it *records*, it does not gate. Nothing blocks on v2 |
| Build rules answered this round | **partly current** | `banana_bush` retirement is in code (`RETIRED_DISEASE_ARMS`); blur → `train_only` stands; "Collection scope: deferred" is closed by the protocol below |
| Standing disciplines — "no fixed never-trained benchmark by choice" | **superseded** | replaced by the locked deployment-representative test set |
| Risks — "labels are a proxy, agent-judged by design" | **superseded** | v9 `eval_ok` requires human adjudication (P6) |

### v9 inventory at handoff (2026-08-25)

No v9 corpus has been assembled or published. The existing local material is a
candidate inventory, not a release (counts quoted from the handoff; not
re-measured here — see "What this plan could not verify"):

- `houseplant-disease-eval-supplement/` is v8: 3,389 images.
- `staging/` has 24,275 images (23,821 unique exact pHashes); 20,747 rows are not
  in v8, but most are rejected or unreviewed legacy candidates.
- `user_data/plantvision_export_20260806T102212Z/` has 2,727 image files. Only
  80 new, head-class images have explicit positive/corrected user feedback and
  are absent from v8. They remain quarantine material until label adjudication
  and the export batch's consent/acquisition record are attached.
- The 11 new user-positive scorch conflicts (`ls_2580`, `ls_2616`, `ls_2617`,
  `ls_2624`, `ls_2626`, `ls_2646`, `ls_2682`, `ls_2793`, `ls_2825`, `ls_2830`,
  `ls_2863`) are out-of-domain, ambiguous, poorly framed, or show a different
  failure mode. None is an accepted early/dull marginal-scorch example.

Do not auto-promote staging rows on folder name, model verdict, pHash uniqueness
or user confirmation alone. For project-owned or user-consented scans, do not
invent per-image public-licence strings: one batch acquisition/consent record
plus opaque row-to-record references (P5). For internet images, retain the
landing URL and reuse evidence. Private storage changes access, not provenance
requirements.

## v9 collection protocol (2026-09-16)

"Accepted" means two things at once: notebook §2 `fetch_kaggle_supplement`
loads the row without raising, and a human can defend the row on rights,
identity and label. **P0 is the minimum for the first; P1–P8 are what makes an
`eval_ok` row defensible.** A number marked **target** is a target; every other
number is traceable to this file, the notebook or `server/tools/`.

### P0. Minimum to collect — read this first (2.7.5)

Since 2.7.5 nothing in the per-row record stops a run: §2 fills defaults,
resolves conflicts the safe way, and prints one `provenance, resolved without
stopping` line with the counts. What a collection round needs:

| | what | why |
|---|---|---|
| **required** | the photo, under `<class>/` (one of the 9 head classes) | the folder is the label |
| rows you want **graded** | a manifest row with `split_hint=eval_ok` and `first_seen_model_version=never` | a row is graded on only when you say so; a photo with no row always trains |
| boundary negatives | `hn_kind=boundary` | train-only at its own sampler weight |
| several shots of one plant, one session | one shared `capture_family_id` | keeps them in one split group; a family with any training member trains as a whole |
| boxed strata | `<name>.txt` beside `<name>.jpg`, YOLO format — the plain export of makesense.ai / CVAT / Label Studio / Roboflow | Stage 1 trains on it (§3); stored in the EXIF-rotated frame the labelling tool showed |
| optional | `source_url`, `license_id`, `creator`, `subject_domain`, `subject_plant`, `confidence`, `immutable_source_id` | recorded to judge source quality; never gated |

The round, end to end:

1. Drop photos into the class folders (box files beside them where you drew boxes).
2. Give the photos you want graded a row: `split_hint=eval_ok`,
   `first_seen_model_version=never` (plus `capture_family_id` when a plant has
   several shots). Leave everything else blank.
3. `python -m server.tools.filter_supplement --root <supp> --bench <oodbench> --out <report>`.
   It adopts every unlisted photo as a `train_only` row, QAs all of them (blur,
   tiny, duplicates, benchmark leak — a leak is a non-zero exit), and counts the
   rows that carry a box file. Copy `<report>.csv` over `manifest.csv`.
4. Publish with `kaggle datasets version`.
5. After training, the bundle's `analysis/supplement_manifest_next.csv` is that
   manifest with `first_seen_model_version` stamped on every row the run trained
   on (held-out rows untouched, so the holdout keeps its membership). Publish it
   as the next revision's `manifest.csv` — that replaces re-stamping by hand.

Two rules protect the comparisons and cost nothing: **never rename or move a
published file, and never add a `capture_family_id` to a row already published**
— the permanent holdout hashes `(source, plant_id)` and both change it.

What still stops a run (each is a real defect, not paperwork): no
`manifest.csv`; no `hn_kind` column or zero boundary rows; fewer than
`SUPPLEMENT_MIN_ROWS` rows (a stale upload); no eval-eligible row at all; a
`bench_leak` or `phash_mismatch` verdict; an unsafe filename, a `class` that
disagrees with its folder, or a duplicate path.

### P1. Strata, targets and acceptance predicates

Targets are **independent capture families** (P2), never files. The predicates
are screening rules for the collection tooling; admission is the adjudication
in P6, not the predicate. Targets and predicates are the §6 table, unchanged.

| stratum | target (families) | acceptance predicate | adjudicated against | boxes | standing |
|---|---|---|---|---|---|
| **scorch, early/dull** | **400** | `brown_frac < 0.05`; marginal necrosis on a **still-green, still-turgid** blade; not wilt, not torn, not end-stage browning | accepted as scorch **and rejected** against the `leaf_spot` exemplars **and** the healthy exemplars | **required** | critical path — the only stratum with no substitute; the scorch↔`leaf_spot` boundary is the one being trained |
| scorch, plain ground | 150 | `bg_plainness ≥ 0.636`, `subj_frac_bg ≤ 0.406`, `border_green_frac ≤ 0.030` (picked leaf on a worktop / sheet) | as above | required | supporting |
| scorch, low chroma | 150 | `sat_mean ≤ 0.15`, dull ambient light | as above | required | supporting; largely subsumed by framing |
| healthy, plain ground | 250 | same framing predicate, healthy subject | rejected against scorch **and** `leaf_spot` exemplars | optional | breaks "plain ground ⇒ `not_a_plant`" — a live product risk, not a benchmark quirk |
| healthy, ND regime | 250 | `det_union_frac ≥ 0.40`, `sat_mean ≥ 0.38`, `achro_bright ≤ 0.05` | rejected against `nutrient_deficiency` exemplars | optional | negatives, not positives: the ND over-fire is a prior distortion |
| leaf_spot, houseplant | 200 | one soft-lit glossy leaf indoors, confluent lesions | accepted as `leaf_spot` **and rejected** against scorch exemplars | required | the other side of the boundary; v8 has 98 houseplant-domain `leaf_spot` rows |
| pests + gray_mold, with boxes | 200 | short side ≥ 384 px (v8 `pests`: 4 of 21 under) | class exemplars | **required** | Stage 1 returns no box on 44.9 % / 43.5 % |

~1,600 families (target). Two additions that are *not* supplement rows:

- **Benchmark v3 material** (scorch arm, healthy arm, non-plant / difficult-plant
  arm) is collected under *separate* acquisition batches by different
  collectors/plants and never enters the supplement (see "OOD benchmark v3").
- **`is_a_plant` feedback rows** (`_FEEDBACK_OUT_OF_HEAD_CLASSES`,
  `server/main.py`) — "the reject was wrong, there is a plant here". They are
  selected by a model failure, so they are `split_hint=train_only` at their
  adjudicated class, never `eval_ok`; they are what the `not_a_plant` head and
  the OOD bank retrain on.

Optional ranking step, never admission: §6 found a transfer axis that
correlates with nothing measurable, so *ordering* candidates in embedding space
against an anchor set is allowed once the predicate and adjudication have
passed. A row is never admitted or rejected on embedding distance.

### P2. Capture-family identity

**One family = one physical plant × one capture session.** Every frame of that
plant in that session — any angle, distance, lighting, crop, re-encode,
rotation or later augmentation — shares one `capture_family_id`. The id is
minted from the acquisition record, never from a filename, folder, pHash or
model output:

```
capture_family_id = 'fam_' + sha1(acquisition_batch_id | plant_ref | session_id)[:16]
```

`plant_ref` is the collector's local label for the plant, `session_id` the
date + device. For internet images one family = one source item: all photos of
one iNaturalist observation / one forum post / one gallery page share the
family and the `source_item_id`. A re-shoot of a plant already in the corpus is
a new family (new session) but is published `split_hint=train_only`, because
the notebook groups by family, not by plant, and a plant straddling a split is
leakage the group key cannot see.

Why pHash uniqueness is not independence: pHash at Hamming ≤ 6 catches
re-encodes and crops and misses rotations and noisy re-uploads —
`build_ood_benchmark.py` found ≥ 11 SIFT+RANSAC pseudo-replicates that pHash
had passed inside the nominal 31 v2 scorch rows, and Roboflow-style rotation
exports survive pHash as distinct (§8). A family id is a *claim by the
collector* about the physical capture; pHash and SIFT+RANSAC are backstops that
can only confirm a duplicate, never certify independence.

What the notebook does with the id (2.7.5 — resolved, never raised):

- a family **routes as one unit**. Any train-side member (`hn_kind=boundary`,
  `train_only`, or a `first_seen_model_version` ≤ `MODEL_VERSION`) makes every
  member train-only; one already-seen member marks the whole family seen. The
  leakage guard keys on `(source, plant_id)` and `eval_ok` / `train_only` rows
  get different `source` tags, so this is what keeps a family off both sides
  of a split; §2 re-checks it on what was saved;
- a family of `eval_ok` rows stays eval **together** — one split group, so it
  can never straddle train and eval. Prefer one eval row per family anyway: the
  paired bootstrap resamples photos, and siblings make its interval optimistic;
- a family may span classes (one plant, a healthy leaf and a sick one).
  `filter_supplement.py --require-provenance` still enforces the strict
  per-family contract for a build that wants to certify it.

### P3. Image floor

- Short side **≥ 384 px** on the published bytes
  (`COLLECTION_MIN_SIDE`, `server/tools/filter_supplement.py`). The tool
  *reports* `below_collection_floor`; v9 *enforces* it — the published manifest
  carries zero rows under the floor. (v8 shipped 54; `pests` p05 = 320.)
- Variance-of-Laplacian **≥ 50.0** (`BLUR_LAPLACIAN_FLOOR`,
  `server/pipeline.py`) for every class except `not_a_plant`; the tool imports
  the server's function so this cannot drift.
- Original capture bytes are retained privately; the published file may be a
  JPEG re-encode (the tool tolerates a pHash drift ≤ 6 against the recorded
  digest). User-consented scans arrive already anonymised at ≤ 1024 px
  (`SCAN_IMG_MAX_SIDE`, `server/main.py`), which still clears the floor.
- No drawn annotations, watermarks, screenshots or AI-looking images (v8 item 7).

### P4. The per-row record a v9 row must carry

`manifest.csv`, one row per published file — or none: a photo without a row
loads train-only (P0). Columns marked *notebook* are read by §2
`fetch_kaggle_supplement`; since 2026-09-21 a **missing** column gets the
fallback named in the notebook's `STRICT_SUPPLEMENT_*` note, and since 2.7.5 a
conflict between rows resolves instead of raising (P0, P2); a **malformed**
present value (an unparseable `first_seen_model_version`) still raises. Columns marked *tool* are
read by `filter_supplement.py` (`--require-provenance` makes the three
provenance columns mandatory; the default accepts a v8-shaped manifest);
columns marked *protocol* are this document's requirement and are recorded
verbatim (the notebook ignores unknown columns).

| column | required by | rule |
|---|---|---|
| `filename` | notebook, tool | `<class>/<file>`; the first path part must equal `class`; POSIX-relative, unique |
| `class` | notebook, tool | one of the 9 head classes (`classifier.DEFAULT_CLASS_NAMES`) |
| `role`, `hn_kind` | notebook | `hn_kind=boundary` is forced train-only via `supplement_boundary`; a manifest with **zero** boundary rows raises; other values are recorded, not routed on |
| `confidence` | notebook (fallback) | in [0, 1]; the routing fallback when `split_hint` is absent uses `≥ 0.80` |
| `split_hint` | notebook | `eval_ok` or `train_only`; anything else routes train-only |
| `filter_verdict` | notebook, tool | optional; the tool writes it. When the column exists, non-`keep` rows are skipped. The notebook accepts the tool's full vocabulary (`keep / blurry / bench_leak / too_small / tiny / duplicate / dup_intra / dup_cross / missing / corrupt / phash_mismatch`), skips every non-`keep` row, and raises on any `bench_leak` or `phash_mismatch` row |
| `filter_independence_method` | notebook, tool | recorded; anything but `capture_family_id+phash_v1` prints one warning. The tool writes `legacy_phash_only_NOT_CERTIFIED` unless run with `--require-provenance` |
| `license_id` | notebook (recorded) | free text, tallied per run; `SUPPLEMENT_KNOWN_LICENSES` supplies a URL for the ids it knows. Never gated on (2026-09-21); `unknown` when blank |
| `license_url` | notebook (optional) | defaulted from the allowed-licence table when blank |
| `creator` | notebook (recorded) | attribution; recommended when `license_id` starts with `cc-by` |
| `source_url` | notebook (recorded) | Internet rows: the landing page. First-party rows: an opaque, non-resolving reference to the batch record, e.g. `plantvision://acquisition/<batch_id>/<immutable_source_id>` — never a real URL that identifies a person or device |
| `source_item_id` | protocol | the source's own id (`observation:<id>/photo:<id>`, post id); first-party: the capture id / scan sha |
| `attribution` | notebook (optional) | as the licence requires; defaults to `creator` |
| `immutable_source_id` | notebook, tool | globally unique per **photograph**, never derived from a mutable path; a second row with the same id is the same photo again and is skipped. Internet: `<host>:<item>:<photo>`. First-party: `scan:<sha256 of original bytes>`. Absent → `sha256:<bytes>` |
| `capture_family_id` | notebook, tool | P2. Absent → none (the row is its own group). Never add one to an already-published row (P0) |
| `first_seen_model_version` | notebook, tool | literal **`never`** for a row no model has trained on; otherwise the semver of the first model whose *training* split held it. The notebook routes any value **≤ `MODEL_VERSION` (2.7.5 today) train-only** whatever `split_hint` says. Absent → routed by `split_hint` alone (v8 behaviour); the permanent `HOLDOUT` carve keeps the never-trained slice |
| `subject_domain`, `subject_plant` | notebook (recorded) | verbatim; never gated |
| `phash` | tool | recorded at build; the tool allows ≤ 6 bits of drift |
| `stratum` | protocol | the P1 stratum name, verbatim |
| `acquisition_batch_id` | protocol | opaque reference to the P5 record; every row has one |
| `verified_by`, `adjudication_status`, `adjudication_note` | protocol | P6 |
| `<name>.txt` beside the photo | notebook | **not a column**: a YOLO label file, one `cls cx cy w h` row per box, normalised 0–1 (polygon rows and a trailing confidence are accepted; pixel coordinates are dropped). Consumed since 2.7.5: §2 indexes the boxes without decoding the photo and stores a boxed photo in its EXIF-rotated frame; §3 hard-links the boxed photos still in the training split into the Stage 1 set (15 % of families to detector val). Boxed photos in a held-out split never train the detector |

**Carry-over of v8 rows.** Under the 2026-09-21 policy every v8 row carries
over as it is; the columns above are what a v9 revision *adds*, not what
admits a row. When they can be supplied, supply them honestly: an immutable
source id, a family id (one row per source item), and
`first_seen_model_version` set to the first release that trained on it (if the
exact release is unknown, the revision it was published in bounds it from
above, and any value ≤ 2.7.5 routes it train-only, which is the safe
direction). `SUPPLEMENT_MIN_ROWS = 3000` is comfortably met by v8's 3,389.

**Post-run re-stamping — automatic since 2.7.5.** After a run, every row that
sat in its *training* split needs `first_seen_model_version = <that
MODEL_VERSION>` in the next revision. §2 now writes exactly that file:
`models/supplement_manifest_next.csv` (shipped as
`analysis/supplement_manifest_next.csv` in the §6b archive) is the input
manifest with the column stamped on every row whose photo was still in
`TRAIN_ROOT` after the splits, existing versions kept, held-out rows untouched,
and a `train_only` row added for each unlisted photo that loaded. Publish it as
the next revision's `manifest.csv`. A photo §3 later dedups out of training is
stamped anyway — the safe direction (it only loses eval eligibility).

### P5. Acquisition / consent batch record (first-party rows)

One private record per batch, `acquisition/<batch_id>.json`, referenced from
rows by `acquisition_batch_id`. It is the rights basis for every `project-owned`
row and replaces any per-image licence string. Contents:

- `batch_id`; `basis` ∈ `project_owned_capture` | `user_consented_scan` |
  `public_licensed`;
- for `user_consented_scan`: the export archive name (e.g.
  `plantvision_export_20260806T102212Z`), the consent instrument (the
  `privacy_policy.html` asset version and app-version range it shipped in — the
  in-app consent screen is the canonical disclosure), the server settings in
  force (`SCAN_RECORDING_ENABLED`, `SCAN_RETENTION_DAYS` = 90,
  `SCAN_COMMON_KEEP_RATE` = 0.1, `SCAN_DAILY_CAP_PER_CLASS` = 50 — defaults in
  `server/main.py`), and the anonymisation applied (re-encode, EXIF/GPS
  stripped, ≤ 1024 px; only linkage `scan_id`);
- for `project_owned_capture`: collector role, device class, location class
  (indoor / garden), date range;
- for `public_licensed`: nothing beyond the per-row columns — the row is the
  record;
- row count, class × stratum counts as **families**, the date the record was
  frozen.

No user identifier appears in the record. `feedback.db` holds the
`user_pseudo_id ↔ scan_id` join and is never published; the record references
`scan_id` at most. A batch is either supplement material or benchmark material,
never both (disjointness is an acquisition property, not a pHash result).

### P6. Human label adjudication

- **Who.** Every `eval_ok` row: two human adjudicators, the maintainer plus one
  independent reviewer (roles, not names — a protocol requirement). Every
  `train_only` row: one human adjudicator. `verified_by=agent_vision` alone is
  never `eval_ok` in v9; the agent judge remains the *screening* pass.
- **Against what.** The exported per-class reference exemplar sets the agent
  judge already uses. A row is judged *for* its class and *against* its
  confused-class set, per the P1 table: scorch strata against `leaf_spot` and
  healthy; houseplant `leaf_spot` against scorch; ND-regime healthy against
  `nutrient_deficiency`; plain-ground healthy against scorch and `leaf_spot`.
  The class definition being adjudicated for scorch is the *early* one:
  marginal, dull, edge-confined necrosis on a still-green blade — not the
  end-stage whole-leaf browning the corpus deciles show.
- **Recording.** `verified_by = human:<id1>+human:<id2>`,
  `adjudication_status ∈ {agreed, disputed, escalated}`, `adjudication_note`.
  A `disputed` row is published `split_hint=train_only` with `confidence < 0.80`
  (so the confidence fallback also routes it train-only) and is never resolved
  by counting the agent as a third vote. Disputed rows are kept — they are
  boundary information — but never grade or calibrate.
- Deferred labels (`edema`, `sunburn_or_scorch`) are adjudicated and kept in
  `staging/` under their own name; they are not published under a head-class
  folder.

### P7. Exclusion checks before publish (run in this order)

1. `python -m server.tools.filter_supplement --root <v9> --bench <oodbench_v3> --out <report>`
   — needs only `filename` / `class`; adopts unlisted photos as `train_only`
   rows; exits non-zero on any `bench_leak` (pHash ≤ 6, plus family id under
   `--require-provenance`). Publish the tool's `<report>.csv` as `manifest.csv`
   (§2 skips non-`keep` rows itself). Check `below_collection_floor` and
   `kept_rows_with_box_file` in the report.
2. pHash + family against **the benchmark v3, the v2 sentinel, and every dataset
   either was built from** — `shuvokumarbasak4004/flower-leaf-diseases-dataset-new-and-update`
   (all folders) and `aditisalvi04/indoor-plant-disease-dataset` (the whole
   `Hibiscus_*` branch), as listed under "Deny-list". For any Kaggle-derived
   folder: `python -m server.tools.kaggle_inventory --gate <ref> --folder <f> --bench <oodbench> --sample 60`
   (a hit rejects; a miss certifies nothing). The §2 deny-lists
   (`KAGGLE_DENY_SLUGS`, `KAGGLE_DENY_FOLDERS`) bind v9 sourcing too — a
   deny-listed branch does not enter v9 by manual download either.
3. Deny at the branch, not the folder (§2 rule; the Hibiscus lesson).
4. Cross-revision consistency: a v9 row that also exists in v8 (same
   `immutable_source_id`, or pHash ≤ 6) carries the same class and a
   `first_seen_model_version` ≤ 2.7.3; a class change is a re-adjudication and
   is noted.
5. Family integrity: §2 resolves mixed families itself (P2). Still check that no
   family id is shared with any benchmark manifest, and prefer one `eval_ok`
   row per family.
6. Rights: recorded, not gated (study-project policy). Fill `source_url` /
   `license_id` / `creator` when known — they are how a bad source is traced.
7. Report **families**, not files: per class × stratum, and `eval_ok` families
   per class. Check the row total against `SUPPLEMENT_MIN_ROWS = 3000` — a v9
   below it raises in §2, and lowering the constant is a reviewed notebook edit,
   not a dataset decision.
8. Publish with `kaggle datasets version` (the stale-upload lesson), then
   confirm the new revision with `kaggle_inventory --list` (uncapped) and file
   the revision number in the batch log; §2 records the resolved revision in
   `model_card.json → source_inventory.kaggle_revisions`.

### P8. What §2 does with an accepted row

Routing precedence, from the notebook: `hn_kind=boundary` → `supplement_boundary`
(train-only, low sampler weight) · `first_seen_model_version` ≤ `MODEL_VERSION`
→ `supplement_train_only` · `split_hint=eval_ok` → `supplement` (eval-eligible)
· anything else → `supplement_train_only`; then a capture family with any
train-side member routes train-only as a whole, and a photo with no manifest
row is `supplement_train_only`. Rows whose `filter_verdict` is not
`keep` are skipped entirely. Eval-eligible rows are then carved by the stable
sha1 bucket over `(source, plant_id)` — `plant_id` *is* the family id for
supplement rows — with `HOLDOUT_PERMILLE = 300` into the permanent holdout
before the ratio splits draw `val_realworld` / `val_indomain` /
`temperature_cal` / `conformal_cal`. A run whose eval-eligible set is empty
raises (`EVAL-ELIGIBLE SUPPLEMENT EMPTY`).

## OOD benchmark v3 — the replacement for the v2 sentinel

**What v2 is.** `maxayyt5/plantvision-ood-benchmark` revision 2, private:
182 images = 151 healthy + 31 hibiscus scorch, one studio source, one
photographic session, ≥ 11 rotated/noised pseudo-replicates among the 31 that
pHash ≤ 6 missed, `rights_status = dataset_page_declaration_only;
per-image_rights_unverified`, `release_eligible = false` on every row. Notebook
§6 scores it classifier-only, marks it `diagnostic_only`, and blocks on nothing.
It stays as a regression sentinel; it grades no release.

**What v3 must be.** No OOD floor is re-enabled (`REQUIRE_OOD_BENCHMARK = True`)
until every item holds:

| requirement | rule |
|---|---|
| species | **8–12 species** on the scorch arm (the open ROADMAP item), no single species above one quarter of the arm's families (target) |
| sessions | ≥ 3 capture sessions per species (target), different plants per session; the arm is families across sessions, never one sheet under one lamp |
| morphology | the P1 early/dull definition, adjudicated by P6 with the scorch-vs-`leaf_spot` exemplar rule; `arm` declared per species in `SPECIES_ARMS`, never inferred (`brown_fraction` is recorded and nothing branches on it) |
| healthy false-fire arm | healthy families from the **same** species and sessions where possible, so cost and benefit share one population and a recall floor cannot be met by firing `leaf_scorch` more often |
| **non-plant / difficult-plant arm** | genuine non-plants in plant-like context (empty pots, foliage prints, textiles, plastic plants, green textures) **and** difficult plant negatives (variegated healthy, senescent-but-healthy, wet leaves, plain-ground healthy). v2 has no such arm — its own docstring says it "contains no non-plant/OOD class". This arm scores the served `not_a_plant` / OOD gate, which nothing measures today |
| scoring unit | **families, not rows**: one representative per family (`build_ood_benchmark.py` keeps one per SIFT+RANSAC cluster), recall = families correct / families; per species, per arm, never pooled (the report prints `POOLED; read the arms below` and a between-species Student-t interval) |
| scoring path | **served** — `tools/paired_eval.py` (served is its default; `--argmax` is the other instrument, reported separately) with `--manifest` for the family join, then `tools/eval_bootstrap.py` (resampling unit = capture family) and `build_ood_benchmark.py --report` per species — the AND-gate alone can move ornamental recall ~20 pp; the classifier-only number in §6 is a different instrument and is labelled so |
| independence | families disjoint from every supplement family and every training source by *acquisition* (separate P5 batches, different collectors/plants), then confirmed by `filter_supplement.py --bench` against v9 and by pHash against the training corpora |
| rights | per-row `license_id` / `source_url` / `creator` as P4, `release_eligible=true` only when a human has verified the rights; `rights_status` says what was verified |
| privacy | **private** Kaggle dataset, never public, never trained, never in a supplement; the v3 slug and every dataset it was built from go into `KAGGLE_DENY_SLUGS` / `KAGGLE_DENY_FOLDERS` before the first row lands |
| manifest | built with `build_ood_benchmark.py` so every row carries `capture_family_id`, `family_method`, `source_family_size`, `species`, `arm`, `source_relative_path` and the rights columns; §6 requires a unique `capture_family_id` per row or it refuses to gate |

**Tool gaps to close before v3 can be built** (notebook/tools lane, not this
document): the tool hard-codes one source (`SOURCE_DATASET_SLUG`, MIT,
`release_eligible = "false"`), one folder layout (`<split>/<species>/<condition>`),
a two-entry `CONDITION_MAP` (`healthy`, `scorch`) and no `not_a_plant` mapping;
`MIN_CLUSTERS_PER_CLASS = 60` is the per-class floor it refuses under; and it
writes `manifest.csv` while §6 looks for `manifest_arms.csv`.

**What a family-level recall floor means.** A floor *f* on arm *A* means: of
the *n_A* independent families on that arm, at least *f·n_A* have served
top-1 = `leaf_scorch_underwatering`, with the healthy arm's false-fire ceiling
scored on the same species. It stays **advisory** until *n_A* is adequate,
because a floor is a decision on a proportion: the 95 % binomial half-width at
*p* ≈ 0.7 is ≈ 1.96·√(0.21/*n*) — ±0.16 at *n* = 31, ±0.09 at *n* = 100,
±0.06 at *n* = 200. Below roughly **100 families per scored arm** (target,
spread across the 8–12 species) a model at 0.62 and one at 0.78 are the same
number, so a 0.70 floor would be deciding on noise; and the tool's own
between-species rule (half-width ≤ 0.15 on the per-species mean) has to hold as
well, or the number is a per-species result wearing a class label. Floors,
when re-enabled, are set from the incumbent's **served** score on v3 — never
carried over from v2.

## Locked deployment-representative test set — carve rule

The only never-trained, real-distribution eval that scales is production scans
(2,593 counted in the August roadmap; the 2026-08-06 export holds 2,727 image files).
The carve:

1. **Source.** `tools/export_data.py` archive → `labeling.db` `candidates` rows
   with `source='user_scan'` and `user_correct=1` (the user confirmed the
   model's prediction; status `auto_yes`) **or** `source='user_feedback'` (the
   user corrected the class), joined on `scan_id` to `feedback.db`
   (`corrected_class`, `predicted_class`, `model_version`, `out_of_head`,
   `image_path`). User feedback is a **prior**, not a label: all 11
   user-positive scorch conflicts at handoff were rejected on inspection.
2. **Adjudication.** P6, two adjudicators, against the exemplar sets. Only
   `agreed` rows with a head-class label proceed; `is_a_plant` and other
   `out_of_head` rows go to the supplement as `train_only` (P1).
3. **Eligibility for candidate *c*.** `first_seen_model_version = never` —
   the row has never been in any supplement revision or training split — and
   `recorded_under_model_version` (from `feedback.model_version`) is recorded
   beside it. "Later than the candidate's lineage" is operationalised the way
   §2 does it: any `first_seen` ≤ *c*'s `MODEL_VERSION` disqualifies the row
   for *c*; `never` qualifies it for every candidate.
4. **Carve.** Deterministic: `sha1('locked|' + capture_family_id)` bucket
   below `LOCKED_PERMILLE` (target 500 until the per-class family counts are
   adequate; then a smaller share) → the locked set **T**; the remainder → v9
   supplement as `split_hint=train_only`, never `eval_ok` (the recorded stream is
   model-verdict-selected: healthy confident scans are kept at 0.1,
   `not_a_plant`/blur are never recorded, disease is always kept, 50/class/day).
   Family: one scan = one family, except rows from the same `user_pseudo_id`
   on the same day with the same adjudicated plant, which are one family
   (conservative; the join lives only in `feedback.db` and is not published).
5. **Lock.** T is a separately versioned **private** artefact (T_v1, T_v2 …,
   append-only), **outside the supplement** — the supplement's
   `HOLDOUT_PERMILLE` carve is not it, and `HOLDOUT_SOURCES = {'supplement'}`
   never sees it. T rows are never published in any supplement revision and
   never re-enter training for any candidate that will be scored on that T
   version; a candidate that did train on a T row is marked
   `contaminated_by` and is not compared on it. T never feeds
   `temperature.json` / `conformal.json` (their `source` / `fit_source` stay
   the supplement calibration split).
6. **Scoring.** Served, through `tools/paired_eval.py --eval-dir <T> --manifest <T manifest>`
   and `tools/eval_bootstrap.py` (paired CI; its resampling unit is the capture
   family, and it flags rows with no family id), per class, with the recording policy
   (step 4) printed beside every number so nobody reads T as the deployment
   prior. Each T row carries the P5 batch record of its export.
7. **Adequacy.** 80 candidate rows at handoff, pooled over classes, is not a
   test set — it is advisory until per-class family counts reach the same
   order as the benchmark rule above (target); until then T reports, it does
   not gate.

## Sequencing

1. **Critical path, start now: scorch early/dull, 400 families with boxes**
   (P1 row 1). Nothing substitutes for it — public supply was searched
   exhaustively and measured at 1–2 % of corpus volume with none in this
   stratum (§5), and every data-free lever is falsified or bounded (ROADMAP
   "Settled"). Adjudicate against `leaf_spot` from the first batch, not after.
2. **In parallel, different collectors/plants: benchmark v3.** The scorch and
   healthy arms need the species × session spread, which is calendar time; the
   non-plant / difficult-plant arm needs no plants and can be done first.
   Open the deny-list entries and the tool gaps at the same time.
3. **In parallel, no collection needed:** adjudicate the 80 export rows and
   carve T_v1; write the P5 record for `plantvision_export_20260806T102212Z`.
   (The v8 rights re-derivation is no longer needed: every v8 row carries over
   under the 2026-09-21 policy, so `SUPPLEMENT_MIN_ROWS = 3000` is met.)
4. **Then the supporting strata:** houseplant `leaf_spot` (the boundary
   partner — collect it alongside item 1, it is cheap indoors), plain-ground
   healthy, ND-regime healthy, `pests` + `gray_mold` with boxes.

**Minimum that unblocks a first 2.7.5 candidate run:** nothing — v8 loads
under the 2026-09-21 policy. **What makes a v9 run *worth running*** (targets): scorch early/dull ≥ 200 families and houseplant `leaf_spot`
≥ 100 families adjudicated across the boundary, plus a family-verified v3 at
whatever *n* exists so the run reports a family-level number (advisory). The
**full round** is the ~1,600 families of P1, v3 at ≥ ~100 families per scored
arm across 8–12 species, and T_v1 — and only the full round can re-enable
`REQUIRE_OOD_BENCHMARK`.

## What this plan could not verify from the repo alone

- **No dataset bytes are here.** v8's 3,389 rows, `staging/` 24,275 / 23,821,
  the export's 2,727 files and 80 feedback-positive rows, the benchmark's
  182 = 151 + 31 and its ≥ 11 pseudo-replicates are quoted from the handoff,
  the audit banner and the tool docstring; none was re-measured.
- **Kaggle is private and there are no credentials here.** Supplement revision
  8 and benchmark revision 2 "as of 2026-08-23" are the banner's statement.
- **The reference exemplar sets** ("exported reference exemplars") and the
  vision-agent judge prompt are not in the repository; P6 assumes they exist on
  the collection machine.
- **The predicate features** (`brown_frac`, `bg_plainness`, `subj_frac_bg`,
  `border_green_frac`, `sat_mean`, `det_union_frac`, `achro_bright`) appear in
  this file only; no implementation exists under `server/tools/`
  (`build_ood_benchmark.brown_fraction` is a different, diagnostic-only measure).
- **Which v8 rows have a recoverable licence** — unknowable without the rows.
- **Whether a P5 record exists** for the 2026-08-06 export; the handoff says it
  must be attached, the repo holds none.
- **Production counts** — the August roadmap's 2,593 scans and the export's 2,727 files are
  different quantities from different dates; neither is checkable here.
- **The 19-of-31 / 3-of-31 morphology-vs-framing split** and every §1–§7 number
  were scored on v2 rows the audit found non-independent; the plan uses their
  direction, not their magnitude.

## Contract frictions found 2026-09-16 (for the notebook/tools lane)

Recorded here because each one decides whether a v9 row is accepted. Items
1, 2, 3, 7 and 8 were fixed on this branch the same day (notebook §2/§6,
`tools/analyse_feedback.py`, the skill); 5 and 6 closed with the 2026-09-21
policy and 4 with 2.7.5.

1. **Verdict vocabulary.** `filter_supplement.py` writes `missing`, `corrupt`,
   `tiny`, `phash_mismatch`, `blurry`, `keep`, `bench_leak`, `dup_cross`,
   `dup_intra`; §2 accepts only `keep / too_small / blurry / duplicate /
   bench_leak` and raises on anything else. A manifest straight from the tool
   with any `dup_intra` row fails to load. **Fixed:** §2 now accepts the tool's
   full vocabulary and raises on `phash_mismatch` (bytes are not the manifest's
   photograph) as it does on `bench_leak`. P7 still publishes `keep` rows only.
2. **Benchmark manifest name.** `build_ood_benchmark.py` writes `manifest.csv`;
   §6 raised unless it found `manifest_arms.csv` (`filter_supplement.py`
   accepts either). **Fixed:** §6 accepts either name.
3. **Family can straddle a split.** The §2 leakage guard keys on
   `(source, plant_id)`; `eval_ok` and `train_only` supplement rows get
   different `source` tags, so one family with mixed hints is two groups. P2
   forbids mixed hints per family at publish time. **Fixed on the notebook
   side too:** since 2.7.5 §2 routes such a family train-only as a whole (P2)
   and re-checks after the pass that no family holds an eval row and a
   train-side sibling. (The 2026-09-21 change that made `capture_family_id`
   optional left a sibling check keyed on the *blank* id, so the second
   eval-eligible v8 row would have aborted §2; that check is gone and a v8-shaped
   manifest is now pinned by `server/tests/test_notebook_supplement.py`.)
4. **Boxes are dropped.** **Fixed (2.7.5):** YOLO label files beside the
   photos feed Stage 1 (P4, "`<name>.txt`").
5. **`source_url` is mandatory for `project-owned` rows.** **Closed
   (2026-09-21):** recorded, not required.
6. **`SUPPLEMENT_MIN_ROWS = 3000`** was set against v8's row count; a v9 that
   drops unlicensed v8 rows may not reach it. **Closed (2026-09-21):** no row
   is dropped for its licence.
7. **`tools/analyse_feedback.py` `IN_HEAD_CLASSES`** lists six classes and says
   it mirrors `main._FEEDBACK_IN_HEAD_CLASSES`, which derives all nine from
   `classifier.DEFAULT_CLASS_NAMES`; its out-of-head share is overstated for
   `leaf_scorch_underwatering`, `nutrient_deficiency` and `pests` corrections.
   **Fixed:** it now derives from `classifier.DEFAULT_CLASS_NAMES` (with an
   identical nine-name fallback for snapshot runs), pinned by a test.
8. **The skill's golden rule 3** said the OOD gate is "blocking since
   2026-08-18"; the notebook has `REQUIRE_OOD_BENCHMARK = False` and labels the
   floors advisory. **Fixed:** the rule now states the diagnostic status and
   the must-run knob.

## Acceptance criteria (every version)

> **Baseline, extended (2026-09-16; light since 2.7.5).** Still true; the manifest column list here is the v8 one. What a v9 row must carry is P0 above; P4 is the full record for rows that will be graded on.

- **Layout:** `<class>/<image>` over the 9 classes + `manifest.csv`
  (`filename, class, role, subject_domain, hn_kind, confidence, source, phash, …`).
- **Source disjointness (anti-leakage):** no image from a training-corpus source
  (iNaturalist, PlantDoc, PlantVillage, the Kaggle training slugs, Wikimedia). A
  held-out training image inflates the gate.
- **Image gates:** real photos, short side ≥ 384 px, pHash near-dup removal
  (Hamming ≤ 6), per-creator cap.
- **Domain gate (v2.6.12, widened 2026-08):** subject is a **houseplant/indoor plant, an
  outdoor / garden ornamental** (potted, garden, landscape) **or a domestic kitchen-garden
  edible** (cucumber, tomato, pepper, berry bushes — a home plot, greenhouse or allotment) —
  still **no commercial field / plantation / greenhouse-monoculture imagery**. The line is
  *the grower*, not the species: a cucumber on a trellis in someone's garden is in-domain;
  a hectare of cucumbers under commercial glass is not.
- **Vision-agent judging:** kept/rejected vs exported reference exemplars; a
  boundary negative is kept only if the agent would *reject* it for the lookalike
  positive class (judged vs both confused-class exemplar sets).
- **Hard negatives role-tagged** (`role=hard_negative`, `hn_kind`) under `healthy/`
  or `not_a_plant/`; `hn_kind=boundary` is routed **train-only** by the notebook.

## Corpus verification — the published v8, measured (2026-08-17)

> **Bytes stand, reading superseded (audit 2026-08-23).** The counts in this section were measured on the published bytes and are not disputed. The conclusions "the eval set is outside the training corpus" and "the scorch arm scores 0.000 across three releases" rest on benchmark v2, which is one species, one session and not independent, and on archive predictions that contradict the headline scores. Exploratory, not release evidence.

Until now these criteria were asserted at build time and never checked against the
bytes that actually shipped. `tools/filter_supplement.py` checks them, on the corpus
downloaded from Kaggle rather than on the local build directory. Re-listed uncapped
against the live API, `maxayyt5/houseplant-disease-eval-supplement` holds 3,392 files
= 3,389 images + `manifest.csv` + `README.md` + `build_info.json`, per-class counts
matching disk exactly. **The published build is current and complete.**

| class | rows | distinct | infl | short-side p05 | <384 px | dropped |
|---|---|---|---|---|---|---|
| healthy | 1126 | 1125 | 1.00 | 480 | 21 | 1 blurry |
| leaf_scorch_underwatering | 663 | **662** | 1.00 | 422 | 9 | 1 blurry |
| nutrient_deficiency | 513 | 512 | 1.00 | 413 | 1 | 1 blurry |
| not_a_plant | 377 | 376 | 1.00 | 417 | 1 | 1 dup |
| gray_mold_botrytis | 259 | 259 | 1.00 | 415 | 1 | — |
| powdery_mildew | 217 | 217 | 1.00 | 426 | 7 | — |
| leaf_spot | 184 | 183 | 1.01 | 355 | 10 | 1 blurry |
| viral_disease | 29 | 29 | 1.00 | 652 | 0 | — |
| pests | 21 | 21 | 1.00 | 320 | 4 | — |

**Benchmark leakage: zero.** Every row pHashed against the OOD benchmark at Hamming ≤ 6;
nothing matches. (The check ran against v1's 213 clusters; v2's 182 are a subset, so it
still holds.) The eval set is outside the training corpus, now
measured rather than assumed — and the tool exits non-zero if that ever stops being
true, because a leak does not merely inflate a metric, it removes our ability to
notice the metric is inflated (see the `aditisalvi04` deny-list entry).

**Duplication: zero. Label contradictions: zero.** Inflation is 1.00 in every class,
so the v8 build's own pHash dedup held. This matters more than it looks:

> **The 663 leaf_scorch rows are 662 distinct photographs.** The scorch arm still
> scores 0.000 across three consecutive releases. So the collapse is not a volume
> problem, not duplicate padding, and not eval contamination — the three
> explanations that would have been cheap to fix. Whatever fails to transfer is a
> **property of the photographs**, and re-sourcing on count alone will not fix it.

**Collection floor: 98.4% compliant.** 54 of 3,384 rows fall below the declared
384 px short side. Reported, not enforced — those rows are already published and
already trained on, and dropping them retroactively would rewrite the corpus every
shipped bundle was fitted to. Worth acting on in the *next* round: `pests` is 19%
non-compliant (4 of 21, p05 = 320) and `leaf_spot` sits at p05 = 355, below the floor
at its own 5th percentile. `pests` is also the class Stage 1 misses 44.9% of the time
— it is the weakest class on every axis measured so far, not just on count.

**Five rows fail.** Four blur-gate failures (variance-of-Laplacian 26.5 / 30.5 / 43.4
/ 44.7 against the serving floor of 50) and one genuine near-duplicate pair in
`not_a_plant`. The blur check imports `pipeline._variance_of_laplacian` rather than
reimplementing it, so it cannot drift from what the server enforces at request time.
Two of the four carry `split_hint=eval_ok`, i.e. they currently grade models on frames
the server would short-circuit to `not_a_plant` before Stage 2 ever ran — a real
eval/serving mismatch, two rows wide.

**One caveat on the method.** The manifest-digest check first ran as exact equality
and condemned 131 rows (3.9%) as "not the dataset the manifest describes". Measuring
the distances: 128 at 2 bits, 3 at 4 bits, none worse — JPEG re-encoding during bundle
assembly, not different photographs. Every other gate treats digests inside the dedup
radius as the same image; that one demanded byte equality and so contradicted the
tool's own definition. It now uses the radius. A gate that is stricter than its own
semantics manufactures findings.

## Domain — houseplants → + garden ornamentals (v2.6.12) → + kitchen-garden edibles (2026-08)

> **Policy current, numbers exploratory (audit 2026-08-23).** The domain line (the grower, not the species; commercial agriculture out) stands and is what P6 adjudicates against. The `kaggle_ornamental` ablation numbers below were scored on the v2 sentinel and are exploratory.

**The domain is still widening.** After ornamentals, the next step admits **domestic edible
garden plants** — cucumber, tomato, pepper, courgette, berry bushes — grown by a person for
themselves. The exclusion that survives every widening is **commercial agriculture**
(plantations, monoculture fields, production greenhouses): that imagery is a different
photographic distribution, it is what the 2.6.7 gray_mold fix removed, and it is still ~39%
of gray_mold's eval slice today. Species is not the test; the grower is.

> **ANSWERED (2026-08-18): commercial agriculture stays OUT for now**, and is recorded as a
> target for a future domain expansion rather than a permanent exclusion. So the reading above
> holds: home-grown edibles in, plantations / monoculture fields / production greenhouses out.
> Revisiting it is a one-line change to the upstream judge prompt and **zero** notebook changes.

Code-side this costs nothing, by design. As of **2.6.13** the notebook **records and reports**
`subject_domain` verbatim and **never gates on it** — no enum, no allowlist, no mapping table.
A new bucket (`garden_edible`, whatever you name it) appears in the per-split domain report and
in `model_card.json → source_inventory.per_split_subject_domain` the first run after it is
collected. Domain policy lives entirely in the offline labeling pipeline; the runtime gate stays
bundle-derived (a rebuilt OOD bank on the broader in-distribution).

One weight comment in §4 still lags the policy: `doctorp_pepper` is down-weighted 0.8
(prior 0.5) because "pepper is a container CROP", and if domestic edibles are in-domain that
argues against the deployment distribution. Still open, but low priority.

> **`kaggle_ornamental` is SETTLED — do not revisit.** 2.7.1 ran it as its single knob:
> down-weighting 1.0 → 0.35 moved never-trained ornamental scorch recall by *exactly nothing*
> (0.065 argmax / 0.000 served, identical to 2.7.0 to three decimals) and cost healthy
> (served healthy recall 0.947 → 0.861, false-disease-fire 0.046 → 0.119, McNemar p=0.0074).
> Reverted to 1.0. What happened to the source itself is "Source loss" below.
> **Sampler weights as a class are a falsified lever** — 2.7.3's regime-aware
> within-class reweighting then failed the same way (§7).

### Original v2.6.12 pivot (kept for the record)

The target domain broadens to **houseplants + outdoor / garden ORNAMENTALS** (potted, patio,
garden, landscape); commercial **field / agricultural crops stay excluded**. The v8
data-quality work below still applies — it now targets **field-crop** leakage (not "outdoor"),
and outdoor ornamentals are welcome. In-code the pivot is just the notebook up-weighting the one
ornamental source (`kaggle_ornamental` 0.8→1.0, §4). The real broadening is **upstream**: loosen
the supplement's external `field_crop`/`orchard_fruit` gate to admit ornamentals + collect
garden-ornamental scans. The runtime gate is **bundle-derived** (0 server code changes; a rebuilt
OOD bank on the broader in-distribution is what accepts ornamentals and still rejects field crops
+ non-plants).

## Version status

> **Historical (audit 2026-08-23).** Pre-audit release metrics; exploratory. `MODEL_VERSION` is 2.7.5 and no historical bundle is deployable under the current manifest contract (`docs/ROADMAP.md`, Model track).

- **v5 / 2.6.7** (06-26): gray_mold fixed on clean foliage (R/P 0.83/0.83) after the
  agricultural strawberry-Botrytis proxy was removed. realworld disease macro-F1 0.862.
- **v6 / 2.6.8** (06-29): +boundary negatives; mixed — nutrient precision 0.679→0.747
  (still <0.80), recall slipped; healthy→disease false-fire 8.4% on hard boundary.
- **v7 / 2.6.9** (07-03): +boundary negatives + first powdery; the notebook
  aqua-collapse fix — §5 now ships the faithful **float32** TFLite (~5.8 MB per
  notebook §5, downloaded not bundled): INT8
  collapsed MobileNetV3 to ≈0.01, float16 won't prepare on LiteRT (`CONV_2D`); aqua
  realworld 0.76. **A paired A/B says the v7 changes net-hurt gamma — keep 2.6.8 (below).**

**Leakage-aware A/B — 2.6.5, 2.6.8 (shipped), 2.6.9 (candidate)** on the v4 & v7 eval
slices (2026-07-16, boundary excluded, paired bootstrap, `tools/eval_bootstrap.py`).
Supported disease macro-F1 (the 4 disease classes present — the supplement has no
leaf_spot/pests/viral) + healthy false-fire:

| model | v4 | v7 | false-fire v4 / v7 |
|---|---|---|---|
| 2.6.5 | 0.940 | 0.872 | 0.033 / 0.033 |
| **2.6.8 (shipped)** | **0.947** | **0.929** | **0.006 / 0.010** |
| 2.6.9 (candidate) | 0.901 | 0.908 | 0.022 / 0.030 |

**Paired 2.6.8 vs 2.6.9 → 2.6.8 wins both:** v4 **+0.046** [+0.029,+0.063], v7 **+0.021**
[+0.003,+0.038] (CIs exclude 0). It wins even on v7 — 2.6.9's *own* training era, where
2.6.9 carries more train leakage — so it is not a leakage artifact. Per-class (v7),
2.6.8→2.6.9 **regressed**: leaf_scorch recall 0.89→0.77 (below the 0.80 floor), nutrient
0.92→0.89 (prec 0.98→0.93), healthy precision 0.87→0.76, false-fire ↑; it gained only
powdery recall (0.84→0.93). The v7 dataset iteration **net-hurt gamma**.

**Deploy call: keep 2.6.8; do NOT ship 2.6.9.** Fix the data (v8) → train 2.6.11.
(Caveats: this eval can't see leaf_spot↔viral precision, where the boundary work was
also aimed, but the classes that regressed are the ones v7 targeted; the older
2.6.5↔2.6.9 gap is contamination — each model wins on its own era, 71% of v7 is
byte-identical to v4.)

## Source loss — `kaggle_ornamental` died mid-run (found 2026-07-24)

> **Historical, with standing rules (audit 2026-08-23).** The metrics are exploratory. What stands in code: `REQUIRED_SOURCE_FLOORS` / `STRICT_SOURCE_FLOORS`, the persisted `source_inventory`, and the deny-list + "deny at the branch" rule below, which P7 step 2 applies to v9 sourcing.

`pappu54/indoor-plant-disease-dataset-23-classes` — the **only** outdoor/garden
ornamental source, and the one the v2.6.12 pivot up-weighted — was **deleted or
unlisted by its owner**. The Kaggle API returns `403 {"code":403,"message":"Permission
'datasets.get' was denied"}` on a token that reads every other slug we use, and
`datasets/list?user=pappu54` returns `[]`. It is not coming back.

It stopped loading on the **2026-07-20** run (2.6.10). `_kaggle_download` is fail-soft,
so the loss was one `skipping` line in a 900-line log:

| | 07-03 (2.6.9) | 07-20 (2.6.10) |
|---|---|---|
| `kaggle_ornamental` images | 2,264 | **0** |
| train features | 14,315 | **12,025** |
| val_indomain disease macro-F1 | 0.865 | **0.809** |
| val_indomain leaf_spot recall | 0.852 | **0.695** |
| val_indomain viral_disease recall | 0.952 | **0.792** |
| head best epoch | 5 | **3** |

The source supplied 600 images each of `leaf_spot` / `viral_disease` /
`powdery_mildew` / `healthy` (+77 nutrient, +67 scorch) — exactly the classes that fell.

**Why nothing caught it.** `kaggle_ornamental` is in **no** `make_*_split`
`allowed_sources`, i.e. train-only, so `val_realworld` — the ship gate — contains none
of it and **passed**. Two lessons, both now enforced in code:

1. **A train-only source's disappearance is invisible to the ship gate.** §2 now has
   `REQUIRED_SOURCE_FLOORS`, which raises before training when any load-bearing source
   is missing or under floor (`STRICT_SOURCE_FLOORS=True`). This generalises the
   `REQUIRE_SUPPLEMENT` gate, whose own comment had already described this exact
   failure mode for the supplement.
2. **Version-over-version A/B assumes a shared corpus, and nothing verified that.**
   The per-source inventory is now persisted and shipped in
   `model_card.json → source_inventory`. **Diff it before believing any paired
   bootstrap** — two models trained on different corpora are not comparable, however
   tight the CI.

**Replacement (2.6.13).** `KAGGLE_ORNAMENTAL_SLUGS` pools two live sets, both
folder-inventoried via the Kaggle API before wiring:

| slug | licence | maps to (after DROP) |
|---|---|---|
| `itselif/ornamental-plant-diseases-master-dataset` | CC0 | healthy 389, leaf_spot 287, powdery_mildew 133 (rust dropped) |
| `warcoder/rose-leaves-disease-detection` (FlowerNet) | CC BY 4.0 | healthy 404, leaf_spot 313 (downy mildew dropped) |

At the shipped `per_class_cap=600` that is **healthy 600, leaf_spot 600,
powdery_mildew 133 ≈ 1,333 of 2,544**. **Unreplaced: `viral_disease` −600 and
`powdery_mildew` −467.** No live ornamental mosaic-virus set surfaced in the search;
closing viral needs collection or a new source, so treat viral as supply-starved rather
than reading a regression as a modelling problem.

**Candidate, NOT wired in** — `shuvokumarbasak4004/flower-leaf-diseases-dataset-new-and-update`
(MIT, ~6k+ files, `flower_data/<split>/<species>/<condition>`) carries `healthy` 2,200
and **`scorch` 1,400** — attractive because `leaf_scorch_underwatering` is our weakest
class (realworld R 0.75 / P 0.76; the §3 DINOv2 probe recalls only 0.44). It is
deliberately left out: 1,400 unverified positives into the most precision-sensitive,
boundary-sensitive class is the same bet that produced the 2.6.9 regression. **Judge a
sample against the reference exemplars first**, then decide.

**⚠ Now permanently eval-only, not "not yet decided" (2026-08-15).** This set became
`server/tools/build_ood_benchmark.py`'s source — the only never-trained benchmark we
have — so training on it is no longer a judgement call, it is contamination. Two facts
seal it: its own official split is 100 % pHash-leaked (2,800 `scorch` files → **62
distinct clusters**, 61/61 test clusters also in train), and a *different* Kaggle
dataset re-uploads the same photographs.

**Deny-list — never route these into training, at any weight:**

| ref | folders | why |
|---|---|---|
| `shuvokumarbasak4004/flower-leaf-diseases-dataset-new-and-update` | all | is the OOD benchmark |
| `aditisalvi04/indoor-plant-disease-dataset` | **the whole `Hibiscus_*` branch** | re-upload of the row above — `Scorch` 60/60 and `Healthy` 20/20 match the benchmark at Hamming 0; `Necrosis` 12/12 matches its *source* at Hamming 0 |

### The deny-list widened: a clean pHash gate is not proof of independence (2026-08-17)

This entry used to name only `Hibiscus_Scorch` and `Hibiscus_Healthy`, and the paragraph
that followed recommended the rest of the slug — **`Hibiscus_Necrosis` explicitly** — as
"genuine candidate supply, clean of the benchmark on a 0/101 sample". That reading was
wrong, and it was wrong in a way worth understanding rather than just patching.

`Hibiscus_Necrosis` really does read 0/30 against the then-213-cluster benchmark (v1). It is also
`flower_data/*/hibiscus/death_leaf` from the deny-listed `shuvokumarbasak4004` dataset,
re-uploaded: an independent fresh sample of 12 files, drawn under a different seed and
downloaded from Kaggle rather than reused from any cache, matches that folder at pHash
Hamming **0**, 12 of 12. The candidate's four folders are one-to-one with the upstream's
four hibiscus subclasses (`blight / death_leaf / healthy / scorch`).

It passed because **the gate can only detect the frames we happened to sample into the
benchmark.** We built the benchmark from that source's `healthy` and `scorch` folders and
left `death_leaf` out, so `death_leaf` was invisible to that cluster-level comparison while
carrying the same leaves, the same session, the same sheet and the same lighting. Those
are exactly the properties that make the eval set independent. Wiring it in would have
been the `aditisalvi04` incident a second time, through a gate built to prevent it.

Two rules follow, and both are now enforced in §2 of the notebook:

1. **Gate against the benchmark's upstream datasets, not only against its clusters.** A
   re-upload of the *source* passes a cluster-level gate.
2. **Deny at the branch, not the folder.** If one folder of a genus/collection is the
   benchmark, treat the whole branch as contaminated until proven otherwise — the
   deny-list is substring-matched, so the entry is `('hibiscus',)`.

The rest of the slug (49,878 files — Aloe, Rose, Money Plant, Turmeric) remains clean on
sampling and is genuine candidate supply; `Aloe_Sunburn` (3,343 files, a real houseplant
genus) maps to the deferred `sunburn_or_scorch` label. The same rule as ever applies
before wiring any of it: judge a sample against the reference exemplars, count
**originals** rather than folder size, re-run the pHash gate on the specific folders you
intend to use — and now also check the folder's provenance against every dataset the
benchmark was built from.

**Fixed — the lister now raises rather than truncating** (`server/tools/kaggle_inventory.py:90-93`;
the rationale is in its module docstring). It used to cap pagination and return silently:
a full listing of the dataset above is 49,878 files where the inventory recorded ~24,000,
and a follow-up "uncapped" re-run still stopped short. Kept here as the standing rule —
**a negative result from a capped lister is not evidence of absence.**

## 2.6.10 — the boundary fix on v7: recovered (07-20) — see the caveat above

> **Historical (audit 2026-08-23).** Exploratory; the v7 eval was contaminated by its own account.

Re-ran v7 with the fix (`supplement_boundary` 0.9→0.35, §4) — boundary draw 5.8%→2.8%.
**leaf_scorch recall 0.61→0.75**, disease macro-F1 **0.834→0.845**, **ship gate passed**,
aqua healthy (float32). The 2.6.9 *recall* regression is undone and 2.6.10 ≈ 2.6.8 on gamma.
Still open: weak-class **precision** (nutrient P **0.67**, leaf_scorch P 0.76) — the boundary
fix recovers recall, not precision → that's the v8 job.

## v8 — the data round (shipped into 2.7.0; historical)

> **Historical (audit 2026-08-23).** The routing mechanics (`split_hint`, `supplement_train_only`, boundary first) are current and described in P8. The metrics are exploratory. v8 rows carry no per-row licence, immutable source id, capture family or first-seen version; since 2026-09-21 §2 loads them with defaults (and since 2.7.5 without the blank-family crash — see "Contract frictions" item 3). The carry-over rule is in P4.

The 2.6.10 fix recovered *recall* but not *precision*, and the eval is still untrustworthy
(71% recycled, ag-leak, sub-0.80 noise). v8 fixes the data; **2.6.11 retrains** on it (the
boundary fix already carried forward; `MODEL_VERSION` bumped to 2.6.11 in §1).

**Root cause of the 2.6.9 regression (found 2026-07-16; notebook fix applied).** Between
2.6.8 and 2.6.9, commit `12623c4` began routing **all** `hn_kind=boundary` hard-negatives
(scorch/nutrient-lookalike healthy) to **train-only** as a new `supplement_boundary`
source at sampler weight **0.9** (and dropped the per-class cap). In 2.6.8 those negs sat
in the eval-eligible `supplement`, so ~½ went to *val*; in 2.6.9 all **697** are pinned to
TRAIN and drawn at ~5.8% of every batch — on par with the real physiological-positive
source (5.5%) — while scorch/nutrient TRAIN supply stayed frozen (healthy TRAIN +384 →
25.3% of the set). The head over-learned "browning/chlorosis = healthy": leaf_scorch
recall 0.89→0.77, nutrient down, healthy precision 0.87→0.76, and it plateaus earlier
(head best epoch 19→5). It is the precision/recall seesaw over-rotated; §1 constants, loss,
early-stop, and checkpoint logic were byte-identical, so they are **not** the cause.

**Fix applied + validated (§4):** `SOURCE_WEIGHTS['supplement_boundary']` **0.9 → 0.35** —
boundary hard-negatives now *nudge* precision without swamping the physiological classes,
train-only (eval stays clean). Validated on the **2.6.10 run** (07-20): boundary draw
5.8%→2.8%, leaf_scorch recall 0.61→0.75, disease macro-F1 0.834→0.845, gate passed — the
recall regression is undone. Kept for 2.6.11. Don't over-tune the weight on the contaminated
v7 eval; let v8's clean benchmark guide any further move.

**Why v7 can't prove a weak-class gain (the dataset amplifiers):**
1. **Non-independent eval** — 71% recycled from v4 (scorch/nutrient positives 100%,
   gray_mold 91%); version-over-version reads on those classes are circular.
2. **Unfloored positive noise** — the ≥0.80 floor hit the negatives + powdery, not the
   physiological positives (~30% still <0.80, min 0.55, eval-eligible).
3. **Agricultural-domain leak (bigger than first estimated).** Crop/outdoor imagery is
   **~8–22% of disease positives** (strict query-only → broad query+notes match),
   concentrated in **gray_mold ~39%** (grape/strawberry Botrytis — the exact proxy the
   2.6.7 removal fought) and **nutrient ~31%** (tomato/rice/citrus/potato); leaf_scorch
   ~10%, powdery ~0%.

**v8 plan (priority):**
1. **Purge field/agricultural crops from disease positives** — crop-term blocklist + an
   "is it a houseplant or garden ornamental (not a field crop)?" re-judge; populate
   `subject_domain`/`subject_plant`. (v2.6.12: outdoor ornamentals are now **kept**.)
   **gray_mold (~39%) and nutrient (~31%) are the most contaminated — start there**
   (gray_mold's grape/strawberry Botrytis is the retired proxy). A net trim (on-plan for
   the precision-bound classes); pair with item 2 so gray_mold/nutrient don't lose supply.
2. **Refresh recycled weak-class eval** — ~50–100 *new* houseplant positives each for
   nutrient + scorch, ~40 gray_mold, from new creators/genera (Stack Exchange/Reddit;
   folded production scans). Restores independence + targets the fallen recall.
3. **Extend the ≥0.80 floor to physiological positives** — route sub-0.80 disease
   positives **train-only** (don't delete: protects aqua's data-hungry volume).
   ✅ **Landed both sides as of 2.6.13.** The manifest stamps `split_hint`
   (`eval_ok` / `train_only`); `fetch_kaggle_supplement` now honours it and routes
   `train_only` rows to a new **`supplement_train_only`** source — TRAIN-ONLY (in no
   `make_*_split allowed_sources`) at the **full 0.9 `supplement` sampler weight**, so the
   student keeps every image. Until this landed the notebook read *only* `hn_kind`, so of
   v8b's 1,226 `train_only` rows just the ~700 boundary negatives were held back and the
   rest — the sub-floor positives — drained into `val_realworld` / `val_indomain` /
   `temperature_cal` / `conformal_cal` at ~52%. **They were not only grading the ship gate,
   they were fitting the shipped temperature and conformal q̂.** Routing precedence:
   boundary first, then `eval_ok`; anything else (blank, unrecognised, absent from the
   manifest) routes train-only — an unknown verdict must never *promote* a row into a
   held-out slice. **Measured: the slices GREW** (nutrient 30→77, scorch 45→107, gray_mold
   57→82, powdery 66→99 versus the supplement-less run) — the supplement adds more than
   the routing withholds, even with 695 rows held back.
   **Fallback:** a build that predates `split_hint` but carries `confidence` routes on
   `confidence >= 0.80` instead — the same floor the hint is derived from, so the fix
   works without waiting on a re-upload. Only a build with *neither* column raises.
   Measured on the *then*-published v7-era manifest (superseded; the published v8 routes
   **1,907 / 695 / 787**): 1,502 eval-eligible / 340 train-only / 697 boundary, and the 340 are exactly the sub-floor positives the item asks for
   (gray_mold 75, leaf_scorch 112, nutrient 113, plus 40 hard-negatives).

## ✅ RESOLVED — the stale-upload incident (2026-08-05 → 2026-08-07)

> **Historical (audit 2026-08-23).** The lesson stands (`SUPPLEMENT_MIN_ROWS = 3000`, P7 step 8). Note the constant was sized against v8's row count; see contract friction 6.

**Version 8 is published and verified: 3,389 rows, `split_hint` + `provenance` present,
585 `garden_ornamental` (353 eval-eligible), `leaf_spot` 184.** Routing measured on the
real manifest: **1,907 eval-eligible / 695 train-only / 787 boundary**. Kept below
because the failure mode is worth remembering.

For two days the published dataset — `maxayyt5/houseplant-disease-eval-supplement`
**version 7**, what the notebook actually downloaded — was a **v7-era build, not v8b**. The v8 and v8b rounds were finalized locally
but the `kaggle datasets version` publish step never landed, so none of that work has
ever reached a training run:

| class | v8b (round notes) | **published v7** |
|---|---|---|
| nutrient_deficiency | 493 | **378** |
| leaf_scorch_underwatering | 560 | **387** |
| gray_mold_botrytis | 282 | **249** |
| powdery_mildew | 188 | **146** |
| healthy | 1126 | **1002** |
| leaf_spot | 99 | **0 — absent entirely** |
| not_a_plant | 377 | 377 |
| **total** | **3125** | **2539** |

The manifest also has **no `split_hint` and no provenance column**, and `subject_domain`
holds only `houseplant` / `not_a_plant` — **zero `garden_ornamental` rows**. So the
v2.6.12 domain pivot has never been in a corpus either, and the non-recycled provenance
subset that the vs-2.6.8 A/B depends on does not exist upstream.

**The lesson, now enforced.** An offline round only reaches training once
`kaggle datasets version` has actually run, and a stale build is invisible: it loads
fine and passes every gate. Guard added in §2: `SUPPLEMENT_MIN_ROWS = 3000` fails the run loud rather than letting a previous era's
corpus train + grade silently — which is the same failure class as the `kaggle_ornamental`
source loss, just upstream of the notebook. Set it to 0 to train on an older build on
purpose.
4. **Thicken thin eval** — +50–100 powdery past its 14-query/54%-one-engine monoculture;
   broaden gray_mold via new multilingual sources. Don't grow nutrient/scorch count.
5. **Boundary negatives: hold ~700, don't grow**; keep `hn_kind=boundary` train-only;
   hold ~300 generic-healthy in *eval* (the pool shrank 362→305).
6. **Variegated-healthy boundary negatives** (Calathea/Maranta/Croton/Aglaonema/Begonia
   rex → `healthy/boundary`) vs the viral over-trigger; add a small clean-houseplant
   `leaf_spot` eval slice (leaf_spot/pests/viral have no leakage-free held-out today).
7. **Hygiene** — reject drawn-annotation/watermark/AI-looking images; add a cross-version
   pHash tag (`carried_from_vN`/`new`); fix the v7 folder README (still titled "v6").

**Added 2026-07-24 after the `kaggle_ornamental` source loss (items 8–10).** The plan above
was written when that source was still supplying 2,264 train images. Three of its
assumptions no longer hold.

8. **DONE as of v8 — an ornamental slice that is EVAL-ELIGIBLE.** *Was:* every
   ornamental source — the dead `pappu54` and both 2.6.13 replacements — was
   train-only, so `val_realworld` held **zero** garden ornamentals and could not
   measure the v2.6.12 pivot at all; that is also why a 2.26k image loss passed the
   gate unnoticed. **v8 ships 585 `garden_ornamental` rows, 353 of them
   eval-eligible on `source=supplement`**, which every `make_*_split` allows. For
   the first time `val_realworld` contains garden ornamentals, so the pivot is
   measurable rather than assumed.

   **Still open is the train-side gap**, which is a different ask: there is no
   ornamental `leaf_scorch` and no ornamental `viral_disease` in the corpus at all.
9. **NEW — `viral_disease` and `powdery_mildew` lost TRAIN supply, not eval supply.**
   The dead source carried 600 viral + 600 powdery; the replacements restore powdery to
   133 and viral to **0**. This is a different ask from items 2/4 (which target *eval*
   independence): these two classes now need **train-side volume**. Expect
   `val_indomain` viral recall to stay depressed (it fell 0.952 → 0.792 on the loss) —
   **do not read that as a modelling regression in the 2.6.13 A/B.** Ornamental
   mosaic-virus imagery is the specific gap; no live public set was found.
10. **NEW — re-weight the effort between `gray_mold` and `nutrient`.** They are failing
    for *different* reasons and the v8b round treats them as one job:
    - `nutrient_deficiency` has a **real precision defect** — P **0.670** on the 2.6.10
      run (07-20), 109 predicted vs 87 actual (1.25× over-fire). This is the genuine
      model problem. Prioritise.
    - `gray_mold_botrytis` **measures fine** (R 0.846 / P 0.856) — but its eval slice is
      ~39% agricultural leak, so that number is partly *untrustworthy rather than good*.
      Its job is item 1 (purge + re-judge for a believable benchmark), **not** volume.
      Don't spend collection effort chasing a recall number that isn't broken.

**Careful with `leaf_scorch` — more data may be the wrong lever.** The §3 DINOv2 linear
probe recalls scorch at only 0.39–0.52 across all four runs on record, by far the lowest
of any class. That says the **frozen features** barely separate it, not that positives are
scarce. Scorch is a leaf-**margin** phenomenon and the eval transform is
`Resize(224×1.05) → CenterCrop(224)`, which trims exactly the margin carrying the signal.
**RESOLVED — no longer a gate on collection.** The transform probe was run on frozen
features (corpus scorch-vs-healthy → benchmark): centre-crop 0.954, resize-and-pad 0.966,
**delta +0.013** against a +0.03 bar set in advance. Letterboxing at *inference* was
separately falsified (2.6.8 unchanged 31/31; 2.7.2 got **worse**, 3/31 → 1/31). If a
variant is retried it is a **training**-transform change, not an eval-transform one, and
it does not block the collection round — collection *is* the critical path.
And the obvious volume shortcut is closed: `shuvokumarbasak4004`'s 1,400 `scorch`
images are **permanently deny-listed** (see the deny-list above).

**2.6.11 verify (paired, not aggregate):** paired bootstrap 2.6.11 vs 2.6.8 (and vs 2.6.10)
on the *new* (non-recycled) v8 eval rows — nutrient/scorch **precision** up with recall
staying ≥ 0.80, healthy false-fire not up, powdery held. Keep aqua KD val-macro-F1 a watch-item.
**Diff `model_card.json → source_inventory` between the two runs first** — a paired
bootstrap across two different corpora is not a comparison, however tight the CI. That is
the lesson of 2.6.10, and the inventory now exists precisely so this check is possible.

## v9 — what to collect, and the finding that changes the target (2026-08-17)

> **Exploratory (audit 2026-08-23).** §1–§5, §7 and §9 below were scored on benchmark v2 (one species, one session, ≥ 11 pseudo-replicates among the nominal 31) and on classifier-only argmax; denominators were mixed across probes. Use their *direction* — early/dull scorch is confused with `leaf_spot`, plain ground reads as `not_a_plant`, public supply for the missing stratum does not exist — not their magnitudes. The §6 stratum table is carried into P1 unchanged as the target set.

Four sourcing lanes (Kaggle uncapped, HuggingFace/Zenodo/Mendeley, iNaturalist/GBIF
live, CC web) plus two zero-train characterisation probes on the shipped embeddings.
**Eight candidates were pHash-gated; none survived.** That is the honest headline, and
the reasons are more useful than a source list would have been.

### 1. The scorch collapse is a decision-layer failure, not a data shortage

Measured on the shipped ONNX graphs (384-d DINOv2 CLS, `output[1]`), both bundles.
**Benchmark v1 (pooled, 62 scorch clusters) — superseded by v2's 31 hibiscus:**

| | 2.6.8 | 2.7.2 |
|---|---|---|
| within-benchmark 5-fold CV probe AUROC | 0.997 ± 0.002 | **0.993 ± 0.004** |
| corpus-trained probe, transferred to benchmark | 0.924 | 0.824 |
| shipped head, P(leaf_scorch) AUROC on benchmark | 0.935 | 0.744 |
| shipped head, argmax recall on the scorch arm | 36/62 | **5/62** |
| served (full pipeline) | 0.565 | 0.000 |
| **in-domain** supplement scorch recall | 0.784 | **0.941** |

2.7.2's representation has **not** lost the distinction — a linear probe on its own
frozen features separates the benchmark's arms at 0.993. The head allocates that
signal elsewhere: on the 62 scorch clusters the argmax moves scorch 36→5, healthy
18→32, leaf_spot 8→20. Meanwhile in-domain recall went **up** 15.7 points, which is
precisely why the ship gate passed three collapsed releases in a row.

**Ceiling on any data-side fix.** Oracle-perfect reselection of the existing 663 rows
— using benchmark labels to choose, which no real collector can do — moves the probe
from 0.812 to 0.921 AUROC. The best label-free rule reaches 0.859. The gap that has to
close is a head ranking at 0.744 and deciding at 0.000. **Data selection is
second-order here; the head and the operating point are first-order.**

### 2. The target is 31 clusters, not 62 — and 0.565 was one arm

Served recall decomposes: 2.6.8 scored **hibiscus 31/31 and banana_bush 4/31**. On the
benchmark the 31 banana_bush/turgor clusters are statistically indistinguishable from
the 151 healthy ones on nearly every measured property (nongreen-in-subject AUC 0.605,
marginality 0.581, lesion count 0.492, tan-necrosis 0.529); the largest separation is
an exposure difference. No release has ever exceeded 4/31 there.

> Sizing a collection round against "restore 0.565" is really "restore hibiscus", and
> sizing against 62 clusters systematically over-promises. **Superseded:** the arm
> was retired outright in benchmark v2 (see "Build rules answered this round"),
> rather than merely reported separately. The ship gate now floors only the
> hibiscus arm (`OOD_ARM_FLOORS = {'hibiscus': 0.70}`, notebook §6).

### 3. Framing: two probes disagreed, and the disagreement is the finding

The transfer probe found photographic regime carries **no** transfer signal (|Δ| ≤
0.031 AUROC across border-plainness, subject fraction, saturation, blur, resolution —
with signs that flip between bundles). The causal ablation found framing is the
**single largest** axis: shrinking each of the 663 corpus scorch images onto a plain
light canvas at the benchmark's subject fraction drops recall 0.950 → 0.630 (McNemar
221 lost / 9 gained, p=5.1e-54), and the errors land on `healthy` (16 → 205).

Both are correct, and reconciling them is the point:

- The probe asked **"can I select better rows from what I have, by framing?"** No —
  because only **4.4%** of corpus scorch is in that regime. There is almost no
  variance to select on, so nothing correlates.
- The ablation asked **"does the model fail when framing changes?"** Yes,
  catastrophically — it had to *create* the variance synthetically to see it.

So framing is a **coverage** deficit, not a **selection** criterion. The probe's
conclusion ("restaging collection to imitate the plain-sheet regime is unsupported")
is right about re-weighting existing rows and wrong as collection guidance. A control
rules out the transform simply being destructive: the same operation on 300 healthy
hard-negatives costs 4.6× less (0.967 → 0.897).

**Mechanism.** In the corpus a plain foliage-free ground is a 16× `not_a_plant` cue:
237/377 `not_a_plant` rows sit inside the benchmark's own plainness/subject-fraction
window versus 117/3,012 across all eight plant classes; P(not_a_plant | plain ground)
= 0.669. This is a live product risk, not a benchmark quirk — **a user who photographs
a picked leaf on a worktop is in the regime we reject.**

### 4. A second, independent axis: morphology, and it is disjoint

Re-framing the benchmark does **not** recover scorch — it converts it to `leaf_spot`.
Tight-cropping the 31 hibiscus clusters to corpus-like framing moves leaf_spot 11 → 14;
adding a 3× chroma boost moves it to 23 (p=4.9e-4) while scorch never rises above 2.

The cause is what the two populations mean by "scorch". Chromatic tan/brown fraction,
corpus scorch deciles [10,25,50,75,90] = [0.044, 0.087, 0.170, 0.267, 0.409]; benchmark
hibiscus = [0.0029, 0.0051, 0.0079, 0.0098, 0.0110], **max 0.0176**. Only 9 of 663
corpus scorch images (1.4%) fall below the benchmark's brownest cluster. Most damning:
corpus **healthy** hard-negatives have brown fraction 0.122 — **15.4× the benchmark's
scorch median.**

> In our corpus's own colour statistics, the benchmark's scorched leaves are *less
> brown than our healthy images*. A classifier that learned "scorch = large tan area"
> **must** call them healthy. The corpus defines the class at end-stage desiccation;
> the benchmark shows it early, dull and achromatic, edge-concentrated on a green
> blade — which is the model's `leaf_spot` prototype.

Measured on the live 2.7.3 head (2026-08-22): of the **31** hibiscus clusters,
**19 are lost to morphology** (called `leaf_spot`), **3 to framing** (called `healthy`),
4 are correct, 3 → viral, 2 → powdery. **Morphology outweighs framing 6:1** — the earlier
"22 of 62 framing / 24 morphology" estimate was pooled over the since-retired arm and is
superseded.

### 5. Public supply does not exist. This needs first-party collection.

Every lane searched exhaustively, with the truncation bug fixed in each (see below):

| lane | redistributable abiotic-scorch supply found |
|---|---|
| HuggingFace / Zenodo / Mendeley / Figshare | **0** — HF full-text "leaf scorch" = 2 hits, both PlantVillage strawberry *Diplocarpon* (rule 4); "tip burn"/"tipburn" = 0; Zenodo title queries all 0 |
| iNaturalist + GBIF | ~20–39 usable, and the gate rejected 41% of a hand-audit as mislabelled |
| Wikimedia / Flickr CC / Openverse | ~50–95 distinct, best single block ~30 CC0 images |
| Kaggle (1,684 datasets across 100 query formulations) | 0 clean, correctly-labelled ornamental abiotic scorch |
| Bugwood / IPM Images / extension services | ~47, **licence-blocked** (non-commercial / all-rights-reserved) |

Total redistributable: **~50–95 distinct photographs**, or 1–2% of what the corpus
already holds — and none of it in the missing stratum. Measured on the project's own
statistics, every candidate pool sits with the corpus, not the benchmark:

```
                corpus  bench   Bugwood  ScotNelson  Openverse  Commons
bg_plainness     0.491  0.960    0.636      0.545      0.628     0.605
subj_frac_bg     0.541  0.233    0.388      0.457      0.424     0.392
sat_mean         0.371  0.101    0.397      0.369      0.354     0.222
colorfulness     44.2    9.3     37.9       41.8       40.7      28.4
```

**Conclusion: the scorch gap is not closeable from public sources.** Not because the
search was shallow — it was exhaustive and the negatives are measured — but because
the missing thing is a photographic regime and a disease stage that nobody publishes.

### 6. What to collect first-party, sized against measured deficits

Per-stratum acceptance predicates, not volume targets. Current coverage in brackets.

| stratum | target | acceptance predicate | why |
|---|---|---|---|
| **scorch, early/dull** ← **#1** | 400 | `brown_frac < 0.05`, marginal necrosis on a **still-green** blade, labelled against `leaf_spot` exemplars | 1.4% coverage. **Measured 2026-08-22: 19 of the 31 benchmark clusters are lost to this axis** (the head calls them `leaf_spot`), vs 3 to framing. Every data-free lever is exhausted below the floor, so this is the critical path |
| **scorch, plain ground** | 150 | `bg_plainness ≥ 0.636`, `subj_frac_bg ≤ 0.406`, `border_green_frac ≤ 0.030` | 4.4% coverage; −31.9 pp in ablation, but only ~3 of 31 real clusters. Demoted from 250: supporting, not critical |
| **scorch, low chroma** | 150 | `sat_mean ≤ 0.15`, dull ambient light | 12.2% coverage; −10.6 pp, largely subsumed by framing |
| **healthy, plain ground** | 250 | same framing predicate, healthy subject | breaks "plain ground ⇒ not_a_plant" |
| **healthy, ND regime** | 250 | `det_union_frac ≥ 0.40`, `sat_mean ≥ 0.38`, `achro_bright ≤ 0.05` | ND precision 0.621 (v9 characterisation, 2026-08) is a 6.4× prior distortion, not a label problem — the fix is **negatives**, not positives |
| **leaf_spot, houseplant** | 200 | one soft-lit glossy leaf indoors, confluent lesions | 98 of the 184 `leaf_spot` rows are houseplant-domain, over ~10 genera; recall 0.311 vs 0.744 garden |
| **pests + gray_mold, with boxes** | 200 | must carry bounding boxes | Stage 1 returns no box on 44.9% / 43.5%; `pests` is 21 rows and 19% under the 384 px floor |

~1,600 clusters. Only **`scorch, early/dull` has no substitute** — it is the 19-of-31
axis. Everything else, `scorch, plain ground` included, is supporting.

**A words-and-thresholds spec cannot capture all of it.** A cross-validated oracle split
of the existing scorch shows a real 0.33-AUROC transfer axis — 4–8× larger than any
measurable property — that correlates with *nothing* we can measure (|ρ| ≤ 0.16 across
15 properties, R² ≤ 2.6%; medians of the top and bottom quartiles are indistinguishable
on brown fraction, plant fraction, subject fraction, saturation, resolution). Visual
inspection names it only loosely: high-transfer = whole-plant collapse and dieback;
low-transfer = textbook marginal tip-burn on a turgid leaf. **Final selection should be
done in embedding space against an anchor set, not from a photographic checklist.**

### 6b. The out-of-domain ship gate — published and validated (2026-08-18)

> **Superseded (audit 2026-08-23).** Nothing blocks on this benchmark now: notebook §6 has `REQUIRE_OOD_BENCHMARK = False`, `OOD_ARM_FLOORS = {'hibiscus': 0.70}` and `OOD_MAX_HEALTHY_FALSE_FIRE = 0.12` as advisory heuristics, and records `diagnostic_only: True, release_blocking: False`; `REQUIRE_OOD_DIAGNOSTIC_RUN = True` makes scoring the sentinel mandatory to *record*, not to pass. The 1.000 / 0.097 / 0.129 figures include a runtime veto, pseudo-replicated rows and mixed instruments. The replacement is "OOD benchmark v3" above.

The benchmark now lives at `maxayyt5/plantvision-ood-benchmark` (**private**, **182**
images + both manifests, 1.6 MB) and §6 pulls it through the same
`_kaggle_download` choke point as every other source, then **blocks**. A missing
benchmark is a hard failure unless `REQUIRE_OOD_BENCHMARK` is turned off as a
deliberate edit — absence is how a check like this quietly stops existing.

Validated against both bundles on the published data, through the real
kagglehub download path, with the gate's own preprocessing (resize ×1.05,
centre-crop 224, hflip TTA).

**Mind which instrument each number comes from.** The shipped gate records
`eval_mode: classifier_only` — Stage-2 ONNX argmax, no detector, no OOD AND-gate,
no NAP veto. The 2.6.8 = 1.000 figure below is the **served** number
(`paired_eval --served`, i.e. 2.6.8 + the veto), and `build_ood_benchmark.py`
warns that the OOD AND-gate alone can move ornamental recall ~20pp. The two agree
today; do not assume they always will, and set floors from served numbers.

| | hibiscus (31) | healthy false-fire (151) | verdict |
|---|---|---|---|
| **2.6.8** (deployed) | 1.000 | 0.026 | **PASSED** |
| **2.7.2** (rejected) | 0.097 | 0.040 | **FAILED** — 0.097 < 0.70 |
| **2.7.3** (rejected) | 0.129 | 0.079 | **FAILED** — 0.129 < 0.70 |

The gate accepts the model we kept and rejects both we rejected. Every one of them
passed in-domain.

**One species, 31 clusters, one photographic session.** That is the whole scorch
side after `banana_bush` was retired in v2 (turgor loss, not our class — it does
not exist in the artefact, so it is not "reported but unfloored"; see
`RETIRED_DISEASE_ARMS` in `build_ood_benchmark.py`). The floor is worth blocking
on — it separates the deployed and rejected bundles by 10× — but read a pass as
*"did not regress on the one arm we can measure"*, not as *"scorch works"*. A
second arm (8–12 species) is the outstanding eval work.

The healthy ceiling sits on the same never-trained clusters so cost and
benefit share one population — a recall floor alone is satisfiable by firing
`leaf_scorch` more often, which the bias sweep in `docs/ROADMAP.md` (Model track,
"Settled — do not retry") shows explicitly.

**Keep the dataset private.** Its whole value is being outside every training
corpus; a public copy gets scraped into someone's "houseplant disease dataset"
and then into ours, which is exactly how the `aditisalvi04` contamination
happened.

### 7. Both "cheap levers" were RUN on 2.7.3 — and both FAILED

> **Exploratory (audit 2026-08-23).** Measured on v2; the falsification record in notebook §1 and `docs/ROADMAP.md` ("Settled — do not retry") still stands as a reason not to re-run either lever.

They were the whole content of the 2.7.3 release. Recorded here so neither is
re-proposed as a cheap next step.

1. **Re-weight the in-regime slice** — *code removed after falsification*; it was
   `REGIME_UPWEIGHT_CLASSES` / `REGIME_BOOST` in §1 and `regime_multipliers()` in §4.
   297 of 1,188 scorch rows boosted, stratum draw share 25% → 50%, class draw share
   held. Benchmark hibiscus moved **3/31 → 4/31, Fisher exact p=1.000.** One cluster.
2. **Regime-stratified calibration** — *code removed after falsification*; it was the
   §6 conformal audit. Marginal coverage 0.967; **low_chroma 0.972 vs rest 0.965** —
   the dull stratum was already the *better* covered one. There was no under-coverage
   to fix.

The removal is recorded in the notebook itself (§1, "2.7.3 RESULT: both levers
falsified, code removed"), which points back here and at `docs/ROADMAP.md`
("Settled — do not retry"). A grep for either constant now returns only that comment.

Why the probe did not translate: the 0.677 figure came from a probe trained
**exclusively** on the low-chroma quartile. Upweighting that stratum to half a class's
draws inside a head trained on everything is a far weaker intervention — the head can
fit both strata and has no pressure to rely on the transferring one. The boosted cut
was also `sat ≤ 0.179` against a benchmark median of ~0.09: even the dullest quarter of
the corpus is roughly twice the target chroma.

Also: Stage 1 misses benchmark scorch clusters at **8.5× the healthy rate** (Fisher
p=3.2e-05) — measured on the old pooled 62-cluster arm as 14/62 vs 5/151; re-measure on
the 31 hibiscus clusters before quoting a rate. A shrivelled, curled leaf is not
leaf-shaped to a PlantDoc-trained detector, the same structural cause as pests and
gray_mold. **New `scorch, early/dull` data should ship with boxes** — that is the
stratum carrying 19 of 31 clusters, not the plain-ground one.

### 8. Method corrections found while searching

- **Kaggle's *search* endpoint truncates too**, not just the file lister: it caps at 20
  results per page regardless of `pageSize`. Paginating to an empty page returned
  **1,684** datasets versus 329 — 5.1×. Any negative result from a short-page break
  condition is worthless.
- **The iNat harvest capped at 600/term**, silently truncating 5 of 9 broad terms.
- **pHash alone does not count distinct images.** Roboflow exports augmented by
  *rotation* survive pHash as distinct. Count by original stem where the source
  exposes one.
- **`kaggle_inventory.gate()` reports `augmentation_ratio` as sampled/distinct**, which
  understates collapse when the sample is small relative to the folder.

### 9. What remains unknown

- Whether the 0.33-AUROC latent transfer axis is *collectable* by instruction at all,
  or only selectable in embedding space after the fact.
**Answered since:**

- *banana_bush* — **retired** in benchmark v2 (turgor loss, not our class).
- *Can the head/operating point alone recover hibiscus?* **No, and it is bounded.**
  A linear probe on 2.7.3's own frozen features, trained corpus-scorch vs
  corpus-`leaf_spot`, puts **48.4% (15/31)** on the scorch side where the shipped
  head puts 12.9% (4/31) — the head discards 3.75× real signal, but a *perfect*
  linear readout still lands below the 0.70 floor. `CLASS_LOGIT_BIAS` swept on
  2.7.3 tops out at **0.323** within the 0.12 false-fire ceiling; reaching the
  floor needs bias 3.0 at FF 0.417.

## Build rules answered this round (2026-08-18)

> **Partly current.** `banana_bush` retirement is enforced in `build_ood_benchmark.py` (`RETIRED_DISEASE_ARMS`); blur → `train_only` stands. "Collection scope: deferred" is closed by the v9 collection protocol above.

**`banana_bush` scorch arm retired** — benchmark v2, 213 → 182 clusters. Its folder is
turgor loss, not `leaf_scorch_underwatering`, so the pooled 0.565 was never measuring one
thing; the honest 2.6.8 figure is hibiscus 1.000. The 24 *healthy* banana_bush clusters
stay (correctly labelled, in-domain ornamental, and cutting them would shrink the
false-fire arm to 127 for no gain). Enforced in `tools/build_ood_benchmark.py`
(`RETIRED_DISEASE_ARMS`, dropped before anything reaches disk) with tests.

**Blur-gate failures route `train_only` in the next build.** A frame below
`BLUR_LAPLACIAN_FLOOR` is short-circuited to `not_a_plant` by the server before Stage 2
ever runs, so grading a disease class on one measures something that cannot happen in
production. **Be clear about the size of this: it is 2 rows of 3,389 and it will not move
any metric.** It is an eval-correctness rule for every future build, not a model
improvement — worth having precisely because the corpus will grow and the rule should
already be in place. `not_a_plant` is exempt: blur is a legitimate example of that class.

**Serve-time scorch bias: not shipped.** +10 scorch clusters for +5 healthy false alarms
on the never-trained set, every gain on the arm since retired as mislabelled. Contradicts
the safety asymmetry. Recorded so it is not re-proposed.

**Collection scope: deferred** — to be scoped separately against the §6 per-stratum table.

## Standing disciplines

> **One item superseded (2026-09-16).** "There is no fixed never-trained benchmark by choice" no longer holds: the locked deployment-representative test set above is exactly that, carved from production scans rather than from scarce weak-class supply. The Eval-integrity fold-in contract (`auto_yes` train-only, held-out from judged rows only) stands and P6/T make the judge human.

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
calls — **the fold-in contract for when production volume exists, not something the
notebook implements today** (`auto_yes` appears nowhere in `train_notebook.ipynb`;
the only sha-based carve is `HOLDOUT_PERMILLE = 300` over the `(source, plant_id)`
group key): every `auto_yes` row is **train-only**; held-out splits draw only from
vision-agent-judged rows (deterministic ~20% by sha → calibration, rest eval). Near-dup
sweep on fold-in (pHash ≤ 6; provenance `agent_vision` > `model+user` > `model`).

## Production signal loop (server, no review)

Recorded post-response (consent-gated, anonymized: JPEG q88, EXIF/GPS stripped, ≤1024 px;
only linkage `scan_id`):
- **Scans:** every `/api/analyze` except `not_a_plant`/blur; uncertain (`pending`) + all
  disease always kept, confident healthy sampled at 0.1; `pending` expires 90 days.
- **Feedback:** negative → re-opens `pending` under `user_feedback` at the corrected
  class; positive → promotes to `auto_yes`.
- **Export:** `tools/export_data.py` → one `tar.gz` of `labeling.db`/`feedback.db`/
  `community.db` (read-only); maintainer judges offline + folds in.

## Risks

> **One item superseded (2026-09-16).** "Labels are a proxy — agent-judged, not human ground-truth (by design)" applies to v8 and earlier; v9 `eval_ok` rows require two-human adjudication (P6).

- **Calibration drift** — re-fit every ship (§6), from judged production items only.
- **Labels are a proxy** — agent-judged, not human ground-truth (by design).
- **Consent/regulatory** — recording is consent-gated + anonymized; the bundled privacy policy +
  the in-app consent screen are the canonical disclosure.
