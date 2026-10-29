package com.plantdiseases.app.data.remote

import com.plantdiseases.app.data.model.PopularResponse
import com.plantdiseases.app.data.model.SpeciesDiseasesResponse
import com.plantdiseases.app.data.model.SpeciesInfoResponse
import com.plantdiseases.app.data.model.SpeciesProfileResponse
import com.plantdiseases.app.util.ReachabilityReporter
import com.plantdiseases.app.util.ServerConfig
import io.ktor.client.HttpClient
import io.ktor.client.call.body
import io.ktor.client.engine.okhttp.OkHttp
import io.ktor.client.plugins.contentnegotiation.ContentNegotiation
import io.ktor.client.plugins.defaultRequest
import io.ktor.client.request.get
import io.ktor.client.request.parameter
import io.ktor.client.request.url
import io.ktor.http.isSuccess
import io.ktor.serialization.kotlinx.json.json
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import okhttp3.Interceptor
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import java.io.IOException
import java.util.concurrent.TimeUnit

// All HTTP endpoints have migrated from Retrofit to the Ktor client below
// (PlantApiClient.httpClient + the ScanRepository call sites). The Retrofit
// ApiService interface and its Gson converter have been removed; OkHttp +
// RetryInterceptor remain as the shared engine the Ktor client runs on.

/**
 * Retries on transient network failures and on HTTP 429 / 503, honouring the
 * Retry-After header when present. Ordinary POST requests are retried at most
 * once. Metered requests carrying X-Idempotency-Key are never retried here:
 * the quota service permits only the explicit pre-consumption 401 refresh
 * replay, while a replay after quota consumption is rejected as already used.
 *
 * `maxDelayMs` is intentionally high (30 s) because the server's cold-start
 * path can legitimately ask for ~20 s — model artefacts deserialise on the
 * first request after deploy. Capping at 8 s used to abandon the retry just
 * before the server would have been ready.
 */
class RetryInterceptor(
    private val maxRetries: Int = 3,
    private val initialDelayMs: Long = 1000,
    private val maxDelayMs: Long = 30_000,
) : Interceptor {

    @Throws(IOException::class)
    override fun intercept(chain: Interceptor.Chain): Response {
        val request = chain.request()
        val isPost = request.method == "POST"
        val isMetered = !request.header("X-Idempotency-Key").isNullOrBlank()
        val effectiveRetries = when {
            isMetered -> 0
            isPost -> minOf(maxRetries, 1)
            else -> maxRetries
        }
        var lastException: IOException? = null

        for (attempt in 0..effectiveRetries) {
            try {
                val response = chain.proceed(request)
                if (response.code != 429 && response.code != 503) {
                    return response
                }
                if (attempt >= effectiveRetries) {
                    return response
                }
                val serverDelay = parseRetryAfterMs(response.header("Retry-After"))
                response.close()
                sleepBackoff(attempt, serverDelay)
            } catch (e: IOException) {
                // Never retry a POST on a transport-level IOException: the
                // server may have already received and processed the
                // (non-idempotent) multipart body — only the response was
                // lost — so a blind retry can create duplicate feedback /
                // community rows or re-run analyze. Ordinary POSTs still
                // retry on an explicit 429/503. Metered POSTs have zero
                // interceptor retries because quota may already be consumed;
                // their sole safe replay is the AuthRepository's 401 refresh
                // path before consumption. GETs retry freely (idempotent).
                lastException = e
                if (isPost || attempt >= effectiveRetries) throw e
                sleepBackoff(attempt, null)
            }
        }
        throw lastException ?: IOException("Retry loop exited without a response")
    }

    private fun sleepBackoff(attempt: Int, serverDelayMs: Long?) {
        val backoff = minOf(initialDelayMs * (1L shl attempt), maxDelayMs)
        val delay = serverDelayMs?.coerceAtLeast(backoff) ?: backoff
        try {
            Thread.sleep(delay)
        } catch (e: InterruptedException) {
            Thread.currentThread().interrupt()
            throw IOException("Interrupted during retry backoff", e)
        }
    }

    private fun parseRetryAfterMs(header: String?): Long? {
        if (header.isNullOrBlank()) return null
        val seconds = header.trim().toLongOrNull() ?: return null
        return (seconds.coerceAtLeast(0L) * 1000L).coerceAtMost(maxDelayMs)
    }
}

