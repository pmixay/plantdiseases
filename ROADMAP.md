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

- [ ] **Coroutine `delay` instead of `Thread.sleep`.**
      `app/src/main/java/com/plantdiseases/app/data/remote/ApiService.kt:110`.
      **Check:** retries don't block the network thread.

- [ ] **`ResolutionSelector` instead of `setTargetResolution`.**
      `app/src/main/java/com/plantdiseases/app/ui/camera/CameraFragment.kt:269`.
      Drop `@Suppress("DEPRECATION")`.

- [ ] **Cold-start UX toast.** On 503 + retry — show a single
      "Server is warming up..." toast per session.
      **Check:** the toast shows exactly once even after 5+
      retries.

### Release

- [ ] **Signed release APK from CI on tag.** Workflow on `v*.*.*`
      push: `./gradlew assembleRelease` + sign with the keystore
      from secrets, upload to release assets.
      **Check:** tag → CI green → APK downloadable.

## Out of scope

- Pruning, distillation on top of KD, Compose rewrite, Hilt.
- Pest taxonomy, outdoor crops.
- Marketplace, social feed, expert consultations.
- Per-session chat lock map (only when metrics show lock-wait
  latency > 5 ms under load).

## Eval contract

| Slice | Role | Pass criterion |
|---|---|---|
| `val_realworld` | **ship gate** | macro-F1 ≥ 0.70; recall > 0 for every class; healthy-bias < 1.5; recall[healthy] ≥ 0.70 |
| `val_indomain` | early stopping | best EMA checkpoint |
| `val_studio` | transfer monitor | tracked, never blocking |
| `temperature_cal` | INT8 calibration + temperature scaling | held out |
| `conformal_cal` | RAPS (fit-half + audit-half) | held out; out-of-sample coverage ≈ 1−α |
