# PlantVision — roadmap

Open work only; the code is the record. Companions: `DATASET_LABELING_PLAN.md`
(data loop), `../DEPLOYMENT.md` (deploy + Android signing/FCM),
`../server/models/MODEL_CARD.md` (**stale — see the model track**).

Every status here was verified against the tree. Two independently releasable
tracks: the **app** (`versionName` 1.9 / `versionCode` 19 on the account-system
staging branch; permanent 1.8 signing identity still needs owner confirmation)
and the **model** (bundle version, moves per training run).

---

## App 1.9

Everything else has landed: AI-doctor chat subtitle via `localizedSpeciesName`,
shared `ConnectivityObserver`, `GET /api/species-profile` returning all three
fields, flowering chart, native-range map, coach-mark onboarding, and the
species-info consolidation onto one screen behind one reusable card.

- [ ] **Cut the 1.9 tag and the RuStore build** — human-only, after the
      permanent signing/provider and real account-system gates pass.
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

Items checked off below were fixed in the dependency/CI pass; where a finding
was only partly addressed the remaining half is called out inline rather than
closed.

- [x] **`/api/feedback/image` unbounded** — fixed. The magic-byte allowlist
      (`_validate_image_bytes`) now gates the endpoint, and the stored
      extension is sniffed from the bytes rather than taken from the client's
      `Content-Type` (whose `.bin` fallback is what let arbitrary content in).
      The quota gap is closed by binding one image per feedback row: image
      count can no longer exceed row count, which *is* quota-limited, while an
      identical re-upload stays idempotent for retrying clients.
      **Still open:** files are never deleted — retention prune covers rows,
      not the image directory.
- [ ] **No inference admission control** (`main.py:2004`) — at `INFER_WORKERS=1`
      a client within the rate limit pins RAM and starves everyone. Wants a
      semaphore before decode → 503.
- [ ] **Cost caps** (`ai_services.py:1257`, `main.py:3865`) — caches key on
      client-chosen strings, so every distinct `name` is a guaranteed miss and a
      billable call. *OpenRouter has a monthly dollar cap, so bounded; stricter
      per-endpoint limits planned.* PlantNet has no server-side budget.
- [x] **`/api/metrics`** — the rate-limit exemption is removed, so the most
      expensive unauthenticated request is no longer the only unmetered one.
      **Still open:** the aggregates themselves still run full-table SQLite on
      the event loop; they want a short TTL cache or a threadpool hop.
- [x] **`/api/plant-photo` amplifier** (partly) — the LRU now enforces a byte
      budget (`_CACHE_MAX_BYTES`, 2 GB) alongside the file cap, so the cache
      can no longer reach ~24 GB or be flushed by a handful of large files.
      **Still open:** the WebP variants on the serving side are still
      unbounded.
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
- [x] **EXIF/GPS stripping at every trust boundary** — Android now routes all
      upload paths through `UploadImageSanitizer`; the server independently
      decodes, orients, bounds, and re-encodes a fresh RGB JPEG before PlantNet,
      community storage, or consented training storage. Direct API clients can
      no longer bypass the mobile sanitizer. Regression tests cover camera EXIF.
- [ ] **Pseudo-id in GET paths** (`ScanRepository.kt:775`) — a bearer capability
      landing in access logs.
- [ ] **Dependency constraints** — `starlette` unconstrained above 0.46.0
      (resolves clean today, range does not); `firebase-admin` pins
      `httpx==0.28.1`, colliding with `httpx>=0.28` once 0.29 ships; no lockfile.
      CI still catches a resolution break as a red job rather than at deploy
      time — but a lockfile is still the actual fix.
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

`MODEL_VERSION` is **2.6.13** (notebook §1). `server/models/` holds only
`.gitkeep` + `MODEL_CARD.md` — **nothing past 2.6.8 is deployed.**

Closed history: **2.6.9** gate-passed but lost a paired A/B vs 2.6.8 (kept
2.6.8). **2.6.10** recovered leaf_scorch but silently lost a 2.26k-image source
mid-run, so it cannot anchor an A/B. **2.6.11/v8** superseded by the 2.6.12
general-plant pivot, whose data-cleaning it folded in. Details in
`DATASET_LABELING_PLAN.md`.

### 2.6.13 — makes the pivot real

