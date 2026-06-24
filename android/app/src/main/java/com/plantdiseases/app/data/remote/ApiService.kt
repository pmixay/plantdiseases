package com.plantdiseases.app.data.remote

import com.plantdiseases.app.BuildConfig
import com.plantdiseases.app.data.model.CarePlanRequest
import com.plantdiseases.app.data.model.CarePlanResponse
import com.plantdiseases.app.data.model.SymptomDiagnoseRequest
import com.plantdiseases.app.data.model.SymptomDiagnoseResponse
import com.plantdiseases.app.data.model.ChatRequest
import com.plantdiseases.app.data.model.ChatResponse
import com.plantdiseases.app.data.model.CommunityOpinionRequest
import com.plantdiseases.app.data.model.CommunityOpinionsResponse
import com.plantdiseases.app.data.model.CommunityRequestOpinionRequest
import com.plantdiseases.app.data.model.CommunityRequestOpinionResponse
import com.plantdiseases.app.data.model.AquaManifestResponse
import com.plantdiseases.app.data.model.CapabilitiesResponse
import com.plantdiseases.app.data.model.CommunityScansResponse
import com.plantdiseases.app.data.model.MyScanOpinionsResponse
import com.plantdiseases.app.data.model.CommunityScanUploadResponse
import com.plantdiseases.app.data.model.CommunityViewRequest
import com.plantdiseases.app.data.model.PushRegisterRequest
import com.plantdiseases.app.data.model.CommunityScanCountsRequest
import com.plantdiseases.app.data.model.CommunityScanCountsResponse
import com.plantdiseases.app.data.model.SpeciesDiseasesResponse
import com.plantdiseases.app.data.model.SpeciesInfoResponse
import com.plantdiseases.app.data.model.FeedbackRequest
import com.plantdiseases.app.data.model.FeedbackResponse
import com.plantdiseases.app.data.model.HealthResponse
import com.plantdiseases.app.data.model.IdentifyResponse
import com.plantdiseases.app.data.model.ProgressionRequest
import com.plantdiseases.app.data.model.ProgressionResponse
import com.plantdiseases.app.data.model.TrendingResponse
import com.plantdiseases.app.util.ServerConfig
import io.ktor.client.HttpClient
import io.ktor.client.engine.okhttp.OkHttp
import io.ktor.client.plugins.contentnegotiation.ContentNegotiation
import io.ktor.client.plugins.defaultRequest
import io.ktor.client.request.url
import io.ktor.serialization.kotlinx.json.json
import kotlinx.serialization.json.Json
import okhttp3.Interceptor
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Response
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import okhttp3.RequestBody
import okhttp3.ResponseBody
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.Multipart
import retrofit2.http.POST
import retrofit2.http.Streaming
import retrofit2.http.Part
import retrofit2.http.Path
import retrofit2.http.Query
import java.io.IOException
import java.util.concurrent.TimeUnit

interface ApiService {

    // NOTE: /api/analyze has migrated to the Ktor client (see PlantApiClient
    // .httpClient and ScanRepository.analyzeImage). Per the migration plan,
    // a Retrofit endpoint is removed once its last call-site is gone — this
    // was that endpoint's only caller.

    /** Per-family availability so the scan-screen picker can grey out a
     *  tier the server doesn't serve (notably frontier when disabled). */
    @GET("api/capabilities")
    suspend fun capabilities(): retrofit2.Response<CapabilitiesResponse>

    /** Manifest for the hosted Aqua on-device model (version + sha + sidecar),
     *  so the app downloads/updates it only when the bytes change. */
    @GET("api/aqua/manifest")
    suspend fun aquaManifest(): retrofit2.Response<AquaManifestResponse>

    /** Stream the Aqua TFLite model bytes (multi-MB — @Streaming avoids
     *  buffering the whole body in memory). */
    @Streaming
    @GET("api/aqua/model")
    suspend fun aquaModel(): retrofit2.Response<ResponseBody>

    @GET("api/health")
    suspend fun healthCheck(): retrofit2.Response<HealthResponse>

    @POST("api/chat")
    suspend fun chat(@Body request: ChatRequest): retrofit2.Response<ChatResponse>

    @Multipart
    @POST("api/identify")
    suspend fun identifySpecies(
        @Part image: MultipartBody.Part,
        @Query("organ") organ: String = "leaf",
        @Query("locale") locale: String = "en",
    ): retrofit2.Response<IdentifyResponse>

    @POST("api/care-plan")
    suspend fun carePlan(@Body request: CarePlanRequest): retrofit2.Response<CarePlanResponse>

