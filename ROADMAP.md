# PlantDiseases — Roadmap

State: PlantVision 2.1, 7 classes, weights not trained yet.
Class taxonomy and architecture are frozen — see `MODEL_CARD.md`
and `server/train_notebook.ipynb`.

## Server / ML

### Release blockers

- [ ] **Train v2.1 weights.** Run `server/train_notebook.ipynb`
      end-to-end (A100-40 ~60 min, or T4 BATCH=64 ~3×).
      **Check:** ship gate passes — disease macro-F1 ≥ 0.70 on
      `val_realworld`, no class with recall=0, healthy-bias < 1.5;
      artefacts `classifier_int8.onnx`, `classifier.onnx`,
      `classifier.pth`, `classes.json`, `temperature.json`,
      `conformal.json`, `model_card.json` land in `server/models/`.

- [ ] **Houseplant data augment.** PlantVillage in train contains
      tomatoes / potatoes / apples — the actual target domain
      (Ficus, Monstera, Anthurium, Dracaena) is uncovered. Add
      ≥ 200 labelled real-world photos per class from iNaturalist
      (filtered), r/houseplants, Roboflow Universe, internal
      donations.
      **Check:** macro-F1 on val_realworld rises by at least +0.05
      vs. the no-augment baseline.

- [ ] **Accuracy smoke test in CI.** Add
      `server/tests/test_accuracy_smoke.py` with a 30+ image golden
      set and a macro-F1 ≥ 0.65 floor.
      **Check:** PRs that regress accuracy fail automatically.

- [ ] **Boot test against real artefacts.** After training,
      `pytest server/tests/`.
      **Check:** all tests green; `/api/version` returns `2.1`;
      `/api/classes` returns 7 classes.

### Competitive parity

- [ ] **`POST /api/feedback`.** Body: `scan_id`, `correct: bool`,
      `corrected_class?`, free-text. Append-only SQLite ledger at
      `server/feedback.db`. Rate-limited to 1/s per IP.
      **Check:** Android thumbs-press round-trips and persists.

- [ ] **Toxicity-to-pets.** Add `toxic_to_pets: bool` +
      `species_list` field to `server/diseases_data.py` and to the
      `/api/analyze` response.
      **Check:** the response carries the flag for all 7 classes.

- [ ] **Historical `/api/progression`.** Accept `history:
      list[dict]`, return a 5-point severity sparkline.
      Backwards-compatible with the pairwise current+previous
      shape.
      **Check:** 3 unit tests in `tests/test_endpoints.py`.

### Operations

- [ ] **`make ship-bundle` target.** ZIP the contents of
      `server/models/` plus a `MANIFEST.json` of SHA-256s.
      **Check:** the deploy script unpacks and verifies hashes.

- [ ] **Rollback path.** `models/current` ↔ `models/previous`
      symlink-style indirection; fall back to `previous` when
      `current` is broken.
      **Check:** removing one file from `current/` boots
      `previous/` cleanly with a warning log entry.

- [ ] **`not_a_plant` negative-sample audit.** From prod / staging
      logs, collect low-MaxLogit true-positives (dark leaves,
      glossy succulents) and label them as additional
      `not_a_plant` training images for v2.2.

- [ ] **MODEL_CARD post-train refresh.** Copy the real
      `train_counts`, `val_realworld_counts`, INT8 drift, best KD
      epoch, `T_value`, `q_hat` from `model_card.json` into
      `MODEL_CARD.md`.
      **Check:** per-class counts match byte-for-byte.

### v2.2 prep

- [ ] **Cold-start data for the 4 deferred classes.**
      `data_v2_pending/` with ≥ 100 indoor photos per class
      (`edema`, `leaf_scorch_underwatering`,
      `nutrient_deficiency`, `sunburn_or_scorch`).
      **Check:** 400 net images with sidecar JSONs; loader
      smoke test threads them through.

### Notebook caveats found during the v2.1 audit

- [ ] **PlantDoc dilutes the houseplant domain.**
      `train_notebook.ipynb` §2 pulls `agyaatcoder/PlantDoc`, which
      is dominated by *outdoor agricultural* crops (tomato, potato,
      apple). It still helps coverage on `leaf_spot_fungal` /
      `bacterial_leaf_spot` / `powdery_mildew`, but inflates val
      scores relative to true houseplant performance. Consider
      reweighting `(source="plantdoc")` rows to 0.5 in the sampler
      or building a `houseplant_only` ablation slice.
- [ ] **OE-pool is COCO-only today.** Documentation talks about a
      blend of OpenImages V7 indoor scenes + iNat Animalia + recycled
      user uploads, but only COCO val2017 is downloaded in the
      notebook. Either drop the broader claim from
      `train_notebook.ipynb` §2 / `MODEL_CARD.md`, or wire up the
      filtered OpenImages slice the docs promise.
