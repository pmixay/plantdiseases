package com.plantdiseases.app.data.remote

import com.plantdiseases.app.BuildConfig
import com.plantdiseases.app.data.model.CarePlanRequest
import com.plantdiseases.app.data.model.CarePlanResponse
import com.plantdiseases.app.data.model.CommunityOpinionRequest
import com.plantdiseases.app.data.model.CommunityOpinionsResponse
import com.plantdiseases.app.data.model.CommunityRequestOpinionRequest
import com.plantdiseases.app.data.model.CommunityRequestOpinionResponse
import com.plantdiseases.app.data.model.CommunityViewRequest
import com.plantdiseases.app.data.model.PushRegisterRequest
import com.plantdiseases.app.data.model.CommunityScanCountsRequest
import com.plantdiseases.app.data.model.CommunityScanCountsResponse
import com.plantdiseases.app.data.model.SpeciesDiseasesResponse
import com.plantdiseases.app.data.model.SpeciesInfoResponse
import com.plantdiseases.app.data.model.FeedbackResponse
import com.plantdiseases.app.data.model.TrendingResponse
import com.plantdiseases.app.util.ServerConfig
import io.ktor.client.HttpClient
import io.ktor.client.call.body
import io.ktor.client.engine.okhttp.OkHttp
import io.ktor.client.plugins.contentnegotiation.ContentNegotiation
import io.ktor.client.plugins.defaultRequest
import io.ktor.client.request.get
import io.ktor.client.request.parameter
import io.ktor.client.request.post
import io.ktor.client.request.setBody
import io.ktor.client.request.url
import io.ktor.http.ContentType
import io.ktor.http.contentType
import io.ktor.http.isSuccess
import io.ktor.serialization.kotlinx.json.json
import kotlinx.serialization.json.Json
import okhttp3.Interceptor
import okhttp3.OkHttpClient
import okhttp3.Response
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import okhttp3.ResponseBody
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query
import java.io.IOException
import java.util.concurrent.TimeUnit

interface ApiService {

    // NOTE: /api/analyze has migrated to the Ktor client (see PlantApiClient
    // .httpClient and ScanRepository.analyzeImage). Per the migration plan,
    // a Retrofit endpoint is removed once its last call-site is gone — this
    // was that endpoint's only caller.

    // GET api/capabilities migrated to Ktor (ScanRepository.fetchCapabilities).

    // GET api/aqua/manifest migrated to Ktor (ScanRepository.syncAquaModel).
    // GET api/aqua/model (@Streaming) migrated to Ktor (ScanRepository.syncAquaModel,
    // streamed via bodyAsChannel().toInputStream()).

    // GET api/health migrated to Ktor (ScanRepository.checkServerHealth).

    // POST api/chat migrated to Ktor (ScanRepository.sendChat).

    // POST api/identify (multipart) migrated to Ktor (ScanRepository.identifySpecies).

    // POST api/care-plan migrated to Ktor (PlantApiClient.carePlan +
    // repo fetchCarePlan, which keeps the ResponseCache + 503 mapping).

    // POST api/symptom-diagnose migrated to Ktor (ScanRepository.diagnoseSymptoms).

    // POST api/progression migrated to Ktor (ScanRepository.fetchProgression).

    // POST api/feedback migrated to Ktor (ScanRepository.submitFeedback).

    // POST api/feedback/image (multipart) migrated to Ktor (ScanRepository.uploadFeedbackImage).
    // POST api/community/scan (multipart) migrated to Ktor (ScanRepository.uploadCommunityScan).

    // GET api/community/nearby migrated to Ktor (ScanRepository.communityNearby).

    @DELETE("api/community/user/{user_pseudo_id}")
    suspend fun communityScanDeleteUser(
        @Path("user_pseudo_id") userPseudoId: String,
    ): retrofit2.Response<FeedbackResponse>

    /** Submit a community "second opinion" on a shared scan. */
    @POST("api/community/opinion")
    suspend fun communitySubmitOpinion(
        @Body request: CommunityOpinionRequest,
    ): retrofit2.Response<CommunityOpinionsResponse>

    /** Flag the scanner's OWN scan as wanting a community second opinion.
     *  The scanner doesn't suggest a diagnosis — they wait for others. */
    @POST("api/community/request-opinion")
    suspend fun communityRequestOpinion(
        @Body request: CommunityRequestOpinionRequest,
    ): retrofit2.Response<CommunityRequestOpinionResponse>