Four of five fixes have landed in the notebook: fail-fast on a truncated corpus
(`REQUIRED_SOURCE_FLOORS` + persisted `source_inventory.json`), re-sourced
ornamental data, a revived MaxLogit OOD floor over plant logits only, and an
unpinned conformal grid.

- [ ] **Train 2.6.13 and paired-bootstrap vs 2.6.8** — diff `source_inventory`
      first. Expect `val_indomain` to recover toward 0.86. `viral_disease` is
      known-starved (its 600 ornamental train images are gone and unreplaced) —
      do **not** read a viral regression as a modelling failure.

Run-log checks, each proving one fix landed:

| Look for | Expect | Means |
|---|---|---|
| `source floors OK: …` | present | §2 gate ran. If it *raised*, a source is down — read it, don't bypass. |
| `Kaggle ornamental per-class:` | `healthy 600, leaf_spot 600, powdery_mildew 133` | Replacement sources loaded; a missing class is the known gap. |
| `MaxLogit OOD floor calibrated [plant_only]` | `sep=` **positive** | Fallback alive for the first time since v2.3. Still negative ⇒ flag it. |
| `conformal: selected alpha=… lambda_reg=…` | **not** the last grid value | Search converged inside the grid. |
| `head early stop @ epN (best ep=M)` | `M` well above 3 | Best-epoch 3 was the corpus-loss symptom. |
| `[final val_indomain] disease_macro_f1` | toward **0.86** | Headline regression undone. |

### Still open from the 2.6.12 pivot

- [ ] **Loosen the supplement's upstream `field_crop`/`orchard_fruit` gate** to
      admit ornamentals; collect garden-ornamental scans, weak classes first.
- [ ] **Broaden the eval seed** — add an ornamental source to `val_realworld`.
      **Load-bearing:** the ornamental leg is train-only today, which is exactly
      why its disappearance was invisible.
- [ ] **Refresh `MODEL_CARD.md`** — still says "v2.6.9 (in training — NOT YET
      TRAINED)" and contradicts this file on aqua export precision. Do not
      import its numbers until rewritten.

### 2.7 — the leap

> From "a good leaf classifier" to a **holistic, trustworthy plant doctor**.
> Scope + real-world reliability + data scale, not a bigger backbone — the probe
> says features aren't the ceiling.

1. **Data flywheel** — fold thousands of offline-judged production scans into
   the next dataset. Bigger lever than any backbone.
2. **Scope expansion** — activate `edema` + `sunburn` (cards and deferred
   feedback labels exist); add root rot via image + symptom fusion. Diagnose
   *conditions*, not leaf classes.
3. **Multimodal diagnosis** — fuse vision verdict with species ID and symptom
   Q&A so species + context condition the disease priors.
4. **`frontier` (DINOv2 ViT-B/14, GPU)** — A/B vs gamma **only if** the probe
   shows headroom, then distill back to ship cheaply.
5. **Segmentation** — Stage 1 toward lesion area for severity, not one box.
6. **Safety at scale** — per-class conformal, explicit abstain →
   expert/community escalation, calibrated severity that never over-treats.

Prep now, in parallel:

- [ ] Wire `frontier` end-to-end in the notebook + a probe-gate (cheap go/no-go).
- [ ] Stand up the production-scan export→judge loop at volume.
- [ ] Spike multimodal fusion behind a flag.
- [ ] Design the segmentation-detector path — research spike.

---

## Documentation debt

None of it blocks a release.

- [ ] **Privacy policy — third-party processors undisclosed. Highest-priority
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
- **Stay free + honest** — calibrated, OOD-aware, on-device offline,
  privacy-first. "No account" is superseded as of 17 Aug 2026: the forum
  direction (COMMUNITY_FORUM_PLAN.docx §3.1) introduces a durable per-device
  identity, one `community_authors` row per install, claimable by a real
  account system later. This is not a login — no email, no password, nothing
  to sign into — so the app itself is still free and anonymous by default.
  As of the identity binding fix (COMMUNITY_FORUM_CORRECTION_PLAN.docx C1)
  it is attached to community opinions when the app sends it; there is no
  forum to post to yet (F1-F3 unbuilt), so nothing beyond that carries it.
  Disclosed in privacy_policy.html.
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
**server** jobs only when their trees change. Android: `ktlintCheck` +
`detekt` + `testDebugUnitTest` + R8 `assembleRelease`. Server: `ruff` +
`pytest`, on Python 3.14. Dependency updates are made by hand.

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
