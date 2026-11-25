# PlantDiseases — roadmap

**Open work only, in priority order.** Finished work lives in git history; every model
run, with its numbers, lives in `MODEL_HISTORY.md`. Companions: `DATASET_LABELING_PLAN.md`
(data loop, v9 protocol), `../DEPLOYMENT.md` (deploy, Android signing/FCM),
`../server/models/MODEL_CARD.md` (version-agnostic pipeline overview).

★ = needs a human decision or a human-only task. Tiers are in priority order, and so
are the items inside each tier. Human (★) and code items run in parallel: a code item
in a lower tier can start while a ★ item above it waits on a person. Code-checkable
items were re-verified against the tree on 2026-11-24; data and human-task items are
as last recorded. Line counts are approximate; re-measure before acting on one.

---

## Status

| track | now |
|---|---|
| **App** | 1.9 / versionCode 19 on RuStore since 2026-11-22, and it stays the current version for now (no release planned). Later app work waits on the branch for the next release. PRO stays hidden (`ProUi.ENABLED = false`). |
| **Server** | `main`'s app API and account service have the second-opinion system retired and scan likes, author profiles and scan report/block removed, and following spaces added (74 API operations). For 1.9, which still calls those routes, the reads answer empty (`routes_legacy_app.py`, outside the schema) so its opinion poll stops retrying; the retired writes and profiles answer 404, which 1.9 shows as a failed action. |
| **Model** | 2.7.5 deployed (dataset v8). Weak spot: scorch read as healthy. A statistical tie with 2.7.2 on the shared holdout. |
| **Accounts backend** | Live on the production VDS under `/id/`; real Yandex flow and deletion verified; privacy policy served at `/privacy.html`; a Telegram alert watches for stuck deletions. No backup job (owner's decision). |
| **Data** | v8 is the corpus. v9 collection, locked test set T and OOD benchmark v3 are specified; none started. |
| **Code / CI** | All work since 2026-11-19 is on `ccr-333aa3d6-u20r4l`; `main` has not moved since. The server job gates on ruff, mypy (every server module) and pytest; every app response is checked against the app's own models (`tests/app_contract.py`). |

---

## P0 — Production

- [ ] ★ **Create the first forum admin** on production:
      `python manage_roles.py grant --nickname … --role admin` in the account-api
      container (migration 0011 must run first).

---

## P1 — Scorch, the model's critical path

Nothing done to the scorch class can be judged without T, and nothing fixes it
without data. Specified in `DATASET_LABELING_PLAN.md`.

- [ ] ★ **Carve T_v1.** Adjudicate the 80 export rows; carve T before any export row
      enters a supplement. It is the only slice fair to the deployed model and a
      candidate alike.
- [ ] ★ **Scorch data.** Real v9 collection: early-stage marginal necrosis, 400
      families with boxes (`brown_frac < 0.05`, edge-confined, blade mostly green),
      judged against `leaf_spot` exemplars; worth a run at ≥ 200 scorch and ≥ 100
      houseplant `leaf_spot` families. Boxes matter: Stage 1 returns no box on 21 % of
      scorch vs 19 % of healthy. It takes calendar time. A second synthetic batch is
      the alternative only if it meets the conditions under "Synthetic scorch" below.
      The production recipe needs the Kaggle houseplant supplement at v9; the private
      one was still at v8 at the last check (2026-08-23), which fails the strict v9
      manifest contract (a compliant v9 under the same handle is picked up
      automatically).
- [ ] ★ **OOD benchmark v3**: 8–12 species plus a non-plant arm (the non-plant arm
      needs no plants; do it first). The tool is ready: declare the sources in a
      `--config sources.json` for `tools/build_ood_benchmark.py` (layout, class map,
      arms, rights), and §6 scores the negative arms. Put the benchmark's slug in
      §2's `KAGGLE_BENCHMARK_SLUGS`, not `KAGGLE_DENY_SLUGS` (§6 downloads it).
      `REQUIRE_OOD_BENCHMARK` stays `False` until it exists.
- [ ] ★ **Augmentation ablation on Colab at `SEED=0`** (ready to run now). One `'off'`
      run (§4 `TRAIN_AUGMENTATION`; geometry only, profile
      `serve_parity_v2_geometry_only`) vs a default run on the same corpus; score with
      `tools/eval_bootstrap.py` on capture families.
- [ ] ★ **Ship Aqua's OOD floor** from the next notebook run: §5 writes it into the
      Aqua sidecar (`ood`); copy that `classes.json` with the model into
      `server/static/aqua/`. Until then Aqua serves without the gate, and only apps
      from the next release (after 1.9) read it.
- [ ] **Watch feedback correcting "healthy" to scorch** (ongoing,
      `server/tools/analyse_training_data.py`). 15 of 2.7.5's 25 wrong "healthy" calls
      on the holdout are scorch; production feedback is the only real-distribution
      signal until T exists.

---

## P2 — Code that unblocks the data loop (no data needed)

- [ ] **Check that capture-family detection catches rotated copies.** In a
      synthetic run of `build_ood_benchmark.py` a 90-degree rotated copy of an image
      stayed its own family under SIFT/RANSAC. The matcher is unchanged and the
      cause is not known; measure it on real photos before v3's families are
      trusted, since a missed duplicate leaks across the split.
- [ ] **Abstention reasons in `paired_eval`'s `preds.json`.** It writes `-1` for any
      abstention, so `build_ood_benchmark --report` cannot tell an OOD-gate
      rejection from a blur-gate one and counts both as a rejection.
- [ ] **OOD benchmark v3 follow-ups that block nothing yet** (details in
      `DATASET_LABELING_PLAN.md`): per-row rights for internet sources (a rights
      record is per source today; the non-plant arm doesn't need it), and per-arm
      floors, since `OOD_ARM_FLOORS` is keyed by species and scorch-only (decide when
      `REQUIRE_OOD_BENCHMARK` returns).

---

## P3 — Model experiments (Colab, alongside the collection round)

None is expected to fix scorch alone. `MODEL_VERSION = '2.7.5'` (notebook §1, matches
the deployed bundle); default mode `combined`: gamma ViT-S/14 plus the distilled Aqua
MobileNetV3-Small. Read "Settled — do not retry" before proposing a lever.

- [ ] **Crop-vs-whole gap.** `served_vote` 0.824 vs 0.865 whole-image; nutrient loses
      most (0.86 → 0.69). Diagnose before treating segmentation as the fix.
- [ ] **Capacity:** `frontier` (ViT-B/14) or DINOv3 (one run; **check DINOv3's
      licence**, not DINOv2's Apache-2.0).
- [ ] **`USE_REGISTERS`** (§4, default `False`): one flag, one run, one paired bootstrap.
- [ ] **Domain-robust training objective** stronger than a sampling nudge.

When 2.7.5's successor ships, refresh `site/index.html` (list in `site/README.md`), the
README status section and `docs/assets/per-class.svg` (its numbers are hand-copied into
`generate_assets.py`).

---

## P4 — Product

- [ ] ★ **Check `util/PlantHealth.kt` thresholds against real scan histories.**
      `PlantHealth` turns a plant's scans into the one status the plant header, the
      health card and the room list show. Each scan scores 0 to 100: a healthy
      verdict 50 + 50 × confidence, a disease verdict 50 − 50 × confidence. The
      tunables: only scans from the last 60 days count; no score before 2 of them;
      HEALTHY needs the latest score ≥ 75 (healthy at ≥ 0.5 confidence) and no
      decline; POOR needs the last 2 or more scores < 25 (disease at > 0.5 confidence
      each), the first of them at least a day before the latest, so retakes in one
      sitting confirm nothing;
      a change of 10 points or more against a scan at least 7 days older is a trend,
      less is noise; under a treatment plan, RECOVERING needs ≥ 70 % of due work done
      and a healthy reading or a rising trend. They were reasoned, not measured:
      compare them with a few real plants before trusting a red "Poor condition".
- [ ] **Desktop web admin panel** for moderation.

---

## P5 — Engineering debt

Each item is its own PR; don't fold one into another.

**Server**

- [ ] **Delete `routes_legacy_app.py`** (with `tests/test_legacy_app_routes.py` and
      `tests/fixtures/app_1_9_models.kt`) once few installs still run 1.9: it
      answers 1.9's retired-feature reads with empty shapes.

**Android**

- [ ] ★ **Check the XML-converted cards on a phone.** The plant-detail extra cards
      and the care-plan card were rebuilt from XML layouts without a device. Look
      for: 12sp captions now bold (they were regular), the "Not now" borderless
      button's padding, icon tints, the climate-chip gaps and the health-card heart.
      Also open a disease, a healthy, a not-a-plant and an offline result:
      `ResultActivity.displayResult` was split without a test that renders it.

---

## P6 — Documentation

- [ ] ★ **README app screenshots** (scan result, garden, care calendar, community map)
      → `docs/assets/screenshots/` + a row in "What it does". Needs a device.

---

## Later — not scheduled

- **2.8, from leaf classifier to plant doctor.** Waits for a stable scorch/nutrient
  boundary. Real-distribution data at scale (production scans; DoctorP already feeds
  starved classes); multi-label (sigmoid head, per-class thresholds and conformal,
  after the boundary is stable, never during); segmentation instead of a box, for
  measured lesion area and severity.
- ★ **PRO subscriptions (distant future).** The purchase validation, callback inbox
  and Android payment client stay in the code, switched off and invisible
  (`ProUi.ENABLED = false`, `RUSTORE_LIFECYCLE_ENABLED` / `RUSTORE_CHECKOUT_ENABLED`
  off). Reviving it needs RuStore keys and products, a sandbox purchase test and the
  app switch, plus a decision on the achievement discount: the account service
  already derives 2 % per achievement up to 40 % (`/v1/me/community-reward`), but
  RuStore has one price per product (21 discount products per period, or coarser
  steps; and whether a revoked discount changes an auto-renewing price).
- **VK ID sign-in.** Merged, switched off until VK registration.
- Spider-mite split from `pests` (precision already high).
- More UI languages (server content is already bilingual).
- Expert-human consultation (ties into 2.8 safety escalation; builds on the forum).

---

## Model track: reference

### Synthetic scorch

Owner's choice (2026-11-14): try generated images for the weak class. An independent
review of batch 1 (`scorch_synthetic_100`) found the stage right (all early,
edge-confined) but said do not train on it: only scorch is synthetic, so "generator
look ⇒ scorch" is easier to learn than the rim; the 100 images are about 10 templates
(lesion position tied to species, 10 fixed scenes); about 20 are ambiguous against
healthy / `leaf_spot`; 3 fail the server's blur statistic and 11 boxes need redrawing.
It also runs against plan rule P3 ("no … AI-looking images"): change that rule on
purpose or not at all. Before a batch 2 is worth a run:

1. carve T_v1 (with ~39 unseen scorch rows in the holdout no result could be told
   from noise);
2. a matched synthetic *healthy* image per scorch image, same generator and scene;
3. lesion position, extent and colour randomised independently of species, more
   genera, fewer flat-lays, no tan backgrounds, every visible leaf boxed;
4. human adjudication of each image;
5. a `supplement_synthetic` source that sits in no `allowed_sources`, at reduced
   weight, train-only, with template-level family ids.

Then one paired `SEED=0` run, real-only evaluation, and stop if healthy false alarms
rise or scorch recall's CI straddles 0.

### Settled — do not retry

Each was run as a single knob and measured; re-proposing costs a run. (Scorch numbers
come from the v2 hibiscus arm, a regression alarm rather than an independent
instrument.)

| lever | verdict | why |
|---|---|---|
| `CLASS_LOGIT_BIAS` | dead | best bias inside the 0.12 false-fire ceiling gives hibiscus 0.323; the floor needs 0.417 false-fire |
| regime-aware sampling | falsified | hibiscus 3/31 → 4/31, p=1.000 |
| regime-stratified conformal | falsified | dull stratum already better covered (0.972 vs 0.965) |
| margin-preserving preprocessing | not supported | probe +0.013 vs a +0.03 bar; letterboxing made 2.7.2 worse |
| sampler weights generally | falsified | `kaggle_ornamental` 1.0 → 0.35 left scorch flat, cost healthy recall (McNemar p=0.0074) |
| temperature refitting | impossible | `argmax(z/T) == argmax(z)`; moves confidence only |

Run-to-run noise is ~2 pp at `SEED=0`; read any single-knob delta inside it as noise.

---

## Decisions

- **Reliability and safety first.** Precision, honest uncertainty and OOD/blur
  refusal over headline recall/F1.
- **Data over architecture.** The ceiling is data and precision.
- **One species surface.** `SpeciesDetailActivity` only; everything else embeds
  `ui/common/SpeciesInfoCardView`.
- **Knowledge cards are best-effort**, never blocking (LLM + cache, graceful `available:false`).
- **No new disease classes** until the scorch/nutrient boundary is stable.
- **Target store: RuStore.** No Google Play.
- **The app stays at 1.9 for now** (owner's decision, 2026-11-24). No release is
  planned; later app work waits on the branch for the next one.
- **No backups of production data** (owner's decision, 2026-11-14). A lost VDS
  loses the accounts and the community store; there is nothing to restore from.
- **Cost cap is the OpenRouter key's own spend limit**; no in-server price
  ceiling and no dependency lockfile are planned.
- **Release signing runs in CI** (owner's decision, 2026-11-14): the android job
  signs with the permanent key from repository secrets.
- **VK ID stays switched off** until VK registration.
- **PRO is a distant-future idea, not a plan** (owner's decision, 2026-11-16).
  The app and server show no PRO wording and lock nothing behind it; only
  documents may mention it, and only as something far off. Signed-in accounts
  share one set of daily limits (2026-11-22: 15 standard and 5 larger-model
  analyses, 30 identifications, 15 Plant Doctor messages, 30 care plans; guests
  5, 1, 10, 5, 10). Only uncached work counts against them.
- **Account data stays on the German VDS** (owner's decision, 2026-11-13).
- **Scans by area are for community members** (owner's decision, 2026-11-22).
  Joining means location access plus sharing; there is no separate sharing switch.
  Worldwide popular counts everyone with consent, anonymously, location or not.
- **Shared scans carry no identity** (2026-11-23): a shared scan opens as a photo
  with its diagnosis, distance and time; no likes, author, profile, report or block.
  Forum blocks keep the blocked-authors list. New posts and answers with photos
  always send training consent; there is no switch.
- **Bilingual data stays in Kotlin where both languages are needed at once**
  (2026-11-24): offline scan cards, disease names and the species catalog are
  saved with each scan in EN and RU from plain-JVM code. `BilingualTablesTest`
  guards them; on-screen text goes to string resources (`StringParityTest`).
  The remaining `isRussian(` calls pick server or stored EN/RU fields.
- **SQLite stores share `server/sqlite_store.py`** (2026-11-24): connection
  setup and `add_column_if_missing`; schema, migrations and pruning stay per
  store. No `PRAGMA user_version`: each store's idempotent checks run once per
  process and are cheap. Telemetry keeps day granularity (it stores only the
  error's day, by design), so it has no rolling-24 h counter to align.
- **Responses are checked against the app, not described in OpenAPI**
  (2026-11-24): `server/tests/app_contract.py` reads the Android DTOs and the
  test client fails any 2xx JSON response from an app route that would not
  decode in the app (39 routes, every one exercised). No `response_model`:
  FastAPI would validate and filter live responses, so a wrong model becomes a
  production 500 or a silently dropped field.
- **One JSON library on Android: kotlinx.serialization** (2026-11-24). Gson is
  gone; `@SerialName` names are unchanged, so rows and cache files written by the
  Gson-era app keep decoding.
- **No Compose rewrite, single `:app` module.**
- **Stay free and honest.** Calibrated, OOD-aware, offline-capable, privacy-first.
  Diagnosis and Community reading need no account; Community writes need a verified
  Yandex sign-in (no email/password in production). Posts stay linked to the
  pseudonymous actor until the account is deleted.
- **Not planned:** a first-launch intro video; room-level care defaults
  (`RoomEntity.baselineLux` stays unused).
- **The data loop is local.** Collect and judge offline → Kaggle supplement → notebook pulls it.
  Production data enters it only through a server export bundle that the owner
  processes and labels offline (2026-11-24); no notebook cell reads a server export.

---

## Working agreements

- **Training runs on Colab, never locally** (A100, ≈20–35 min per run) via
  `server/train_notebook.ipynb`. Edit the notebook or pipeline code and hand the
  maintainer the cells.
- **Model choice for delegated work.** Pattern already in the repo → Sonnet 5 (spell
  out scope). New architecture across many files → Opus 5 (`effort: xhigh`; state the
  boundary, don't ask it to double-check). Undecided design → Fable 5, once. Omit
  `thinking`; give intent and constraints.
- **RU string review is a standing gate.** Review the `values-ru/strings.xml` diff on every merge.
- **CI:** see `CLAUDE.md` and the `.github/workflows/ci.yml` header. A branch matching
  no push glob gets no CI (that is how #158–#167 merged unbuilt); any new branch
  automation adds its prefix to the trigger list in the same commit.
