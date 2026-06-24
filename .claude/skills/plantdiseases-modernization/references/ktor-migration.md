# Retrofit + Gson → Ktor + kotlinx.serialization

> **STATUS: scaffolded, in progress.** The serialization plugin + Ktor (ktor-bom 2.3.13) are in
> the build; `PlantApiClient` (at the END of `data/remote/ApiService.kt`, ≈ lines 377–425) already
> builds **both** a Retrofit+Gson client and a Ktor `HttpClient` over the **same OkHttp engine**
> (shared base-URL seam, `RetryInterceptor`, `ContentNegotiation(Json)`, `expectSuccess=false`).
> `/api/analyze` is migrated (`ScanRepository.analyzeImage`); 9/73 models are `@Serializable`.
> **Remaining = 31 endpoints + 64 models** — the per-endpoint recipe + checklist is in
> [endpoint-inventory.md](endpoint-inventory.md). The setup sections below describe what's already
> wired (reference, not steps to redo); use them to mirror the existing pattern for each new endpoint.

A contained data-layer swap behind the existing `ApiService`/`PlantApiClient` boundary. Goal:
identical behavior (every endpoint, multipart upload, streaming, retry, status→exception
mapping), the base-URL test seam preserved, `ScanRepositoryNetworkTest` still green.

> **Keep the OkHttp engine.** Use `ktor-client-okhttp` (not CIO). It keeps the existing
> `MockWebServer` tests working (Ktor's OkHttp engine talks to MockWebServer fine), reuses the
> OkHttp the app already ships, and gives a clean place for the logging/retry config. This is
> what makes "a day with tests" realistic — minimal test-transport churn.

## Build setup (`app/build.gradle.kts`)
```kotlin
plugins {
    // ...existing...
    id("org.jetbrains.kotlin.plugin.serialization") version "2.1.21" // == Kotlin version
}
dependencies {
    val ktor = "3.0.0"  // Ktor 3.x — verify latest stable
    implementation("io.ktor:ktor-client-core:$ktor")
    implementation("io.ktor:ktor-client-okhttp:$ktor")
    implementation("io.ktor:ktor-client-content-negotiation:$ktor")
    implementation("io.ktor:ktor-serialization-kotlinx-json:$ktor")
    implementation("io.ktor:ktor-client-logging:$ktor")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.7.3") // verify latest
    testImplementation("io.ktor:ktor-client-mock:$ktor") // optional; or keep MockWebServer
}
```
Keep `retrofit`/`converter-gson`/`gson` in the build until the **last** Retrofit call-site is
gone (other code may still use Gson — e.g. `ResponseCache`, `ResultActivity`; migrate or leave
those on Gson independently), then remove them.

## Models: Gson → kotlinx.serialization
For each class in `data/model/Models.kt` + `LabelingModels.kt`:
```kotlin
// Before (Gson)
data class AnalysisResponse(
    @SerializedName("top_label") val topLabel: String,
    val confidence: Float,
)
// After (kotlinx.serialization)
@Serializable
data class AnalysisResponse(
    @SerialName("top_label") val topLabel: String,
    val confidence: Float = 0f,            // give defaults — see lenient JSON below
)
```
- `@SerializedName` → `@SerialName`; add `@Serializable` to the class.
- Provide **defaults** for nullable/optional fields; the server evolves and sends extra/missing
  keys. Combined with the Json config below, this prevents crashes Gson silently tolerated.
- Enums: add `@Serializable` (+ `@SerialName` per value if the wire name differs).

## The Json instance (critical — match Gson's leniency)
```kotlin
val appJson = Json {
    ignoreUnknownKeys = true   // server may add fields — Gson ignored them; Json must be told to
    coerceInputValues = true   // null for a non-null-with-default → use the default
    explicitNulls = false      // don't emit nulls for absent optionals
    isLenient = true
}
```
Forgetting `ignoreUnknownKeys` is the #1 migration crash. Set it.

## Rebuild PlantApiClient with Ktor (keep the base-URL seam!)
```kotlin
class PlantApiClient(baseUrl: String = ServerConfig.baseUrl) {   // ← seam preserved
    private val client = HttpClient(OkHttp) {
        expectSuccess = true                       // non-2xx → throws; map below
        install(ContentNegotiation) { json(appJson) }
        install(HttpTimeout) {
            requestTimeoutMillis = 60_000
            connectTimeoutMillis = 15_000
            socketTimeoutMillis  = 60_000
        }
        install(HttpRequestRetry) {                // replaces RetryInterceptor
            retryOnServerErrors(maxRetries = 2)
            retryOnException(maxRetries = 2, retryOnTimeout = true)
            exponentialDelay()
        }
        install(Logging) { level = if (BuildConfig.DEBUG) LogLevel.BODY else LogLevel.NONE }
        defaultRequest { url(baseUrl) }
        // engine { preconfigured = existingOkHttp } // optional: reuse the app's OkHttp
    }

    // Map HTTP status → the SAME typed exceptions the Retrofit version threw.
    private suspend inline fun <reified T> HttpResponse.decodeOrThrow(): T {
        if (status.isSuccess()) return body()
        throw when (status.value) {
            400 -> BadRequestException(/* ... */)   // reuse existing exception types
            413 -> PayloadTooLargeException()
            in 500..599 -> ServerException(status.value)
            else -> ApiException(status.value)
        }
    }
}
```
Convert each `ApiService` method to a Ktor call:
```kotlin
// @Multipart @POST("api/analyze") — multipart upload
suspend fun analyzeImage(image: File, species: String?): AnalysisResponse =
    client.post("api/analyze") {
        species?.let { parameter("species", it) }
        setBody(MultiPartFormDataContent(formData {
            append("image", image.readBytes(), Headers.build {
                append(HttpHeaders.ContentType, "image/jpeg")
                append(HttpHeaders.ContentDisposition, "filename=\"upload.jpg\"")
            })
        }))
    }.body()

// @GET with @Query
suspend fun capabilities(): CapabilitiesResponse =
    client.get("api/capabilities").body()

// @Streaming download → response.bodyAsChannel() / readBytes()
```
- `@Header` → `header(name, value)` or `defaultRequest { header(...) }`.
- `@Path` → string-interpolate into the URL; `@Query` → `parameter(...)`.
- `@Streaming` → read `response.bodyAsChannel()` incrementally instead of buffering.
- Keep method **names/signatures** so `ScanRepository` and other callers don't change (or keep
  `ApiService` as an interface and make the Ktor client implement it — least caller churn).

