package com.plantdiseases.app.data.repository

import com.google.gson.Gson
import com.plantdiseases.app.data.local.ScanChatMessageDao
import com.plantdiseases.app.data.local.ScanChatMessageEntity
import com.plantdiseases.app.data.local.ScanDao
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.model.AnalysisResponse
import com.plantdiseases.app.data.model.CapabilitiesResponse
import com.plantdiseases.app.data.model.CarePlanRequest
import com.plantdiseases.app.data.model.CarePlanResponse
import com.plantdiseases.app.data.model.ChatRequest
import com.plantdiseases.app.data.model.ChatResponse
import com.plantdiseases.app.data.model.CommunityOpinionRequest
import com.plantdiseases.app.data.model.CommunityOpinionsResponse
import com.plantdiseases.app.data.model.FeedbackRequest
import com.plantdiseases.app.data.model.FeedbackResponse
import com.plantdiseases.app.data.model.HealthResponse
import com.plantdiseases.app.data.model.IdentifyResponse
import com.plantdiseases.app.data.model.MyScanOpinionsResponse
import com.plantdiseases.app.data.model.ProgressionRequest
import com.plantdiseases.app.data.model.ProgressionResponse
import com.plantdiseases.app.data.model.ScanHistoryItem
import com.plantdiseases.app.data.remote.PlantApiClient
import com.plantdiseases.app.util.AquaModelManager
import io.ktor.client.call.body
import io.ktor.client.request.forms.MultiPartFormDataContent
import io.ktor.client.request.delete
import io.ktor.client.request.forms.formData
import io.ktor.client.request.get
import io.ktor.client.request.parameter
import io.ktor.client.request.post
import io.ktor.client.request.setBody
import io.ktor.client.request.url
import io.ktor.client.statement.bodyAsChannel
import io.ktor.utils.io.jvm.javaio.toInputStream
import io.ktor.http.ContentType
import io.ktor.http.Headers
import io.ktor.http.HttpHeaders
import io.ktor.http.contentType
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.MultipartBody
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.File
import java.net.SocketTimeoutException
import java.net.UnknownHostException

