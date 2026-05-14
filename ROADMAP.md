# PlantDiseases — Roadmap

State: PlantVision 2.1, 7 classes, weights not trained yet.
Class taxonomy and architecture are frozen — see `MODEL_CARD.md` and
`server/train_notebook.ipynb`.

## Server / ML

### Release blockers

- [ ] **Train v2.1 weights.** Run `server/train_notebook.ipynb`
      end-to-end (A100-40 ~60 min, T4 BATCH=64 ~3×). Ship gate: disease
      macro-F1 ≥ 0.70 on `val_realworld`, no class with recall=0,
      healthy-bias < 1.5; artefacts land in `server/models/`.
- [ ] **Held-out houseplant smoke set (~500 photos, 7 classes).**
      `python server/tools/collect_smoke_set.py --target 500` writes
      to `data_v2/smoke/<class>/` (gitignored). Initial run landed
      312/500 before Cloudflare WAF blocked Openverse; `viral_disease`
      is the gap. Run the deployed ONNX session against this set
      after the ship gate passes; refuse public release if smoke
      macro-F1 < 0.45.
- [ ] **Houseplant data augment.** ≥ 200 labelled real indoor photos
      per class from iNat (filtered), r/houseplants, internal donations,
      Roboflow Universe houseplant projects. This is the dominant lever
      for real-domain accuracy.
- [ ] **Boot test against real artefacts.** `pytest server/tests/`
      green; `/api/version` returns `2.1`; `/api/classes` returns 7.

### Competitive parity

- [x] **`POST /api/feedback`.** Body: `scan_id`, `correct: bool`,
      `corrected_class?`, free-text. Append-only SQLite at
      `server/feedback.db`. Rate-limited 1/s per IP (inherits the
      global limiter). Counts surface on `/api/metrics`. Android
      surfaces a thumbs / correction sheet on the result screen.
- [x] **Toxicity-to-pets.** `toxic_to_pets: bool` + `species_list`
      on `diseases_data.py`, `/api/analyze`, `/api/care-plan`. The
      server resolves against an ASPCA-derived table when the caller
      passes a `species` hint; ``source="unknown"`` otherwise so the
      Android UI never falsely reassures.
- [x] **Historical `/api/progression`.** Accepts `history: list[dict]`
      and returns a 5-point severity sparkline plus a slope-based
      trend; back-compatible with the pairwise `current+previous`
      shape. Android `PlantDetailActivity` renders the sparkline +
      trend chip above the timeline.

### Operations

- [ ] **`make ship-bundle`.** ZIP `server/models/` plus `MANIFEST.json`
      of SHA-256s; deploy script verifies hashes.
- [ ] **Rollback path.** `models/current` ↔ `models/previous`
      symlink indirection; fall back when `current` is broken.
- [ ] **`not_a_plant` audit.** Collect low-MaxLogit true-positives
      from prod logs; label as additional NAP for v2.2.
- [ ] **MODEL_CARD post-train refresh.** Copy real `train_counts`,
      INT8 drift, best KD epoch, `T_value`, `q_hat` from
      `model_card.json` into `MODEL_CARD.md`.

### Notebook + data caveats

- [ ] **PlantDoc dilutes the houseplant domain.** Source-weighting
      down-ranks PlantDoc rows in the sampler but does not remove
      them; ship-gate on `val_realworld` still overestimates real
      houseplant accuracy until the data augment lands.
- [ ] **OpenImages V7 indoor scenes (NAP).** iNat Animalia is wired;
      OpenImages (~2 GB CSV index) remains a v2.2 task.
- [ ] **SWIN-S teacher ablation.** SWIN transformers report ~88 %
      vs ~53 % for CNNs on real-world disease (Springer 2025). One-off
      ablation against `tf_efficientnetv2_s` after v2.1 ships.

### Stage 1 (YOLO leaf detector)

- [ ] **Detector training data is external.** §3 trains YOLO only
      when `data_v2/**/data.yaml` exists. Document the prep step or
      wire it in.
- [ ] **`MIN_CONFIDENCE=0.15` sweep.** Recall-biased on purpose.
      Sweep against `val_realworld` once weights ship.
