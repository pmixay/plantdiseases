package com.plantdiseases.app.data.remote

import com.plantdiseases.app.BuildConfig
import com.plantdiseases.app.data.model.AnalysisResponse
import com.plantdiseases.app.data.model.CarePlanRequest
import com.plantdiseases.app.data.model.CarePlanResponse
import com.plantdiseases.app.data.model.ChatRequest
import com.plantdiseases.app.data.model.ChatResponse
import com.plantdiseases.app.data.model.CommunityScansResponse
import com.plantdiseases.app.data.model.CommunityScanUploadResponse
import com.plantdiseases.app.data.model.FeedbackRequest
import com.plantdiseases.app.data.model.FeedbackResponse
import com.plantdiseases.app.data.model.HealthResponse
import com.plantdiseases.app.data.model.IdentifyResponse
import com.plantdiseases.app.data.model.ProgressionRequest
import com.plantdiseases.app.data.model.ProgressionResponse
import com.plantdiseases.app.util.ServerConfig
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
import retrofit2.http.Part
import retrofit2.http.Path
import retrofit2.http.Query
import java.io.IOException
import java.util.concurrent.TimeUnit

interface ApiService {

    @Multipart
    @POST("api/analyze")
    suspend fun analyzeImage(
        @Part image: MultipartBody.Part,
        @Query("species") species: String? = null,
        @Query("locale") locale: String = "en",
    ): retrofit2.Response<AnalysisResponse>

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

    /** Download a community thumbnail by filename. The full URL is
     *  built relative to ``ServerConfig.baseUrl`` — the Android client
     *  never gets an absolute URL from the server. */
    @GET("api/community/thumb/{filename}")
    suspend fun communityScanThumb(
        @Path("filename") filename: String,
    ): retrofit2.Response<ResponseBody>

    @DELETE("api/community/user/{user_pseudo_id}")
    suspend fun communityScanDeleteUser(
        @Path("user_pseudo_id") userPseudoId: String,
    ): retrofit2.Response<FeedbackResponse>
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
        var lastResponse: Response? = null

        for (attempt in 0..effectiveRetries) {
            lastResponse?.close()
            lastResponse = null

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
        lastResponse?.let { return it }
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
}
