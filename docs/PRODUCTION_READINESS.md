# Production-readiness checklist — competing with PictureThis / Plantix

This is the gap list between the current state and a polished commercial
plant-doctor app. Items the branch `claude/funny-cannon-G1dAj` already
addressed are checked. Everything unchecked is owed work, grouped by
how directly it affects users.

## Done in this pass

- [x] Model-metadata drift fixed — `models/classes.json` matched to the
      deployed v2.2 8-class head (was stale v2.1/7-class, which would
      mislabel every prediction by index on a clean-room deploy).
- [x] Classifier fails loudly on class-count/order mismatch instead of
      silently padding with `class_N`.
- [x] Server version sourced from metadata (stops advertising `2.1` for
      a `2.2` model — was poisoning feedback/community version tags).
- [x] EXIF orientation applied before inference.
- [x] Degenerate tiny uploads rejected (8×8 noise no longer becomes
      `viral_disease@0.72 / severity=high`).
- [x] Detector forces RGB (RGBA/grayscale no longer corrupt Stage 1).
- [x] Conformal `prediction_set` suppressed for `not_a_plant`.
- [x] PlantNet API key no longer in the URL query string.
- [x] `/api/feedback/image` size-capped; community quota enforced before
      reading the body; thumbnails streamed via `FileResponse`.
- [x] AI-doctor diagnosis context sanitized against prompt injection.
- [x] Android parses the authoritative `severity` and `prediction_set.advisory`
      (were silently dropped); safe DTO defaults; all pipeline warnings
      surfaced incl. dynamic `disease_suspected:<class>`.
- [x] Android cleartext traffic disabled (HTTPS-only) except local dev hosts.
- [x] Android POSTs no longer retried on transport errors (no duplicate
      feedback/community rows).
- [x] Training notebook: OOD MaxLogit gate calibrated on a single
      held-out split (root cause of the gate shipping disabled).

## Model & data (the core moat — biggest gap vs PictureThis)

- [ ] **Class coverage.** 7 disease/condition classes vs PictureThis's
      ~1000s of species + hundreds of disorders. The current head can
      only say one of {gray_mold, leaf_spot, powdery_mildew, viral,
      leaf_scorch, nutrient_deficiency, healthy, not_a_plant}.
- [ ] **OOD rejection actually on.** The MaxLogit gate ships disabled
      because NAP negatives (COCO/OpenImages/iNat-Animalia) are *easier*
      than the real disease classes. Rebuild NAP from hard negatives
      (leaf-like-but-not-leaf, partial plants, cluttered indoor frames),
      re-run the (now split-correct) calibration, and only then enable a
      floor. Today, noise/grayscale images get detector_confidence
      0.9+ and a confident disease verdict.
- [ ] **Detector quality.** YOLOv8n fires at 0.9+ confidence on pure
      noise — retrain Stage 1 with hard negatives so non-leaf textures
      don't pass.
- [ ] **Disease-class data quality.** `gray_mold_botrytis` is starved
      (~80 train images, crude HSV green-gate); the physiological
      classes (`leaf_scorch_underwatering`, `nutrient_deficiency`) are
      free-text-searched iNat with intrinsically ambiguous labels.
      Curate/expand these before trusting their verdicts.
- [ ] **Leakage audit.** PlantDoc near-duplicates can straddle the
      train / val_realworld boundary (the ship-gate metric). Run a
      global cross-class pHash index before splitting.
- [ ] **Train/serve transform parity.** Eval/calibration uses
      Resize(268)→CenterCrop(256); serving uses short-side-256→crop.
      Align them so temperature/conformal/INT8 scales match the served
      framing.
- [ ] **Commit the v2.2 artifacts.** `model_card.json`,
      `training_metrics.json`, `conformal.json`, `temperature.json`,
      `run_log.jsonl`, `dinov2_probe.json` in the repo are the v2.1 run.
      Regenerate from the v2.2 run.
- [ ] **Held-out, human-curated test set** that no automated source
      feeds, used only for the ship gate.

## Reliability & scale

- [ ] **Replace ultralytics/torch with a raw onnxruntime detector** —
      currently ~hundreds of MB of deps + slow cold start even though
      ONNX is the production path. Biggest single lightweight win.
- [ ] Community/feedback DB: connection pooling + a normalized indexed
      `species_lower` column (the nearby query opens up to 6 connections
      and uses a non-sargable `LOWER(species)`).
- [ ] OpenRouter calls need an overall wall-clock budget across retries
      (worst case ~76 s pins a threadpool worker).
- [ ] Care-plan LLM polish must not silently overwrite deterministic
      dosages/intervals — keep the rule-engine summary canonical.
- [ ] Persist server `severity` on `ScanEntity` (Room migration) so
      chat/progression stop re-deriving it from a confidence heuristic.
- [ ] Load/soak test for concurrency; autoscaling; structured request
      logging + tracing; Sentry-style error reporting.

## Product polish (table-stakes for a commercial app)

- [ ] Accounts + cloud sync of garden/scan history across devices.
- [ ] Offline / on-device inference fallback.
- [ ] Subscription/paywall + billing if monetized.
- [ ] Richer species DB with care guides per species (not per disease).
- [ ] Push notifications for care reminders (have WorkManager; add FCM).
- [ ] Onboarding, empty states, accessibility (TalkBack), tablet layouts.
- [ ] More languages beyond EN/RU.
- [ ] In-app feedback → visible model improvements loop.

## Compliance, privacy, store readiness

- [ ] Privacy policy + data-deletion that also sweeps stored feedback
      images (currently the delete-for-user path skips that directory).
- [ ] GDPR/CCPA data-export + deletion endpoints.
- [ ] "Not medical/agronomic advice" disclaimers on diagnoses.
- [ ] Play Store listing assets, content rating, data-safety form.
- [ ] Secrets management (no keys in config files committed); rotate
      keys; per-key rate limits.
- [ ] Crash-free-rate / ANR monitoring; staged rollout.
