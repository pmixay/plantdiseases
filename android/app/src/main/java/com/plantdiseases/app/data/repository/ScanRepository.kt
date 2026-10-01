package com.plantdiseases.app.data.repository

import android.util.AtomicFile
import com.google.gson.Gson
import com.plantdiseases.app.auth.AuthRepository
import com.plantdiseases.app.auth.AuthState
import com.plantdiseases.app.auth.AuthenticationRequiredException
import com.plantdiseases.app.data.account.AccountApiException
import com.plantdiseases.app.data.local.ScanChatMessageDao
import com.plantdiseases.app.data.local.ScanChatMessageEntity
import com.plantdiseases.app.data.local.ScanDao
import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.model.AnalysisResponse
import com.plantdiseases.app.data.model.AnalysisAbstentionReasons
import com.plantdiseases.app.data.model.CapabilitiesResponse
import com.plantdiseases.app.data.model.CarePlanRequest
import com.plantdiseases.app.data.model.CarePlanResponse
import com.plantdiseases.app.data.model.ChatRequest
import com.plantdiseases.app.data.model.ChatResponse
import com.plantdiseases.app.data.model.CommunityOpinionRequest
import com.plantdiseases.app.data.model.CommunityOpinionsResponse
import com.plantdiseases.app.data.model.CommunityPublicProfile
import com.plantdiseases.app.data.model.FeedbackRequest
import com.plantdiseases.app.data.model.FeedbackResponse
import com.plantdiseases.app.data.model.IdentifyResponse
import com.plantdiseases.app.data.model.MyScanOpinionsResponse
import com.plantdiseases.app.data.model.ProgressionRequest
import com.plantdiseases.app.data.model.ProgressionResponse
import com.plantdiseases.app.data.model.ScanHistoryItem
import com.plantdiseases.app.data.model.diagnosticContractViolation
import com.plantdiseases.app.data.model.effectiveAbstentionReason
import com.plantdiseases.app.data.remote.PlantApiClient
import com.plantdiseases.app.util.AquaModelManager
import com.plantdiseases.app.util.DefaultUploadImageSanitizer
import com.plantdiseases.app.util.FeedbackReceiptStore
import com.plantdiseases.app.util.NoOpFeedbackReceiptStore
import com.plantdiseases.app.util.SanitizedImage
import com.plantdiseases.app.util.UploadImageSanitizer
import io.ktor.client.call.body
import io.ktor.client.request.forms.MultiPartFormDataContent
import io.ktor.client.request.forms.formData
import io.ktor.client.request.delete
import io.ktor.client.request.get
import io.ktor.client.request.header
import io.ktor.client.request.parameter
import io.ktor.client.request.post
import io.ktor.client.request.setBody
import io.ktor.client.request.url
import io.ktor.client.statement.HttpResponse
import io.ktor.client.statement.bodyAsChannel
import io.ktor.utils.io.jvm.javaio.toInputStream
import io.ktor.http.ContentType
import io.ktor.http.Headers
import io.ktor.http.HttpHeaders
import io.ktor.http.contentType
import io.ktor.http.isSuccess
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import java.io.File
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import java.security.MessageDigest
import java.util.UUID