## Tests — keep them, port the transport
The existing `ScanRepositoryNetworkTest` uses `MockWebServer` + the base-URL seam. With the
**OkHttp engine** it keeps working almost as-is:
```kotlin
repo = ScanRepository(FakeScanDao(), PlantApiClient(server.url("/").toString()))
```
Still valid — Ktor-on-OkHttp hits the MockWebServer URL. Just verify the assertions
(multipart body, retry count, status→exception). If you prefer hermetic tests, swap to Ktor
`MockEngine`, but porting MockWebServer is the lower-risk path for "a day with tests".

## Definition of done
- [ ] All `data/model` DTOs `@Serializable`; `@SerializedName` → `@SerialName`; defaults added.
- [ ] `appJson` has `ignoreUnknownKeys`/`coerceInputValues`/`explicitNulls=false`.
- [ ] Ktor `PlantApiClient` keeps the **base-URL constructor seam** and reproduces retry +
      timeout + status→exception mapping; every endpoint ported (multipart + streaming included).
- [ ] `ScanRepositoryNetworkTest` (and any other API tests) green via MockWebServer/MockEngine.
- [ ] Retrofit/Gson removed only after the last call-site is migrated; `assembleRelease` builds.
      (kotlinx.serialization ships its own consumer R8 rules; no manual keeps needed.)