class ScanRepository(
    private val scanDao: ScanDao,
    private val apiClient: PlantApiClient,
    // Nullable so unit tests that exercise only the network / scan-history
    // surface don't need a Room database. Chat persistence quietly no-ops
    // when absent.
    private val scanChatMessageDao: ScanChatMessageDao? = null,
) {
    private val gson = Gson()

    // Serialises Aqua model installs so two concurrent syncAquaModel calls
    // (e.g. rapid config-change recreations each firing one) can't race the
    // shared temp/model file and corrupt it.
    private val aquaSyncMutex = Mutex()

    /**
     * Send image to server for analysis. `speciesHint` is optional —
     * when provided, the server's pet-toxicity surface can resolve
     * against its ASPCA-derived table; without it the response carries
     * `toxicity_source = "unknown"`.
     */
    suspend fun analyzeImage(
        imageFile: File,
        speciesHint: String? = null,
        locale: String = "en",
        family: String = "gamma",
    ): Result<AnalysisResponse> =
        withContext(Dispatchers.IO) {
            try {
                // Ktor multipart over the shared OkHttp engine. Reading the
                // bytes up front (rather than streaming the File) keeps the
                // body re-writable, so the OkHttp RetryInterceptor can replay
                // the POST once on an explicit 429/503 — the same behaviour the
                // Retrofit File-backed body had.
                val imageBytes = imageFile.readBytes()
                val response = apiClient.httpClient.post("api/analyze") {
                    url {
                        speciesHint?.takeIf { it.isNotBlank() }?.let {
                            parameters.append("species", it)
                        }
                        parameters.append("locale", locale)
                        // Model tier (aqua | gamma | frontier). Aqua normally
                        // never reaches here (on-device); gamma is the default;
                        // frontier only when the operator enabled it.
                        parameters.append("family", family)
                    }
                    setBody(
                        MultiPartFormDataContent(
                            formData {
                                append(
                                    "image",
                                    imageBytes,
                                    Headers.build {
                                        append(HttpHeaders.ContentType, "image/jpeg")
                                        append(
                                            HttpHeaders.ContentDisposition,
                                            "filename=\"${imageFile.name}\"",
                                        )
                                    },
                                )
                            },
                        ),
                    )
                }
                when (response.status.value) {
                    in 200..299 -> Result.success(response.body<AnalysisResponse>())
                    // 403 = a temporary abuse-control cooldown (the server bans
                    // an IP that briefly burst past the rate limit). Treat it
                    // like 429: show the "server busy, retry shortly" card
                    // instead of a scary "Server error: 403".
                    429, 403 -> Result.failure(RateLimitException())
                    503 -> Result.failure(ServerStartingException())
                    400, 415 -> Result.failure(UnsupportedImageException())
                    else -> Result.failure(
                        Exception(
                            "Server error: ${response.status.value} ${response.status.description}",
                        ),
                    )
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
     * Fetch per-family availability for the scan-screen model picker.
     * Best-effort: on any failure the caller keeps its last-known state
     * (frontier stays disabled, aqua/gamma stay available).
     */
    suspend fun fetchCapabilities(): Result<CapabilitiesResponse> =
        withContext(Dispatchers.IO) {
            try {
                val response = apiClient.httpClient.get("api/capabilities")
                when (response.status.value) {
                    in 200..299 -> Result.success(response.body<CapabilitiesResponse>())
                    else -> Result.failure(Exception("Server error: ${response.status.value}"))
                }
            } catch (e: Exception) {
                Result.failure(e)
            }
        }

    /**
     * Download/update the Aqua on-device model from the server if needed.
     * Best-effort: returns success(false) when there's nothing to do (no
     * server model, or already up to date) and success(true) when a new model
     * was installed. The model is fetched only when its sha differs from the
     * installed one, so repeated calls are cheap (one manifest GET).
     */
    suspend fun syncAquaModel(context: android.content.Context): Result<Boolean> =
        withContext(Dispatchers.IO) {
            try {
                val manifestResp = apiClient.httpClient.get("api/aqua/manifest")
                if (manifestResp.status.value !in 200..299) {
                    return@withContext Result.success(false)
                }
                val manifest =
                    manifestResp.body<com.plantdiseases.app.data.model.AquaManifestResponse>()
                if (!manifest.available) {
                    return@withContext Result.success(false)
                }
                val sha = manifest.sha256
                val classes = manifest.classes
                if (sha.isNullOrBlank() || classes == null) {
                    return@withContext Result.success(false)
                }
                if (AquaModelManager.isUpToDate(context, sha)) {
                    return@withContext Result.success(false)
                }
                // Serialise the download+install; re-check freshness inside the
                // lock so a waiter that the first install already satisfied
                // no-ops instead of racing the shared temp/model file.
                aquaSyncMutex.withLock {
                    if (AquaModelManager.isUpToDate(context, sha)) {
                        return@withContext Result.success(false)
                    }
                    val modelResp = apiClient.httpClient.get("api/aqua/model")
                    if (modelResp.status.value !in 200..299) {
                        return@withContext Result.failure(
                            Exception("Aqua model download failed: ${modelResp.status.value}"),
                        )
                    }
                    // Stream the multi-MB body through a channel-backed
                    // InputStream rather than buffering it all in memory — the
                    // Retrofit path used @Streaming for the same reason.
                    val installed = modelResp.bodyAsChannel().toInputStream().use { stream ->
                        AquaModelManager.install(context, stream, sha, manifest.version, classes)
                    }
                    Result.success(installed)
                }
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
    suspend fun identifySpecies(
        imageFile: File,
        organ: String = "leaf",
        locale: String = "en",
    ): Result<IdentifyResponse> =
        withContext(Dispatchers.IO) {
            try {
                val imageBytes = imageFile.readBytes()
                val response = apiClient.httpClient.post("api/identify") {
                    url {
                        parameters.append("organ", organ)
                        parameters.append("locale", locale)
                    }
                    setBody(
                        MultiPartFormDataContent(
                            formData {
                                append(
                                    "image",
                                    imageBytes,
                                    Headers.build {
                                        append(HttpHeaders.ContentType, "image/jpeg")
                                        append(
                                            HttpHeaders.ContentDisposition,
                                            "filename=\"${imageFile.name}\"",
                                        )
                                    },
                                )
                            },
                        ),
                    )
                }
                when (response.status.value) {
                    in 200..299 -> Result.success(response.body<IdentifyResponse>())
                    429 -> Result.failure(RateLimitException())
                    503 -> Result.failure(ServerStartingException())
                    400, 415 -> Result.failure(UnsupportedImageException())
                    else -> Result.failure(Exception("Server error: ${response.status.value}"))
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
            // Care plans are deterministic per (disease, species, light, locale)
            // and expensive to generate, so serve from the persistent cache once
            // fetched — reopening a result no longer re-hits the LLM.
            val cacheKey = "careplan:v1:${request.disease}|${request.species}" +
                "|${request.light}|${request.locale}"
            com.plantdiseases.app.util.ResponseCache
                .get(cacheKey, CarePlanResponse::class.java)
                ?.let { return@withContext Result.success(it) }
            try {
                val response = apiClient.httpClient.post("api/care-plan") {
                    contentType(ContentType.Application.Json)
                    setBody(request)
                }
                when (response.status.value) {
                    in 200..299 -> {
                        val body = response.body<CarePlanResponse>()
                        com.plantdiseases.app.util.ResponseCache.put(
                            cacheKey,
                            body,
                            com.plantdiseases.app.util.ResponseCache.TTL_CARE_PLAN_MS,
                        )
                        Result.success(body)
                    }
                    503 -> Result.failure(ServerStartingException())
                    else -> Result.failure(Exception("Server error: ${response.status.value}"))
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
     * No-photo symptom triage. Returns a ranked shortlist of likely
     * conditions for the symptom questionnaire. Not cached — the input space
     * is large and the call is cheap (rules-based, no LLM).
     */
    suspend fun diagnoseSymptoms(
        request: com.plantdiseases.app.data.model.SymptomDiagnoseRequest,
    ): Result<com.plantdiseases.app.data.model.SymptomDiagnoseResponse> =
        withContext(Dispatchers.IO) {
            try {
                val response = apiClient.httpClient.post("api/symptom-diagnose") {
                    contentType(ContentType.Application.Json)
                    setBody(request)
                }
                when (response.status.value) {
                    in 200..299 -> Result.success(
                        response.body<com.plantdiseases.app.data.model.SymptomDiagnoseResponse>(),
                    )
                    503 -> Result.failure(ServerStartingException())
                    else -> Result.failure(Exception("Server error: ${response.status.value}"))
                }
            } catch (e: SocketTimeoutException) {
                Result.failure(ServerTimeoutException())
            } catch (e: UnknownHostException) {
                Result.failure(NoNetworkException())
            } catch (e: Exception) {
                Result.failure(e)
            }
        }

    /** List community "second opinions" + consensus for a shared scan. */
    suspend fun getCommunityOpinions(scanId: String): Result<CommunityOpinionsResponse> =
        withContext(Dispatchers.IO) {
            try {
                val response = apiClient.httpClient.get("api/community/opinions/$scanId")
                when (response.status.value) {
                    in 200..299 -> Result.success(response.body<CommunityOpinionsResponse>())
                    else -> Result.failure(Exception("Server error: ${response.status.value}"))
                }
            } catch (e: SocketTimeoutException) {
                Result.failure(ServerTimeoutException())
            } catch (e: UnknownHostException) {
                Result.failure(NoNetworkException())
            } catch (e: Exception) {
                Result.failure(e)
            }
        }

    /** List the second opinions this install has authored. */
    suspend fun getMyCommunityOpinions(pseudoId: String): Result<CommunityOpinionsResponse> =
        withContext(Dispatchers.IO) {
            try {
                val response = apiClient.httpClient.get("api/community/my-opinions/$pseudoId")
                when (response.status.value) {
                    in 200..299 -> Result.success(response.body<CommunityOpinionsResponse>())
                    else -> Result.failure(Exception("Server error: ${response.status.value}"))
                }
            } catch (e: SocketTimeoutException) {
                Result.failure(ServerTimeoutException())
            } catch (e: UnknownHostException) {
                Result.failure(NoNetworkException())
            } catch (e: Exception) {
                Result.failure(e)
            }
        }

    /** Submit one community "second opinion". Returns the refreshed list. */
    suspend fun submitCommunityOpinion(
        request: CommunityOpinionRequest,
    ): Result<CommunityOpinionsResponse> =
        withContext(Dispatchers.IO) {
            try {
                val response = apiClient.httpClient.post("api/community/opinion") {
                    contentType(ContentType.Application.Json)
                    setBody(request)
                }
                when (response.status.value) {
                    in 200..299 -> Result.success(response.body<CommunityOpinionsResponse>())
                    else -> Result.failure(Exception("Server error: ${response.status.value}"))
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
     * Flag the scanner's OWN scan as wanting a community second opinion.
     * The scanner doesn't suggest a diagnosis — they raise a hand and
     * wait for others to weigh in. Returns `true` when the server
     * actually flagged an owned community row; `false` (still a success)
     * when no owned row exists yet — the community upload is
     * fire-and-forget and the user may have opted out of sharing. The UI
     * shows its local "waiting" state regardless.
     */
    suspend fun requestSecondOpinion(
        scanId: String,
        userPseudoId: String,
    ): Result<Boolean> =
        withContext(Dispatchers.IO) {
            try {
                val response = apiClient.httpClient.post("api/community/request-opinion") {
                    contentType(ContentType.Application.Json)
                    setBody(
                        com.plantdiseases.app.data.model.CommunityRequestOpinionRequest(
                            scanId = scanId,
                            userPseudoId = userPseudoId,
                        ),
                    )
                }
                when (response.status.value) {
                    in 200..299 -> Result.success(
                        response.body<com.plantdiseases.app.data.model.CommunityRequestOpinionResponse>()
                            .flagged,
                    )
                    else -> Result.failure(Exception("Server error: ${response.status.value}"))
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
                val response = apiClient.httpClient.post("api/chat") {
                    contentType(ContentType.Application.Json)
                    setBody(request)
                }
                when (response.status.value) {
                    in 200..299 -> Result.success(response.body<ChatResponse>())
                    429 -> Result.failure(RateLimitException())
                    503 -> Result.failure(ServerStartingException())
                    // Server rejected our payload (oversize diagnosis,
                    // empty message, etc.). Surface as a generic
                    // failure — the UI keeps a chat-level error toast.
                    400 -> Result.failure(Exception("Bad request"))
                    else -> Result.failure(Exception("Server error: ${response.status.value}"))
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
                val response = apiClient.httpClient.post("api/feedback") {
                    contentType(ContentType.Application.Json)
                    setBody(request)
                }
                when (response.status.value) {
                    in 200..299 -> Result.success(response.body<FeedbackResponse>())
                    429 -> Result.failure(RateLimitException())
                    503 -> Result.failure(ServerStartingException())
                    422 -> Result.failure(Exception("Invalid feedback payload"))
                    else -> Result.failure(Exception("Server error: ${response.status.value}"))
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
     * Upload the original image bytes for a ``correct=false``
     * feedback row. Best-effort — failures are swallowed because the
     * primary feedback signal has already landed; the image is only
     * additional training value. Caller is responsible for only
     * invoking this on negative-feedback paths so the privacy
     * disclosure on the feedback sheet matches the network traffic.
     */
    suspend fun uploadFeedbackImage(
        scanId: String,
        imageFile: java.io.File,
    ): Result<Unit> = withContext(Dispatchers.IO) {
        try {
            if (!imageFile.exists()) {
                return@withContext Result.failure(Exception("Image file missing"))
            }
            val mime = when (imageFile.extension.lowercase()) {
                "png" -> "image/png"
                "webp" -> "image/webp"
                else -> "image/jpeg"
            }
            val imageBytes = imageFile.readBytes()
            val response = apiClient.httpClient.post("api/feedback/image") {
                setBody(
                    MultiPartFormDataContent(
                        formData {
                            append("scan_id", scanId)
                            append(
                                "image",
                                imageBytes,
                                Headers.build {
                                    append(HttpHeaders.ContentType, mime)
                                    append(
                                        HttpHeaders.ContentDisposition,
                                        "filename=\"${imageFile.name}\"",
                                    )
                                },
                            )
                        },
                    ),
                )
            }
            if (response.status.value in 200..299) {
                Result.success(Unit)
            } else {
                Result.failure(Exception("Server error: ${response.status.value}"))
            }
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    suspend fun fetchProgression(request: ProgressionRequest): Result<ProgressionResponse> =
        withContext(Dispatchers.IO) {
            try {
                val response = apiClient.httpClient.post("api/progression") {
                    contentType(ContentType.Application.Json)
                    setBody(request)
                }
                when (response.status.value) {
                    in 200..299 -> Result.success(response.body<ProgressionResponse>())
                    else -> Result.failure(Exception("Server error: ${response.status.value}"))
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
            "severity" to scan.severity,
            "warnings" to parseWarnings(scan.warnings),
        )
    }

    /** Check server health */
    suspend fun checkServerHealth(): Result<HealthResponse> =
        withContext(Dispatchers.IO) {
            try {
                val response = apiClient.httpClient.get("api/health")
                when (response.status.value) {
                    in 200..299 -> Result.success(response.body<HealthResponse>())
                    else -> Result.failure(Exception("Server error: ${response.status.value}"))
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
            val primaryIndex = if (primary == null) {
                null
            } else {
                regions.indexOfFirst {
                    it.x == primary.x &&
                        it.y == primary.y &&
                        it.width == primary.width &&
                        it.height == primary.height
                }.takeIf { it >= 0 }
            }
            val warningsJson = result.warnings
                ?.takeIf { it.isNotEmpty() }
                ?.let { gson.toJson(it) }
            val best = result.species?.best
            // Prefer the server-issued scan_id so /api/feedback can
            // bind this scan back to the row in the issued_scan_ids
            // table. Fall back to a local UUID only if the server
            // didn't return one (older deployment) — feedback for
            // those will fail validation, which matches the legacy
            // behaviour anyway.
            val serverScanId = result.scanId?.takeIf { it.isNotBlank() }
            // Persist the conformal classes list (if any) so feedback
            // submission can echo it back to the server later.
            val predictionSetJson = result.predictionSet?.classes
                ?.takeIf { it.isNotEmpty() }
                ?.let { gson.toJson(it) }
            // ``classKey`` (when the server is recent enough to emit
            // it) is the post-reroute verdict. Persisting it as
            // ``displayedClassKey`` keeps feedback's predicted_class
            // aligned with what the UI showed.
            val displayedClassKey = result.classKey?.takeIf { it.isNotBlank() }
            val entity = ScanEntity(
                clientScanId = serverScanId ?: java.util.UUID.randomUUID().toString(),
                predictionSet = predictionSetJson,
                displayedClassKey = displayedClassKey,
                speciesScientific = best?.scientificName,
                speciesCommon = best?.commonName,
                speciesScore = best?.score?.takeIf { it > 0f },
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
                detectionImageWidth = detection?.imageWidth ?: 0,
                detectionImageHeight = detection?.imageHeight ?: 0,
                allProbs = result.allProbs?.let { gson.toJson(it) },
                warnings = warningsJson,
                toxicToPets = result.toxicToPets,
                toxicSpeciesList = result.toxicSpeciesList
                    .takeIf { it.isNotEmpty() }
                    ?.let { gson.toJson(it) },
                toxicitySource = result.toxicitySource,
                severity = result.severity ?: "low",
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

    /** Find the local scan a community scan_id maps to (community scan_id ==
     *  the local scan's clientScanId), so a notification can open it. */
    suspend fun getScanByClientScanId(clientScanId: String): ScanEntity? =
        withContext(Dispatchers.IO) {
            scanDao.getScanByClientScanId(clientScanId)
        }

    /** Every local scan's client id — used to filter the user's OWN scans out
     *  of the community feed (scan_ids are globally unique, so a match is
     *  definitely this device's own scan, even if the server-side pseudo-id
     *  exclusion missed it after a reinstall). */
    suspend fun localClientScanIds(): Set<String> =
        withContext(Dispatchers.IO) {
            scanDao.allClientScanIds().toHashSet()
        }

    /** Opinion counts on this install's OWN shared scans (for the
     *  second-opinion notification poll). Best-effort. */
    suspend fun fetchMyScanOpinions(pseudoId: String): Result<MyScanOpinionsResponse> =
        withContext(Dispatchers.IO) {
            try {
                val r = apiClient.httpClient.get("api/community/my-scan-opinions/$pseudoId")
                when (r.status.value) {
                    in 200..299 -> Result.success(r.body<MyScanOpinionsResponse>())
                    else -> Result.failure(Exception("Server error: ${r.status.value}"))
                }
            } catch (e: Exception) {
                Result.failure(e)
            }
        }

    /** Register this install's FCM token with the server (best-effort,
     *  fire-and-forget) so opinion notifications can be pushed to a closed
     *  device. The server upserts by token. */
    suspend fun registerPushToken(pseudoId: String, token: String) {
        withContext(Dispatchers.IO) {
            try {
                apiClient.httpClient.post("api/push/register") {
                    contentType(ContentType.Application.Json)
                    setBody(
                        com.plantdiseases.app.data.model.PushRegisterRequest(
                            userPseudoId = pseudoId,
                            token = token,
                        ),
                    )
                }
            } catch (_: Exception) {
                // best-effort; the poll fallback still covers delivery
            }
        }
    }

    /** Record that this install opened someone else's shared scan. Best-effort
     *  and fire-and-forget — the server dedups per viewer and ignores
     *  owner/self + unknown scans, so failures are swallowed silently. */
    suspend fun recordCommunityView(scanId: String, viewerPseudoId: String) {
        withContext(Dispatchers.IO) {
            try {
                apiClient.httpClient.post("api/community/view") {
                    contentType(ContentType.Application.Json)
                    setBody(
                        com.plantdiseases.app.data.model.CommunityViewRequest(
                            scanId = scanId,
                            userPseudoId = viewerPseudoId,
                        ),
                    )
                }
            } catch (_: Exception) {
                // best-effort telemetry; never surface to the user
            }
        }
    }

    /** Batched {opinions, views} per scan_id for the Gallery badges. Returns
     *  an empty map on failure so the UI just shows no badges. */
    suspend fun getCommunityScanCounts(
        scanIds: List<String>,
    ): Map<String, com.plantdiseases.app.data.model.CommunityScanCount> =
        withContext(Dispatchers.IO) {
            if (scanIds.isEmpty()) return@withContext emptyMap()
            try {
                val r = apiClient.httpClient.post("api/community/scan-counts") {
                    contentType(ContentType.Application.Json)
                    setBody(com.plantdiseases.app.data.model.CommunityScanCountsRequest(scanIds))
                }
                if (r.status.value in 200..299) {
                    r.body<com.plantdiseases.app.data.model.CommunityScanCountsResponse>().counts
                } else {
                    emptyMap()
                }
            } catch (e: Exception) {
                emptyMap()
            }
        }

    /** Delete a scan */
    suspend fun deleteScan(id: Long) = withContext(Dispatchers.IO) {
        scanDao.deleteById(id)
    }

    // ─── AI Plant Doctor chat persistence (scan-keyed sessions) ──────

    /** Persisted transcript for a scan-keyed AI Plant Doctor session. */
    suspend fun getChatMessages(scanId: Long): List<ScanChatMessageEntity> =
        withContext(Dispatchers.IO) {
            scanChatMessageDao?.getForScan(scanId) ?: emptyList()
        }

    /** Append one message to a scan-keyed chat transcript. */
    suspend fun saveChatMessage(entity: ScanChatMessageEntity) {
        withContext(Dispatchers.IO) {
            scanChatMessageDao?.insert(entity)
        }
    }

    /** Drop the persisted transcript for a scan-keyed chat. */
    suspend fun clearChat(scanId: Long) {
        withContext(Dispatchers.IO) {
            scanChatMessageDao?.deleteForScan(scanId)
        }
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
        timestamp = timestamp,
        clientScanId = clientScanId,
    )

    /** Persisted top-class key from `all_probs`; null when not stored. */
    fun topClassKey(scan: ScanEntity): String? =
        parseAllProbs(scan.allProbs).entries.maxByOrNull { it.value }?.key

    /**
     * The canonical verdict class key the user actually saw — the
     * server-issued ``displayedClassKey`` (which already reflects the
     * weak-healthy reroute / healthy-fallback / OOD gate), falling back
     * to ``argmax(all_probs)`` for legacy scans saved before that column
     * existed.
     *
     * Use this — NOT [topClassKey] — for any UI surface that asks "what
     * is THIS scan's diagnosis" (guide deep-link, example photos, …).
     * The raw argmax can disagree with the displayed verdict after a
     * server reroute, which made the result screen's example photos
     * appear for some scans and silently vanish for others.
     */
    fun verdictClassKey(scan: ScanEntity): String? =
        scan.displayedClassKey?.takeIf { it.isNotBlank() } ?: topClassKey(scan)

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
     * "Healthy" verdict for the result UI. Trusts the server's final
     * ``is_healthy`` flag — that's already what the server's
     * post-processing rules (weak-healthy reroute, healthy-fallback,
     * OOD gate) settled on, so using it here keeps the badge / image
     * highlight / share copy consistent with the description and
     * stored disease name. Falling back to the top-class key here
     * caused a visible mismatch where a server-rerouted "Fungal Leaf
     * Spot" verdict showed as "Healthy Plant" in the UI.
     */
    fun isHealthyVerdict(scan: ScanEntity): Boolean = scan.isHealthy

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
    fun parseStringList(json: String): List<String> = try {
        gson.fromJson(json, Array<String>::class.java).toList()
    } catch (e: Exception) {
        emptyList()
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

    // ─── Community (similar scans in your region) ────────────────────

    /**
     * Upload one community-shared scan. Best-effort: any failure
     * (network, server, validation) is logged but never thrown — a
     * failed upload must not block the user's normal scan flow.
     *
     * The caller decides whether to send a thumbnail; see
     * [CommunityRepository.uploadIfEnabled] which handles the
     * opt-out pref + thumbnail downscale before reaching this hop.
     */
    suspend fun uploadCommunityScan(
        scanId: String,
        userPseudoId: String,
        country: String? = null,
        region: String = "",
        disease: String,
        confidence: Float? = null,
        species: String? = null,
        speciesScore: Float? = null,
        locale: String? = null,
        thumbnail: File? = null,
    ): Result<com.plantdiseases.app.data.model.CommunityScanUploadResponse> =
        withContext(Dispatchers.IO) {
            try {
                val response = apiClient.httpClient.post("api/community/scan") {
                    setBody(
                        MultiPartFormDataContent(
                            formData {
                                // Required fields (callers always supply these).
                                append("scan_id", scanId)
                                append("user_pseudo_id", userPseudoId)
                                country?.takeIf { it.isNotBlank() }?.let { append("country", it) }
                                append("disease", disease)
                                // Optional fields — only sent when present/non-blank,
                                // matching the old `text()` helper exactly.
                                region.takeIf { it.isNotBlank() }?.let { append("region", it) }
                                confidence?.let { append("confidence", it.toString()) }
                                species?.takeIf { it.isNotBlank() }?.let { append("species", it) }
                                speciesScore?.let { append("species_score", it.toString()) }
                                locale?.takeIf { it.isNotBlank() }?.let { append("locale", it) }
                                thumbnail?.takeIf { it.exists() }?.let { f ->
                                    append(
                                        "image",
                                        f.readBytes(),
                                        Headers.build {
                                            append(HttpHeaders.ContentType, "image/jpeg")
                                            append(
                                                HttpHeaders.ContentDisposition,
                                                "filename=\"${f.name}\"",
                                            )
                                        },
                                    )
                                }
                            },
                        ),
                    )
                }
                when (response.status.value) {
                    in 200..299 -> Result.success(
                        response.body<com.plantdiseases.app.data.model.CommunityScanUploadResponse>(),
                    )
                    else -> Result.failure(
                        Exception("Community upload failed: ${response.status.value}"),
                    )
                }
            } catch (e: Exception) {
                Result.failure(e)
            }
        }

    /** Fetch the most recent community scans in a country (optionally
     *  narrowed to one sub-country region, one disease, and one
     *  scientific-name species). The species filter is the key
     *  reason this carousel feels relevant — without it the user sees
     *  "leaf spot on Monstera" next to "leaf spot on Ficus" and the
     *  care advice is wildly different. */
    suspend fun communityNearby(
        country: String? = null,
        region: String? = null,
        disease: String?,
        species: String? = null,
        userPseudoId: String?,
        limit: Int = 12,
    ): Result<com.plantdiseases.app.data.model.CommunityScansResponse> =
        withContext(Dispatchers.IO) {
            try {
                val response = apiClient.httpClient.get("api/community/nearby") {
                    country?.takeIf { it.isNotBlank() }?.let { parameter("country", it) }
                    region?.takeIf { it.isNotBlank() }?.let { parameter("region", it) }
                    disease?.takeIf { it.isNotBlank() }?.let { parameter("disease", it) }
                    species?.takeIf { it.isNotBlank() }?.let { parameter("species", it) }
                    userPseudoId?.takeIf { it.isNotBlank() }?.let {
                        parameter("user_pseudo_id", it)
                    }
                    parameter("limit", limit)
                }
                when (response.status.value) {
                    in 200..299 -> Result.success(
                        response.body<com.plantdiseases.app.data.model.CommunityScansResponse>(),
                    )
                    else -> Result.failure(
                        Exception("Community nearby failed: ${response.status.value}"),
                    )
                }
            } catch (e: Exception) {
                Result.failure(e)
            }
        }

    /** Localized Wikipedia blurb + lead image for a scientific name. */
    suspend fun getSpeciesInfo(
        name: String,
        locale: String,
    ): Result<com.plantdiseases.app.data.model.SpeciesInfoResponse> =
        withContext(Dispatchers.IO) {
            // Wikipedia blurb + lead image are stable per (name, locale) — cache
            // once available so the result screen doesn't re-fetch every open.
            val cacheKey = "species:v1:${name.trim().lowercase()}|$locale"
            com.plantdiseases.app.util.ResponseCache
                .get(cacheKey, com.plantdiseases.app.data.model.SpeciesInfoResponse::class.java)
                ?.let { return@withContext Result.success(it) }
            try {
                val body = apiClient.speciesInfo(name, locale)
                if (body != null) {
                    // Don't cache a transient "unavailable" (e.g. Wikipedia
                    // miss / rate-limit) — only a real hit is worth keeping.
                    if (body.available) {
                        com.plantdiseases.app.util.ResponseCache.put(
                            cacheKey,
                            body,
                            com.plantdiseases.app.util.ResponseCache.TTL_SPECIES_MS,
                        )
                    }
                    Result.success(body)
                } else {
                    Result.failure(Exception("species-info failed"))
                }
            } catch (e: Exception) {
                Result.failure(e)
            }
        }

    /** Community scans of one species (scientific name), across regions. */
    suspend fun communityScansBySpecies(
        species: String,
        userPseudoId: String?,
        limit: Int = 12,
    ): Result<com.plantdiseases.app.data.model.CommunityScansResponse> =
        withContext(Dispatchers.IO) {
            try {
                val response = apiClient.httpClient.get("api/community/by-species") {
                    parameter("species", species)
                    parameter("limit", limit)
                    userPseudoId?.takeIf { it.isNotBlank() }?.let {
                        parameter("user_pseudo_id", it)
                    }
                }
                when (response.status.value) {
                    in 200..299 -> Result.success(
                        response.body<com.plantdiseases.app.data.model.CommunityScansResponse>(),
                    )
                    else -> Result.failure(Exception("by-species failed: ${response.status.value}"))
                }
            } catch (e: Exception) {
                Result.failure(e)
            }
        }

    /** Erase every community-shared row for this install. */
    suspend fun deleteCommunityContributions(userPseudoId: String): Result<Unit> =
        withContext(Dispatchers.IO) {
            try {
                val response = apiClient.httpClient.delete("api/community/user/$userPseudoId")
                if (response.status.value in 200..299) {
                    Result.success(Unit)
                } else {
                    Result.failure(Exception("Community delete failed: ${response.status.value}"))
                }
            } catch (e: Exception) {
                Result.failure(e)
            }
        }
}
