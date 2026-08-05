# Execution plan — App 1.8 and the Garden refactor

Companion to `ROADMAP.md`. The roadmap says *what* is left; this file says **who
runs it, what can run at the same time, and where a human has to look before the
next thing starts.**

Every task below is a ready-to-paste prompt with a model assignment. Status of
each item was verified against the tree, not taken from the roadmap.

---

## Model assignment rule

| Model | ID | Price /MTok | Used here for |
|---|---|---|---|
| **Fable 5** | `claude-fable-5` | $10 / $50 | Problems where nothing has been decided yet. Long autonomous turns (a single request can run 15+ min). Thinking always on. |
| **Opus 5** | `claude-opus-5` | $5 / $25 | The agentic-coding workhorse — difficult multi-file work, larger refactors, end-to-end features. Its edge shrinks on easy single-file edits. |
| **Sonnet 5** | `claude-sonnet-5` | $3 / $15 (intro $2 / $10 through 2026-08-31) | Well-scoped work where the repo already contains the pattern to copy. |

**The rule applied throughout:** *is there already a pattern in this repo to
follow?* Yes → Sonnet 5. New architecture across many files → Opus 5. Nobody has
decided what it should be → Fable 5, once, to decide it.

### Run settings

- Opus 5 coding tasks: `effort: "xhigh"`, generous `max_tokens` (long turns truncate otherwise).
- Sonnet 5: `high` (the default) is right.
- Fable 5: omit the `thinking` parameter entirely — it is always on and an explicit config is rejected.

### Prompt style differs per model — this is load-bearing

- **Opus 5 verifies its own work unprompted.** Adding "double-check your answer"
  makes output *worse*. None of the prompts below say it. It also expands scope
  and delegates to subagents readily, so each prompt states its boundary.
- **Fable 5 degrades when you enumerate steps.** The design prompt below gives
  intent and constraints, then gets out of the way.
- **Sonnet 5 follows instructions literally** and won't generalize an
  instruction from one case to another — so the Sonnet prompts spell out scope.

---

## Wave map

```
WAVE 1  ─┬─ 1. RU chat subtitle      (sonnet-5)   ─┐
         ├─ 2. Connectivity signal   (opus-5)     ─┤
         └─ 3. Species endpoint      (sonnet-5)   ─┘
                                                   │
                                          ▼ HUMAN GATE A — endpoint content
                                                   │
WAVE 2  ─┬─ 4. Flowering chart       (sonnet-5)   ─┐
         ├─ 5. World map             (opus-5)     ─┤
         └─ 6. Onboarding rework     (opus-5)     ─┘
                                                   │
                                    ▼ HUMAN GATE B — visual + first-run review
                                                   │
WAVE 3  ─── 7. Tag 1.8 + RuStore build  (human only)
                                                   │
                          ═══════ 1.9 starts ══════
                                                   │
WAVE 4  ─── 8a. Garden design pass    (fable-5, alone)
                                                   │
                                       ▼ HUMAN GATE C — schema approval ★
                                                   │
WAVE 5  ─── 8b. Today queue + checklists (opus-5, alone — owns the migration)
                                                   │
WAVE 6  ─┬─ 9.  Health header        (opus-5)     ─┐
         └─ 10. Rooms                (sonnet-5)   ─┘
                                                   │
                                       ▼ HUMAN GATE D — heuristic review
```

**Blocked and outside all waves:** the first-launch intro video. It needs a
user-generated Veo clip (vertical 9:16 macro monstera, scan-light sweep,
`#16A34A` accent, seamless ~8 s loop, no text). No model can start it. Slot it
into Wave 2 whenever the clip arrives.

### Why the waves break where they do

| Boundary | Reason |
|---|---|
| 3 → 4, 5 | The endpoint defines the DTO both views consume. Building the views first means guessing the shape twice. |
| 8a → 8b | 8a decides the Room schema. Shipping a wrong migration is expensive to undo on installed devices. |
| 8b → 9 | The health header derives from checklist-adherence data that doesn't exist until 8b lands. |
| 8b → 10 | **Migration collision.** The DB is at `version = 5` (`Database.kt:447`). Both 8b and 10 need a migration; two agents writing `MIGRATION_5_6` in parallel produces a conflict that unit tests won't catch. 8b owns 5→6; 10 takes 6→7 afterwards. |

