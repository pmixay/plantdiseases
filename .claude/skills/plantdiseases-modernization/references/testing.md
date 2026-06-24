# Testing the modernization

Keep CI green at every step. CI runs `lintDebug` + `testDebugUnitTest` (+ R8 `assembleRelease`).
Prefer JVM/Robolectric tests — no device needed. The "~a day **with tests**" estimate for step 2
depends on porting one test per migrated endpoint, not skipping them.

## The transport test that already exists (your template)
`data/repository/ScanRepositoryNetworkTest` drives a real **`MockWebServer`** through the
`PlantApiClient(baseUrl)` **seam**. Because `PlantApiClient` builds both the Retrofit and the
Ktor client against that same base URL, MockWebServer tests work for **both transports** —
porting an endpoint to Ktor does NOT mean rewriting its test transport.

It already covers the migrated `/api/analyze` (Ktor):
- `analyze_success_parsesBodyAndBuildsMultipartPost` — asserts multipart POST, query params,
  and parsed body.
- `analyze_includesSpeciesHintQuery_whenProvided` — query building.
- status → typed exception: `400`/`415` → `UnsupportedImageException`, `429` → `RateLimitException`,
  `503` → `ServerStartingException`, `500` → generic "Server error".

## Two gotchas the migrated tests must respect
1. **`Content-Type: application/json` on success mocks.** Ktor `ContentNegotiation` only
   deserializes a response that declares JSON. Gson didn't care. So every success
   `MockResponse` must `.setHeader("Content-Type", "application/json")` — the real server
   already sends it. (The existing analyze tests do this; copy the pattern.)
2. **Retry doubles the enqueue.** A POST that retries an explicit `429`/`503` once consumes
   **two** queued responses — `server.enqueue(...)` twice (see the 429/503 tests). Match the
   `RetryInterceptor`/`HttpRequestRetry` policy when counting enqueues.

## Adding a test per newly-migrated endpoint (do this every time)
```kotlin
@Test
fun capabilities_parsesFamilies() {
    server.enqueue(
        MockResponse().setResponseCode(200)
            .setHeader("Content-Type", "application/json")          // ← gotcha #1
            .setBody("""{"default":"gamma","families":[{"id":"gamma","available":true}]}"""),
    )
    val result = runBlocking { repo.capabilities() }               // via the migrated call
    assertThat(result.getOrNull()?.default).isEqualTo("gamma")
    assertThat(server.takeRequest().path).isEqualTo("/api/capabilities")
}
```
Cover, per endpoint: the success shape, the relevant error-status mapping, and (for
`@Query`/`@Path`/`@Header`) that the request is built correctly (`server.takeRequest()`).
A response with **extra/unknown fields** is a good case — it proves `ignoreUnknownKeys` is on.

## Koin tests
The graph is wired (`di/AppModule.kt`). Add a verification test so a broken module fails fast:
```kotlin
class AppModuleTest : KoinTest {
    @Test fun graphResolves() {
        startKoin { androidContext(ApplicationProvider.getApplicationContext()); modules(appModule) }
        // checkModules() / koinApplication { modules(appModule) }.checkModules()
        stopKoin()
    }
}
```
- Existing tests that construct objects directly (e.g. `ScanRepository(FakeScanDao(),
  PlantApiClient(server.url("/")))`) **stay as-is** — they bypass Koin; don't force them onto it.
- Mind the "second `startKoin()` in one JVM throws" note in `PlantDiseasesApp` — `stopKoin()` in
  `@After`, or use `KoinTestRule`.

## Don't break the rest
- Robolectric JVM tests keep running unchanged (they cover data/logic, not transport).
- `unitTests.isIncludeAndroidResources = true` is already set — needed for Robolectric.
- If you delete an `ApiService` method, repoint any test that referenced it at the new call.

## Definition of done (testing)
- [ ] Every migrated endpoint has a MockWebServer success + error test (JSON `Content-Type` set).
- [ ] Retry-sensitive endpoints enqueue the right number of responses.
- [ ] `AppModule` resolves in a Koin test.
- [ ] `testDebugUnitTest` green locally on the touched tests; full suite + `assembleRelease` in CI.
