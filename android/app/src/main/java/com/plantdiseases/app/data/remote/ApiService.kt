package com.plantdiseases.app.data.remote

import android.content.Context
import com.plantdiseases.app.BuildConfig
import com.plantdiseases.app.data.model.AnalysisResponse
import com.plantdiseases.app.data.model.HealthResponse
import com.plantdiseases.app.util.ServerConfig
import okhttp3.Interceptor
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Response
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import retrofit2.http.*
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

/**
 * Retrofit client. The base URL is resolved through [ServerConfig] on every
 * request so users can change the server address at runtime (Profile screen)
 * without restarting the app.
 */
class PlantApiClient(private val context: Context) {

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

    @Volatile private var cachedBaseUrl: String = ServerConfig.getBaseUrl(context)
    @Volatile private var cachedService: ApiService = buildService(cachedBaseUrl)

    /** Returns the up-to-date [ApiService], rebuilding Retrofit when the URL changes. */
    val apiService: ApiService
        get() {
            val current = ServerConfig.getBaseUrl(context)
            if (current != cachedBaseUrl) {
                synchronized(this) {
                    if (current != cachedBaseUrl) {
                        cachedBaseUrl = current
                        cachedService = buildService(current)
                    }
                }
            }
            return cachedService
        }

    private fun buildService(baseUrl: String): ApiService =
        Retrofit.Builder()
            .baseUrl(baseUrl)
            .client(client)
            .addConverterFactory(GsonConverterFactory.create())
            .build()
            .create(ApiService::class.java)
}