    // GET api/community/opinions/{scan_id} migrated to Ktor (ScanRepository.getCommunityOpinions).
    // GET api/community/my-opinions/{id} migrated to Ktor (ScanRepository.getMyCommunityOpinions).
    // GET api/community/my-scan-opinions/{id} migrated to Ktor (ScanRepository.fetchMyScanOpinions).

    /** Register this install's FCM token so the server can push opinion
     *  notifications to a closed / idle device. */
    @POST("api/push/register")
    suspend fun registerPushToken(
        @Body request: PushRegisterRequest,
    ): retrofit2.Response<ResponseBody>

    /** Record that the caller opened someone else's shared scan. Deduped per
     *  viewer server-side, so it's safe to fire on every open. */
    @POST("api/community/view")
    suspend fun communityRecordView(
        @Body request: CommunityViewRequest,
    ): retrofit2.Response<ResponseBody>

    /** Batched {opinions, views} per scan_id — used to badge the caller's own
     *  rows in the Gallery in a single round-trip. */
    @POST("api/community/scan-counts")
    suspend fun communityScanCounts(
        @Body request: CommunityScanCountsRequest,
    ): retrofit2.Response<CommunityScanCountsResponse>

    // GET api/community/trending, api/species-info, api/species-diseases
    // migrated to Ktor (PlantApiClient.communityTrending / .speciesInfo /
    // .speciesDiseases — best-effort body-or-null wrappers used by UI).

    // GET api/community/by-species migrated to Ktor (ScanRepository.communityScansBySpecies).
}

/**
 * Retries on transient network failures and on HTTP 429 / 503, honouring the
 * Retry-After header when present. POST requests are retried at most once to
 * avoid re-uploading large payloads on slow connections.
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
        val effectiveRetries = if (isPost) minOf(maxRetries, 1) else maxRetries
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
                // community rows or re-run analyze. POSTs still retry on an
                // explicit 429/503, where the server signals it did NOT
                // process the request. GETs retry freely (idempotent).
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
 * Retrofit client. Defaults to the build-time API base URL; the
 * constructor parameter is a test seam so unit/integration tests can
 * point the whole stack at a local MockWebServer without a device.
 */
class PlantApiClient(
    private val baseUrl: String = ServerConfig.baseUrl,
) {

    private val client: OkHttpClient = run {
        val logging = HttpLoggingInterceptor().apply {
            level = if (BuildConfig.DEBUG) {
                HttpLoggingInterceptor.Level.BODY
            } else {
                HttpLoggingInterceptor.Level.NONE
            }
        }
        OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(60, TimeUnit.SECONDS)
            .writeTimeout(60, TimeUnit.SECONDS)
            .addInterceptor(RetryInterceptor(maxRetries = 3, initialDelayMs = 1000))
            .addInterceptor(logging)
            .build()
    }

    val apiService: ApiService by lazy {
        Retrofit.Builder()
            .baseUrl(baseUrl)
            .client(client)
            .addConverterFactory(GsonConverterFactory.create())
            .build()
            .create(ApiService::class.java)
    }

    /**
     * Ktor client for endpoints migrated off Retrofit. It reuses the same
     * preconfigured [client] (so the RetryInterceptor, logging and timeouts
     * are shared, not duplicated) and resolves relative paths against the same
     * [baseUrl] test seam. `expectSuccess = false` keeps non-2xx responses as
     * values so the repository can map HTTP status → typed exceptions itself,
     * exactly as it did with Retrofit's `Response`.
     */
    val httpClient: HttpClient by lazy {
        HttpClient(OkHttp) {
            engine { preconfigured = client }
            expectSuccess = false
            install(ContentNegotiation) {
                json(Json { ignoreUnknownKeys = true })
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

    /** Care plan for a (disease, species, light, locale). Best-effort
     *  body-or-null, no caching (the repository's fetchCarePlan owns the
     *  ResponseCache + status→exception mapping; this is the UI's direct,
     *  uncached lookup on the species screen). */
    suspend fun carePlan(request: CarePlanRequest): CarePlanResponse? {
        val response = httpClient.post("api/care-plan") {
            contentType(ContentType.Application.Json)
            setBody(request)
        }
        return if (response.status.isSuccess()) response.body() else null
    }

    /** Trending species/diseases for a region (graceful, never 422s). */
    suspend fun communityTrending(
        country: String? = null,
        region: String? = null,
        days: Int = 7,
        limit: Int = 6,
    ): TrendingResponse? {
        val response = httpClient.get("api/community/trending") {
            country?.let { parameter("country", it) }
            region?.let { parameter("region", it) }
            parameter("days", days)
            parameter("limit", limit)
        }
        return if (response.status.isSuccess()) response.body() else null
    }
}