class ScanRepository(
    private val scanDao: ScanDao,
    private val apiClient: PlantApiClient,
    // Nullable so unit tests that exercise only the network / scan-history
    // surface don't need a Room database. Chat persistence quietly no-ops
    // when absent.
    private val scanChatMessageDao: ScanChatMessageDao? = null,
    private val imageSanitizer: UploadImageSanitizer = DefaultUploadImageSanitizer,
    private val feedbackReceiptStore: FeedbackReceiptStore = NoOpFeedbackReceiptStore,
    private val authRepository: AuthRepository? = null,
) {
    companion object {
        internal const val RETAINED_ANALYSIS_IMAGE_TTL_MS = 30L * 24 * 60 * 60 * 1000
        internal const val MAX_PENDING_ANALYSIS_IMAGES = 2
        private const val HEADER_IDEMPOTENCY_KEY = "X-Idempotency-Key"
        private const val HEADER_PLAN = "X-PlantDiseases-Plan"
        private const val HEADER_QUOTA_LIMIT = "X-Quota-Limit"
        private const val HEADER_QUOTA_REMAINING = "X-Quota-Remaining"
        private const val HEADER_QUOTA_RESET_AT = "X-Quota-Reset-At"
    }

    private val gson = Gson()
    private val retainedAnalysisImageLock = Any()
    private val pendingAnalysisImages = LinkedHashMap<PendingAnalysisImageKey, SanitizedImage>()
    private val scanArtifactMutex = Mutex()

    // Serialises Aqua model installs so two concurrent syncAquaModel calls
    // (e.g. rapid config-change recreations each firing one) can't race the
    // shared temp/model file and corrupt it.
    private val aquaSyncMutex = Mutex()

    private suspend fun <T> authenticatedAppCall(
        block: suspend (accessToken: String) -> T,
    ): Result<T> {
        val result = authRepository?.authenticatedCall(block)
            ?: return Result.failure(AuthenticationRequiredException())
        return result.fold(
            onSuccess = { Result.success(it) },
            onFailure = { error ->
                Result.failure(
                    when (error) {
                        is SocketTimeoutException -> ServerTimeoutException()
                        is UnknownHostException -> NoNetworkException()
                        is java.net.ConnectException -> ServerTimeoutException()
                        else -> error
                    },
                )
            },
        )
    }

    private fun io.ktor.client.request.HttpRequestBuilder.bearer(token: String) {
        header(HttpHeaders.Authorization, "Bearer $token")
    }

    private fun io.ktor.client.request.HttpRequestBuilder.metered(
        token: String?,
        requestId: String,
    ) {
        token?.let { bearer(it) }
        header(HEADER_IDEMPOTENCY_KEY, requestId)
    }

    /**
     * Run one metered app request as the signed-in account when possible, or as
     * a guest otherwise. A signed-in 401 is converted to the account client's
     * refresh signal, so [AuthRepository] retries the block exactly once with a
     * fresh Bearer token. Callers create their idempotency UUID outside this
     * block; the refresh retry therefore reuses the same logical operation id.
     */
    private suspend fun optionalMeteredCall(
        request: suspend (accessToken: String?) -> HttpResponse,
    ): Result<HttpResponse> {
        if (authRepository?.state?.value is AuthState.Authenticated) {
            return authenticatedAppCall { token ->
                val response = request(token)
                if (response.status.value == 401) {
                    throw AccountApiException(401, "auth_required")
                }
                response
            }
        }
        return try {
            Result.success(request(null))
        } catch (_: SocketTimeoutException) {
            Result.failure(ServerTimeoutException())
        } catch (_: UnknownHostException) {
            Result.failure(NoNetworkException())
        } catch (_: java.net.ConnectException) {
            Result.failure(ServerTimeoutException())
        } catch (failure: Exception) {
            Result.failure(failure)
        }
    }

    /** Only quota denials carry the complete quota-header tuple. An ordinary
     * anti-abuse 429 deliberately stays a [RateLimitException]. */
    private fun HttpResponse.meteredDenialOrNull(): Exception? {
        val plan = headers[HEADER_PLAN]?.trim()?.takeIf { it.isNotEmpty() }
        val limit = headers[HEADER_QUOTA_LIMIT]?.toIntOrNull()
        val remaining = headers[HEADER_QUOTA_REMAINING]?.toIntOrNull()
        val resetAt = headers[HEADER_QUOTA_RESET_AT]?.trim()?.takeIf { it.isNotEmpty() }
        return when {
            status.value == 402 -> ProRequiredException(plan, limit, remaining, resetAt)
            status.value == 429 &&
                plan != null &&
                limit != null &&
                remaining != null &&
                resetAt != null -> QuotaExhaustedException(plan, limit, remaining, resetAt)
            else -> null
        }
    }

    private suspend inline fun <reified T> HttpResponse.protectedBody(): T {
        if (status.value == 401) throw AccountApiException(401, "auth_required")
        if (!status.isSuccess()) throw CommunityRequestException(status.value)
        return body()
    }

    private suspend fun HttpResponse.requireProtectedSuccess() {
        if (status.value == 401) throw AccountApiException(401, "auth_required")
        if (!status.isSuccess()) throw CommunityRequestException(status.value)
    }

    /** Public community reads carry an account token when one is available so
     * server-side block filtering follows the user across devices. Guests keep
     * the same public read path. A signed-in 401 gets the standard one-time
     * refresh/retry semantics through [authenticatedAppCall]. */
    private suspend inline fun <reified T> optionalAccountGet(
        crossinline request: suspend (accessToken: String?) -> HttpResponse,
    ): Result<T> {
        if (authRepository?.state?.value is AuthState.Authenticated) {
            return authenticatedAppCall { token ->
                request(token).protectedBody<T>()
            }
        }
        return try {
            val response = request(null)
            if (!response.status.isSuccess()) {
                throw CommunityRequestException(response.status.value)
            }
            Result.success(response.body<T>())
        } catch (_: SocketTimeoutException) {
            Result.failure(ServerTimeoutException())
        } catch (_: UnknownHostException) {
            Result.failure(NoNetworkException())
        } catch (failure: Exception) {
            Result.failure(failure)
        }
    }

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
        trainingConsent: Boolean = false,
        trainingConsentVersion: String? = null,
    ): Result<AnalysisResponse> =
        withContext(Dispatchers.IO) {
            try {
                // Ktor multipart over the shared OkHttp engine. Reading the
                // bytes up front keeps the body re-writable for the one safe
                // pre-consumption replay: AuthRepository's 401 token refresh.
                // The shared interceptor deliberately does not replay metered
                // 429/503 responses after quota may have been consumed.
                val image = imageSanitizer.sanitize(imageFile)
                val requestId = UUID.randomUUID().toString()
                val response = optionalMeteredCall { token ->
                    apiClient.httpClient.post("api/analyze") {
                        metered(token, requestId)
                        url {
                            speciesHint?.takeIf { it.isNotBlank() }?.let {
                                parameters.append("species", it)
                            }
                            parameters.append("locale", locale)
                            // Model tier (aqua | gamma | frontier). Aqua normally
                            // never reaches here (on-device); gamma is the default;
                            // frontier only when the operator enabled it.
                            parameters.append("family", family)
                            parameters.append("training_consent", trainingConsent.toString())
                            trainingConsentVersion?.takeIf { trainingConsent && it.isNotBlank() }?.let {
                                parameters.append("training_consent_version", it)
                            }
                        }
                        setBody(
                            MultiPartFormDataContent(
                                formData {
                                    append(
                                        "image",
                                        image.bytes,
                                        Headers.build {
                                            append(HttpHeaders.ContentType, "image/jpeg")
                                            append(
                                                HttpHeaders.ContentDisposition,
                                                "filename=\"${image.filename}\"",
                                            )
                                        },
                                    )
                                },
                            ),
                        )
                    }
                }.getOrElse { return@withContext Result.failure(it) }
                response.meteredDenialOrNull()?.let {
                    return@withContext Result.failure(it)
                }
                when (response.status.value) {
                    in 200..299 -> {
                        val analysis = response.body<AnalysisResponse>()
                        if (!analysis.available) {
                            Result.failure(
                                ModelUnavailableException(
                                    family = analysis.family,
                                    reason = analysis.unavailableReason,
                                    serverMessage = analysis.availabilityMessage,
                                ),
                            )
                        } else {
                            val violation = analysis.diagnosticContractViolation()
                            if (violation == null) {
                                analysis.scanId?.takeIf { it.isNotBlank() }?.let { scanId ->
                                    // Hold only a small in-memory handoff until saveScan creates
                                    // the owning Room row. A process death then fails publication
                                    // closed instead of leaving an unowned private disk sidecar.
                                    rememberPendingAnalysisImage(imageFile, scanId, image)
                                }
                                Result.success(analysis)
                            } else {
                                Result.failure(InvalidAnalysisResponseException(violation))
                            }
                        }
                    }
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
     * Post one client error/problem report to /api/telemetry/errors. Strictly
     * best-effort and silent: any failure (including no network) is swallowed —
     * telemetry must never surface to the user or throw into a catch handler.
     */
    suspend fun reportError(report: com.plantdiseases.app.data.model.ErrorReport) {
        withContext(Dispatchers.IO) {
            runCatching {
                apiClient.httpClient.post("api/telemetry/errors") {
                    contentType(ContentType.Application.Json)
                    setBody(report)
                }
            }
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
     * was installed. Freshness is keyed by the combined model+semantic-sidecar
     * bundle digest, so a classes/preprocess contract update cannot be missed
     * when the TFLite bytes stay unchanged.
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
                    // A successfully decoded authoritative manifest can
                    // withdraw a broken/stale generation. Clear only the
                    // active pointer; retained immutable bytes remain a
                    // rollback. HTTP and network failures return above/below
                    // without changing the last-known-good installation.
                    return@withContext if (AquaModelManager.deactivate(context)) {
                        Result.success(false)
                    } else {
                        Result.failure(Exception("Could not deactivate withdrawn Aqua bundle"))
                    }
                }
                if (manifest.bundleSchemaVersion != 1) {
                    return@withContext Result.success(false)
                }
                val modelSha = manifest.sha256?.takeIf(String::isNotBlank)
                    ?: return@withContext Result.success(false)
                val classesSha = manifest.classesSha256?.takeIf(String::isNotBlank)
                    ?: return@withContext Result.success(false)
                val bundleSha = manifest.bundleSha256?.takeIf(String::isNotBlank)
                    ?: return@withContext Result.success(false)
                val classes = manifest.classes
                    ?: return@withContext Result.success(false)
                if (AquaModelManager.isUpToDate(context, bundleSha)) {
                    return@withContext Result.success(false)
                }
                // Serialise the download+install; re-check freshness inside the
                // lock so a waiter that the first install already satisfied
                // no-ops instead of racing the shared temp/model file.
                aquaSyncMutex.withLock {
                    if (AquaModelManager.isUpToDate(context, bundleSha)) {
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
                        AquaModelManager.install(
                            context,
                            stream,
                            modelSha,
                            classesSha,
                            bundleSha,
                            manifest.version,
                            classes,
                        )
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
                val image = imageSanitizer.sanitize(imageFile)
                val requestId = UUID.randomUUID().toString()
                val response = optionalMeteredCall { token ->
                    apiClient.httpClient.post("api/identify") {
                        metered(token, requestId)
                        url {
                            parameters.append("organ", organ)
                            parameters.append("locale", locale)
                        }
                        setBody(
                            MultiPartFormDataContent(
                                formData {
                                    append(
                                        "image",
                                        image.bytes,
                                        Headers.build {
                                            append(HttpHeaders.ContentType, "image/jpeg")
                                            append(
                                                HttpHeaders.ContentDisposition,
                                                "filename=\"${image.filename}\"",
                                            )
                                        },
                                    )
                                },
                            ),
                        )
                    }
                }.getOrElse { return@withContext Result.failure(it) }
                response.meteredDenialOrNull()?.let {
                    return@withContext Result.failure(it)
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
            // Care plans are deterministic per (disease, species, light, locale,
            // season) and expensive to generate, so serve from the persistent
            // cache once fetched — reopening a result no longer re-hits the LLM.
            // `season` is part of the key: the server's plan differs by season
            // (dormant stretches watering / pauses feeding), so an explicit
            // season must not collide with another on the same cache entry.
            // v3: the server's LLM summary now covers the follow-up checkpoint
            // and red flags, so v2 entries hold a materially poorer plan for
            // the rest of their week — re-fetch instead of serving it.
            val cacheKey = "careplan:v3:${request.disease}|${request.species}" +
                "|${request.light}|${request.locale}|${request.season ?: ""}"
            com.plantdiseases.app.util.ResponseCache
                .get(cacheKey, CarePlanResponse::class.java)
                ?.let { return@withContext Result.success(it) }
            try {
                val requestId = UUID.randomUUID().toString()
                val response = optionalMeteredCall { token ->
                    apiClient.httpClient.post("api/care-plan") {
                        metered(token, requestId)
                        contentType(ContentType.Application.Json)
                        setBody(request)
                    }
                }.getOrElse { return@withContext Result.failure(it) }
                response.meteredDenialOrNull()?.let {
                    return@withContext Result.failure(it)
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

    /** List community opinions with viewer-relative state when signed in. */
    suspend fun getCommunityOpinions(scanId: String): Result<CommunityOpinionsResponse> =
        withContext(Dispatchers.IO) {
            optionalAccountGet { token ->
                apiClient.httpClient.get("api/community/opinions/$scanId") {
                    token?.let { bearer(it) }
                }
            }
        }

    /** Public account activity behind an opaque community author token. */
    suspend fun getCommunityProfile(authorToken: String): Result<CommunityPublicProfile> =
        withContext(Dispatchers.IO) {
            optionalAccountGet { token ->
                apiClient.httpClient.get("api/community/profile/$authorToken") {
                    token?.let { bearer(it) }
                }
            }
        }

    /** Submit one community "second opinion". Returns the refreshed list. */
    suspend fun submitCommunityOpinion(
        request: CommunityOpinionRequest,
    ): Result<CommunityOpinionsResponse> =
        withContext(Dispatchers.IO) {
            authenticatedAppCall { token ->
                val response = apiClient.httpClient.post("api/community/opinion") {
                    bearer(token)
                    contentType(ContentType.Application.Json)
                    setBody(request)
                }
                if (response.status.value == 423) throw CommunityOpinionsLockedException()
                response.protectedBody<CommunityOpinionsResponse>()
            }
        }

    /** Report a shared scan or one reply using only its opaque public token. */
    suspend fun reportCommunityContent(
        targetType: String,
        targetId: String,
        reason: String,
    ): Result<Boolean> =
        withContext(Dispatchers.IO) {
            authenticatedAppCall { token ->
                val response = apiClient.httpClient.post("api/community/safety/report") {
                    bearer(token)
                    contentType(ContentType.Application.Json)
                    setBody(
                        com.plantdiseases.app.data.model.CommunityReportRequest(
                            targetType = targetType,
                            targetId = targetId,
                            reason = reason,
                        ),
                    )
                }
                response
                    .protectedBody<com.plantdiseases.app.data.model.CommunitySafetyMutationResponse>()
                    .ok
            }
        }

    /** Block the server-resolved author of a scan or reply. */
    suspend fun blockCommunityAuthor(
        targetType: String,
        targetId: String,
    ): Result<com.plantdiseases.app.data.model.CommunitySafetyMutationResponse> =
        withContext(Dispatchers.IO) {
            authenticatedAppCall { token ->
                val response = apiClient.httpClient.post("api/community/safety/block") {
                    bearer(token)
                    contentType(ContentType.Application.Json)
                    setBody(
                        com.plantdiseases.app.data.model.CommunityBlockRequest(
                            targetType = targetType,
                            targetId = targetId,
                        ),
                    )
                }
                response.protectedBody()
            }
        }

    suspend fun communityBlocks(): Result<List<com.plantdiseases.app.data.model.CommunityBlockItem>> =
        withContext(Dispatchers.IO) {
            authenticatedAppCall { token ->
                val response = apiClient.httpClient.get("api/community/safety/blocks") {
                    bearer(token)
                }
                response
                    .protectedBody<com.plantdiseases.app.data.model.CommunityBlocksResponse>()
                    .items
            }
        }

    suspend fun unblockCommunityAuthor(blockId: String): Result<Unit> =
        withContext(Dispatchers.IO) {
            authenticatedAppCall { token ->
                val response = apiClient.httpClient.delete(
                    "api/community/safety/blocks/$blockId",
                ) {
                    bearer(token)
                }
                response.requireProtectedSuccess()
            }
        }

    /**
     * Flag the scanner's OWN scan as wanting a community second opinion.
     * The scanner doesn't suggest a diagnosis — they raise a hand and
     * wait for others to weigh in. Returns `true` when the server
     * actually flagged an owned community row; `false` (still a success)
     * when no owned row exists yet — the community upload is
     * fire-and-forget and the user may have opted out of sharing. Callers must
     * treat that false result as a failed request rather than showing a local
     * waiting state that the server does not know about.
     */
    suspend fun requestSecondOpinion(
        scanId: String,
    ): Result<Boolean> =
        withContext(Dispatchers.IO) {
            authenticatedAppCall { token ->
                val response = apiClient.httpClient.post("api/community/request-opinion") {
                    bearer(token)
                    contentType(ContentType.Application.Json)
                    setBody(
                        com.plantdiseases.app.data.model.CommunityRequestOpinionRequest(
                            scanId = scanId,
                        ),
                    )
                }
                response
                    .protectedBody<com.plantdiseases.app.data.model.CommunityRequestOpinionResponse>()
                    .flagged
            }
        }

    /**
     * Withdraw an open second-opinion request on the user's OWN scan —
     * the community stops seeing the ask and no further opinion
     * notifications arrive for it. Opinions already left stay visible.
     * Returns `true` when an open request was actually cleared (requests
     * also lapse server-side on their own after 72 h).
     */
    suspend fun cancelSecondOpinion(
        scanId: String,
    ): Result<Boolean> =
        withContext(Dispatchers.IO) {
            authenticatedAppCall { token ->
                val response = apiClient.httpClient.post("api/community/request-opinion/cancel") {
                    bearer(token)
                    contentType(ContentType.Application.Json)
                    setBody(
                        com.plantdiseases.app.data.model.CommunityRequestOpinionRequest(
                            scanId = scanId,
                        ),
                    )
                }
                response
                    .protectedBody<com.plantdiseases.app.data.model.CommunityCancelOpinionResponse>()
                    .cleared
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
                val requestId = UUID.randomUUID().toString()
                val response = optionalMeteredCall { token ->
                    apiClient.httpClient.post("api/chat") {
                        metered(token, requestId)
                        contentType(ContentType.Application.Json)
                        setBody(request)
                    }
                }.getOrElse { return@withContext Result.failure(it) }
                response.meteredDenialOrNull()?.let {
                    return@withContext Result.failure(it)
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
     * The server consumes a one-use receipt and updates the matching anonymous
     * training sample. The endpoint is rate-limited — if the client posts
     * faster than 1/sec the
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
                    409 -> Result.failure(FeedbackReceiptException())
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
     * Submit negative feedback and its sanitized image atomically. The receipt
     * is one-use, so this replaces (rather than follows) the JSON-only route.
     */
    suspend fun uploadFeedbackImage(
        request: FeedbackRequest,
        imageFile: java.io.File,
    ): Result<Unit> = withContext(Dispatchers.IO) {
        try {
            if (!imageFile.exists()) {
                return@withContext Result.failure(Exception("Image file missing"))
            }
            val image = imageSanitizer.sanitize(imageFile)
            val response = apiClient.httpClient.post("api/feedback/image") {
                setBody(
                    MultiPartFormDataContent(
                        formData {
                            append("feedback_receipt", request.feedbackReceipt)
                            append("correct", request.correct.toString())
                            request.correctedClass?.let { append("corrected_class", it) }
                            request.reason?.let { append("reason", it) }
                            append(
                                "image",
                                image.bytes,
                                Headers.build {
                                    append(HttpHeaders.ContentType, image.contentType)
                                    append(
                                        HttpHeaders.ContentDisposition,
                                        "filename=\"${image.filename}\"",
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
                when (response.status.value) {
                    429 -> Result.failure(RateLimitException())
                    503 -> Result.failure(ServerStartingException())
                    409 -> Result.failure(FeedbackReceiptException())
                    422 -> Result.failure(Exception("Invalid feedback payload"))
                    else -> Result.failure(Exception("Server error: ${response.status.value}"))
                }
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
        val effectiveAbstentionReason = abstentionReason(scan)
        return mapOf(
            "class_name" to classKey,
            "disease_name" to scan.diseaseName,
            "confidence" to scan.confidence.toDouble(),
            "is_healthy" to (scan.isHealthy && effectiveAbstentionReason == null),
            "severity" to scan.severity,
            "warnings" to parseWarnings(scan.warnings),
            "abstained" to (effectiveAbstentionReason != null),
            "abstention_reason" to effectiveAbstentionReason,
        )
    }

    class ServerTimeoutException : Exception("Server is not responding")
    class NoNetworkException : Exception("No network connection")
    class RateLimitException : Exception("Server is busy, please wait")
    class ProRequiredException(
        val plan: String?,
        val limit: Int?,
        val remaining: Int?,
        val resetAt: String?,
    ) : Exception("PlantDiseases PRO is required")

    class QuotaExhaustedException(
        val plan: String,
        val limit: Int,
        val remaining: Int,
        val resetAt: String,
    ) : Exception("Daily $plan quota exhausted")

    class ServerStartingException : Exception("Server is starting / pipeline unavailable")
    class ModelUnavailableException(
        val family: String,
        val reason: String?,
        val serverMessage: String?,
    ) : Exception("Model family $family is unavailable${reason?.let { ": $it" }.orEmpty()}")
    class InvalidAnalysisResponseException(
        val violation: String,
    ) : Exception("Analysis response violated the diagnostic contract: $violation")
    class UnsupportedImageException : Exception("Unsupported image format")
    class CommunityOpinionsLockedException : Exception("Second opinions are locked")
    class FeedbackReceiptException : Exception("Feedback receipt is unavailable")
    class CommunityRequestException(val statusCode: Int) : Exception("Community request failed: $statusCode")

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
            val effectiveAbstentionReason = result.effectiveAbstentionReason()
            val accepted = effectiveAbstentionReason == null
            val persistedWarnings = LinkedHashSet(result.warnings.orEmpty())
            if (effectiveAbstentionReason != null) {
                // No Room migration: explicit API fields are persisted as
                // durable warning markers so old and new rows share one policy.
                persistedWarnings += "abstained"
                persistedWarnings += "abstention_reason:$effectiveAbstentionReason"
            }
            val warningsJson = persistedWarnings
                .takeIf { it.isNotEmpty() }
                ?.let { gson.toJson(it.toList()) }
            val best = if (accepted) result.species?.best else null
            // Keep the server-issued community publication capability when
            // present. Feedback uses a separate one-use receipt and never
            // sends this value.
            val serverScanId = result.scanId?.takeIf { it.isNotBlank() }
            // Persist the conformal classes list for local result/history UI.
            val predictionSetJson = result.predictionSet?.classes
                ?.takeIf { accepted }
                ?.takeIf { it.isNotEmpty() }
                ?.let { gson.toJson(it) }
            // The probability-backed diagnostic argmax is preserved even when
            // a separate abstention verdict prevents diagnosis presentation.
            // Persisting it as ``displayedClassKey`` keeps local UI labels
            // aligned with what the user saw; feedback itself trusts only the
            // server receipt.
            val displayedClassKey = result.classKey?.takeIf { it.isNotBlank() }
            val clientScanId = serverScanId ?: java.util.UUID.randomUUID().toString()
            val entity = ScanEntity(
                clientScanId = clientScanId,
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
                treatment = gson.toJson(if (accepted) result.treatment else emptyList<String>()),
                treatmentRu = gson.toJson(if (accepted) result.treatmentRu else emptyList<String>()),
                prevention = gson.toJson(if (accepted) result.prevention else emptyList<String>()),
                preventionRu = gson.toJson(if (accepted) result.preventionRu else emptyList<String>()),
                isHealthy = accepted && result.isHealthy,
                regions = if (regions.isEmpty()) null else gson.toJson(regions),
                primaryRegionIndex = primaryIndex,
                detectionImageWidth = detection?.imageWidth ?: 0,
                detectionImageHeight = detection?.imageHeight ?: 0,
                allProbs = result.allProbs?.let { gson.toJson(it) },
                warnings = warningsJson,
                toxicToPets = accepted && result.toxicToPets,
                toxicSpeciesList = result.toxicSpeciesList
                    .takeIf { accepted && it.isNotEmpty() }
                    ?.let { gson.toJson(it) },
                toxicitySource = if (accepted) result.toxicitySource else "unknown",
                severity = result.severity ?: "low",
            )
            val rowId = scanArtifactMutex.withLock {
                val inserted = try {
                    scanDao.insert(entity)
                } catch (error: Throwable) {
                    discardPendingAnalysisImage(File(imagePath), clientScanId)
                    throw error
                }
                takePendingAnalysisImage(File(imagePath), clientScanId)?.let { image ->
                    // The exact bytes reach disk only after Room established their
                    // owner. AtomicFile makes a crash either retain the old complete
                    // object or no publishable object, never a partial JPEG.
                    runCatching { retainAnalysisImage(File(imagePath), clientScanId, image) }
                }
                inserted
            }
            result.feedbackReceipt?.takeIf { it.isNotBlank() }?.let { receipt ->
                feedbackReceiptStore.put(rowId.toString(), receipt)
            }
            rowId
        }

    fun feedbackReceipt(scan: ScanEntity): String? = feedbackReceiptStore.get(scan.id.toString())

    fun consumeFeedbackReceipt(scan: ScanEntity) {
        feedbackReceiptStore.remove(scan.id.toString())
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

    /** Get all scans */
    suspend fun getAllScans(): List<ScanHistoryItem> =
        withContext(Dispatchers.IO) {
            // Rejected raw argmaxes must not reappear as accepted diagnoses in
            // gallery/history. The backing row remains addressable by id for
            // the immediate retake/correction result screen.
            scanDao.getAllScans()
                .filterNot { isInconclusiveScan(it) }
                .map { it.toHistoryItem() }
        }

    /** Local scan ids used to hide the current user's own posts from discovery. */
    suspend fun localClientScanIds(): Set<String> =
        withContext(Dispatchers.IO) {
            scanDao.allClientScanIds().filterTo(mutableSetOf()) { it.isNotBlank() }
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

    /** Opinion counts on the signed account's own shared scans. */
    suspend fun fetchMyScanOpinions(): Result<MyScanOpinionsResponse> =
        withContext(Dispatchers.IO) {
            authenticatedAppCall { token ->
                apiClient.httpClient.get("api/community/my-scan-opinions") {
                    bearer(token)
                }
                    .protectedBody<MyScanOpinionsResponse>()
            }
        }

    /** Record an authenticated view. Guests browse without creating a row. */
    suspend fun recordCommunityView(scanId: String): Result<Unit> =
        withContext(Dispatchers.IO) {
            authenticatedAppCall { token ->
                val response = apiClient.httpClient.post("api/community/view") {
                    bearer(token)
                    contentType(ContentType.Application.Json)
                    setBody(
                        com.plantdiseases.app.data.model.CommunityViewRequest(
                            scanId = scanId,
                        ),
                    )
                }
                response.requireProtectedSuccess()
            }
        }

    /** Record that this account scrolled past (not necessarily opened) up to
     *  50 shared scans in the feed. Same fire-and-forget contract as
     *  [recordCommunityView]; the caller is expected to have already
     *  filtered out ids already reported this screen session. */
    suspend fun recordCommunityViews(scanIds: List<String>): Result<Unit> =
        withContext(Dispatchers.IO) {
            if (scanIds.isEmpty()) return@withContext Result.success(Unit)
            authenticatedAppCall { token ->
                val response = apiClient.httpClient.post("api/community/view") {
                    bearer(token)
                    contentType(ContentType.Application.Json)
                    setBody(
                        com.plantdiseases.app.data.model.CommunityViewRequest(
                            scanIds = scanIds.take(50),
                        ),
                    )
                }
                response.requireProtectedSuccess()
            }
        }

    /** Batched {opinions, views} per scan for account-owned Gallery rows. */
    suspend fun getCommunityScanCounts(
        scanIds: List<String>,
    ): Map<String, com.plantdiseases.app.data.model.CommunityScanCount> =
        withContext(Dispatchers.IO) {
            if (scanIds.isEmpty()) return@withContext emptyMap()
            authenticatedAppCall { token ->
                val response = apiClient.httpClient.post("api/community/scan-counts") {
                    bearer(token)
                    contentType(ContentType.Application.Json)
                    setBody(com.plantdiseases.app.data.model.CommunityScanCountsRequest(scanIds))
                }
                response.protectedBody<com.plantdiseases.app.data.model.CommunityScanCountsResponse>()
                    .counts
            }.getOrDefault(emptyMap())
        }

    /** Like or unlike a shared scan. Returns the resulting liked state on
     *  success — the caller should already have applied it optimistically
     *  and only needs to roll back on failure. */
    suspend fun setCommunityLike(
        scanId: String,
        liked: Boolean,
    ): Result<com.plantdiseases.app.data.model.CommunityLikeResponse> =
        withContext(Dispatchers.IO) {
            authenticatedAppCall { token ->
                val response = apiClient.httpClient.post("api/community/like") {
                    bearer(token)
                    contentType(ContentType.Application.Json)
                    setBody(
                        com.plantdiseases.app.data.model.CommunityLikeRequest(
                            scanId = scanId,
                            liked = liked,
                        ),
                    )
                }
                response.protectedBody<com.plantdiseases.app.data.model.CommunityLikeResponse>()
            }
        }

    /** Delete a scan */
    suspend fun deleteScan(id: Long) = withContext(Dispatchers.IO) {
        scanArtifactMutex.withLock {
            val scan = scanDao.getScanById(id)
            if (scan != null) {
                check(deleteRetainedAnalysisImage(scan) != ArtifactDeletion.FAILED) {
                    "Unable to delete retained analysis image"
                }
            }
            feedbackReceiptStore.remove(id.toString())
            scanDao.deleteById(id)
        }
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

    /** Persisted probability argmax from `all_probs`; null when not stored. */
    fun topClassKey(scan: ScanEntity): String? =
        parseAllProbs(scan.allProbs).entries.maxByOrNull { it.value }?.key

    /**
     * Canonical diagnostic class key. Modern responses keep this equal to the
     * calibrated probability argmax; explicit abstention is a separate
     * verdict and callers must check [isAbstainedScan] before disease UI.
     */
    fun verdictClassKey(scan: ScanEntity): String? =
        scan.displayedClassKey?.takeIf { it.isNotBlank() } ?: topClassKey(scan)

    /** Effective rejection reason, including markers persisted from new API
     * fields and legacy warnings from cached responses. */
    fun abstentionReason(scan: ScanEntity): String? =
        PersistedScanAbstentionPolicy.reason(scan)

    fun isAbstainedScan(scan: ScanEntity): Boolean = abstentionReason(scan) != null

    fun isInconclusiveScan(scan: ScanEntity): Boolean =
        abstentionReason(scan)?.let { it != AnalysisAbstentionReasons.NOT_A_PLANT } == true

    /** Natural trained-head NAP only. Quality/OOD rejection has neutral copy. */
    fun isNotAPlantScan(scan: ScanEntity): Boolean =
        abstentionReason(scan) == AnalysisAbstentionReasons.NOT_A_PLANT

    /**
     * "Healthy" verdict for the result UI. The server flag is trusted only for
     * an accepted scan; an OOD/quality-rejected healthy argmax is inconclusive.
     */
    fun isHealthyVerdict(scan: ScanEntity): Boolean =
        !isAbstainedScan(scan) && scan.isHealthy

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
                isInconclusiveScan(s) -> Unit
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
        scanArtifactMutex.withLock {
            clearPendingAnalysisImages()
            val scans = scanDao.getAllScans()
            check(scans.none { deleteRetainedAnalysisImage(it) == ArtifactDeletion.FAILED }) {
                "Unable to delete retained analysis images"
            }
            feedbackReceiptStore.clear()
            scanDao.deleteAll()
        }
    }

    /**
     * Sweep stale abstention rows. The row backs the
     * verdict's result screen (incl. the "this is a plant" feedback path),
     * but NAP scans are hidden from the gallery and stats, so after
     * [olderThanMs] the row is unreachable dead weight. NAP is a derived
     * property (no dedicated column), so this filters in Kotlin — the
     * table is small. Returns the number of rows removed.
     */
    suspend fun purgeNotAPlantScans(
        olderThanMs: Long = 48L * 60 * 60 * 1000,
    ): Int = withContext(Dispatchers.IO) {
        scanArtifactMutex.withLock {
            val cutoff = System.currentTimeMillis() - olderThanMs
            val stale = scanDao.getAllScans()
                .filter { it.timestamp < cutoff && isAbstainedScan(it) }
            var removed = 0
            for (s in stale) {
                if (deleteRetainedAnalysisImage(s) == ArtifactDeletion.FAILED) continue
                feedbackReceiptStore.remove(s.id.toString())
                scanDao.deleteById(s.id)
                removed++
            }
            removed
        }
    }

    /**
     * Delete exact analysis bytes once the server's community-publication
     * capability has expired. The scan row and ordinary history image remain;
     * only the additional SHA-bound upload sidecar is removed.
     */
    suspend fun purgeExpiredRetainedAnalysisImages(
        nowMs: Long = System.currentTimeMillis(),
        olderThanMs: Long = RETAINED_ANALYSIS_IMAGE_TTL_MS,
    ): Int = withContext(Dispatchers.IO) {
        scanArtifactMutex.withLock {
            val cutoff = nowMs - olderThanMs
            scanDao.getAllScans()
                .asSequence()
                .filter { it.timestamp <= cutoff }
                .count { deleteRetainedAnalysisImage(it) == ArtifactDeletion.DELETED }
        }
    }

    /**
     * Retrieve the byte-for-byte /api/analyze upload retained for this server
     * scan. Returning null is deliberate: old rows and interrupted disk writes
     * cannot safely publish an independently encoded substitute because the
     * server binds publication to the original image digest.
     */
    fun retainedAnalysisImage(scan: ScanEntity): SanitizedImage? =
        synchronized(retainedAnalysisImageLock) {
            val scanId = scan.clientScanId.takeIf { it.isNotBlank() }
                ?: return@synchronized null
            val file = retainedAnalysisImageFile(File(scan.imagePath), scanId)
                ?: return@synchronized null
            val length = file.length()
            if (!file.isFile || length !in 1..DefaultUploadImageSanitizer.MAX_OUTPUT_BYTES.toLong()) {
                return@synchronized null
            }
            runCatching {
                SanitizedImage(
                    bytes = file.readBytes(),
                    width = scan.detectionImageWidth.coerceAtLeast(1),
                    height = scan.detectionImageHeight.coerceAtLeast(1),
                )
            }.getOrNull()
        }

    private fun retainAnalysisImage(
        source: File,
        scanId: String,
        image: SanitizedImage,
    ) = synchronized(retainedAnalysisImageLock) {
        val file = retainedAnalysisImageFile(source, scanId)
            ?: error("Analysis image has no parent directory")
        val atomicFile = AtomicFile(file)
        val output = atomicFile.startWrite()
        try {
            output.write(image.bytes)
            atomicFile.finishWrite(output)
        } catch (error: Throwable) {
            atomicFile.failWrite(output)
            throw error
        }
    }

    private fun rememberPendingAnalysisImage(
        source: File,
        scanId: String,
        image: SanitizedImage,
    ) = synchronized(retainedAnalysisImageLock) {
        val key = PendingAnalysisImageKey(source.absolutePath, scanId)
        pendingAnalysisImages[key] = image.copy(bytes = image.bytes.copyOf())
        while (pendingAnalysisImages.size > MAX_PENDING_ANALYSIS_IMAGES) {
            pendingAnalysisImages.remove(pendingAnalysisImages.keys.first())
        }
    }

    private fun takePendingAnalysisImage(source: File, scanId: String): SanitizedImage? =
        synchronized(retainedAnalysisImageLock) {
            pendingAnalysisImages.remove(PendingAnalysisImageKey(source.absolutePath, scanId))
        }

    private fun discardPendingAnalysisImage(source: File, scanId: String) {
        synchronized(retainedAnalysisImageLock) {
            pendingAnalysisImages.remove(PendingAnalysisImageKey(source.absolutePath, scanId))
        }
    }

    private fun clearPendingAnalysisImages() {
        synchronized(retainedAnalysisImageLock) { pendingAnalysisImages.clear() }
    }

    private fun deleteRetainedAnalysisImage(scan: ScanEntity): ArtifactDeletion {
        if (scan.clientScanId.isBlank()) return ArtifactDeletion.ABSENT
        return synchronized(retainedAnalysisImageLock) {
            val file = retainedAnalysisImageFile(File(scan.imagePath), scan.clientScanId)
                ?: return@synchronized ArtifactDeletion.ABSENT
            val atomicArtifacts = listOf(file, File("${file.path}.new"), File("${file.path}.bak"))
            val existed = atomicArtifacts.any(File::exists)
            if (!existed) return@synchronized ArtifactDeletion.ABSENT
            runCatching { AtomicFile(file).delete() }
            if (atomicArtifacts.any(File::exists)) {
                ArtifactDeletion.FAILED
            } else {
                ArtifactDeletion.DELETED
            }
        }
    }

    private fun retainedAnalysisImageFile(source: File, scanId: String): File? {
        val parent = source.parentFile ?: return null
        val key = MessageDigest.getInstance("SHA-256")
            .digest(scanId.encodeToByteArray())
            .joinToString("") { "%02x".format(it.toInt() and 0xff) }
        return File(parent, ".${source.name}.analysis-$key.jpg")
    }

    private data class PendingAnalysisImageKey(
        val sourcePath: String,
        val scanId: String,
    )

    private enum class ArtifactDeletion {
        ABSENT,
        DELETED,
        FAILED,
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
     * The caller must send the retained /api/analyze image bytes; see
     * [CommunityUploader.uploadIfEnabled]. The publication capability is
     * SHA-bound, so a separately encoded thumbnail is never valid here.
     */
    suspend fun uploadCommunityScan(
        scanId: String,
        country: String? = null,
        region: String = "",
        lat: Double? = null,
        lon: Double? = null,
        disease: String,
        confidence: Float? = null,
        species: String? = null,
        speciesScore: Float? = null,
        locale: String? = null,
        analysisImage: SanitizedImage,
    ): Result<com.plantdiseases.app.data.model.CommunityScanUploadResponse> =
        withContext(Dispatchers.IO) {
            authenticatedAppCall { token ->
                val response = apiClient.httpClient.post("api/community/scan") {
                    bearer(token)
                    setBody(
                        MultiPartFormDataContent(
                            formData {
                                // Required fields (callers always supply these).
                                append("scan_id", scanId)
                                country?.takeIf { it.isNotBlank() }?.let { append("country", it) }
                                append("disease", disease)
                                // Optional fields — only sent when present/non-blank,
                                // matching the old `text()` helper exactly.
                                region.takeIf { it.isNotBlank() }?.let { append("region", it) }
                                lat?.let { append("lat", it.toString()) }
                                lon?.let { append("lon", it.toString()) }
                                confidence?.let { append("confidence", it.toString()) }
                                species?.takeIf { it.isNotBlank() }?.let { append("species", it) }
                                speciesScore?.let { append("species_score", it.toString()) }
                                locale?.takeIf { it.isNotBlank() }?.let { append("locale", it) }
                                append(
                                    "image",
                                    analysisImage.bytes,
                                    Headers.build {
                                        append(HttpHeaders.ContentType, "image/jpeg")
                                        append(
                                            HttpHeaders.ContentDisposition,
                                            "filename=\"${analysisImage.filename}\"",
                                        )
                                    },
                                )
                            },
                        ),
                    )
                }
                response.protectedBody<com.plantdiseases.app.data.model.CommunityScanUploadResponse>()
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
        lat: Double? = null,
        lon: Double? = null,
        disease: String?,
        species: String? = null,
        limit: Int = 12,
        scope: String? = null,
        // `recent` (default) or `recommended`. The server rejects anything
        // else with 422, and pairs both orders with the opaque `cursor`
        // below — there is no `before` timestamp cursor any more, because
        // recommendation order is score + time + id, not time alone.
        ranking: String? = null,
        cursor: String? = null,
    ): Result<com.plantdiseases.app.data.model.CommunityScansResponse> =
        withContext(Dispatchers.IO) {
            optionalAccountGet { token ->
                apiClient.httpClient.get("api/community/nearby") {
                    token?.let { bearer(it) }
                    country?.takeIf { it.isNotBlank() }?.let { parameter("country", it) }
                    region?.takeIf { it.isNotBlank() }?.let { parameter("region", it) }
                    lat?.let { parameter("lat", it) }
                    lon?.let { parameter("lon", it) }
                    disease?.takeIf { it.isNotBlank() }?.let { parameter("disease", it) }
                    species?.takeIf { it.isNotBlank() }?.let { parameter("species", it) }
                    scope?.takeIf { it.isNotBlank() }?.let { parameter("scope", it) }
                    ranking?.takeIf { it.isNotBlank() }?.let { parameter("ranking", it) }
                    cursor?.takeIf { it.isNotBlank() }?.let { parameter("cursor", it) }
                    parameter("limit", limit)
                }
            }
        }

    /** Individual community scans inside a map bbox (zoomed-in markers). */
    suspend fun communityMap(
        minLat: Double,
        minLon: Double,
        maxLat: Double,
        maxLon: Double,
        disease: String? = null,
        days: Int = 30,
        limit: Int = 300,
    ): Result<com.plantdiseases.app.data.model.CommunityScansResponse> =
        withContext(Dispatchers.IO) {
            optionalAccountGet { token ->
                apiClient.httpClient.get("api/community/map") {
                    token?.let { bearer(it) }
                    parameter("min_lat", minLat)
                    parameter("min_lon", minLon)
                    parameter("max_lat", maxLat)
                    parameter("max_lon", maxLon)
                    disease?.takeIf { it.isNotBlank() }?.let { parameter("disease", it) }
                    parameter("days", days)
                    parameter("limit", limit)
                }
            }
        }

    /** K-anonymized scan density for the zoomed-out map heat layer. */
    suspend fun communityHeatmap(
        minLat: Double,
        minLon: Double,
        maxLat: Double,
        maxLon: Double,
        disease: String? = null,
        days: Int = 30,
    ): Result<com.plantdiseases.app.data.model.CommunityHeatmapResponse> =
        withContext(Dispatchers.IO) {
            try {
                val response = apiClient.httpClient.get("api/community/heatmap") {
                    parameter("min_lat", minLat)
                    parameter("min_lon", minLon)
                    parameter("max_lat", maxLat)
                    parameter("max_lon", maxLon)
                    disease?.takeIf { it.isNotBlank() }?.let { parameter("disease", it) }
                    parameter("days", days)
                }
                when (response.status.value) {
                    in 200..299 -> Result.success(
                        response.body<com.plantdiseases.app.data.model.CommunityHeatmapResponse>(),
                    )
                    else -> Result.failure(
                        Exception("Community heatmap failed: ${response.status.value}"),
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
            // v2: image_url/images are now server-proxied (api/plant-photo).
            // v3: those proxied URLs now point at a card-sized Wikimedia render
            // instead of the multi-megabyte original, which is what made lead
            // photos time out and fall back to a placeholder.
            val cacheKey = "species:v3:${name.trim().lowercase()}|$locale"
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

    // v2: regenerated by a different model at a higher reasoning effort, so
    // v1 entries are the previous generation's answer — a week of them would
    // hide the change entirely.
    private fun speciesDiseasesCacheKey(name: String, locale: String): String =
        "species_diseases:v2:${name.trim().lowercase()}|$locale"

    /** Peek the client cache for a previously-fetched species-diseases list,
     *  without ever hitting the network. Lets the UI render a reopened species
     *  instantly (no loading flash) instead of looking like it re-fetches. */
    suspend fun cachedSpeciesDiseases(
        name: String,
        locale: String,
    ): com.plantdiseases.app.data.model.SpeciesDiseasesResponse? =
        withContext(Dispatchers.IO) {
            com.plantdiseases.app.util.ResponseCache.get(
                speciesDiseasesCacheKey(name, locale),
                com.plantdiseases.app.data.model.SpeciesDiseasesResponse::class.java,
            )
        }

    /** Conditions a species is commonly affected by (AI, best-effort). The
     *  answer is stable per (name, locale), and the LLM call is slow, so cache
     *  a real (`available`) result — reopening the species no longer re-hits
     *  the model and re-renders the list from scratch. */
    suspend fun fetchSpeciesDiseases(
        name: String,
        common: String?,
        locale: String,
    ): Result<com.plantdiseases.app.data.model.SpeciesDiseasesResponse> =
        withContext(Dispatchers.IO) {
            val cacheKey = speciesDiseasesCacheKey(name, locale)
            com.plantdiseases.app.util.ResponseCache
                .get(cacheKey, com.plantdiseases.app.data.model.SpeciesDiseasesResponse::class.java)
                ?.let { return@withContext Result.success(it) }
            try {
                val body = apiClient.speciesDiseases(name, common, locale)
                if (body != null) {
                    // Cache the answer either way so reopening the same species
                    // never re-fetches: a real AI hit stays a week; an
                    // "unavailable" answer (which renders the bundled guide) is
                    // kept only briefly so it still retries within the day once
                    // the AI becomes available. Only a hard network failure
                    // (body == null) is left uncached to retry on next open.
                    com.plantdiseases.app.util.ResponseCache.put(
                        cacheKey,
                        body,
                        if (body.available) {
                            com.plantdiseases.app.util.ResponseCache.TTL_SPECIES_MS
                        } else {
                            com.plantdiseases.app.util.ResponseCache.TTL_SPECIES_MISS_MS
                        },
                    )
                    Result.success(body)
                } else {
                    Result.failure(Exception("species-diseases failed"))
                }
            } catch (e: Exception) {
                Result.failure(e)
            }
        }

    // v4: matches the server's v4 profile generation (new model, high reasoning
    // effort, schema-constrained flowering calendar + native range).
    private fun speciesProfileCacheKey(name: String): String =
        "species_profile:v4:${name.trim().lowercase(java.util.Locale.ROOT)}"

    /**
     * Flowering and native-range profile (AI, best-effort). Real profiles are
     * stable botanical metadata and use the species TTL; unavailable answers
     * are retained only briefly so a restored provider is picked up later.
     */
    suspend fun fetchSpeciesProfile(
        name: String,
        common: String?,
        locale: String,
    ): Result<com.plantdiseases.app.data.model.SpeciesProfileResponse> =
        withContext(Dispatchers.IO) {
            // The canonical profile contains both EN/RU text copies, so one cached response is
            // safe to reuse after an offline language switch.
            val cacheKey = speciesProfileCacheKey(name)
            com.plantdiseases.app.util.ResponseCache
                .get(cacheKey, com.plantdiseases.app.data.model.SpeciesProfileResponse::class.java)
                ?.let { return@withContext Result.success(it) }
            try {
                val body = apiClient.speciesProfile(name, common, locale)
                if (body != null) {
                    com.plantdiseases.app.util.ResponseCache.put(
                        cacheKey,
                        body,
                        if (body.available) {
                            com.plantdiseases.app.util.ResponseCache.TTL_SPECIES_MS
                        } else {
                            com.plantdiseases.app.util.ResponseCache.TTL_SPECIES_MISS_MS
                        },
                    )
                    Result.success(body)
                } else {
                    Result.failure(Exception("species-profile failed"))
                }
            } catch (e: Exception) {
                Result.failure(e)
            }
        }

    /** Community scans of one species (scientific name), across regions. */
    suspend fun communityScansBySpecies(
        species: String,
        limit: Int = 12,
    ): Result<com.plantdiseases.app.data.model.CommunityScansResponse> =
        withContext(Dispatchers.IO) {
            optionalAccountGet { token ->
                apiClient.httpClient.get("api/community/by-species") {
                    token?.let { bearer(it) }
                    parameter("species", species)
                    parameter("limit", limit)
                }
            }
        }
}
