package com.plantdiseases.app.data.remote

import android.content.Context
import com.plantdiseases.app.BuildConfig
import com.plantdiseases.app.data.model.AnalysisResponse
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Response
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import retrofit2.http.*
import java.util.concurrent.TimeUnit

interface ApiService {

    @Multipart
    @POST("api/analyze")
    suspend fun analyzeImage(
        @Part image: MultipartBody.Part
    ): Response<AnalysisResponse>

    @GET("api/health")
    suspend fun healthCheck(): Response<Map<String, Any>>
}

class PlantApiClient(context: Context) {

    val apiService: ApiService

    init {
        val logging = HttpLoggingInterceptor().apply {
            level = HttpLoggingInterceptor.Level.BODY
        }

        val client = OkHttpClient.Builder()
            .connectTimeout(30, TimeUnit.SECONDS)
            .readTimeout(60, TimeUnit.SECONDS)
            .writeTimeout(60, TimeUnit.SECONDS)
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