### Conflict warnings inside a wave

- **Wave 1:** task 2 touches `ResultActivity`; so does task 6's coach-mark work
  in Wave 2. Merging 2 before starting 6 avoids the overlap.
- **Wave 2:** tasks 4 and 5 both wire into `SpeciesDetailActivity`. Land 4
  first, or give each its own section of the layout and expect a small manual
  merge.
- **Wave 6:** both tasks touch the Garden list card. Small, but review the
  merge rather than trusting a clean auto-merge.

---

## Human gates

These are the points where no test can substitute for a person looking. Each one
blocks the wave after it.

### ★ GATE A — endpoint content quality (after task 3)

Run the new endpoint by hand for 5–10 real species in **both** EN and RU and
read the output.

- Is the flowering data actually *true* for that plant? An LLM will confidently
  return a bloom calendar for a species that never flowers indoors.
- Does `native_range` name plausible countries, or has it invented a range?
- Does the RU response read like Russian, or like translated English?
- Does it degrade correctly with `OPENROUTER_API_KEY` unset (`available:false`,
  no crash)?

This is the gate most likely to fail. The two UI tasks that follow are wasted
work if the data underneath is wrong.

### ★ GATE B — visual and first-run review (after tasks 4, 5, 6)

Custom `Canvas` views and first-run flows cannot be validated by Robolectric.

- Install a **debug build on a real device** and look at the flowering chart and
  the world map. Check the map at small ranges (single island) and large ones.
- Check both light and dark theme, and both locales.
- Test onboarding on a **clean install** (uninstall first — the once-only hint
  state will otherwise already be set).
- Confirm the consent page still blocks and still links to `privacy.html`.

### ★★ GATE C — schema approval (after task 8a) — the highest-stakes gate

Read the proposed Room migration before any implementation starts. Once a
migration ships to installed devices it cannot be casually revised.

- Does the checklist-step storage fit the actual care-plan `treatment` shape?
- Is the Today-queue aggregation derivable from existing tables, or does it
  need storage the design didn't mention?
- Are you comfortable with the rejected alternatives it lists?

**Do not skip this one.** Everything in Waves 5 and 6 is built on it.

### ★ GATE D — health heuristic review (after task 9)

The header tells users their plant is *declining*. A wrong verdict is worse than
no verdict.

- Feed it real scan histories from your own garden data and sanity-check the
  verdicts.
- Are the thresholds in one tunable place, as the prompt required?

### Running throughout — RU string review

Every task below produces bilingual strings. Machine-written Russian UI copy
reads wrong in ways tests never catch. Review the `values-ru/strings.xml` diff
on each merge.

### Release gate — task 7

Human-only by nature: tag 1.8, cut the signed RuStore build, submit. The version
fields (`versionName 1.8` / `versionCode 18`) are already set in
`app/build.gradle.kts`.

---

## Wave 1 — parallel, three agents

### 1 · RU chat subtitle — `claude-sonnet-5`

One file, known bug, no dependencies.

> In `android/app/src/main/java/com/plantdiseases/app/ui/garden/DoctorChatSheet.kt`, the plant name shown under the AI-doctor chat header uses `scientificName ?: species` (~line 296), so RU locale shows the English common name. Make it prefer the localised `commonName` when the app language is Russian, falling back to `scientificName`, then `species`. Check how other screens resolve the display name and match that. Add a Robolectric unit test under `src/test/` covering both locales. Change only the subtitle resolution — do not touch the chat logic.

### 2 · App-wide connectivity signal — `claude-opus-5` (effort `xhigh`)

New shared infrastructure across many screens. Build it before the Wave 2
screens, which are network-fed and would otherwise need a retrofit.