- [ ] **Phytophthora cinnamomi filter is name-only.** The README
      calls out dropping *Phytophthora cinnamomi* from iNat (root
      pathogen, not a leaf disease). The notebook only rejects taxa
      by genus/species name and would still let stray observations
      through on tag-only matches. Add a hard `id ≠ Phytophthora
      cinnamomi taxon_id` filter when v2.2 retrains.
- [x] **PlantDoc image-level fallback.**
      `train_notebook.ipynb` §2 used to assume `agyaatcoder/PlantDoc`
      always ships object-level annotations under `row['objects']`.
      An HF revision flip (or every bbox failing the XYWH sanity
      check) silently dropped PlantDoc to zero rows, and the
      `THRESH_TRAIN=30` guard only fired three downloads later. Now
      falls back to `row['label']` and writes a whole-image crop with
      `label_confidence=0.75`.
- [x] **Bbox XYXY-vs-XYWH sanity check.** `_clip_bbox_xywh` now
      rejects bboxes whose w/h both exceed the image dimensions
      (the XYXY-as-XYWH telltale) instead of producing whole-image
      crops mislabelled as bbox crops.

### Stage 1 (YOLO leaf detector) caveats

- [ ] **Stage 1 training data is external.** `train_notebook.ipynb` §3
      only trains a YOLO if a `data.yaml` is already present under
      `data_v2/`. The README claim of "300–800 hand-labelled indoor
      leaf bboxes + Roboflow Universe houseplant slices" is met by a
      separate dataset preparation step, not by the notebook itself.
      Document that prep step (or wire it in) before the v2.1 train
      run, otherwise §3 silently skips and the deploy bundle ships
      with the previous detector weights.
