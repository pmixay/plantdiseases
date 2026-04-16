package com.plantdiseases.app.data.remote

import android.content.Context
import com.plantdiseases.app.BuildConfig
import com.plantdiseases.app.data.model.AnalysisResponse
import com.plantdiseases.app.data.model.HealthResponse
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
 * OkHttp interceptor that retries failed requests with exponential backoff.
 * Only retries on network errors (IOException), not on HTTP error responses.
 * POST requests (e.g. /api/analyze) get at most 1 retry to avoid long waits
 * when uploading large images on slow connections.
 */
class RetryInterceptor(
    private val maxRetries: Int = 3,
    private val initialDelayMs: Long = 1000
) : Interceptor {

    @Throws(IOException::class)
    override fun intercept(chain: Interceptor.Chain): Response {
        val request = chain.request()
        val effectiveRetries = if (request.method == "POST") minOf(maxRetries, 1) else maxRetries
        var lastException: IOException? = null

        for (attempt in 0..effectiveRetries) {
            try {
                val response = chain.proceed(request)
                return response
            } catch (e: IOException) {
                lastException = e
                if (attempt < effectiveRetries) {
                    val delay = initialDelayMs * (1L shl attempt) // 1s, 2s, 4s
                    try {
                        Thread.sleep(delay)
                    } catch (_: InterruptedException) {
                        Thread.currentThread().interrupt()
                        throw e
                    }
                }
            }
        }
        throw lastException!!
    }
}

class PlantApiClient(context: Context) {

    val apiService: ApiService

    init {
        val logging = HttpLoggingInterceptor().apply {
            level = if (BuildConfig.DEBUG) {
                HttpLoggingInterceptor.Level.BODY
            } else {
                HttpLoggingInterceptor.Level.NONE
            }
        }

        val client = OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(60, TimeUnit.SECONDS)
            .writeTimeout(60, TimeUnit.SECONDS)
            .addInterceptor(RetryInterceptor(maxRetries = 3, initialDelayMs = 1000))
            .addInterceptor(logging)
            .build()

        val retrofit = Retrofit.Builder()
            .baseUrl(BuildConfig.API_BASE_URL)
            .client(client)
            .addConverterFactory(GsonConverterFactory.create())
            .build()

        apiService = retrofit.create(ApiService::class.java)
    }
}
