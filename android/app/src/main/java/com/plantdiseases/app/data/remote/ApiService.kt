package com.plantdiseases.app.data.remote

import com.plantdiseases.app.BuildConfig
import com.plantdiseases.app.data.model.AnalysisResponse
import com.plantdiseases.app.data.model.CarePlanRequest
import com.plantdiseases.app.data.model.CarePlanResponse
import com.plantdiseases.app.data.model.ChatRequest
import com.plantdiseases.app.data.model.ChatResponse
import com.plantdiseases.app.data.model.HealthResponse
import com.plantdiseases.app.data.model.IdentifyResponse
import com.plantdiseases.app.util.ServerConfig
import okhttp3.Interceptor
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Response
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.Multipart
import retrofit2.http.POST
import retrofit2.http.Part
import retrofit2.http.Query
import java.io.IOException
import java.util.concurrent.TimeUnit

interface ApiService {

    @Multipart
    @POST("api/analyze")
    suspend fun analyzeImage(
        @Part image: MultipartBody.Part
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
    ): retrofit2.Response<IdentifyResponse>

    @POST("api/care-plan")
    suspend fun carePlan(@Body request: CarePlanRequest): retrofit2.Response<CarePlanResponse>
}

/**
 * Retries on transient network failures and on HTTP 429 / 503, honouring the
 * Retry-After header when present. POST requests are retried at most once to
 * avoid re-uploading large payloads on slow connections.
 */
class RetryInterceptor(
    private val maxRetries: Int = 3,
    private val initialDelayMs: Long = 1000,
    private val maxDelayMs: Long = 8000
) : Interceptor {

    @Throws(IOException::class)
    override fun intercept(chain: Interceptor.Chain): Response {
        val request = chain.request()
        val effectiveRetries = if (request.method == "POST") minOf(maxRetries, 1) else maxRetries
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
                lastException = e
                if (attempt >= effectiveRetries) throw e
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

/** Retrofit client using the build-time API base URL. */
class PlantApiClient {

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
            .baseUrl(ServerConfig.baseUrl)
            .client(client)
            .addConverterFactory(GsonConverterFactory.create())
            .build()
            .create(ApiService::class.java)
    }
}
