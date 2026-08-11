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
- [x] **Verdict: DO NOT SHIP 2.7.0. Keep 2.6.8.** (paired eval, 2026-08-11,
      `tools/paired_eval.py`; bundles for 2.6.0 / 2.6.8 / 2.7.0 scored on
      identical image lists.)

**2.7.0 is better in-domain and much worse out-of-domain.** On supplement rows
2.6.8 never trained on it wins clearly (disease macro-F1 0.895 vs 0.716, paired
CI excludes 0) — but 2.7.0 trained on ~48% of those rows, and on the recycled
rows *both* models trained on, the two are statistically tied (+0.000,
CI [-0.007, +0.005]). So most of the headline win is contamination.

Then a **never-trained ornamental benchmark** — `shuvokumarbasak4004/flower-leaf-
diseases-dataset-new-and-update`, MIT, deliberately in no training corpus, 1,200
healthy + 800 scorch across 6 garden-ornamental species — found a regression the
ship gate and all four held-out splits missed:

| model | healthy false-disease-fire | leaf_scorch recall |
|---|---|---|
| 2.6.0 | 0.093 | 0.638 |
| **2.6.8 (deployed)** | **0.019** | 0.576 |
| 2.7.0 | 0.084 | **0.071** |

2.7.0 predicts `leaf_scorch_underwatering` on **7%** of scorch-labelled
ornamental leaves against 2.6.8's 58%, sending 60% to `healthy` and 22% to
`leaf_spot` (McNemar 404–0 discordant, p≈5e-122). It also fires disease on
healthy ornamentals 4.4× more often than 2.6.8 (100–22 discordant, p≈5e-13),
undoing the false-fire improvement 2.6.8 delivered over 2.6.0. Label noise
cannot explain it: 2.6.0 and 2.6.8 score ~0.6 on the *same* labels, so the
collapse is specific to 2.7.0.

**Read:** 2.7.0 learned a scorch representation coupled to its training domain
(supplement + DoctorP houseplant scorch, where it beats 2.6.8 at 0.862 vs 0.710)
that does not transfer to ornamental species. The §3 probe already said scorch is
the class frozen features separate worst (0.39–0.52), so the head carries it and
can overfit. Suspects, in order: the 787 boundary hard-negatives (healthy
near-misses of scorch, 2.63% draw), `healthy` at 26.8% of train, and the
margin-cropping eval transform.

**Why nothing caught it:** every scorch row in `val_realworld` comes from the
same sources as train. The gate has zero out-of-domain scorch — the "broaden the
eval seed" item, and this is its first concrete cost.

- [ ] **Make the never-trained benchmark a standing gate.** It is public, MIT,
      in no training corpus, and it caught on day one what four held-out splits
      missed. Wire it as a release check, not a one-off.
- [ ] **Diagnose the scorch collapse before 2.7.1** — ablate the boundary weight
      (0.35 → 0.2) and the aspect-preserving preprocessing separately, and score
      each on the never-trained benchmark. One change per run.

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