    /** No-photo symptom triage. Returns a ranked shortlist of likely
     *  conditions (each a full bilingual care card) for the symptom
     *  questionnaire shown when the camera/ML can't get a confident read. */
    @POST("api/symptom-diagnose")
    suspend fun symptomDiagnose(
        @Body request: SymptomDiagnoseRequest,
    ): retrofit2.Response<SymptomDiagnoseResponse>

    @POST("api/progression")
    suspend fun progression(@Body request: ProgressionRequest): retrofit2.Response<ProgressionResponse>

    @POST("api/feedback")
    suspend fun feedback(@Body request: FeedbackRequest): retrofit2.Response<FeedbackResponse>

    /**
     * Upload the original image bytes for a negative-feedback row.
     * Called immediately after a successful ``correct=false``
     * /api/feedback POST. The server stores the bytes under
     * ``feedback_images/<sha256>.<ext>`` and backfills the
     * ``image_path`` column on the matching feedback row.
     */
    @Multipart
    @POST("api/feedback/image")
    suspend fun feedbackImage(
        @Part("scan_id") scanId: RequestBody,
        @Part image: MultipartBody.Part,
    ): retrofit2.Response<okhttp3.ResponseBody>

    /**
     * Upload one community-shared scan. ``image`` is optional — when
     * omitted, the server records the row without a thumbnail and the
     * carousel renders a fallback icon. All other parts are form
     * fields validated by ``CommunityScanRequest`` on the server.
     */
    @Multipart
    @POST("api/community/scan")
    suspend fun communityScanUpload(
        @Part("scan_id") scanId: RequestBody,
        @Part("user_pseudo_id") userPseudoId: RequestBody,
        @Part("country") country: RequestBody,
        @Part("region") region: RequestBody? = null,
        @Part("disease") disease: RequestBody,
        @Part("confidence") confidence: RequestBody? = null,
        @Part("species") species: RequestBody? = null,
        @Part("species_score") speciesScore: RequestBody? = null,
        @Part("locale") locale: RequestBody? = null,
        @Part image: MultipartBody.Part? = null,
    ): retrofit2.Response<CommunityScanUploadResponse>

    @GET("api/community/nearby")
    suspend fun communityScanNearby(
        @Query("country") country: String,
        @Query("region") region: String? = null,
        @Query("disease") disease: String? = null,
        @Query("species") species: String? = null,
        @Query("limit") limit: Int = 12,
        @Query("user_pseudo_id") userPseudoId: String? = null,
    ): retrofit2.Response<CommunityScansResponse>

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

    /** List community opinions + consensus tally for a shared scan. */
    @GET("api/community/opinions/{scan_id}")
    suspend fun communityGetOpinions(
        @Path("scan_id") scanId: String,
    ): retrofit2.Response<CommunityOpinionsResponse>

    /** List the second opinions this install has authored. */
    @GET("api/community/my-opinions/{user_pseudo_id}")
    suspend fun communityMyOpinions(
        @Path("user_pseudo_id") userPseudoId: String,
    ): retrofit2.Response<CommunityOpinionsResponse>

    /** Opinion counts on the user's OWN shared scans — polled to notify when
     *  someone leaves a second opinion on a scan they shared. */
    @GET("api/community/my-scan-opinions/{user_pseudo_id}")
    suspend fun communityMyScanOpinions(
        @Path("user_pseudo_id") userPseudoId: String,
    ): retrofit2.Response<MyScanOpinionsResponse>

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

    /** Home-screen "trending nearby" module. Country/region optional — the
     *  server widens region → country → global until a scope has data, and
     *  never 422s, so the widget works on a fresh install with no location. */
    @GET("api/community/trending")
    suspend fun communityTrending(
        @Query("country") country: String? = null,
        @Query("region") region: String? = null,
        @Query("days") days: Int = 7,
        @Query("limit") limit: Int = 6,
    ): retrofit2.Response<TrendingResponse>

    /** Localized Wikipedia blurb + lead image for a scientific name. */
    @GET("api/species-info")
    suspend fun speciesInfo(
        @Query("name") name: String,
        @Query("locale") locale: String = "en",
    ): retrofit2.Response<SpeciesInfoResponse>

    /** Common conditions a species is affected by indoors (AI, best-effort).
     *  `available=false` is graceful — the client falls back to the bundled
     *  disease guide. */
    @GET("api/species-diseases")
    suspend fun speciesDiseases(
        @Query("name") name: String,
        @Query("common") common: String? = null,
        @Query("locale") locale: String = "en",
    ): retrofit2.Response<SpeciesDiseasesResponse>

    /** Community scans of one species (scientific name), across regions. */
    @GET("api/community/by-species")
    suspend fun communityBySpecies(
        @Query("species") species: String,
        @Query("limit") limit: Int = 12,
        @Query("user_pseudo_id") userPseudoId: String? = null,
    ): retrofit2.Response<CommunityScansResponse>
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
}
