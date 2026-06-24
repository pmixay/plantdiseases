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

## Progress snapshot — PARTLY DONE on `main` (verify against code; don't redo finished work)

The build already wires Koin (koin-bom 4.0.0) + Ktor (ktor-bom 2.3.13) + the kotlinx
serialization plugin. State as of this upgrade:

**Step 1 — Koin DI: ✅ graph wired, ⬜ call sites pending.**
- `di/AppModule.kt` defines `appModule`, mirroring the former `by lazy` singletons 1:1
  (`AppDatabase`, `PlantApiClient`, `ScanRepository`, `PlantRepository`, `CommunityUploader`).
- `PlantDiseasesApp.onCreate` calls `startKoin{}`; the Application keeps its
  `database/apiClient/scanRepository/...` accessors as `by inject()` — the **safety net**.
- **Remaining:** migrate the **~54 `application as PlantDiseasesApp`** call sites to direct
  injection (`by inject()` / constructor / `viewModel { }`), then delete the Application
  accessors. `koin-androidx-workmanager` is intentionally NOT added yet (no Koin worker).
- Details + the call-site procedure: [koin-di.md](references/koin-di.md).

**Step 2 — Ktor + kotlinx.serialization: 🟡 scaffolded, analyze path migrated.**
- `PlantApiClient` **lives at the END of `data/remote/ApiService.kt`** (≈ lines 377–425), NOT
  a separate file. It builds **both** a Retrofit+Gson client **and** a Ktor `HttpClient` over
  the **same OkHttp engine** (shared `RetryInterceptor`, **base-URL seam**,
  `ContentNegotiation(Json)`, `expectSuccess = false`). Both transports coexist by design.
- `/api/analyze` is **done**: `ScanRepository.analyzeImage` uses `apiClient.httpClient` (Ktor
  multipart). **9 of 73 models** are `@Serializable` (the analyze path).
- **Remaining: 31 Retrofit endpoints + 64 Gson-only models.** Full checklist with done/todo
  status and the per-endpoint recipe: [endpoint-inventory.md](references/endpoint-inventory.md).

**Step 3 — RuStore publish: ⬜ not started.** [rustore-publishing.md](references/rustore-publishing.md).

Stack: Kotlin **2.1.21**, AGP 8.13.2, compileSdk 35, minSdk 26, `build.gradle.kts`, single
`:app`, signed releases, EN/RU (`values/` + `values-ru/`), `values-night` dark theme.

## Remaining-work order

1. **Finish Ktor migration** endpoint-by-endpoint ([endpoint-inventory.md](references/endpoint-inventory.md)),
   each with a test ([testing.md](references/testing.md)). Migrate a model's `@Serializable`
   the first time an endpoint that returns it moves to Ktor. Keep Retrofit+Gson until the
   **last** endpoint is migrated, then delete Retrofit/Gson + the dual `@SerializedName`.
2. **Finish Koin** call-site migration ([koin-di.md](references/koin-di.md)) — can proceed in
   parallel; it's mechanical and independent of networking.
3. **RuStore** once code is stable ([rustore-publishing.md](references/rustore-publishing.md)).

| Step | Reference | Status | Remaining effort |
|---|---|---|---|
| Koin DI | [koin-di.md](references/koin-di.md) | ✅ wired / ⬜ ~54 call sites | ~half a day, mechanical |
| Ktor + Serialization | [ktor-migration.md](references/ktor-migration.md) · [endpoint-inventory.md](references/endpoint-inventory.md) | 🟡 1/32 endpoints, 9/73 models | ~a day w/ tests |
| RuStore publish | [rustore-publishing.md](references/rustore-publishing.md) | ⬜ not started | ~half a day, process |

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
