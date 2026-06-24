---
name: plantdiseases-modernization
description: MANUAL-INVOKE ONLY — do NOT auto-trigger. Invoke solely when the user runs /plantdiseases-modernization or asks for it by name. Playbook for modernizing the plantdiseases/PlantVision Android app (com.plantdiseases.app) WITHOUT a Compose rewrite: (1) add Koin DI in place of the hand-rolled service locator, (2) migrate Retrofit + Gson → Ktor + kotlinx.serialization. Keeps XML Views, single-module, EN/RU, and the ML/TFLite pipeline intact. Even when a request clearly involves DI or networking work on this app, do NOT load on your own — wait for explicit invocation.
---

# PlantDiseases modernization (no Compose)

Per the mentor's guidance, plantdiseases is **NOT** being migrated to Compose (it's the
graded Samsung-campus project; a UI-paradigm rewrite is high-risk and the differentiator is
the ML, not the UI). Instead, modernize the guts and ship:

1. **DI: hand-rolled service locator → Koin** (simpler than Hilt; runtime, no codegen).
2. **Networking: Retrofit + Gson → Ktor + kotlinx.serialization** (~a day, *with tests*).

(RuStore publishing — the original step 3 — is **already done** and out of scope here.)

> MetalRestrict is on a *different* track (full Compose migration — see the
> `android-compose-migration` skill). This skill is plantdiseases-only and Compose-free.

## Progress snapshot — ✅ BOTH STEPS COMPLETE (verify against code before any new work)

Both modernization steps are finished and on green CI (`testDebugUnitTest` + `lintDebug` +
R8 `assembleRelease`). The build wires Koin (koin-bom 4.0.0) + Ktor (ktor-bom 2.3.13) + the
kotlinx serialization plugin.

**Step 1 — Koin DI: ✅ DONE.**
- `di/AppModule.kt` defines `appModule` (`AppDatabase`, `PlantApiClient`, `ScanRepository`,
  `PlantRepository`, `CommunityUploader`); `PlantDiseasesApp.onCreate` calls `startKoin{}`.
- All ~53 `application as PlantDiseasesApp` accessor call sites across 34 files migrated to
  direct injection — Activities/Fragments via `org.koin.android.ext.android.inject`; plain
  controllers/workers/receiver/push via `KoinComponent` + `org.koin.core.component.inject`.
- The Application's repository accessors are deleted; only `communityUploader` remains
  (private, used by `launchCommunityUpload`). `appScope`/`launchCommunityUpload`/the
  opinion-scheduling helpers stay on the Application (not injectable). `koin-androidx-
  workmanager` intentionally NOT added — workers are `KoinComponent`s, no Koin WorkerFactory.

**Step 2 — Ktor + kotlinx.serialization: ✅ DONE.**
- `PlantApiClient` (at the END of `data/remote/ApiService.kt`) is now a **Ktor `HttpClient`**
  over the OkHttp engine (shared `RetryInterceptor`, **base-URL seam**, `ContentNegotiation
  (Json{ignoreUnknownKeys})`, `expectSuccess=false`). All 32 endpoints migrated; the Retrofit
  `ApiService` interface + `converter-gson` removed. Gson the library STAYS (Room field JSON +
  `ResponseCache`); the dual `@SerializedName` annotations were left in place (harmless, still
  used by the Gson cache).
- Notable custom serializers in `data/model/Models.kt`: `WateringDaysSerializer` (care-plan
  int-or-string), `AnyMapSerializer`/`AnyMapListSerializer` (chat/progression `Map<String,Any?>`),
  `GsonJsonObjectSerializer` (Aqua manifest `classes`). Streaming aqua/model download uses
  `bodyAsChannel().toInputStream()`.
- Full per-endpoint/model record: [endpoint-inventory.md](references/endpoint-inventory.md).

> The `api/label/*` endpoints + `LabelingModels.kt` the older inventory listed were removed
> upstream (commit 16da03c) and are not part of this migration.

Stack: Kotlin **2.1.21**, AGP 8.13.2, compileSdk 35, minSdk 26, `build.gradle.kts`, single
`:app`, signed releases, EN/RU (`values/` + `values-ru/`), `values-night` dark theme.

## Remaining-work order

Nothing required — both steps are complete. Optional future cleanup only:
- Strip the now-redundant dual `@SerializedName` from models the `ResponseCache` does NOT
  cache (kept for now; removing them risks subtle Gson cache field-name changes — see
  [ktor-migration.md](references/ktor-migration.md)).
- Add a Koin `checkModules()` graph test if `koin-test` is added to the build.

| Step | Reference | Status |
|---|---|---|
| Koin DI | [koin-di.md](references/koin-di.md) | ✅ done (graph + all call sites + accessors removed) |
| Ktor + Serialization | [ktor-migration.md](references/ktor-migration.md) · [endpoint-inventory.md](references/endpoint-inventory.md) | ✅ done (32/32 endpoints; Retrofit removed) |

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
   "not configured" rather than crashing — keep that, including the **FCM → WorkManager-poll
   fallback** for devices without Google Play services.
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