- [ ] **Detector validation slice.** §3 never reports its own
      AP@0.5. Add a Stage-1 mAP report alongside the Stage-2 gate.

### v2.2 prep

- [ ] **Cold-start data for the 4 deferred classes.** `data_v2_pending/`
      with ≥ 100 indoor photos per class (`edema`,
      `leaf_scorch_underwatering`, `nutrient_deficiency`,
      `sunburn_or_scorch`).

## Android

### Release blockers

- [ ] **HTTPS-only release variant.** Drop
      `cleartextTrafficPermitted="true"` from
      `network_security_config.xml` for release. Replace
      `API_BASE_URL` default in `app/build.gradle.kts` with the
      HTTPS prod URL; debug keeps HTTP for LAN. Wire via
      `manifestPlaceholders`.
- [ ] **Minimal unit tests.** `app/src/test/`. Cover `ApiService`
      retry logic, `AnalyzeResponse` parsing, `ScanRepository`
      transactions. ≥ 10 tests via `./gradlew test`.
- [ ] **End-to-end rebuild verification.** Install debug APK and
      confirm AI Doctor sheet POSTs to `/api/chat` and receives a
      real reply after the recent `Models.kt` + `DoctorChatSheet`
      rewrite.

### Competitive parity

- [ ] **Photo-coach onboarding.** First-time camera open: 3 slides
      on framing (distance, lighting, background). Track blur-rejected
      retry rate.
- [x] **Result-screen feedback prompt.** Thumbs / correction sheet
      on every diagnosed plant verdict (NAP excluded). POSTs to
      `/api/feedback`; surfaces 429 / network failures distinctly.
- [ ] **AI Doctor session budget UI.** Progress chip
      "3 / 12 messages left" when remaining ≤ 20 % of cap. Source —
      `ChatResponse.session.turns_used / turns_remaining`.
- [ ] **`available=false` actionable copy.** EN/RU strings for every
      `reason` branch (`session_turn_cap`, `session_token_cap`,
      `no_api_key`, `message_too_long`, `empty_message`,
      `empty_reply`, `api_error`).
- [x] **`/api/progression` in PlantDetailActivity.** ≥ 2 scans →
      sparkline + `improving / stable / worsening / baseline` chip
      above the timeline. POSTs the full history (capped to last 12
      scans).
- [x] **Toxicity warning UI.** Tri-state badge on the Result screen:
      red "Toxic to pets" with species list, green "Pet-safe", or
      amber "Pet safety unconfirmed" (when server returns
      ``source="unknown"``).
- [ ] **`IdentifyResponse.cached` UI.** "Served from cache" pill on
      species suggestions. Field is already on the wire; only the UI
      hook is missing.
- [ ] **On-device TFLite/ONNX fallback.** Server outage = product
      outage today. Export the INT8 ONNX as TFLite for offline
      inference.

### Architectural debt

- [ ] **Split `ResultActivity.kt` (≈1.2 k lines).** Extract
      `ResultHeaderView`, `ResultTreatmentView`, `ResultCarePlanView`,
      `ResultDoctorView`. Activity ≤ 350 lines.
- [ ] **Cold-start UX toast.** On 503 + retry — single
      "Server is warming up…" toast per session, even after 5+
      retries.

### Release

- [ ] **Signed release APK from CI on tag.** Workflow on `v*.*.*`:
      `./gradlew assembleRelease` + sign with keystore from secrets;
      upload to release assets.

## Eval contract

| Slice | Role | Pass criterion |
|---|---|---|
| `val_realworld` | **ship gate** | macro-F1 ≥ 0.70; recall > 0 every class; healthy-bias < 1.5; recall[healthy] ≥ 0.70 |
| `val_indomain` | early stopping | best EMA checkpoint |
| `val_studio` | transfer monitor | tracked, never blocking |
| `temperature_cal` | INT8 + temperature scaling | held out |
| `conformal_cal` | RAPS (50/50 fit/audit) | held out; out-of-sample coverage ≈ 1−α |
| **houseplant smoke** | honest field metric | run after ship gate; refuse release if macro-F1 < 0.45 |
