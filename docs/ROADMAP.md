# PlantVision — roadmap

**Open work only.** The code is the record; finished work lives in git history,
not here. Companions: `DATASET_LABELING_PLAN.md` (the data loop and every
dataset finding), `../DEPLOYMENT.md` (deploy + Android signing/FCM),
`../server/models/MODEL_CARD.md` (deliberately version-agnostic — per-run
numbers ship as `model_card.json` inside each bundle).

Two independently releasable tracks: the **app** (`versionName` 1.9 /
`versionCode` 19 at `android/app/build.gradle.kts:31-32`, tag not cut) and the
**model** (bundle version, moves per training run). Every status here was
verified against the tree on 2026-09-18 (branch `model-fix-test`, which
contains all of `main`).

---

## App 1.9

What 1.9 carries beyond 1.8: the Garden rooms / care-calendar / treatment-plan
release (`c3560c4`), the Option-A rebuild of the community opinion sheet, and
`minSdk` 31. Details are in git history.

- [ ] **Cut the 1.9 tag and the RuStore build** — human-only.
- [ ] **First-launch intro video** — *externally blocked.* Server-hosted
      (~3 MB, background download, `/api/aqua`-style manifest), above the
      consent page, existing Lottie as offline fallback. Blocked on the
      user-generated Veo clip (vertical 9:16 macro monstera, scan-light sweep,
      `#16A34A`, ~8 s seamless loop, no text). Not started in the tree — no
      video view, no clip in `res/raw/`.

### Loose ends

- [ ] **Wikipedia attribution on the species card** — `SpeciesInfoCardView`
      renders a Wikipedia extract with no source line; CC BY-SA makes this
      licensing, not design. Decide: source line on the card, or accept the
      link one level down.
- [ ] **No test drives the real map hit-test.** `1faddd8` moved hit-testing to
      `Region` + `Path.FillType.EVEN_ODD` and the old tests were deleted, not
      replaced, because `MapCanvasView` is private (`ui/widget/WorldMapView.kt:228`).
      Make it internal and test a dispatched `MotionEvent`. `WorldMapViewTest`
      covers the bundled asset and `WorldMapGeometryTest` the normalizer; the
      hit test itself is untested.

---

## Security — open findings

August 2026 audit: 49 raised, 38 verified, 22 confirmed. Fixed items are in git
history. Theme is **cost and availability, not compromise** — an unauthenticated
caller is throttled only by a 2 req/s bucket, and staying under it is free.

Where a finding was only partly addressed the remaining half is called out
inline rather than closed.

- [ ] **No inference admission control** (`analyze_image`, `main.py:2184`;
      `INFER_WORKERS` at `main.py:886`) — at `INFER_WORKERS=1` a client within the rate limit pins
      RAM and starves everyone. Wants a semaphore before decode → 503; there is
      no `Semaphore` anywhere in `main.py` today.
- [ ] **Cost caps** (`ai_services.py:1533`, the species-diseases endpoint in
      `main.py`) — the
      species-diseases cache keys on client-chosen strings, so every distinct
      `name` is a guaranteed miss and a billable call. *OpenRouter has a monthly
      dollar cap, so bounded; stricter per-endpoint limits planned.* PlantNet
      has no server-side budget.
- [ ] **Prompt-cache poisoning** (`ai_services.py:1533` key vs `:1545` prompt) —
      `common_name` reaches the prompt through `_prompt_subject` but not the
      `v2:{name}:{lang}` cache key, poisoning the shared answer.
- [ ] **Unbounded list on `/api/progression`** (`ProgressionRequest.history` at
      `main.py:2882`, endpoint `main.py:3336`), handed straight to
      `compare_progression_history` on the event loop; every comparable field is
      capped.
- [ ] **`/api/analyze` replays another user's `scan_id`** (`main.py:2184`) — the
      dedup key is image sha256 + PlantNet lang + family, with no caller
      binding, and `scan_id` authorises mutations.
- [ ] **`/api/chat` caps decorative** (`ChatRequest` at `main.py:2588`) —
      client-chosen `session_id` resets turns and tokens.
- [ ] **Forgeable second opinions — half fixed** (`community.py:958`, tally at
      `community.py:1187-1197`). The durable `X-Community-Author-Key` identity
      landed (`POST /api/community/authors/adopt`, `main.py:3981`) and the
      consensus tally dedupes on `COALESCE(author_id, author_pseudo_id)`
      (`community.py:1198`).
      Remaining: the header is optional on the opinion endpoint
      (`main.py:3872`), so a client that simply omits it still votes under a
      self-chosen `author_pseudo_id`.
- [ ] **EXIF GPS still leaves the device on two secondary paths.**
      `/api/analyze` is fixed — `AnalysisActivity.kt:194` runs
      `ImageUtils.prepareImageForUpload`, which decodes to a Bitmap and
      re-encodes, so no EXIF survives and the PlantNet fan-out is stripped too.
      Not converted: the negative-feedback image upload
      (`FeedbackController.kt:200` → `ScanRepository.kt:662`) and species
      identify (`ui/identify/SpeciesIdentifyActivity.kt:126` →
      `ScanRepository.kt:298`)
      still post the raw camera JPEG. The bundled privacy policy states that
      photo metadata including GPS is stripped before storage.
      *Smallest change, largest privacy return.*
