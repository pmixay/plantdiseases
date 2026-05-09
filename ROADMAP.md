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
- [ ] **Held-out houseplant smoke set (~500 photos, all 7 classes).**
      `python server/tools/collect_smoke_set.py --target 500` pulls
      from Openverse (Wikimedia + Flickr CC) and iNat into
      `data_v2/smoke/<class>/` with per-image licence sidecars (the
      directory is gitignored — it's evaluation-only). Initial run
      from the cloud sandbox landed **312/500** before Cloudflare WAF
      blocked further Openverse calls; `viral_disease` is the gap and
      needs the script run from a regular network. Manifest at
      `server/tools/smoke_set_manifest.json` records counts +
      licences. Run the INT8 ONNX session against this set after the
      v2.1 ship gate passes — the delta between `val_realworld`
      macro-F1 (≈0.75 expected, agricultural) and the smoke score
      (≈0.45 expected, real domain) is the honest accuracy. Refuse
      public release if smoke F1 < 0.45.
- [ ] **Houseplant data augment.** ≥ 200 labelled real indoor photos
      per class from iNat (filtered), r/houseplants, internal donations,
      Roboflow Universe houseplant projects. Reweight
      `(source="plantdoc")` rows ≤ 0.5 in the sampler so agricultural
      close-ups don't dominate.
- [ ] **Accuracy smoke test in CI.**
      `server/tests/test_accuracy_smoke.py` with a 30+ image golden set
      and a macro-F1 ≥ 0.65 floor.
- [ ] **Boot test against real artefacts.** `pytest server/tests/`
      green; `/api/version` returns `2.1`; `/api/classes` returns 7.

### Competitive parity

- [ ] **`POST /api/feedback`.** Body: `scan_id`, `correct: bool`,
      `corrected_class?`, free-text. Append-only SQLite at
      `server/feedback.db`. Rate-limited 1/s per IP.
- [ ] **Toxicity-to-pets.** Add `toxic_to_pets: bool` + `species_list`
      to `diseases_data.py` and the `/api/analyze` + `/api/care-plan`
      responses.
- [ ] **Historical `/api/progression`.** Accept `history: list[dict]`,
      return a 5-point severity sparkline; back-compatible with the
      pairwise `current+previous` shape.

### Operations

- [ ] **`make ship-bundle`.** ZIP `server/models/` plus `MANIFEST.json`
      of SHA-256s; deploy script verifies hashes.
- [ ] **Rollback path.** `models/current` ↔ `models/previous`
      symlink-style indirection; fall back when `current` is broken.
- [ ] **`not_a_plant` audit.** Collect low-MaxLogit true-positives from
      prod logs; label as additional NAP for v2.2.
- [ ] **MODEL_CARD post-train refresh.** Copy real `train_counts`,
      INT8 drift, best KD epoch, `T_value`, `q_hat` from
      `model_card.json` into `MODEL_CARD.md`.

### Notebook + data caveats

- [ ] **PlantDoc dilutes the houseplant domain.** ~80 % of train data
      is agricultural; ship-gate on `val_realworld` overestimates real
      accuracy by 25–40 pp (cross-domain literature: Frontiers /
      Springer 2025).
- [ ] **OE-pool is COCO-only.** README claims OpenImages V7 + iNat
      Animalia + recycled user uploads but only COCO val2017 is
      downloaded. Either drop the broader claim or wire OpenImages.
- [ ] **Phytophthora cinnamomi filter is name-only.** Hard-filter the
      iNat taxon_id, not just genus/species name (root pathogen, not a
      leaf disease).
- [ ] **SWIN-S teacher ablation.** SWIN transformers hit ~88 % on
      real-world disease vs ~53 % for CNNs (Springer 2025). Worth a
      one-off ablation against `tf_efficientnetv2_s` after v2.1 lands.

### Stage 1 (YOLO leaf detector)

- [ ] **Detector training data is external.** §3 trains YOLO only when
      `data_v2/**/data.yaml` exists. Document the prep step or wire
      it in.
- [ ] **`MIN_CONFIDENCE=0.15` sweep.** `detector.py:36` is recall-biased
      on purpose. After v2.1 ships, sweep against `val_realworld` to
      confirm the recall-vs-FP balance still holds.
- [ ] **No detector validation slice.** §3 never reports its own AP@0.5.
      Add a Stage-1 mAP report alongside the Stage-2 ship gate.

### v2.2 prep

- [ ] **Cold-start data for the 4 deferred classes.** `data_v2_pending/`
      with ≥ 100 indoor photos per class (`edema`,
      `leaf_scorch_underwatering`, `nutrient_deficiency`,
      `sunburn_or_scorch`). 400 net images with sidecar JSONs.

## Android

### Release blockers

- [ ] **HTTPS-only release variant.** Drop
      `cleartextTrafficPermitted="true"` from
      `network_security_config.xml` for release. Replace `API_BASE_URL`
      default in `app/build.gradle.kts:26` with the HTTPS prod URL;
      debug keeps HTTP for LAN. Wire via `manifestPlaceholders`.
- [ ] **Enable lint in CI.** `app/build.gradle.kts:70` —
      `abortOnError = true`.
- [ ] **Minimal unit tests.** `app/src/test/`. Cover: `ApiService`
      retry logic, `AnalyzeResponse` parsing, `ScanRepository`
      transactions. ≥ 10 tests via `./gradlew test`.
- [ ] **Rebuild after Models.kt + DoctorChatSheet rewrite.** Old
      call-sites referencing `ChatContext`, `suggestedQuestions` are
      broken — debug build must install on a device and AI Doctor
      sheet must POST to `/api/chat` and get a real reply.

### Competitive parity

- [ ] **Photo-coach onboarding.** First-time camera open: 3 slides on
      framing (distance, lighting, background). Track blur-rejected
      retry rate as the success metric.
- [ ] **Result-screen feedback prompt.** Thumbs chip on
      `low_confidence=true` or any non-empty warning. Persist locally;
      flush to `/api/feedback` once it lands.
- [ ] **AI Doctor session budget UI.** Progress chip "3 / 12 messages
      left" when remaining ≤ 20 % of cap.
      Source — `ChatResponse.session.turns_used / turns_remaining`.
- [ ] **`available=false` actionable copy.** EN/RU strings for every
      `reason` branch (`session_turn_cap`, `session_token_cap`,
      `no_api_key`, `message_too_long`, `empty_message`, `empty_reply`,
      `api_error`).
- [ ] **`/api/progression` in PlantDetailActivity.** ≥ 2 scans →
      `improving / stable / worsening / baseline` chip above the
      timeline. Helper `scanToProgressionPayload(scan)`. Tap → before /
      after sheet with `delta_severity`.
- [ ] **Toxicity warning UI.** "Toxic to pets" badge on species
      suggestions + Result screen once the server response carries
      the flag.
- [ ] **`IdentifyResponse.cached` UI.** "Served from cache" pill on
      species suggestions. Field is already on the wire; only the UI
      hook is missing.
- [ ] **On-device TFLite/ONNX fallback.** Server outage = product
      outage today. Export the INT8 ONNX as TFLite for offline
      inference; PictureThis / NatureID already ship offline ID.

### Architectural debt

- [ ] **Split `ResultActivity.kt` (1189 lines).** Extract
      `ResultHeaderView`, `ResultTreatmentView`, `ResultCarePlanView`,
      `ResultDoctorView`. Activity ≤ 350 lines.
- [ ] **`ResolutionSelector` instead of `setTargetResolution`.**
      `CameraFragment.kt:269`. Drop `@Suppress("DEPRECATION")`.
- [ ] **Cold-start UX toast.** On 503 + retry — single "Server is
      warming up..." toast per session, even after 5+ retries.

### Release

- [ ] **Signed release APK from CI on tag.** Workflow on `v*.*.*`:
      `./gradlew assembleRelease` + sign with keystore from secrets;
      upload to release assets.

## Competitor-parity backlog

Surveyed against PlantIn, PictureThis, Greg, PlantNet, NatureID.
Things we already ship: scanner with framing coach, gallery history,
"my garden", journal + watering log, light meter, PlantNet identify,
AI Doctor (OpenRouter), disease heat-map overlay, top-3 with
uncertainty, EN + RU. Net-new ideas:

- [ ] **Weather-aware reminders.** Skip a watering reminder when locale
      + IP suggest a humid day. New `weather_hint` field on the
      reminder worker; opt-in.
- [ ] **Growth journal sparkline.** CartesianChart of severity vs time
      on `PlantDetailActivity`.
- [ ] **Voice-driven care log.** STT "watered today" via
      `RecognizerIntent` from the Garden tab.
- [ ] **Premium / subscription tier.** Behind `BuildConfig.IS_PREMIUM`
      if monetisation lands.

## Eval contract

| Slice | Role | Pass criterion |
|---|---|---|
| `val_realworld` | **ship gate** | macro-F1 ≥ 0.70; recall > 0 every class; healthy-bias < 1.5; recall[healthy] ≥ 0.70 |
| `val_indomain` | early stopping | best EMA checkpoint |
| `val_studio` | transfer monitor | tracked, never blocking |
| `temperature_cal` | INT8 + temperature scaling | held out |
| `conformal_cal` | RAPS (50/50 fit/audit) | held out; out-of-sample coverage ≈ 1−α |
| **houseplant smoke** | honest field metric | run after ship gate; refuse release if macro-F1 < 0.45 |

## Predicted accuracy (web-validated, May 2026)

| Stage | Realistic range |
|---|---|
| `val_realworld` macro-F1 once trained | **0.70–0.78** (PlantDoc-anchored) |
| Real houseplant photos today | **0.35–0.55** (severe domain shift) |
| After houseplant data refresh + iNat reweighting | **0.55–0.70** projected (mid-tier territory) |
| Plantix / PictureThis bar | **>0.85** (years of feedback iteration) |

End-to-end latency on a 2-core CPU VPS: ~100–180 ms per request
(YOLOv8n 30–60 ms + MobileNetV3-L INT8 × ≤4 crops × 15–30 ms).
Throughput ~5–10 req/s server-wide at `INFER_WORKERS=2`.

## Out of scope

- Pruning, distillation on top of KD, Compose rewrite, Hilt.
- Pest taxonomy, outdoor crops.
- Marketplace, social feed, expert consultations.
- Per-session chat lock map (only when metrics show lock-wait > 5 ms
  under load).
- IoT soil-moisture pairing.