- [ ] **Recall-bias vs hand-coverage vs OOD.** `MIN_CONFIDENCE=0.15`
      in `server/detector.py:36` is biased high-recall on purpose
      (so wide-frame indoor shots don't drop the only leaf bbox).
      The cost is more false-positive bboxes routed into Stage 2;
      the OOD gate in `pipeline.py` (`detector_conf < 0.40` AND
      `max_logit < 4.0` → re-route to NAP) cleans those up. Once
      v2.1 weights ship, sweep this floor against `val_realworld`
      to confirm the recall vs FP balance still holds.
- [ ] **No detector validation slice.** §3 trains YOLO end-to-end
      with the user's `data.yaml`, but the notebook never reports
      the detector's own AP@0.5 on a held-out indoor slice. After
      the first v2.1 train, add a Stage-1 mAP report alongside the
      Stage-2 ship gate.

## Android

### Release blockers

- [ ] **HTTPS-only release variant.** Drop
      `cleartextTrafficPermitted="true"` from
      `app/src/main/res/xml/network_security_config.xml` for the
      release variant. Replace the default `API_BASE_URL` in
      `app/build.gradle.kts:26` with the HTTPS prod URL. The
      debug variant keeps HTTP for LAN testing. Wire via
      `manifestPlaceholders`.
      **Check:** the release APK refuses cleartext to any
      non-debug host; debug LAN testing still works.

- [ ] **Enable lint in CI.** `app/build.gradle.kts:70` —
      `abortOnError = true`.
      **Check:** PRs that introduce lint errors fail.

- [ ] **Minimal unit tests.** Create `app/src/test/`. Cover:
      `ApiService` retry logic, `AnalyzeResponse` parsing,
      `ScanRepository` transactions.
      **Check:** `./gradlew test` runs in CI with ≥ 10 tests.

- [ ] **Rebuild after Models.kt + DoctorChatSheet rewrite.**
      Old call-sites referencing `ChatContext`,
      `suggestedQuestions` are broken.
      **Check:** debug build installs on a device; AI Doctor
      sheet POSTs to `/api/chat` and gets a real reply.

- [x] **POST_NOTIFICATIONS runtime prompt (Android 13+).** Wired
      into `MainActivity.maybeRequestNotificationPermission()`.
      Without it, `PlantReminderWorker` would silently skip
      `notify()` (line 31-36) and reminders would never appear on
      Tiramisu+ devices.

- [x] **Care plan no longer materialises without a scan.**
      `PlantDetailActivity.buildCarePlanSlot()` now shows an
      "awaiting scan" placeholder for plants with no scans, instead
      of asking the server for a generic `disease=unknown` plan that
      reads like personalised advice when it isn't.

### Competitive parity

- [ ] **Photo-coach onboarding.** First-time camera open shows 3
      slides explaining how to frame a leaf well (distance,
      lighting, background).
      **Check:** blur-rejected retry rate drops (log a metric).

- [ ] **Result-screen feedback prompt.** When `low_confidence=true`
      or any warning is non-empty, show a "Did this match?"
      thumbs-up / thumbs-down chip. Persist locally in a Room
      `feedback` table for now. Once `/api/feedback` lands,
      replace with a POST + offline retry queue.
      **Check:** thumbs press writes a row in Room; once the
      server endpoint is live it sends through.

- [ ] **AI Doctor session budget UI.** Progress chip "3 / 12
      messages left" when remaining ≤ 20 % of cap. Source —
      `ChatResponse.session.turns_used / turns_remaining`.
      **Check:** the chip appears before the cap fires.

- [ ] **`available=false` actionable copy.** After live training
      cross-check with `server/ai_services.py` and add EN/RU
      strings for every `reason` branch (`session_turn_cap`,
      `session_token_cap`, `no_api_key`, `message_too_long`,
      `empty_message`, `empty_reply`, `api_error`).

- [ ] **`/api/progression` in PlantDetailActivity.** For plants
      with ≥ 2 scans, show an `improving / stable / worsening /
      baseline` chip above the timeline. Helper —
      `scanToProgressionPayload(scan)`. Tapping the chip opens a
      half-sheet with before / after photos and `delta_severity`.
      **Check:** the chip flips when a fresh scan changes the
      trend.

- [ ] **Toxicity warning UI.** "Toxic to pets" badge next to
      species suggestions and on the Result screen once the
      server response carries the flag.

- [ ] **`IdentifyResponse.cached` UI.** Small "served from cache"
      pill next to species suggestions. The field already
      travels on the wire — only the UI hook is missing.

### Architectural debt

- [ ] **Split `ResultActivity.kt` (1189 lines).** Extract
      `ResultHeaderView`, `ResultTreatmentView`,
      `ResultCarePlanView`, `ResultDoctorView`. Keep the activity
      as a thin coordinator.
      **Check:** every new file is ≤ 350 lines.

- [ ] **`ResolutionSelector` instead of `setTargetResolution`.**
      `app/src/main/java/com/plantdiseases/app/ui/camera/CameraFragment.kt:269`.
      Drop `@Suppress("DEPRECATION")`.

- [ ] **Cold-start UX toast.** On 503 + retry — show a single
      "Server is warming up..." toast per session.
      **Check:** the toast shows exactly once even after 5+
      retries.

> Note on `Thread.sleep` in
> `ApiService.kt:110` — that lives inside an OkHttp `Interceptor`,
> which already runs on a dedicated dispatcher thread (not a
> coroutine). `Thread.sleep` is the documented OkHttp idiom there;
> `delay` would require a `runBlocking { … }` wrapper that doesn't
> buy us anything. Marking the previous TODO as resolved.

### Release

- [ ] **Signed release APK from CI on tag.** Workflow on `v*.*.*`
      push: `./gradlew assembleRelease` + sign with the keystore
      from secrets, upload to release assets.
      **Check:** tag → CI green → APK downloadable.

## Competitor-parity backlog

Surveyed against PlantIn, PictureThis, Greg, PlantNet and NatureID.
Things we **already** ship: scanner with framing coach, gallery
history, garden / "my plants", journal & watering log, light meter,
PlantNet identify, AI Doctor, disease heat-map overlay, top-3 with
uncertainty, EN+RU localisation. Net-new ideas worth scoring:

- [ ] **Toxicity-to-pets / kids badge.** Per-species `toxic_to_pets`
      flag served from `/api/care-plan` and rendered as a chip on
      the Result + Garden screens. (Already promised in §Server →
      Competitive parity.)
- [ ] **Photo-coach onboarding.** Already listed under §Android →
      Competitive parity. PictureThis's three-slide framing tutorial
      is the bar.
- [ ] **Weather-aware reminders.** Skip a watering reminder when the
      device locale + IP suggest a humid day. Add a `weather_hint`
      field to the reminder worker; opt-in.
- [ ] **Growth journal sparkline.** Already have per-plant scans, just
      need a CartesianChart of severity vs time on `PlantDetailActivity`.
- [ ] **Voice-driven care log.** Speech-to-text "watered today" log
      from the Garden tab; uses on-device `RecognizerIntent`.
- [ ] **Premium / subscription tier.** Out of MVP scope but worth
      planning behind a `BuildConfig.IS_PREMIUM` flag if monetisation
      ever lands.

## Out of scope

- Pruning, distillation on top of KD, Compose rewrite, Hilt.
- Pest taxonomy, outdoor crops.
- Marketplace, social feed, expert consultations.
- Per-session chat lock map (only when metrics show lock-wait
  latency > 5 ms under load).
- IoT soil-moisture pairing — interesting but a hardware partnership,
  not a code change.

## Eval contract

| Slice | Role | Pass criterion |
|---|---|---|
| `val_realworld` | **ship gate** | macro-F1 ≥ 0.70; recall > 0 for every class; healthy-bias < 1.5; recall[healthy] ≥ 0.70 |
| `val_indomain` | early stopping | best EMA checkpoint |
| `val_studio` | transfer monitor | tracked, never blocking |
| `temperature_cal` | INT8 calibration + temperature scaling | held out |
| `conformal_cal` | RAPS (fit-half + audit-half) | held out; out-of-sample coverage ≈ 1−α |
