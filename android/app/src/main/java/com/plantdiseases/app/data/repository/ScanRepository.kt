package com.plantdiseases.app.data.repository

import com.google.gson.Gson
import com.plantdiseases.app.data.local.ScanDao
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.model.AnalysisResponse
import com.plantdiseases.app.data.model.HealthResponse
import com.plantdiseases.app.data.model.ScanHistoryItem
import com.plantdiseases.app.data.remote.ApiService
import com.plantdiseases.app.data.remote.PlantApiClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.MultipartBody
import okhttp3.RequestBody.Companion.asRequestBody
import java.io.File
import java.net.SocketTimeoutException
import java.net.UnknownHostException

class ScanRepository(
    private val scanDao: ScanDao,
    private val apiClient: PlantApiClient,
) {
    private val apiService: ApiService
        get() = apiClient.apiService

    private val gson = Gson()

    /** Send image to server for analysis */
    suspend fun analyzeImage(imageFile: File): Result<AnalysisResponse> =
        withContext(Dispatchers.IO) {
            try {
                val requestBody = imageFile.asRequestBody("image/jpeg".toMediaTypeOrNull())
                val part = MultipartBody.Part.createFormData("image", imageFile.name, requestBody)
                val response = apiService.analyzeImage(part)
                if (response.isSuccessful && response.body() != null) {
                    Result.success(response.body()!!)
                } else if (response.code() == 429) {
                    Result.failure(RateLimitException())
                } else {
                    Result.failure(Exception("Server error: ${response.code()} ${response.message()}"))
                }
            } catch (e: SocketTimeoutException) {
                Result.failure(ServerTimeoutException())
            } catch (e: UnknownHostException) {
                Result.failure(NoNetworkException())
            } catch (e: java.net.ConnectException) {
                Result.failure(ServerTimeoutException())
            } catch (e: Exception) {
                Result.failure(e)
            }
        }

    /** Check server health */
    suspend fun checkServerHealth(): Result<HealthResponse> =
        withContext(Dispatchers.IO) {
            try {
                val response = apiService.healthCheck()
                if (response.isSuccessful && response.body() != null) {
                    Result.success(response.body()!!)
                } else {
                    Result.failure(Exception("Server error: ${response.code()}"))
                }
            } catch (e: SocketTimeoutException) {
                Result.failure(ServerTimeoutException())
            } catch (e: Exception) {
                Result.failure(e)
            }
        }

    class ServerTimeoutException : Exception("Server is not responding")
    class NoNetworkException : Exception("No network connection")
    class RateLimitException : Exception("Server is busy, please wait")

    /** Save analysis result to local DB */
    suspend fun saveScan(imagePath: String, result: AnalysisResponse): Long =
        withContext(Dispatchers.IO) {
            val detection = result.detection
            val regions = detection?.regions ?: emptyList()
            val primary = detection?.primaryRegion
            val primaryIndex = if (primary == null) null
                else regions.indexOfFirst {
                    it.x == primary.x && it.y == primary.y &&
                        it.width == primary.width && it.height == primary.height
                }.takeIf { it >= 0 }
            val warningsJson = result.warnings
                ?.takeIf { it.isNotEmpty() }
                ?.let { gson.toJson(it) }
            val entity = ScanEntity(
                imagePath = imagePath,
                diseaseName = result.diseaseName,
                diseaseNameRu = result.diseaseNameRu,
                confidence = result.confidence,
                description = result.description,
                descriptionRu = result.descriptionRu,
                treatment = gson.toJson(result.treatment),
                treatmentRu = gson.toJson(result.treatmentRu),
                prevention = gson.toJson(result.prevention),
                preventionRu = gson.toJson(result.preventionRu),
                isHealthy = result.isHealthy,
                regions = if (regions.isEmpty()) null else gson.toJson(regions),
                primaryRegionIndex = primaryIndex,
                allProbs = result.allProbs?.let { gson.toJson(it) },
                warnings = warningsJson
            )
            scanDao.insert(entity)
        }

    /** Parse stored regions JSON back into a list of DetectionRegion. */
    fun parseRegions(json: String?): List<com.plantdiseases.app.data.model.DetectionRegion> {
        if (json.isNullOrEmpty()) return emptyList()
        return try {
            gson.fromJson(
                json,
                Array<com.plantdiseases.app.data.model.DetectionRegion>::class.java,
            ).toList()
        } catch (e: Exception) {
            emptyList()
        }
    }

    /** Get recent scan history */
    suspend fun getRecentScans(limit: Int = 20): List<ScanHistoryItem> =
        withContext(Dispatchers.IO) {
            scanDao.getRecentScans(limit).map { it.toHistoryItem() }
        }

    /** Get all scans */
    suspend fun getAllScans(): List<ScanHistoryItem> =
        withContext(Dispatchers.IO) {
            scanDao.getAllScans().map { it.toHistoryItem() }
        }

    /** Get full scan details */
    suspend fun getScanById(id: Long): ScanEntity? =
        withContext(Dispatchers.IO) {
            scanDao.getScanById(id)
        }

    /** Delete a scan */
    suspend fun deleteScan(id: Long) = withContext(Dispatchers.IO) {
        scanDao.deleteById(id)
    }

    /** Convert entity to display model */
    private fun ScanEntity.toHistoryItem() = ScanHistoryItem(
        id = id,
        imagePath = imagePath,
        diseaseName = diseaseName,
        diseaseNameRu = diseaseNameRu,
        confidence = confidence,
        isHealthy = isHealthy,
        timestamp = timestamp
    )

    /** Get scan statistics */
    suspend fun getStats(): ScanStats = withContext(Dispatchers.IO) {
        val total = scanDao.getCount()
        val healthy = scanDao.getHealthyCount()
        val diseased = scanDao.getDiseasedCount()
        val commonDisease = scanDao.getMostCommonDisease()
        val commonDiseaseRu = scanDao.getMostCommonDiseaseRu()
        ScanStats(total, healthy, diseased, commonDisease, commonDiseaseRu)
    }

    /** Clear all scan history */
    suspend fun clearAll() = withContext(Dispatchers.IO) {
        scanDao.deleteAll()
    }

    data class ScanStats(
        val totalScans: Int,
        val healthyCount: Int,
        val diseasedCount: Int,
        val mostCommonDisease: String?,
        val mostCommonDiseaseRu: String?
    )

    /** Parse treatment/prevention JSON arrays */
    fun parseStringList(json: String): List<String> {
        return try {
            gson.fromJson(json, Array<String>::class.java).toList()
        } catch (e: Exception) {
            emptyList()
        }
    }

    /** Parse all_probs JSON map */
    fun parseAllProbs(json: String?): Map<String, Float> {
        if (json.isNullOrEmpty()) return emptyMap()
        return try {
            val type = object : com.google.gson.reflect.TypeToken<Map<String, Float>>() {}.type
            gson.fromJson(json, type)
        } catch (e: Exception) {
            emptyMap()
        }
    }

    /** Parse pipeline warnings JSON array (flags like "uncertain_healthy"). */
    fun parseWarnings(json: String?): List<String> {
        if (json.isNullOrEmpty()) return emptyList()
        return try {
            gson.fromJson(json, Array<String>::class.java).toList()
        } catch (e: Exception) {
            emptyList()
        }
    }
}
