---
name: plantdiseases-modernization
description: MANUAL-INVOKE ONLY — do NOT auto-trigger. Invoke solely when the user runs /plantdiseases-modernization or asks for it by name. Playbook for modernizing the plantdiseases/PlantVision Android app (com.plantdiseases.app) WITHOUT a Compose rewrite: (1) add Koin DI in place of the hand-rolled service locator, (2) migrate Retrofit + Gson → Ktor + kotlinx.serialization, (3) publish to RuStore. Keeps XML Views, single-module, EN/RU, and the ML/TFLite pipeline intact. Even when a request clearly involves DI, networking, or release work on this app, do NOT load on your own — wait for explicit invocation.
---

# PlantDiseases modernization (no Compose)

Per the mentor's guidance, plantdiseases is **NOT** being migrated to Compose (it's the
graded Samsung-campus project; a UI-paradigm rewrite is high-risk and the differentiator is
the ML, not the UI). Instead, modernize the guts and ship:

1. **DI: hand-rolled service locator → Koin** (simpler than Hilt; runtime, no codegen).
2. **Networking: Retrofit + Gson → Ktor + kotlinx.serialization** (~a day, *with tests*).
3. **Distribution: publish to RuStore** (RU audience; app is already bilingual).

> MetalRestrict is on a *different* track (full Compose migration — see the
> `android-compose-migration` skill). This skill is plantdiseases-only and Compose-free.

## Current state (verified — build on this, don't guess)

- **DI today:** `PlantDiseasesApp : Application` is a **service locator** holding `by lazy`
  singletons: `database` (Room), `apiClient: PlantApiClient`, `scanRepository`,
  `plantRepository`, `communityUploader`, plus `appScope`. Consumers reach them via
  **`application as PlantDiseasesApp`** — there are **~54 such call sites** to migrate.
- **Networking today:** `data/remote/PlantApiClient.kt` builds OkHttp + Retrofit + Gson and
  exposes `data/remote/ApiService.kt` (a large `@Multipart`/`@GET`/`@POST` interface). It has
  a **base-URL constructor seam** (`PlantApiClient(baseUrl)`) used by tests, a
  **`RetryInterceptor`**, and HTTP-status → typed-exception mapping.
- **Models:** `data/model/Models.kt` + `LabelingModels.kt` use Gson (`@SerializedName`).
- **Tests:** `ScanRepositoryNetworkTest` drives a **`MockWebServer`** through the base-URL seam
  (multipart upload, retry, status→exception mapping). These must stay green after migration.
- Kotlin **2.1.21**, AGP 8.13.2, compileSdk 35, minSdk 26, `build.gradle.kts`, single `:app`,
  signed releases, EN/RU (`values/` + `values-ru/`), `values-night` dark theme.

## Recommended order (and why)

**Koin → Ktor/Serialization → RuStore.**
- Koin **first**: once dependencies come from Koin modules, swapping the networking *impl*
  behind the same `ApiService`/repository boundary is a one-line rebinding, not 54 edits twice.
- Ktor **second**: contained data-layer swap behind the existing interfaces + test seam.
- RuStore **last**: it's release/process — do it once the code is stable.

| Step | Reference | Rough effort | Risk |
|---|---|---|---|
| Koin DI | [koin-di.md](references/koin-di.md) | ~half a day | low–med (touches ~54 call sites, mechanical) |
| Ktor + Serialization | [ktor-migration.md](references/ktor-migration.md) | ~a day w/ tests | med (preserve every endpoint + error mapping) |
| RuStore publish | [rustore-publishing.md](references/rustore-publishing.md) | ~half a day | low (process, not code) |

## Hard constraints — do not violate

1. **No Compose. No multi-module.** XML Views + single `:app` stay.
2. **Keep the base-URL test seam.** Whatever replaces `PlantApiClient`, it must still accept a
   base URL so `MockWebServer`/`MockEngine` tests can point at it. Don't break
   `ScanRepositoryNetworkTest`.
3. **Preserve behavior exactly:** every endpoint, multipart upload, streaming, the retry
   policy, and the HTTP-status → typed-exception mapping must behave identically. This is a
   refactor, not a redesign.
4. **EN/RU bilingual** stays — no hardcoded user-facing strings.
5. **Graceful degradation stays.** Optional integrations (PlantNet/OpenRouter/FCM) return
   "not configured" rather than crashing — keep that. RuStore devices may lack Google Play
   services, so the existing **FCM → WorkManager-poll fallback** matters (see RuStore ref).
6. **Don't touch the ML path or WorkManager init quirk.** TFLite/Aqua, the server diagnosis
   flow, and the "don't call WorkManager in `Application.onCreate`" rule (scheduling happens in
   `MainActivity`) stay as-is.
7. **Builds run in CI.** Make edits, run cheap unit tests; let CI do `lintDebug` +
   `testDebugUnitTest` + R8 `assembleRelease`. The whole point of step 2 is "doable with tests."

## Anti-patterns to reject
- ❌ Bundling a Compose or multi-module change "while we're refactoring".
- ❌ Replacing Gson with kotlinx.serialization but leaving Json lenient settings off (server
   may send unknown/extra fields — see ktor ref; you'll get crashes without `ignoreUnknownKeys`).
- ❌ Dropping the base-URL seam / rewriting the tests' transport instead of porting them.
- ❌ Doing Koin and Ktor in one giant commit. One step, green CI, merge, next step.
- ❌ Shipping to RuStore without a privacy policy (the app uploads user photos).
