# Production-readiness TODO — gap vs PictureThis / Plantix

Open work to reach a polished commercial plant-doctor app, grouped by
area. Ordered roughly by impact within each section.

## ML (the core moat)

- [ ] **Expand the taxonomy** — 7 conditions today vs thousands of
      species + hundreds of disorders. Add species ID and more diseases.
- [ ] **Turn OOD rejection back on** — rebuild the `not_a_plant` set from
      *hard* negatives (leaf-like-but-not-leaf, partial plants, cluttered
      indoor frames) so the MaxLogit gate separates, then enable a floor.
      (Calibration split bug already fixed in the notebook.)
- [ ] **Retrain Stage 1 detector with hard negatives** — it fires at
      0.9+ confidence on pure noise, which is the main source of confident
      garbage verdicts.
- [ ] **Fix starved / noisy classes** — `gray_mold_botrytis` (~80 imgs,
      crude HSV gate) and the free-text-labelled physiological classes
      (`leaf_scorch_underwatering`, `nutrient_deficiency`) are unreliable.
- [ ] **Cross-class pHash leakage audit** before splitting — PlantDoc
      near-duplicates can straddle train / val_realworld (the ship gate).
- [ ] **Train/serve transform parity** — eval/calibration uses
      Resize(268)→CenterCrop(256); serving uses short-side-256→crop.
- [ ] **Curated held-out test set** that no automated source feeds, used
      only for the ship gate.
- [ ] **Per-class / Dirichlet calibration** — one global temperature is
      miscalibrated for `not_a_plant` vs plant heads.
- [ ] **Production drift monitoring** — track confidence/coverage and
      per-class accuracy from the feedback loop; alert on drift.
- [ ] **Active-learning loop** — mine `out_of_head` + corrected feedback
      rows to prioritise the next labelling round.

## Server

- [ ] **Drop torch/ultralytics** — rewrite the detector on raw
      onnxruntime. Biggest size + cold-start win (hundreds of MB).
- [ ] **DB scale** — connection pooling + an indexed `species_lower`
      column; `/community/nearby` opens up to 6 connections and uses a
      non-sargable `LOWER(species)`.
- [ ] **Multi-instance cache** — move the in-memory analyze dedup + LRUs
      to Redis so horizontal scaling doesn't lose them.
- [ ] **OpenRouter budget** — overall wall-clock cap across retries
      (worst case ~76 s pins a worker); keep deterministic care-plan
      dosages canonical (never overwritten by LLM polish).
- [ ] **Auth + per-key rate limits** — today it's IP token-bucket only;
      add API keys/quotas and abuse controls.
- [ ] **GDPR data deletion** — the delete-for-user path must also sweep
      stored feedback images; add export endpoint.
- [ ] **Secrets management** — move keys out of `config.json` to env /
      secret manager; rotate; never commit.
- [ ] **Observability** — structured logs + tracing + Sentry; Grafana
      dashboards & alerts on the existing Prometheus metrics.
- [ ] **CI hardening** — OpenAPI contract tests, Trivy image scan,
      non-root multi-stage Docker, pinned base image.
- [ ] **Load/soak test + autoscaling** with readiness-gated healthchecks.
- [ ] **API versioning** (`/v2/`) so client/model rollouts stay
      backward-compatible.

## Android

- [ ] **Persist server `severity`** — add a `ScanEntity` column (Room
      migration) and stop re-deriving it from a confidence heuristic.
- [ ] **Offline / on-device inference** — ship a TFLite export of the
      INT8 model as a fallback when the server is unreachable.
- [ ] **Accounts + cloud sync** of garden & scan history across devices.
- [ ] **Push notifications (FCM)** for care reminders (local WorkManager
      exists; add remote).
- [ ] **Certificate pinning** for the API now that transport is HTTPS-only.
- [ ] **Crash/ANR reporting** (Crashlytics / Sentry) + staged rollout.
- [ ] **Camera UX** — real-time framing guidance, auto-capture on focus
      lock, multi-shot best-frame pick.
- [ ] **Shareable result cards** (image export) for organic growth.
- [ ] **Accessibility & form factors** — TalkBack labels, dynamic font
      scaling, tablet/landscape layouts.
- [ ] **More locales** beyond EN/RU; pull strings audit.
- [ ] **Startup perf** — R8 full mode + Baseline Profiles; Coil image
      caching + paginated history list.
- [ ] **Onboarding, empty states, and a paywall** if monetised; consent-
      gated analytics.