> Connectivity is currently checked ad-hoc in only `CameraFragment` and `ResultActivity`; every other screen silently renders blank when offline. Build one shared connectivity observer, expose it through Koin (`di/AppModule.kt`), and add a single reusable "offline — showing cached content" affordance. Wire it into the screens that fetch from the network: home, community, guide, species, symptom, identify. Do not build offline replacements for online-only features — surface the state only. Keep XML Views + Koin, no Compose. Bilingual strings in both `res/values/strings.xml` and `res/values-ru/strings.xml`. Add unit tests for the observer. Deliver what's asked at the scope asked — don't refactor surrounding code or add abstractions for hypothetical future use.

### 3 · `GET /api/species-profile` — `claude-sonnet-5`

Server work, separate toolchain and separate CI job. Gates tasks 4 and 5.

> In `server/`, add `GET /api/species-profile?name=&locale=` following the exact discipline of the existing `species_common_diseases` endpoint: one OpenRouter call per (species, locale), strict JSON, `reasoning_effort=minimal`, in-memory + disk cache with a long TTL, graceful `available:false` when `OPENROUTER_API_KEY` is unset, and it must never raise. Response fields: `flowering` (12-month intensity array of 0/1/2 plus an indoor note), `flowering_conditions` (short bloom-trigger tips), `native_range` (per-country `zone` of grows-more/grows-less plus a summary). Read `ai_services.py` first and mirror its structure. Add pytest coverage using the existing `FakePipeline` conftest setup. Run `ruff check .` before finishing.

---

## Wave 2 — parallel, three agents (after Gate A)

### 4 · Flowering chart + conditions card — `claude-sonnet-5`

`ui/widget/BarChartView.kt` already exists as the idiom to follow.

> Add `FloweringChartView` under `ui/widget/`, modelled on the existing `BarChartView.kt`: 12 intensity bars, month initials, a current-month marker, and a derived "blooms now / next bloom in <month>" line based on today's date. "Rarely flowers indoors" must be a first-class rendered state, not a faked flat curve. Add a flowering-conditions card beside it. Render both in `SpeciesDetailActivity` and, collapsed with tap-to-expand, on the scan result screen. Consume the `/api/species-profile` DTO; the screen must render normally when the endpoint returns `available:false`. Bilingual strings. Robolectric tests for the date-derived bloom line.

### 5 · Native-range world map — `claude-opus-5` (effort `xhigh`)

Largest single item in 1.8 — bundled geometry, projection, and hit-testing from
scratch.

> Build a self-contained `WorldMapView` under `ui/widget/`. Bundle simplified Natural Earth country outlines as an asset (~100–150 KB budget, no new Gradle dependencies) and render with Canvas so it works fully offline. Two-tone fill for grows-more vs grows-less zones, auto-fit the viewport to the highlighted range, tap a country to show a name + zone chip. Below the map, list the same countries as text chips for accessibility and for ranges too small to tap. Consume `native_range` from `/api/species-profile`. Bilingual country labels where the server supplies them. Add Robolectric tests for the projection and hit-testing math. This is one view — do not refactor the surrounding species screen beyond wiring it in.

### 6 · Interactive onboarding — `claude-opus-5`

A rework of first-run state, not a screen tweak. `OnboardingActivity` is still
the 278-line `ViewPager2` tour.

