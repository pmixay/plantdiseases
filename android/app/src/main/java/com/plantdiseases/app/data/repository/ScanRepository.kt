package com.plantdiseases.app.data.repository

import com.google.gson.Gson
import com.plantdiseases.app.data.local.ScanDao
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.model.AnalysisResponse
import com.plantdiseases.app.data.model.CarePlanRequest
import com.plantdiseases.app.data.model.CarePlanResponse
import com.plantdiseases.app.data.model.ChatRequest
import com.plantdiseases.app.data.model.ChatResponse
import com.plantdiseases.app.data.model.FeedbackRequest
import com.plantdiseases.app.data.model.FeedbackResponse
import com.plantdiseases.app.data.model.HealthResponse
import com.plantdiseases.app.data.model.IdentifyResponse
import com.plantdiseases.app.data.model.ProgressionRequest
import com.plantdiseases.app.data.model.ProgressionResponse
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

    /**
     * Send image to server for analysis. `speciesHint` is optional —
     * when provided, the server's pet-toxicity surface can resolve
     * against its ASPCA-derived table; without it the response carries
     * `toxicity_source = "unknown"`.
     */
    suspend fun analyzeImage(imageFile: File, speciesHint: String? = null): Result<AnalysisResponse> =
        withContext(Dispatchers.IO) {
            try {
                val requestBody = imageFile.asRequestBody("image/jpeg".toMediaTypeOrNull())
                val part = MultipartBody.Part.createFormData("image", imageFile.name, requestBody)
                val response = apiService.analyzeImage(part, speciesHint?.takeIf { it.isNotBlank() })
                if (response.isSuccessful && response.body() != null) {
                    Result.success(response.body()!!)
                } else if (response.code() == 429) {
                    Result.failure(RateLimitException())
                } else if (response.code() == 503) {
                    Result.failure(ServerStartingException())
                } else if (response.code() == 400 || response.code() == 415) {
                    Result.failure(UnsupportedImageException())
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

    /**
     * Ask the server's PlantNet proxy to identify the species in `imageFile`.
     *
     * The server normalises the "no API key / quota / upstream error" cases
     * into a 200 OK with `available=false`, so a `Result.success` here may
     * still mean "no candidates" — callers must check `IdentifyResponse.available`.
     */
    suspend fun identifySpecies(imageFile: File, organ: String = "leaf"): Result<IdentifyResponse> =
        withContext(Dispatchers.IO) {
            try {
                val body = imageFile.asRequestBody("image/jpeg".toMediaTypeOrNull())
                val part = MultipartBody.Part.createFormData("image", imageFile.name, body)
                val response = apiService.identifySpecies(part, organ)
                if (response.isSuccessful && response.body() != null) {
                    Result.success(response.body()!!)
                } else if (response.code() == 429) {
                    Result.failure(RateLimitException())
                } else if (response.code() == 503) {
                    Result.failure(ServerStartingException())
                } else if (response.code() == 400 || response.code() == 415) {
                    Result.failure(UnsupportedImageException())
                } else {
                    Result.failure(Exception("Server error: ${response.code()}"))
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

    /**
     * Fetch a watering / fertilising / humidity plan for a given disease +
     * species + light tier. Always returns a usable plan when the server is
     * reachable; LLM polish (`summaryMd`) is best-effort.
     */
    suspend fun fetchCarePlan(request: CarePlanRequest): Result<CarePlanResponse> =
        withContext(Dispatchers.IO) {
            try {
                val response = apiService.carePlan(request)
                if (response.isSuccessful && response.body() != null) {
                    Result.success(response.body()!!)
                } else if (response.code() == 503) {
                    Result.failure(ServerStartingException())
                } else {
                    Result.failure(Exception("Server error: ${response.code()}"))
                }
            } catch (e: SocketTimeoutException) {
                Result.failure(ServerTimeoutException())
            } catch (e: UnknownHostException) {
                Result.failure(NoNetworkException())
            } catch (e: Exception) {
                Result.failure(e)
            }
        }

    /**
     * Send a single AI Plant Doctor turn. The server returns 200 OK
     * even when no API key is configured or the per-session budget is
     * exhausted; in that case the response carries `available=false`
     * and a `reason` / `fallback` string for the UI to display.
     *
     * Network-level failures (no connectivity, timeout, unexpected
     * status) come back as `Result.failure` so the caller can show a
     * retry CTA.
     */
    suspend fun sendChat(request: ChatRequest): Result<ChatResponse> =
        withContext(Dispatchers.IO) {
            try {
                val response = apiService.chat(request)
                if (response.isSuccessful && response.body() != null) {
                    Result.success(response.body()!!)
                } else if (response.code() == 429) {
                    Result.failure(RateLimitException())
                } else if (response.code() == 503) {
                    Result.failure(ServerStartingException())
                } else if (response.code() == 400) {
                    // Server rejected our payload (oversize diagnosis,
                    // empty message, etc.). Surface as a generic
                    // failure — the UI keeps a chat-level error toast.
                    Result.failure(Exception("Bad request"))
                } else {
                    Result.failure(Exception("Server error: ${response.code()}"))
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

    /**
     * Compare the current scan to a previous one and ask the server
     * for a worsening / improving / stable verdict. Both arguments
     * should be the analyse-shaped dicts the client already has
     * (typically built via `scanToProgressionPayload(scan)`).
     */
    /**
     * Submit a thumbs-up / thumbs-down on a diagnosis.
     *
     * The server's `/api/feedback` endpoint is append-only and
     * rate-limited per IP — if the client posts faster than 1/sec the
     * request lands as a 429 and we surface a RateLimitException so
     * the UI can show a brief "try again in a moment" toast.
     */
    suspend fun submitFeedback(request: FeedbackRequest): Result<FeedbackResponse> =
        withContext(Dispatchers.IO) {
            try {
                val response = apiService.feedback(request)
                if (response.isSuccessful && response.body() != null) {
                    Result.success(response.body()!!)
                } else if (response.code() == 429) {
                    Result.failure(RateLimitException())
                } else if (response.code() == 503) {
                    Result.failure(ServerStartingException())
                } else if (response.code() == 422) {
                    Result.failure(Exception("Invalid feedback payload"))
                } else {
                    Result.failure(Exception("Server error: ${response.code()}"))
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

    suspend fun fetchProgression(request: ProgressionRequest): Result<ProgressionResponse> =
        withContext(Dispatchers.IO) {
            try {
                val response = apiService.progression(request)
                if (response.isSuccessful && response.body() != null) {
                    Result.success(response.body()!!)
                } else {
                    Result.failure(Exception("Server error: ${response.code()}"))
                }
            } catch (e: SocketTimeoutException) {
                Result.failure(ServerTimeoutException())
            } catch (e: UnknownHostException) {
                Result.failure(NoNetworkException())
            } catch (e: Exception) {
                Result.failure(e)
            }
        }

    /**
     * Build a server-shaped scan dict from a stored ScanEntity. Used
     * when constructing chat `diagnosis` context and progression
     * payloads — both endpoints accept the analyse response shape so
     * the same builder serves both.
     */
    fun scanToProgressionPayload(scan: ScanEntity): Map<String, Any?> {
        val classKey = topClassKey(scan)
            ?: scan.diseaseName.lowercase().replace(' ', '_')
        return mapOf(
            "class_name" to classKey,
            "disease_name" to scan.diseaseName,
            "confidence" to scan.confidence.toDouble(),
            "is_healthy" to scan.isHealthy,
            "severity" to severityHint(scan),
            "warnings" to parseWarnings(scan.warnings),
        )
    }

    /**
     * Coarse client-side severity hint. The server's `/api/analyze`
     * carries an authoritative `severity` field, but cached scans
     * only have it when the row is post-v2.0 — for older rows we
     * derive a low/medium/high estimate from confidence + healthy.
     */
    private fun severityHint(scan: ScanEntity): String = when {
        scan.isHealthy -> "low"
        scan.confidence >= 0.85f -> "high"
        scan.confidence >= 0.65f -> "medium"
        else -> "low"
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
    class ServerStartingException : Exception("Server is starting / pipeline unavailable")
    class UnsupportedImageException : Exception("Unsupported image format")

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
                warnings = warningsJson,
                toxicToPets = result.toxicToPets,
                toxicSpeciesList = result.toxicSpeciesList
                    .takeIf { it.isNotEmpty() }
                    ?.let { gson.toJson(it) },
                toxicitySource = result.toxicitySource,
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
        isNotAPlant = isNotAPlantScan(this),
        timestamp = timestamp
    )

    /** Persisted top-class key from `all_probs`; null when not stored. */
    fun topClassKey(scan: ScanEntity): String? =
        parseAllProbs(scan.allProbs).entries.maxByOrNull { it.value }?.key

    /**
     * NAP detection: prefer the canonical class key from `all_probs`;
     * fall back to the localised display name (covers legacy scans
     * without `all_probs`).
     */
    fun isNotAPlantScan(scan: ScanEntity): Boolean {
        if (topClassKey(scan) == "not_a_plant") return true
        return scan.diseaseName.equals("Not a Plant", ignoreCase = true) ||
            scan.diseaseNameRu == "Не растение"
    }

    /**
     * "Healthy" verdict for the result UI. Uses the persisted top-class
     * key (locale-independent) so the status badge can never contradict
     * the diagnosis text. Falls back to the server's `is_healthy` flag
     * for legacy rows that pre-date `all_probs` persistence.
     */
    fun isHealthyVerdict(scan: ScanEntity): Boolean = when (topClassKey(scan)) {
        "healthy" -> true
        null -> scan.isHealthy
        else -> false
    }

    /**
     * Stats reflect the user's plant-care reality, not the raw row count.
     *
     * `not_a_plant` rows are surfaced as their own category (not folded
     * into the diseased count) and are excluded from the "most common
     * disease" rollup — otherwise a misfired scan or two would crown
     * "Not a Plant" as the user's most common problem.
     *
     * `totalScans` here is the count of *plant* scans (healthy + real
     * disease). `notAPlantCount` is reported separately so the UI can
     * decide whether to show it.
     */
    suspend fun getStats(): ScanStats = withContext(Dispatchers.IO) {
        val all = scanDao.getAllScans()
        var notAPlant = 0
        var healthy = 0
        var diseased = 0
        val diseaseEnCounts = mutableMapOf<String, Int>()
        val diseaseRuByEn = mutableMapOf<String, String>()
        for (s in all) {
            when {
                isNotAPlantScan(s) -> notAPlant++
                s.isHealthy -> healthy++
                else -> {
                    diseased++
                    diseaseEnCounts[s.diseaseName] =
                        (diseaseEnCounts[s.diseaseName] ?: 0) + 1
                    diseaseRuByEn[s.diseaseName] = s.diseaseNameRu
                }
            }
        }
        val topEn = diseaseEnCounts.maxByOrNull { it.value }?.key
        val topRu = topEn?.let { diseaseRuByEn[it] }
        ScanStats(
            totalScans = healthy + diseased,
            healthyCount = healthy,
            diseasedCount = diseased,
            notAPlantCount = notAPlant,
            mostCommonDisease = topEn,
            mostCommonDiseaseRu = topRu,
        )
    }

    /** Clear all scan history */
    suspend fun clearAll() = withContext(Dispatchers.IO) {
        scanDao.deleteAll()
    }

    data class ScanStats(
        val totalScans: Int,
        val healthyCount: Int,
        val diseasedCount: Int,
        val notAPlantCount: Int,
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

    /** Parse the JSON-encoded toxic-species list (cats/dogs/horses/...). */
    fun parseToxicSpeciesList(json: String?): List<String> {
        if (json.isNullOrEmpty()) return emptyList()
        return try {
            gson.fromJson(json, Array<String>::class.java).toList()
        } catch (e: Exception) {
            emptyList()
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
