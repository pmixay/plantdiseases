# Roadmap to Google Play release

Compiled June 2026, grounded in two evidence sources: the **shipped model
bundle v2.5.0** (trained 2026-06-09; per-class real-world metrics below) and
a **competitor survey** (PictureThis, PlantIn, Planta, Greg, Plantix/Agrio;
sources at the bottom). Companion docs: `DATASET_LABELING_PLAN.md` (the data
loop this roadmap depends on), `PLAY_DATA_SAFETY.md` (store submission),
`ANDROID_RELEASE.md` (signing/build).

## 1. Where we are

**App today:** photo diagnosis (9 classes, conformal uncertainty, severity
scoring, on-device Aqua + server Gamma models), bilingual care plans (EN/RU),
AI chat doctor, no-photo symptom triage, community nearby-scans feed with
second opinions, plant garden tracker + care reminders, disease guide with
example photos, pet-toxicity warnings, progression tracking, species ID as
background enrichment (PlantNet), GDPR export/delete.

**Model v2.5.0, real-world eval (n=1938, 95% CIs in the model card):**

| class | precision | recall | support | verdict |
|---|---|---|---|---|
| leaf_scorch_underwatering | **0.52** | 0.76 | 45 | **#1 defect** — over-triggers; FPs split healthy (11) / not-a-plant (11) |
| nutrient_deficiency | 0.74 | 0.80 | **25** | support too thin to trust |
| gray_mold_botrytis | 0.75 | 0.81 | 53 | weak + supply-limited |
| viral_disease | 0.89 | 0.90 | 148 | leaf_spot↔viral bleed; watch |
| leaf_spot | 0.90 | 0.89 | 234 | fine |
| healthy | 0.94 | 0.92 | 535 | fine |
| pests | 0.94 | 0.96 | 265 | fine |
| powdery_mildew | 0.97 | 0.91 | 77 | **solved** |
| not_a_plant | 0.99 | 0.96 | 556 | fine |

Disease macro-F1 (int8, conservative floor) **0.835**; conformal RAPS α=0.2,
out-of-sample coverage 0.96, mean set size 1.87.

## 2. Competitive landscape (June 2026)

| app | price | diagnosis | known pain points |
|---|---|---|---|
| PictureThis | ~$30/yr | paywalled; strong species ID; accuracy drops on damaged/low-light leaves | credit-card-required "free" trial; aggressive onboarding paywall — the #1 review complaint |
| PlantIn | $7.99/wk / $19.99/mo / $49.99 lifetime | paywalled (incl. light meter, watering calc, 24/7 botanist) | weekly pricing perceived as a trap |
| Planta | $35.99/yr | paywalled (diagnosis, plant ID, light meter); free tier = reminders + logging | most utility behind premium |
| Greg | freemium | community-driven, light diagnosis | reminders paywalled ("Super Greg") |
| Plantix / Agrio | free/B2B | crop/field focus | different segment (agriculture) |

**Category 1-star themes to avoid:** trial/billing traps, paywall before any
value, overconfident wrong diagnoses on poor photos, no offline use.