> `OnboardingActivity` is still the `ViewPager2` swipe tour. Replace it with a single blocking data-consent page (keep the consent disclosure and its link to `privacy.html` exactly as-is — it's the canonical disclosure surface), plus lightweight in-app coach marks: a scan-tab pulse on first home visit and a one-time "understand your result" hint on the first result screen. Persist the shown/dismissed state so each hint fires once. Keep the existing Lottie animation. Bilingual strings. Add unit tests for the once-only hint state.

---

## Wave 3 — release (human)

Tag 1.8 and cut the RuStore build. Version fields are already bumped.

---

## Wave 4 — design, one agent (starts the 1.9 release)

### 8a · Garden refactor design pass — `claude-fable-5`, run once

The one place the premium tier earns out: nothing about the schema or the
aggregation has been decided, and this is exactly the "scope the unsolved
problem" case. Give it intent, not steps.

> I'm turning the Garden from a flat list of saved plants into a daily care-management hub — the screen someone opens each morning to keep plants alive, where every scan feeds an ongoing care story instead of being a one-off verdict. The user base is RU-first, the app is single-module XML Views + Koin + Room, no Compose, and it ships on RuStore.
>
> Read `docs/ROADMAP.md` → "Garden refactor", the Room schema in `data/local/Database.kt` (currently at `version = 5`), the reminder plumbing in `worker/PlantReminderWorker.kt`, and the care-plan `treatment` fields in `data/model/Models.kt`. Then produce a concrete design for: (a) how scan-derived treatment steps get persisted and tracked as checklists, (b) how waterings, feedings and open treatment steps aggregate into one prioritized "today" queue with one-tap Done, (c) how healthy/recovering/declining is derived from scan-history trend plus care adherence. Give me the Room migration (5→6), the repository surface, and the trade-offs you rejected. Do not write the UI yet.

**Output goes to Gate C before anything else starts.**

---

## Wave 5 — implementation, one agent

### 8b · Today queue + scan-driven checklists — `claude-opus-5` (effort `xhigh`)

Runs alone: it owns migration 5→6. Feed it the approved 8a design as the full
spec in the first turn — Opus 5 does its best work from one well-specified
opening turn rather than a spec built up across many.

> Implement the attached design. Ship the Room migration 5→6, the checklist persistence, the aggregated Today queue as the Garden landing surface, and one-tap Done that advances the schedule and writes to the plant's journal. Promote the existing `PlantReminderWorker` data from background notifications into this first-class in-app list. Empty state is a genuine "all caught up 🌿" reward — no streaks, no gamification. Reuse the existing repositories and the modular detail controllers under `ui/garden/detail/`. Bilingual strings. Robolectric tests for the aggregation, the schedule advance, and the migration. Do not add features beyond this scope, and do not build the health header — that's a separate task.

---

## Wave 6 — parallel, two agents

### 9 · Plant health header — `claude-opus-5`

Needs the adherence signal from 8b.

> Add a healthy / recovering / declining status header leading each plant's detail screen, derived from the scan-history trend (verdict + confidence over time) and the care-checklist adherence from the Today queue work. Drive the Garden list card accent from it so a declining plant is visible in the list without opening it. State the heuristic's thresholds explicitly in one place so they're tunable. Bilingual strings, Robolectric tests for the derivation.

### 10 · Rooms / locations — `claude-sonnet-5`

Genuinely small. Takes migration **6→7** — 8b has already taken 5→6.

> Add an optional `room` to plants: one nullable column on the `plants` entity with a Room migration (take the next free version — 8b has already used 5→6), an editable field on plant detail, and a grouping header in the Garden list. No new screens. Bilingual strings, migration test.

---

## Already done — do not re-scope

Verified present in the tree; the roadmap previously listed some of these as open.

| Item | Where |
|---|---|
| Progression / photo-compare strip | `ui/garden/PhotoCompareActivity.kt` + `ui/garden/detail/DetailTimelineController.kt` |
| `versionName` 1.8 / `versionCode` 18 | `app/build.gradle.kts` |
| Guide photo prefetch into Glide disk cache | `worker/GuidePhotoPrefetchWorker.kt` |
| RuStore in-app updates, minSdk 29 | landed on `main` (#131) |
| Toolbar → page-content titles | landed on `main` (#130) |

---

## CI

`.github/workflows/ci.yml` is push-only on `main`/`master`/`claude/**`, with a
`paths-filter` that runs the **android** and **server** jobs only when their
trees change. Task 3 therefore exercises a different CI job from everything
else in Wave 1 — a server failure won't block the Android tasks and vice versa.

Android job: `ktlintCheck` + `detekt` + `testDebugUnitTest` + R8 `assembleRelease`.
Server job: `ruff` + `pytest`.