/**
 * The app's HTTP client — a Ktor [HttpClient] over an OkHttp engine.
 * Defaults to the build-time API base URL; the constructor parameter is a
 * test seam so unit/integration tests can point the whole stack at a local
 * MockWebServer without a device.
 */
class PlantApiClient(
    private val baseUrl: String = ServerConfig.appBaseUrl,
    /** Where call outcomes are published. Defaults to a private instance so
     *  tests (and any standalone use) need not wire one up. */
    private val reachabilityReporter: ReachabilityReporter = ReachabilityReporter(),
) {

    private val client: OkHttpClient = run {
        OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(60, TimeUnit.SECONDS)
            .writeTimeout(60, TimeUnit.SECONDS)
            // Signed community calls share this client with public app calls.
            // Never replay an application bearer—or any request body—to a
            // redirect target; every release endpoint is fixed at build time.
            .followRedirects(false)
            .followSslRedirects(false)
            // Keep all replay policy in RetryInterceptor/AuthRepository.
            // OkHttp's implicit route recovery otherwise sits below our
            // interceptor and can resend a replayable metered POST even when
            // effectiveRetries is zero.
            .retryOnConnectionFailure(false)
            // Tell the reporter whether the server answered, for every call
            // the app makes anyway — which is what lets ConnectivityObserver
            // stop polling a health endpoint on a timer. Installed BEFORE the
            // retry interceptor so it sees a call's settled outcome, not each
            // attempt: a blip the retry absorbs is not a connectivity event.
            // Any HTTP response — 200 or 500 alike — proves the bytes reached
            // the server; only an IOException that survived the retries says
            // otherwise.
            .addInterceptor { chain ->
                try {
                    chain.proceed(chain.request()).also { reachabilityReporter.report(true) }
                } catch (e: IOException) {
                    reachabilityReporter.report(false)
                    throw e
                }
            }
            .addInterceptor(RetryInterceptor(maxRetries = 3, initialDelayMs = 1000))
            .addInterceptor { chain ->
                chain.proceed(
                    chain.request().newBuilder()
                        .header("User-Agent", "PlantDiseases-Android")
                        .build(),
                )
            }
            .build()
    }

    /**
     * Retry-free, short-timeout client for the reachability probe.
     *
     * `newBuilder()` shares [client]'s connection pool and dispatcher, so a
     * probe normally rides an already-open connection: one round-trip, no DNS,
     * no TCP, no TLS handshake. (The probe used to own a second
     * [OkHttpClient] with its own pool, which made every check a cold
     * connection — the expensive path, on the exact networks least able to
     * afford it.)
     *
     * The interceptors are dropped deliberately: retries would make a probe
     * take tens of seconds, and self-reporting into the reporter would feed
     * the observer its own output.
     */
    private val probeClient: OkHttpClient by lazy {
        client.newBuilder()
            .apply { interceptors().clear() }
            .callTimeout(PROBE_TIMEOUT_MS, TimeUnit.MILLISECONDS)
            .connectTimeout(PROBE_TIMEOUT_MS, TimeUnit.MILLISECONDS)
            .readTimeout(PROBE_TIMEOUT_MS, TimeUnit.MILLISECONDS)
            .retryOnConnectionFailure(false)
            .build()
    }

    /**
     * Can this device reach our server right now?
     *
     * "Reached" means the server answered — any status counts, including the
     * 503 it returns while its pipeline is still loading. This is a
     * *connectivity* check; a server that is up but busy is not an offline
     * phone, and the screens have their own handling for server errors.
     */
    suspend fun probeReachable(): Boolean = withContext(Dispatchers.IO) {
        val request = Request.Builder().url("${baseUrl}api/health").get().build()
        // Any response at all is a success; only an IOException isn't.
        runCatching { probeClient.newCall(request).execute().close() }.isSuccess
    }

    /**
     * Ktor client for all HTTP endpoints. It reuses the same
     * preconfigured [client] (so the RetryInterceptor, logging and timeouts
     * are shared, not duplicated) and resolves relative paths against the same
     * [baseUrl] test seam. `expectSuccess = false` keeps non-2xx responses as
     * values so the repository can map HTTP status → typed exceptions itself,
     * exactly as it did with Retrofit's `Response`.
     */
    val httpClient: HttpClient by lazy {
        HttpClient(OkHttp) {
            engine { preconfigured = client }
            // Ktor's redirect plugin is independent of OkHttp's redirect
            // setting. Disable both layers so signed community requests can
            // never replay an application bearer to a Location target.
            followRedirects = false
            expectSuccess = false
            install(ContentNegotiation) {
                json(
                    Json {
                        ignoreUnknownKeys = true
                        // Defence-in-depth: an explicit JSON `null` on a field
                        // that has a default (the server sends these for
                        // unresolved country/region/scope values) coerces to
                        // that default instead of throwing a decode error the
                        // UI can only show as a generic "couldn't reach" state.
                        coerceInputValues = true
                    },
                )
            }
            defaultRequest { url(baseUrl) }
        }
    }

    // ── Best-effort GET lookups called directly by UI ──────────────────
    // These mirror the old Retrofit `.body()` contract: the parsed body on
    // 2xx, or null on any non-2xx. Callers wrap them in runCatching, so a
    // thrown decode error and a null both collapse to "no data" — exactly
    // how the Retrofit Response path behaved. No caching here (the species
    // and trending caches live in their callers / repository).

    /** Localized Wikipedia blurb + lead image for a scientific name. */
    suspend fun speciesInfo(name: String, locale: String = "en"): SpeciesInfoResponse? {
        val response = httpClient.get("api/species-info") {
            parameter("name", name)
            parameter("locale", locale)
        }
        return if (response.status.isSuccess()) response.body() else null
    }

    /** Common conditions a species is affected by indoors (AI, best-effort). */
    suspend fun speciesDiseases(
        name: String,
        common: String? = null,
        locale: String = "en",
    ): SpeciesDiseasesResponse? {
        val response = httpClient.get("api/species-diseases") {
            parameter("name", name)
            common?.let { parameter("common", it) }
            parameter("locale", locale)
        }
        return if (response.status.isSuccess()) response.body() else null
    }

    /** Flowering calendar + native range (AI, graceful available=false). */
    suspend fun speciesProfile(
        name: String,
        common: String? = null,
        locale: String = "en",
    ): SpeciesProfileResponse? {
        val response = httpClient.get("api/species-profile") {
            parameter("name", name)
            common?.let { parameter("common", it) }
            parameter("locale", locale)
            // Cache-buster for intermediaries: the endpoint sends a 7-day
            // Cache-Control, so a bumped value is what stops a proxy from
            // replaying a profile generated by an older server. Bumped to 4
            // alongside the server's v4 profile generation.
            parameter("profile_version", 4)
        }
        return if (response.status.isSuccess()) response.body() else null
    }

    // `POST api/care-plan` has no wrapper here on purpose: every care-plan
    // caller goes through ScanRepository.fetchCarePlan, which owns the
    // ResponseCache lookup and the status→typed-exception mapping.

    /** Personalized popular-plants bundle with images pre-resolved
     *  (graceful: null on any failure, caller falls back to the bundled list). */
    suspend fun popular(
        country: String? = null,
        region: String? = null,
        lat: Double? = null,
        lon: Double? = null,
        locale: String = "en",
    ): PopularResponse? {
        val response = httpClient.get("api/popular") {
            country?.let { parameter("country", it) }
            region?.let { parameter("region", it) }
            lat?.let { parameter("lat", it) }
            lon?.let { parameter("lon", it) }
            parameter("locale", locale)
        }
        return if (response.status.isSuccess()) response.body() else null
    }

    private companion object {
        /** Generous enough for a VPN tunnel's first round-trip after idling,
         *  short enough that a dead network is noticed promptly. The observer
         *  needs two of these to fail before it will call the device offline. */
        const val PROBE_TIMEOUT_MS = 5_000L
    }
}