**Table stakes** (everyone has them; we must match): species ID front and
center, care reminders/tracker ✅, care plans ✅, photo history ✅,
multi-language ✅.
**Our differentiators:** diagnosis is **free** (every competitor paywalls
it), calibrated honest uncertainty ("I'm not sure" instead of a confident
wrong answer — directly counters the category's top accuracy complaint),
community second opinions, pet-toxicity warnings, no-photo triage, offline
on-device model.

**Positioning:** *the free, honest plant doctor* — free diagnosis with
calibrated uncertainty, no account, no trial traps, privacy-respecting
(anonymized data, in-app delete).

## 3. Must-have before release

1. **Consent + privacy (DONE in this refactor):** blocking consent screen +
   upgrade-path dialog, `website/privacy.html`, GDPR cascade,
   `PLAY_DATA_SAFETY.md` answers. Required by Play's prominent-disclosure
   and 2025 health-content policies.
2. **Species ID as a first-class feature** (user-approved decision): a
   dedicated "Identify my plant" entry point reusing the existing PlantNet
   `/api/identify` integration (camera/gallery → candidates with confidence →
   add to garden with the species pre-filled). Every category leader leads
   with species ID; it's the top reason users open these apps. *Separate
   implementation task — next after this refactor.*
3. **leaf_scorch precision fix** (model gate): collect the ~300
   scorch-lookalike hard negatives (offline v2) + verified production scans
   via the new loop → retrain → ship gate: **leaf_scorch precision ≥ 0.75**
   and nutrient_deficiency eval support ≥ 100. A flagship "underwatering"
   misdiagnosis on a healthy plant is exactly the 1-star review generator
   the category suffers from.
4. **Release hygiene:** signed AAB + Play App Signing, crash/ANR triage on a
   small closed-track rollout, R8/proguard sanity pass, version gating
   against the oldest supported server API (all current changes are
   additive).
5. **Store listing:** screenshots EN/RU (scan → uncertainty-aware result →
   care plan → community), short/full descriptions **without accuracy
   guarantees** — "AI-assisted guidance, not a substitute for professional
   advice" disclaimer (Play health-claims policy + honest positioning).
6. **Data Safety form** filled from `PLAY_DATA_SAFETY.md` (photos collected
   not shared, optional coarse region, deletion path).

## 4. Nice-to-have / post-launch

- Light meter + watering calculator (the two most-paywalled competitor
  utilities — strong free differentiators later; need sensor calibration
  work).
- Edema + sunburn class activation (content cards already exist in
  `diseases_data.py`; deferred until the 9 current classes are stable).
- Overwatering/root-rot class (the #1 houseplant killer; visually
  ambiguous — needs the data loop's verified volume first). Spider-mite
  split from `pests` (pests precision is already 0.94 — low urgency).
- Fertilizer / repotting schedules in the garden tracker.
- Expert-human consultation (PlantIn monetizes this; could be community-
  powered for free in our model).
- More UI languages (the server content pipeline is already bilingual;
  adding UI locales is cheap).
- Offline mode polish (Aqua-only flow when the server is unreachable).

## 5. How the data/model loop gets us there

```
this refactor (recording + feedback + export loop)        [shipped]
        │
offline collection v2: houseplant domain gate,
  ~300 scorch hard negatives, gray_mold +160, NO powdery   [maintainer]
        │  publish Kaggle v2 → server auto-ingests (version gate)
        ▼
production beta (closed track): real scans flow in,
  suspicious + feedback cards verified by admins
  (priority: nutrient_deficiency, leaf_scorch, gray_mold)
        │  export bundle: train/calibration/eval splits
        ▼
retrain (notebook): auto-labeled→train only;
  human-verified production scans → calibration + eval
        │
ship gates: leaf_scorch precision ≥ 0.75 (from 0.52),
  nutrient eval support ≥ 100, disease macro-F1 ≥ 0.835 (no regression),
  conformal coverage within [0.88, 0.97]
        ▼
open release on Play
```

Per-class collection targets and the eval-integrity rule live in
`DATASET_LABELING_PLAN.md` (powdery_mildew → 0; leaf_scorch fixed via hard
negatives, not more positives; nutrient_deficiency fixed via verified eval
volume).

## 6. Decisions taken (and why)

- **No new disease classes for v1** — stabilize the 9; expansion candidates
  ranked for v2: overwatering/root-rot > sunburn activation > spider-mite
  split.
- **Species ID elevated to a headline feature** — competitor table stakes;
  we already pay for the PlantNet capability, it just lacks an entry point.
- **Collection retargeted to model defects** — powdery_mildew 150→0
  (solved), leaf_scorch gets hard negatives (precision problem), nutrient
  gets eval volume (support problem).
- **Stay free at launch** — monetization (if ever) must avoid the category's
  trial/paywall traps; differentiation IS the free honest diagnosis.

## Sources

- Model: `model_card.json` in the v2.5.0 bundle (per-class precision/recall
  bootstrap CIs, conformal audit, int8 paired bootstrap).
- [PictureThis complaints/pricing](https://www.complaintsboard.com/picturethis-plant-identifier-b149857),
  [PictureThis reviews](https://justuseapp.com/en/app/1252497129/picturethis-plant-identifier/reviews),
  [PlantIn pricing](https://myplantin.com/subscription),
  [Planta pricing/features](https://gardening.alibaba.com/plant-care/planta-app),
  [Greg pricing](https://greg.app/question/3p6rr4/how-much-does-this-app-cost),
  [Google Play Health Content & Services policy](https://support.google.com/googleplay/android-developer/answer/16679511?hl=en),
  [Play health-apps update Aug 2025](https://myappmonitor.com/blog/google-play-policy-update-for-health-apps).