- [ ] **Pseudo-id in GET paths** (`ScanRepository` →
      `GET /api/community/my-scan-opinions/{id}`, `main.py:4127`) — a bearer
      capability landing in access logs.
- [ ] **No lockfile.** Runtime pins are a mix of exact (`fastapi==0.141.1`,
      `Pillow==12.3.0`) and floors (`numpy>=2.4.6,<3.0`, `onnxruntime>=1.28.0`,
      `firebase-admin>=7.5.0`), and the transitive deps that actually serve
      requests (starlette, httpx) are unconstrained by us. CI catches a
      resolution break as a red job rather than at deploy time — a lockfile is
      still the actual fix.
- [ ] **Host named publicly** — host IP + SSH user `root` in git history
      (`cd4bda2`, removed with the admin panel in #149). **No credentials
      leaked** — no key or password was ever committed. Action is ordinary
      hardening: `PermitRootLogin prohibit-password`, `PasswordAuthentication
      no`, SSH rate-limiting.

---

## Garden refactor

**Shipped in `c3560c4`.** The Garden tab is now **a single surface — the
do-today queue over room cards, each holding a plant collection, with no pane
switcher**. The care month grid is its own screen
(`ui/garden/care/CareCalendarActivity`, opened from the button at the top of the
tab); the lunar calendar is per-plant and lives on the plant detail screen only.
The four planned pieces all exist: the Today queue (`util/CareQueue.kt` +
`CareRepository.queue()`), treatment checklists (`TreatmentPlanEntity` /
`TreatmentStepEntity` + `util/TreatmentPlanFactory.kt`), the health header
(`util/PlantHealth.kt`, consumed by `DetailHeaderController` and
`RoomDetailActivity`), and rooms (`RoomEntity`, `RoomRepository`,
`RoomDetailActivity`, `RoomDialogs`).

Room DB is at `version = 9` (`data/local/Database.kt:876`), 11 entities, with
`MIGRATION_1_2`…`MIGRATION_8_9` registered (`:1182-1190`).

Still open:

- [ ] **★ Human gate — health heuristic.** The header tells users a plant is
      *declining*; a wrong verdict is worse than none. Sanity-check
      `util/PlantHealth.kt`'s thresholds against real scan histories. The
      thresholds are deliberately in one tunable place so this is a review, not
      a refactor.
- [ ] **Room-level care defaults.** A room implies light and humidity, so it
      could seed default care and the light-meter baseline
      (`Database.kt:595` notes the intent). Rooms group and filter today;
      nothing derives care from them.

---

## Model track

> **2026-08-23 audit correction.** The historical experiment narrative below is
> retained as a research trail, not as verified release evidence. The archive's
> raw predictions contradict several headline scores (including 2.7.2's stated
> 3/31), the 31/31 v2.6.8 number includes a runtime veto rather than raw model
> capability, a probe mixed populations/denominators, and the 182-image private
> set is neither OOD nor independent enough to gate a release. Do not tune or
> ship from those numbers. Evidence-backed status and v9 requirements follow
> in this file.

`MODEL_VERSION` is now **2.7.5** (notebook §1; set 2026-09-21 for the next
Colab run). No discovered historical archive has the notebook-bound manifest,
complete lineage, and serving contract, so none is deployable by the strict
current API. `plantvision_combined_v2.7.1.tar` is only the newest structurally
complete **legacy diagnostic candidate**; v2.7.3 failed its own recorded gate
and lacks Aqua. The next candidate is a fresh GPU run on dataset v8 under the
2026-09-21 policy below; v9 is no longer a precondition.

### 2026-10-05 — Aqua export runs on any Colab Python

- Colab moved to CPython 3.13+, where TensorFlow 2.19 (the onnx2tf backend the
  Aqua export is pinned to) has no wheel, so §1 refused every `combined` run.
  The converter now lives in its own CPython 3.12 env that §1 provisions with
  `uv` (TF 2.19.0, onnx2tf 1.28.8 — the last 1.x — and their deps, all pinned);
  §5 exports the ONNX in the kernel and converts it there in a subprocess.
  TensorFlow never loads into the training kernel; verification and the §6
  TFLite eval use `ai-edge-litert` in the kernel. §1 converts §5's starting
  point (ImageNet MobileNetV3-Small, fresh head) through the whole path (FLOAT32, `[1,224,224,3] → [1,9]`,
  parity) before any training, and §6 seals the env into `run_manifest.json`.

### 2026-10-01 — one served pass in §6, supplement boxes, light provenance

- **§2 crash on v8 fixed.** The 2026-09-21 change made `capture_family_id`
  optional but left the "one eval row per family" check keyed on it, so every
  v8 row shared the family `None` and the second eval-eligible row raised
  `independent-family violation`. The 2.7.5 run would have stopped in §2.
  Families now route as one unit (below), and
  `server/tests/test_notebook_supplement.py` runs the real loader on a
  v8-shaped manifest.
- **Stage-1 audit + end-to-end eval: one bounded pass, ON in every mode.**
  The audit used Ultralytics on `detector.pt` and the crop eval decoded every
  photo again at camera resolution; together with §5's TF objects and the idle
  persistent DataLoader worker pools (one per loader, `EVAL_WORKERS` each) that
  OOMed a `combined` run, so the end-to-end number was never produced for the
  shipping mode. Now §6 releases the pools and the training state, then
  streams `val_realworld` once through the shipped `detector.onnx` +
  classifier with the server's own detector / crop / vote rules — decoded at
  the app's 1024 px upload size (JPEG draft), two threads ahead, one photo per
  classifier run (members and mirrors together, as `classifier.py` batches
  them). Peak extra RAM is tens of MB. From that one pass: the Stage-1 audit
  (no-box at 0.25, plus the share below the 0.40 gate floor), the end-to-end
  disease F1 (`served_vote` headline, `crop_only` and `fold` alongside), and
  the served OOD gate scored **out of sample** (fitted on `temperature_cal`).
  The served-gate calibration uses the same streamer. The helpers are pinned
  to `detector.py` / `pipeline.py` / `classifier.py` by
  `test_served_gate_mirror.py`.
- **Supplement boxes reach Stage 1.** A YOLO label file beside a photo
  (`<class>/<name>.txt`) is read in §2 with no image decode and indexed in one
  JSON file. The photo is stored in its EXIF-rotated frame (the one a browser
  labelling tool shows). §3 hard-links boxed photos still in the training
  split into the detector set, 15 % of families to detector val. The PlantDoc
  → YOLO build reads boxes without decoding, decodes and encodes on a bounded
  thread pool, and writes byte-identical files and the identical seeded split
  (pinned against the old serial loop). Background negatives are hard-linked.
  §2's PlantDoc crop pass decodes only rows that write something.
- **Light provenance.** Nothing in the supplement's per-row record stops a run.
  A photo with no manifest row loads train-only; a capture family with any
  train-side member trains as a whole; a reused `immutable_source_id` is
  skipped. `filter_supplement.py` adopts unlisted photos as QA'd `train_only`
  rows. §2 writes `supplement_manifest_next.csv` (in the archive's
  `analysis/`) with `first_seen_model_version` stamped on exactly the rows the
  run trained on, so re-stamping by hand is gone. The minimum is
  `DATASET_LABELING_PLAN.md` P0.

### 2026-09-21 — study-project policy, and the 2.7.5 run is unblocked

- **Licences are provenance, not a gate.** Every fetcher records the per-row
  licence (iNat `license_code`, COCO licence id, Open Images licence key,
  DoctorP `cc-by-nc-sa-4.0`, the supplement's `license_id`) and nothing
  filters on it: `source_inventory.license_policy` in `model_card.json`
  tallies what the bundle trained on. The 2026-08-25 filters would have
  dropped ~87 % of iNat research-grade Plantae (83.0M vs 10.8M observations,
  live API 2026-09-21) and all 1,558 DoctorP rows. Eligibility analysis of the
  returned sources: `DATASET_LABELING_PLAN.md`, "Sources returned under the
  2026-09-21 policy".
- **The supplement contract is soft.** `STRICT_SUPPLEMENT_FILTER_VERDICT /
  _LICENSE_PROVENANCE / _MODEL_PROVENANCE` default to `False`: a v8 manifest
  loads, every absent column gets a documented fallback (sha256 source id, no
  family, `split_hint` routing) and one warning with the count. Present
  columns are validated as before; `bench_leak` / `phash_mismatch` still raise.
- **The served OOD overlay is calibrated and on.** §6 now fits
  `ood.served_gate` through the served path (Stage 1 ONNX → the
  `serving_contract.crop` rules → whole-frame fold → min member distance / max
  member plant logit) at `TARGET_PLANT_FPR` on `temperature_cal`, reports the
  three-of-three gate's NAP recall and plant false-reject beside the head-only
  rates, and `pipeline.py` applies **only** those thresholds. Closes the
  "Served-gate calibration tool" item below; the tool route was not needed.
- **Conformal `coverage` is honest again.** `conformal.py` advertises the
  notebook's `target_coverage` (0.90) when the sidecar carries the
  out-of-sample audit that backs it (`coverage_basis = audit_target`), else
  `1 − alpha`. Closes the alpha = 0.6 item below.
- **One blur gate on both tiers.** Android `util/BlurGate.kt` computes the
  server's statistic (PIL luma, 320 px long edge, interior Laplacian) and uses
  the server's 50.0 floor; the app used to score a ~1000 px decode against 100.
  `test_blur_parity.py` and `BlurGateTest` pin both to one fixture. Aqua's
  `not_a_plant` rejection remains the trained head alone — it has no detector,
  so it cannot run the served gate; §6 reports its head recall in the aqua
  metrics.
- **Still open from this round:** the seed replicate and the augmentation
  ablation (below); and Aqua has no classifier-side OOD signal of its own.
  (`RUN_DETECTOR_CROP_EVAL` for combined mode closed 2026-10-01.)

### 2026-09-16 review — code is done, the model is data-gated

The two `model-fix-test` commits (`ec8916f`, `f002dc8`) are contract and
provenance work: manifest-bound bundle with SHA-256 binding and one selected
graph, per-image licence/provenance on every row, HF/Kaggle revision pinning,
fail-closed Aqua, deterministic torch, DoctorP off by default, the served-gate
precondition on the OOD overlay, and the v2 sentinel demoted to diagnostic.
None of it moves capability; the branch itself concludes the next candidate
needs v9 data. So the answer to "code or data?" is **both, in that order —
and the code half is now mostly done**. What the review changed and what is
still open:

**Fixed on the branch (this review).**

- **Train/serve geometry parity** (§4). The 2.8.0 draft trained on a
  letterboxed full frame (`PadToSquare → Resize(IMG, IMG)`) while every eval
  slice, both calibration splits, `classifier.py` and the Aqua client
  centre-crop after a shorter-side resize at `EVAL_RESIZE_RATIO`. A 4:3 frame
  reached the head ~1.4× larger at serve time than at train time, with
  padding bars serving never produces — introduced together with the removal
  of the only scale augmentation. Training now uses the serving transform
  with `RandomCrop(IMG)` in place of the centre crop
  (`serve_parity_v2_unablated`).
- **The unseen-domain sentinel must run.** `REQUIRE_OOD_BENCHMARK` stays
  `False` (v2 is not a gate), but `REQUIRE_OOD_DIAGNOSTIC_RUN=True` fails a
  run that cannot download and score it, and its alerts are written into
  `model_card.json → ood_benchmark.alerts` and `run_log.jsonl`. A non-blocking
  check that can silently stop existing is the failure mode the original gate
  comment warned about.
- **CI never ran on this branch** — `model-fix-test` matched no push glob.
  `model-*` is now in `.github/workflows/ci.yml`.

- **Evaluation tooling** (fresh-eyes review of `tools/`, `bundle.py` and
  their tests). Fixed: `build_ood_benchmark.py report()` printed healthy
  *recall* where the docs said false-fire, so a bundle abstaining on half its
  healthy images and one calling half of them scorched printed identical rows
  (now recall, abstentions and a separate FALSE-FIRE row); `eval_bootstrap`
  averaged absent disease classes in as hard zeros (a perfect model on the
  healthy+scorch benchmark scored 1/7, and family resampling dragged every
  thin class's CI toward 0 — now macro over the classes present, `nan`-aware
  CIs, ties reported as `p_tie`); `None`/`NaN` capture-family IDs were pooled
  into one pseudo-family (now rejected); `paired_eval --served` re-parsed
  warning strings instead of using `pipeline._derive_abstention` and did not
  count natural `not_a_plant` verdicts as coverage loss (now both);
  `build_ood_benchmark.cluster_paths` silently dropped undecodable images
  (now an error); `verify_bundle` accepted an embedding bank declared without
  `bank_file` that boot then rejected (now rejected at preflight). Still open:
  `paired_eval._load_bundle_binding` in `--argmax` mode follows symlinks and
  accepts `size_bytes == 0` where `bundle.verify_bundle` does not — reuse the
  latter; and `report()`/`predict_served` have no end-to-end test.

**Open, code-only (no data needed), ordered by value.**

- [x] **Served-gate calibration** — done in the notebook (2026-09-21, above),
      with the crop/detector rules mirrored from `serving_contract` and pinned
      to `pipeline.py` / `detector.py` by `test_data_invariants.py`. The
      direction of the old error still holds: min-over-members ≤ whole-frame
      distance, so a per-image threshold under-fires; the served fit is
      strictly more permissive on plants at the same false-reject target.
- [x] **Conformal `coverage` on the wire** — `conformal.py` advertises the
      audit-backed `target_coverage` (2026-09-21, above).
- [x] **Production-faithful eval is ON in every mode** (2026-10-01). One
      bounded served pass feeds the Stage-1 audit, the end-to-end F1 and the
      out-of-sample served-gate check; the first 2.7.5 log shows its
      photos/s and the host RAM available when it started.
- [ ] **Strict determinism is still untested end to end — and §3 switches it
      off.** Ultralytics' trainer calls `unset_deterministic()` when
      `detector.train()` returns (`use_deterministic_algorithms(False)`,
      `cudnn.deterministic = False`, `CUBLAS_WORKSPACE_CONFIG` popped), so the
      2.7.5 run trained §4/§5 non-deterministically and its card records
      `cudnn_deterministic: false`. Re-assert after §3 (restore the cuBLAS env
      var first), preferably `warn_only=True` with the warnings recorded in
      `run_manifest.json`.
- [ ] **The augmentation profile is unablated** (v1 letterbox and v2 parity
      alike). One paired run at `SEED=0`, profile vs no-artifact-augs, scored
      with `tools/eval_bootstrap.py` on capture families, settles whether the
      sensor-artifact block earns its CPU cost.

**Data (the critical path)** — the v9 protocol, the OOD benchmark v3 and the
locked deployment-representative test set are specified in
`DATASET_LABELING_PLAN.md` ("v9 collection protocol").

### Superseded historical interpretation (do not use as a release gate)

**The OOD AND-gate has been three-of-three since 2.7.2.** It fires
`not_a_plant` only when the detector confidence is below
`NAP_DETECTOR_CONF_FLOOR`, the classifier-side OOD signal fires (embedding k-NN
distance over the shipped bank, or the MaxLogit floor when no bank ships),
**and** the classifier's own `not_a_plant` head is not vetoing
(`NAP_HEAD_MASS_FLOOR`, default 0.05, `pipeline.py:150-188`). The veto
is on for every bundle including ones exported before the key existed; a bundle
restores the historical two-of-two gate with `ood.nap_head_mass_floor: null`
(`pipeline.py:315-336`).

**Four releases have failed to beat 2.6.8.** 2.7.0, 2.7.1, 2.7.2 and 2.7.3 each
collapsed on the never-trained OOD benchmark's `leaf_scorch` arm while passing
in-domain: the deployed 2.6.8 scores 1.000 on the 31 hibiscus clusters, the two
measured rejects 0.097 and 0.129, against a floor of 0.70. The instrument that
finally caught it before shipping is the **out-of-domain ship gate** in §6
(`OOD_ARM_FLOORS = {'hibiscus': 0.70}`, `REQUIRE_OOD_BENCHMARK = True`, blocking
since 2026-08-18): 2.7.3 raised there and never became a loadable bundle.
*(Status today: `REQUIRE_OOD_BENCHMARK = False` and `REQUIRE_OOD_DIAGNOSTIC_RUN
= True` — the sentinel must run and be recorded, its floors are advisory; see
the 2026-09-16 section above.)* Full
per-release numbers, the benchmark's construction and the served-vs-argmax
caveat are in `DATASET_LABELING_PLAN.md` §6b.

### The next move is data, and its target changed on 2026-08-22

Every data-free lever has been tested and is falsified or bounded below the
gate's floor. The bound that settles it: a linear probe on 2.7.3's **own frozen
features**, trained corpus-scorch vs corpus-`leaf_spot`, puts 48.4% of the
benchmark's scorch clusters on the scorch side where the shipped head puts
12.9% — the head discards 3.75× real signal, and a *perfect* linear readout
still lands below 0.70.

**The failure is morphology, not framing, 6:1** — of the 31 hibiscus clusters
under 2.7.3, 19 are called `leaf_spot` against 3 called `healthy`. The corpus
defines scorch as end-stage whole-leaf browning; the benchmark shows it early,
dull and edge-confined on a still-green blade, which is the model's `leaf_spot`
prototype. Mechanism, decile statistics and the ablations are in
`DATASET_LABELING_PLAN.md` §1-§4.

- [ ] **Collect early-stage marginal necrosis** — ~400 clusters, `brown_frac <
      0.05`, damage confined to the leaf edge, blade still mostly green, and
      **judged against `leaf_spot` exemplars**. The boundary being trained is
      scorch↔`leaf_spot`; scorch↔healthy already probes at 0.95. New scorch data
      should ship with bounding boxes — Stage 1 misses the benchmark's scorch
      clusters at 8.5× the healthy rate. Full per-stratum spec in
      `DATASET_LABELING_PLAN.md` §6.

### Settled — do not retry

Each was run as a single knob and measured. Re-proposing one costs a run and
returns nothing. The notebook's §1 falsification record points here for the
numbers.

| lever | verdict | evidence |
|---|---|---|
| `CLASS_LOGIT_BIAS` | **dead** | on 2.7.3, best within the 0.12 false-fire ceiling is bias 1.44 → hibiscus 0.323. Reaching the floor needs FF 0.417 — a third of never-trained healthy plants misdiagnosed |
| regime-aware sampling (2.7.3) | **falsified** | stratum draw share 25% → 50%, hibiscus 3/31 → 4/31, Fisher p=1.000. Code removed |
| regime-stratified conformal | **falsified** | marginal coverage 0.967; low-chroma 0.972 vs rest 0.965 — the dull stratum was already better covered. Code removed |
| margin-preserving preprocessing | **not supported** | probe delta +0.013 against a +0.03 bar set in advance; letterboxing at inference made 2.7.2 *worse*, 3/31 → 1/31 |
| sampler weights generally | **falsified** | 2.7.1's `kaggle_ornamental` ablation (1.0 → 0.35) moved ornamental scorch by nothing and cost healthy (recall 0.947 → 0.861, McNemar p=0.0074) |
| temperature refitting | **arithmetically impossible** | `argmax(z/T) == argmax(z)` for every `T > 0`. It moves confidence and set sizes, never a prediction |

**Run-to-run noise is ~2pp.** Two runs of identical config at `SEED=0` gave
`val_indomain` 0.884 and 0.864. That band swallows most single-knob deltas —
which is why 2.6.8→2.7.1 (+1.2pp) and 2.7.2-vs-2.6.8 (−1.2pp) mean nothing on
their own, and why the OOD arm is the instrument that matters.

### Why it broke — the constraint any fix must satisfy

A longitudinal sweep over 14 archived bundles (2.3.1 → 2.7.1) found the collapse
is **a single cliff, not a drift**: 2.6.9 → 2.7.0 lost 31 of 31 hibiscus
clusters, gained 0, McNemar p=9.3e-10, while healthy recall stayed flat. The
cause is a **content swap, not a volume change** — the only ornamental source
was deleted upstream and replaced by one supplying zero scorch. Any fix has to
explain a step change inside the 2026-07-03 → 2026-08-08 window. The source-loss
record and the per-class fallout are in `DATASET_LABELING_PLAN.md`
("Source loss").

**Stage 1 is a real secondary contributor.** The detector trains on PlantDoc
*leaf* boxes and returns no box on 44.9% of `pests` and 43.5% of `gray_mold`
against 13.3% for healthy; a shrivelled, curled leaf is not leaf-shaped either.
Boxes on new data are the cheap mitigation.

### Open experiments that are not data

Ordered by evidence. None is expected to clear the floor alone; all are
compatible with the collection round.

- [ ] **Capacity — `frontier` (ViT-B/14) or DINOv3.** Previously ruled out on
      "features are fine", which was read off the scorch-vs-*healthy* probe
      (0.95). The axis that fails is scorch-vs-`leaf_spot`, where the same
      features sit at 48.4% — telling a scorched margin from discrete spots is a
      fine-grained dense-feature problem, which is what DINOv3 claims to
      improve. **Verify DINOv3's licence before committing**; it is not
      DINOv2's Apache-2.0. `frontier` is a plain mode, so it costs one run.
- [ ] **`USE_REGISTERS`** — `dinov2_vits14_reg` has cleaner features; off today
      only because the validated 0.91 probe used non-register weights. One flag,
      one run, one paired bootstrap.
- [ ] **Domain-robust training objective** — an intervention strong enough to
      match the probe that motivated 2.7.3, rather than a sampling nudge.
- [ ] **Report the production-faithful number.** Every headline metric scores
      the classifier on a whole val image while production classifies
      **detector crops** and votes. Since 2026-10-01 §6 produces
      `end_to_end.served_vote_disease_macro_f1` in every mode; publish it next
      to the whole-image number and treat the gap as a finding, not a footnote.
- [ ] **Build the comparison set** from the exportable production scans (2,593
      and growing) — the only never-trained, real-distribution eval that scales.
      Specified as the locked deployment-representative test set in
      `DATASET_LABELING_PLAN.md`; not yet carved.
- [ ] **A second OOD arm (8–12 species).** The gate rests on one species and one
      photographic session, so a pass reads as "did not regress on the one arm
      we can measure", not "scorch works". Specified as OOD benchmark v3 in
      `DATASET_LABELING_PLAN.md`; not yet collected.

### 2.8 — the leap

> From "a good leaf classifier" to a **holistic, trustworthy plant doctor**.
> Items 1–3 change what the model *is*.

1. **Real-distribution data at scale.** Every source today is scraped or
   curated; production scans are what users actually shoot. The evidence is
   already in the corpus — DoctorP (real app photos) is the source that supplies
   the starved classes. Nothing else moves reliability as much per unit effort.
2. **Multi-label instead of single-label.** A softmax over 9 classes cannot say
   "underwatered **and** nutrient-deficient", the most common real
   co-occurrence, and plausibly part of why `nutrient` over-fires. Sigmoid head
   + per-class thresholds + per-class conformal. Do it *after* the boundary is
   stable, never during.
3. **Segmentation, not a box.** Stage 1 → lesion **area**, so severity is
   measured rather than heuristic, and one diseased leaf stops being diluted in
   a whole frame. Also the honest fix for the crop-vs-whole gap above.

### Closed releases

Per-run numbers, knob diffs, data deltas and the Drive archive for every one of
these (plus the 2.3.1–2.6.7 runs) are in `MODEL_HISTORY.md`, read out of each
archive's own sidecars.

- **2.6.8** — shipped, still deployed. The incumbent on every axis.
- **2.6.9** — gate-passed, lost a paired A/B vs 2.6.8. Not shipped. On the OOD
  scorch arm it is marginally the best model on record.
- **2.6.10** — recovered scorch but lost a 2.26k-image source mid-run, so it
  anchors no A/B.
- **2.6.11 / v8** — superseded by the 2.6.12 general-plant pivot, whose data
  cleaning it folded in. 2.6.12/2.6.13 never trained; the work became 2.7.0.
- **2.7.0** — data-integrity release. Collapsed the scorch arm (the cliff above).
- **2.7.1** — single-knob ablation of `kaggle_ornamental`. Falsified it, and cost
  healthy. Reverted.
- **2.7.2** — archaeology + the NAP head veto. The veto shipped as a serving
  change and made the OOD gate three-of-three; the model did not ship.
- **2.7.3** — regime-aware sampling + regime conformal. Both falsified, both
  removed from the notebook. First release stopped by the out-of-domain gate
  before producing a bundle.
- **2.7.5** — trained 2026-10-05 on v8; ship gate passed; the first bundle that
  boots on the strict server. rw 0.865, holdout 0.897 (2.7.2: 0.863, same
  rows) but holdout healthy precision 0.784 (2.7.2: 0.85), hibiscus 0/31,
  end-to-end served 0.824, Aqua 0.801. Open before it replaces 2.6.8: a paired
  holdout bootstrap vs 2.7.2. Numbers in `MODEL_HISTORY.md` row 16.

---

## Documentation debt

None of it blocks a release.

- [ ] **README license section under-lists runtime deps.** `NOTICE` and
      `THIRD_PARTY.md` were deleted in `73b6ef0` on the grounds that attribution
      is now summarised inline in the README, but that section names DINOv2,
      YOLOv8 and the datasets and then covers everything else with "runtime
      libraries remain under their own licenses". osmdroid and the RuStore
      appupdate/review SDKs are the two worth naming.
- [ ] **`build.gradle.kts` still describes a bundled Aqua asset that does not
      exist.** The comment at `android/app/build.gradle.kts:407-409` says the
      model is provisioned at `assets/models/aqua_classifier.tflite`; the code
      path is filesDir-only — `AquaModelManager` stores the downloaded
      `.tflite` under `filesDir/aqua/`, and `app/src/main/assets/` contains no
      model. `build.gradle.kts:78-79` and the `AquaClassifier` KDoc (fixed on
      `model-fix-test`) already state it correctly.

---

## Architecture — reviewed 2026-08, follow-ups

A full review of both trees ran during the 2026-08 cleanup. The verdict: the
server's domain layer and the app's layering are genuinely sound; the debt is
concentrated in a handful of god-modules and in HTTP-layer idiom. What was safe
to do without a dedicated PR is done — the event-loop fix, the shared feed
serialiser, the global exception handler, test-store isolation, and the detekt
complexity gates. What is left needs its own PR each, and must not be folded
into another change.

**Server**

- [ ] **Split `main.py` (5.4k lines) into routers.** It holds the app, five
      middlewares, the limiter/ban/quota state, the metrics counters, two thread
      pools, image validation, 15 Pydantic models, 38 handlers and the lifespan.
      Order: extract `deps.py` (the nine hand-copied quota preambles become one
      `Depends`) → switch the ~400-line duplicated alias-import block to module
      imports → then one router group per PR, smallest first, community last.
      Gated by one hard constraint: tests `monkeypatch.setattr` names on
      `main` (`conftest.py` swaps the pipeline in), and 12 test modules
      `importlib.reload` it, so a router doing
      `from main import pipeline` binds `None` forever. `openapi.json` plus
      `test_openapi_contract.py` catch a dropped route, which is what makes the
      split safer than it looks.
- [ ] **`analyze_image` is 450 lines**, of which ~150 are pure response
      assembly. Extracting `build_analyze_payload()` makes the entropy guard,
      the dual-care split and the `not_a_plant` species suppression unit-testable
      without a TestClient. Best value-for-effort item on this list.
- [ ] **Group the 15 inline Pydantic models** into `schemas.py` with shared
      `Annotated` field types. The same `scan_id` is `8..64` in two models and
      `1..128` in four others, and the charset check is written longhand four
      times. Standardise on the looser bound; tightening is its own decision,
      because the Android client mints these.
- [ ] **`ai_services.py` (3.4k lines) is three modules** — transport, five
      features, and ~700 lines of bilingual product content that belongs with
      `diseases_data.py`. `community.py` (2.9k lines, 64 functions) is nine
      responsibilities behind one namespace and wants to become a package.
- [ ] **Five hand-rolled SQLite stores** (`community`, `feedback`, `labeling`,
      `push`, `telemetry`) repeat the same connect/init/prune boilerplate, and
      five near-identical `daily_*_count_for_ip_hash` functions
      have silently diverged (rolling 24h vs calendar day). Schema evolution
      replays every migration on every boot with no `PRAGMA user_version`.
- [ ] **No `response_model` on any route**, so `openapi.json` describes request
      bodies only and the contract test compares paths and methods alone. The
      30-key `/api/analyze` payload can change without failing CI while a frozen
      APK parses it. Cheapest first step: assert the response keys in the
      contract test.
- [ ] **Nothing type-checks the annotations.** Coverage is high (92-100% outside
      `main.py`) but neither mypy nor pyright is configured or in CI.

**Android**

- [ ] **`AnalysisActivity` loses a scan on rotation.** The server round-trip
      runs in `lifecycleScope` and the Room write is a suspend call in the same
      scope; there is no `configChanges` and no ViewModel. Rotating mid-analysis
      burns a second paid inference, and rotating between the response and
      `saveScan` loses the scan after the server has already issued its
      `scan_id`. The only outright correctness bug found.
- [ ] **`ScanRepository` is a 1708-line god object** spanning seven bounded
      contexts, injected wholesale into six consumers. Split by context.
- [ ] **XML vs imperative-Kotlin view construction.**
      `DetailExtraCardsController` and `CarePlanCardView` build whole hierarchies
      in code with hardcoded dp and colors — 113 of the ~166 programmatic View
      constructions app-wide, and the entire reason for their size. A shared kit
      already exists (`DetailUiKit.baseCard()`, "single source of truth for every
      detail-screen panel") with two call sites, and the two styles disagree on
      screen today.
- [ ] **Gson is still a live dependency** for Room field JSON, `ResponseCache`
      and intent payloads, so the DTOs in `data/model/Models.kt` carry 389
      hand-mirrored `@SerializedName` annotations beside their `@SerialName`, plus a
      `KSerializer<JsonObject>` bridge and six ProGuard rules. Retiring it is
      M/L but deletes all of that.
- [ ] **`data/GuideDataProvider.kt` (1638 lines) is a second localization
      system** —
      78 EN/RU pairs as Kotlin strings, outside the resource framework, driving
      41 hand-written `LocaleHelper.isRussian(...)` branches. Nothing enforces
      RU parity there the way `strings.xml` gets it for free.
- [ ] **Navigation Component manages a two-destination graph** while 22
      Activities navigate by explicit Intent, and the integration has been
      unwound by hand (no `setupWithNavController`, a re-entrancy flag, tabs
      living in a ViewPager2 instead of as destinations). Its cost now exceeds
      its contribution.
- [ ] **No DAO returns a `Flow`**, so every screen re-reads on resume and
      cross-screen edits are invisible until a foreground transition. That is
      the root cause of the 17 `lifecycleScope.launch(start = UNDISPATCHED) {
      withContext(NonCancellable) { … } }` sites. Migrate one screen per PR,
      additively — and only after the `AnalysisActivity` and `ScanRepository`
      items land, which give it seams to attach to.
- [ ] **Ratchet the detekt thresholds down.** They are set just above today's
      worst case (LongMethod 210 vs a measured 195; LargeClass 1300 vs 1165);
      lower each by ~20% as the splits above land. `GuideDataProvider.kt` is
      excluded from the two size rules until it stops being a content blob.
- [ ] **Single `:app` module is still right** — do not modularize. The one edge
      that blocked a clean `:core:data` extraction (util → ui) is now fixed.

---

## Later / maybe

- [ ] **Spider-mite split** from `pests` (deferred — `pests` P already 0.94).
- [ ] **More UI languages** (server content already bilingual).
- [ ] **Expert-human consultation** — ties into 2.8 safety escalation, builds
      on the shipped second-opinion loop.

---

## Decisions

- **Reliability & safety first** — optimize against a confident wrong diagnosis
  (precision + honest uncertainty + OOD/blur refusal) over headline recall/F1.
- **Data > architecture** — the ceiling is data/precision; spend on the flywheel
  and scope before backbone size.
- **One species surface** — species info lives on `SpeciesDetailActivity` alone;
  every other screen references a species through the reusable
  `ui/common/SpeciesInfoCardView` (Wikipedia photo + name + description + "more
  about this species"), embedded on the scan result, species identify and the
  community opinion sheet. It was dropped from that sheet in Community v2 and
  restored in the Option-A redesign — knowing what the plant *is* changes what
  you would call the problem. Never build a second species card.
- **New knowledge cards are best-effort, never blocking** — LLM + cache with a
  graceful `available:false`.
- **No new disease classes until the scorch/nutrient boundary is stable** —
  scope expansion waits for **2.8**.
- **Target store: RuStore** (RU audience; app already bilingual). No Google Play.
- **No Compose rewrite** — keep XML Views, single-module; modernize the guts
  (Hilt, Ktor).
- **Stay free + honest** — calibrated, OOD-aware, on-device offline,
  privacy-first. The current build has no account, login, email or password.
  Community V2 creates a persistent anonymous per-device handle only after
  Community opt-in or an explicit one-scan share; it deduplicates authors,
  likes and views and enforces moderation without identifying the person.
  Account Beta and any account linkage remain deferred and are not part of
  this branch. The handle and same-device linkability are disclosed in
  `privacy_policy.html`.
- **Data loop is local** — collect/judge offline → Kaggle supplement → notebook
  pulls it.

---

## Working agreements

**Training runs on Colab, never locally.** The target runtime is a Colab **A100
(40 GB VRAM, 80 GB RAM, 100 GB disk)** driving `server/train_notebook.ipynb`;
the notebook's own header sizes a full run at ≈20–35 min on a single
A100-40GB including data prep.
Everything — training, distillation, calibration, ONNX/TFLite export — happens
there. For ML work, edit the notebook or the pipeline code and hand the
maintainer the cells to run.

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
`work/**`, `codex/**` and `model-*`, with a `paths-filter` running the **android** and
**server** jobs only when their trees change. Android: one Gradle invocation of
`ktlintCheck` + `detekt` + `testDebugUnitTest` + R8 `assembleRelease`, then APK
signing verification. Server: `ruff` + `pytest`, on Python 3.14. Dependency
updates are made by hand.

No `pull_request` trigger, deliberately: every branch that reaches a PR was
pushed here first, so a PR trigger only duplicates runs (they land under a
different `github.ref`, so `concurrency` does not cancel them).

Two failure modes worth remembering, both of which produced a *green-looking*
repo with nothing actually running:

1. **A branch that matches no glob gets no CI, and its PR shows an empty
   status rather than a red one.** That is how #158-#167 merged unbuilt —
   they came from automated branches — and five mutually-incompatible
   dependency bumps broke the Android build at Gradle configuration time on
   `main`. The push-only stance is safe *because* dependency updates are
   manual now. Reintroducing any automation means adding its branch prefix
   to the trigger list in the same commit.
2. **`paths-filter` could not `git fetch` its diff base** under
   `persist-credentials: false` with the default depth-1 clone, so the
   `changes` job died on every push — and when `changes` dies, `needs:`
   silently skips android and server. Fixed with `fetch-depth: 0`, which
   makes the base commit already local so no fetch (and no credential) is
   needed.

Note the filters gate only on `android/**` and `server/**` (plus the workflow
itself): a change confined to `docs/`, `README.md`, `DEPLOYMENT.md` or
`CLAUDE.md` runs neither job.

---
