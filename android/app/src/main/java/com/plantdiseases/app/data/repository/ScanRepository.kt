package com.plantdiseases.app.data.repository

import android.content.Context
import com.google.gson.Gson
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
import com.plantdiseases.app.util.CommunityIdentity
import com.plantdiseases.app.util.CommunityPrefs
import com.plantdiseases.app.util.ImageUtils
import com.plantdiseases.app.util.Nicknames
import com.plantdiseases.app.util.TelemetryIds
import io.ktor.client.call.body
import io.ktor.client.request.forms.MultiPartFormDataContent
import io.ktor.client.request.forms.formData
import io.ktor.client.request.get
import io.ktor.client.request.header
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
import java.io.File
import java.net.SocketTimeoutException
import java.net.UnknownHostException

/** The install's pseudo-id authorises reads of its own Community state, so it
 *  travels in this header and never in a URL — proxies and access logs record
 *  URLs. Servers that predate the header ignore it (it is optional there). */
private const val USER_PSEUDO_ID_HEADER = "X-User-Pseudo-Id"

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

    // The key hash the server has already acknowledged, so the adopt POST
    // rides along with the first community write of the process rather than
    // every one of them. Keyed on the hash rather than a boolean because
    // "Leave community" calls CommunityIdentity.clear(), which mints a fresh
    // secret — a boolean would suppress the adopt for that new identity until
    // the process restarted.
    @Volatile
    private var adoptedAuthorKeyHash: String? = null

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
                // Re-encoded, never readBytes(): the file can still carry the
                // camera's EXIF (GPS included) — see ImageUtils.encodeForUpload.
                val imageBytes = ImageUtils.encodeForUpload(imageFile.path)
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
            } catch (e: ImageUtils.ImageDecodeException) {
                Result.failure(UnsupportedImageException())
            } catch (e: OutOfMemoryError) {
                Result.failure(UnsupportedImageException())
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

    /** List community "second opinions" + consensus for a shared scan.
     *  [userPseudoId] is optional — pass the caller's own id to get an
     *  accurate `likedByMe` in the response. [context] is optional too and
     *  drives `myOpinion` the same way — omit it (e.g. a background poll
     *  with no UI context handy) and it just reads as "no opinion yet"
     *  instead of failing; see opinions_for's docstring server-side for why
     *  `myOpinion` keys on the device identity rather than userPseudoId. */
    suspend fun getCommunityOpinions(
        scanId: String,
        userPseudoId: String? = null,
        context: Context? = null,
    ): Result<CommunityOpinionsResponse> =
        withContext(Dispatchers.IO) {
            val authorKey = adoptedCommunityKey(context)
            try {
                val response = apiClient.httpClient.get("api/community/opinions/$scanId") {
                    userPseudoId?.takeIf { it.isNotBlank() }?.let {
                        header(USER_PSEUDO_ID_HEADER, it)
                    }
                    authorKey?.let { header("X-Community-Author-Key", it) }
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

    /** Submit one community "second opinion". Returns the refreshed list.
     *  [context] resolves the device's durable identity key — reachable
     *  unconditionally here because posting an opinion is only ever
     *  reachable after the user has already opted into community sharing
     *  (CommunityController / NearbyScansActivity both gate on
     *  CommunityPrefs.uploadsEnabled before this call site is reachable at
     *  all), so sending it here doesn't contradict the privacy policy's
     *  "nothing sent while community sharing is off". */
    suspend fun submitCommunityOpinion(
        context: Context,
        request: CommunityOpinionRequest,
    ): Result<CommunityOpinionsResponse> =
        withContext(Dispatchers.IO) {
            // The server refuses an opinion without an adopted author key (the
            // consensus tally dedupes on it), so don't send one that can only
            // come back 401 — adoption failed, most likely offline.
            val authorKey = adoptedCommunityKey(context)
                ?: return@withContext Result.failure(CommunityIdentityUnavailableException())
            try {
                val response = apiClient.httpClient.post("api/community/opinion") {
                    contentType(ContentType.Application.Json)
                    header("X-Community-Author-Key", authorKey)
                    setBody(request)
                }
                when (response.status.value) {
                    in 200..299 -> Result.success(response.body<CommunityOpinionsResponse>())
                    423 -> Result.failure(CommunityOpinionsLockedException())
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
     * Withdraw an open second-opinion request on the user's OWN scan —
     * the community stops seeing the ask and no further opinion
     * notifications arrive for it. Opinions already left stay visible.
     * Returns `true` when an open request was actually cleared (requests
     * also lapse server-side on their own after 72 h).
     */
    suspend fun cancelSecondOpinion(
        scanId: String,
        userPseudoId: String,
    ): Result<Boolean> =
        withContext(Dispatchers.IO) {
            try {
                val response = apiClient.httpClient.post("api/community/request-opinion/cancel") {
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
                        response.body<com.plantdiseases.app.data.model.CommunityCancelOpinionResponse>()
                            .cleared,
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
            // Re-encoded, never readBytes(): the file can still carry the
            // camera's EXIF (GPS included) — see ImageUtils.encodeForUpload.
            val imageBytes = ImageUtils.encodeForUpload(imageFile.path)
            val response = apiClient.httpClient.post("api/feedback/image") {
                setBody(
                    MultiPartFormDataContent(
                        formData {
                            append("scan_id", scanId)
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
            if (response.status.value in 200..299) {
                Result.success(Unit)
            } else {
                Result.failure(Exception("Server error: ${response.status.value}"))
            }
        } catch (e: OutOfMemoryError) {
            Result.failure(ImageUtils.ImageDecodeException("Out of memory re-encoding feedback image", e))
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
    class CommunityIdentityUnavailableException : Exception("Community author key is not adopted")

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
                ?.takeIf { accepted }
                ?.takeIf { it.isNotEmpty() }
                ?.let { gson.toJson(it) }
            // The probability-backed diagnostic argmax is preserved even when
            // a separate abstention verdict prevents diagnosis presentation.
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
                val r = apiClient.httpClient.get("api/community/my-scan-opinions") {
                    header(USER_PSEUDO_ID_HEADER, pseudoId)
                }
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
    suspend fun recordCommunityView(
        scanId: String,
        viewerPseudoId: String,
        context: Context? = null,
    ) {
        withContext(Dispatchers.IO) {
            val authorKey = adoptedCommunityKey(context)
            try {
                apiClient.httpClient.post("api/community/view") {
                    contentType(ContentType.Application.Json)
                    authorKey?.let { header("X-Community-Author-Key", it) }
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

    /** Record that this install scrolled past (not necessarily opened) up to
     *  50 shared scans in the feed. Same fire-and-forget contract as
     *  [recordCommunityView]; the caller is expected to have already
     *  filtered out ids already reported this screen session. */
    suspend fun recordCommunityViews(
        scanIds: List<String>,
        viewerPseudoId: String,
        context: Context? = null,
    ) {
        if (scanIds.isEmpty()) return
        withContext(Dispatchers.IO) {
            val authorKey = adoptedCommunityKey(context)
            try {
                apiClient.httpClient.post("api/community/view") {
                    contentType(ContentType.Application.Json)
                    authorKey?.let { header("X-Community-Author-Key", it) }
                    setBody(
                        com.plantdiseases.app.data.model.CommunityViewRequest(
                            scanIds = scanIds.take(50),
                            userPseudoId = viewerPseudoId,
                        ),
                    )
                }
            } catch (_: Exception) {
                // best-effort telemetry; never surface to the user
            }
        }
    }

    /** Like or unlike a shared scan. Returns the resulting liked state on
     *  success — the caller should already have applied it optimistically
     *  and only needs to roll back on failure. */
    suspend fun setCommunityLike(
        scanId: String,
        userPseudoId: String,
        liked: Boolean,
        context: Context? = null,
    ): Result<com.plantdiseases.app.data.model.CommunityLikeResponse> =
        withContext(Dispatchers.IO) {
            val authorKey = adoptedCommunityKey(context)
            try {
                val response = apiClient.httpClient.post("api/community/like") {
                    contentType(ContentType.Application.Json)
                    authorKey?.let { header("X-Community-Author-Key", it) }
                    setBody(
                        com.plantdiseases.app.data.model.CommunityLikeRequest(
                            scanId = scanId,
                            userPseudoId = userPseudoId,
                            liked = liked,
                        ),
                    )
                }
                when (response.status.value) {
                    in 200..299 -> Result.success(
                        response.body<com.plantdiseases.app.data.model.CommunityLikeResponse>(),
                    )
                    else -> Result.failure(Exception("Server error: ${response.status.value}"))
                }
            } catch (e: Exception) {
                Result.failure(e)
            }
        }

    /** Report a scan or reply without requiring an account. The server
     * deduplicates the same reporter/target pair. */
    suspend fun reportCommunityContent(
        userPseudoId: String,
        targetType: String,
        targetId: String,
        reason: String,
        context: Context? = null,
    ): Result<com.plantdiseases.app.data.model.CommunitySafetyResponse> =
        withContext(Dispatchers.IO) {
            val authorKey = adoptedCommunityKey(context)
            try {
                val response = apiClient.httpClient.post("api/community/safety/report") {
                    contentType(ContentType.Application.Json)
                    authorKey?.let { header("X-Community-Author-Key", it) }
                    setBody(
                        com.plantdiseases.app.data.model.CommunityReportRequest(
                            userPseudoId = userPseudoId,
                            targetType = targetType,
                            targetId = targetId,
                            reason = reason,
                        ),
                    )
                }
                if (response.status.value in 200..299) {
                    Result.success(response.body<com.plantdiseases.app.data.model.CommunitySafetyResponse>())
                } else {
                    Result.failure(Exception("Server error: ${response.status.value}"))
                }
            } catch (e: Exception) {
                Result.failure(e)
            }
        }

    /** Block the anonymous author of a scan or reply. Future feed/reply reads
     * hide both directions of the block on the server. */
    suspend fun blockCommunityAuthor(
        userPseudoId: String,
        targetType: String,
        targetId: String,
        context: Context? = null,
    ): Result<com.plantdiseases.app.data.model.CommunitySafetyResponse> =
        withContext(Dispatchers.IO) {
            val authorKey = adoptedCommunityKey(context)
            try {
                val response = apiClient.httpClient.post("api/community/safety/block") {
                    contentType(ContentType.Application.Json)
                    authorKey?.let { header("X-Community-Author-Key", it) }
                    setBody(
                        com.plantdiseases.app.data.model.CommunityBlockRequest(
                            userPseudoId = userPseudoId,
                            targetType = targetType,
                            targetId = targetId,
                        ),
                    )
                }
                if (response.status.value in 200..299) {
                    Result.success(response.body<com.plantdiseases.app.data.model.CommunitySafetyResponse>())
                } else {
                    Result.failure(Exception("Server error: ${response.status.value}"))
                }
            } catch (e: Exception) {
                Result.failure(e)
            }
        }

    /**
     * Create-or-return this device's durable forum identity. [keyHash] is
     * [com.plantdiseases.app.util.CommunityIdentity.keyHash] — never the raw
     * secret — sent as a header, never in the body or a URL (see
     * CommunityIdentity's kdoc for why). Scan and opinion writes send the
     * same key so server-side author dedupe survives a changed pseudo id.
     *
     * This is the raw transport. Production code goes through
     * [ensureCommunityAuthorAdopted], which applies the community opt-in
     * gate; calling this directly bypasses it.
     *
     * [legacyPseudoId] attaches Community activity written by older app versions
     * to this durable identity. That pseudonym can also appear on diagnosis and
     * feedback records, so same-device records can be linkable; onboarding and
     * the privacy policy disclose that explicitly.
     */
    suspend fun adoptCommunityAuthor(
        keyHash: String,
        seedDisplayName: String,
        legacyPseudoId: String? = null,
    ): Result<com.plantdiseases.app.data.model.CommunityAuthorAdoptResponse> =
        withContext(Dispatchers.IO) {
            try {
                val response = apiClient.httpClient.post("api/community/authors/adopt") {
                    header("X-Community-Author-Key", keyHash)
                    contentType(ContentType.Application.Json)
                    setBody(
                        com.plantdiseases.app.data.model.CommunityAuthorAdoptRequest(
                            seedDisplayName = seedDisplayName,
                            legacyPseudoId = legacyPseudoId,
                        ),
                    )
                }
                when (response.status.value) {
                    in 200..299 -> Result.success(
                        response.body<com.plantdiseases.app.data.model.CommunityAuthorAdoptResponse>(),
                    )
                    else -> Result.failure(Exception("Server error: ${response.status.value}"))
                }
            } catch (e: Exception) {
                Result.failure(e)
            }
        }

    /**
     * Lazily register this device's durable forum identity — the consent-gated
     * entry point every community write should call.
     *
     * Adoption is not free: it mints [CommunityIdentity.keyHash], a permanent
     * never-rotating per-device value, and creates a permanent server-side row
     * whose `last_seen_at` is refreshed on every call. Doing that from
     * `Application.onCreate()` would transmit a per-device launch log before
     * the onboarding prominent-disclosure screen has even rendered, and for
     * users who opted into nothing — so it happens here instead, behind
     * [CommunityPrefs.uploadsEnabled] (default OFF, exactly the gate
     * CommunityUploader already applies).
     *
     * [force] is for the second-opinion path, where the tap that shares one
     * scan is itself the consent and deliberately bypasses the global toggle —
     * exactly as `CommunityUploader.uploadFromEntity` does. It never bypasses
     * anything else: this is still only reached from a real share.
     *
     * Best-effort and silent on failure; nothing in the app reads the response
     * yet. Only the first success per process actually hits the network.
     */
    suspend fun ensureCommunityAuthorAdopted(context: Context, force: Boolean = false): Boolean {
        if (!force && !CommunityPrefs.uploadsEnabled(context)) return false
        val keyHash = CommunityIdentity.keyHash(context)
        if (keyHash == adoptedAuthorKeyHash) return true
        val adopted = adoptCommunityAuthor(
            keyHash = keyHash,
            seedDisplayName = Nicknames.nickname(context),
            legacyPseudoId = TelemetryIds.userPseudoId(context),
        ).isSuccess
        if (adopted) adoptedAuthorKeyHash = keyHash
        return adopted
    }

    /** Return a durable header only after the explicit adopt request has
     * succeeded. This prevents a keyed read from creating a blank server-side
     * author before the adoption request can attach legacy Community activity. */
    private suspend fun adoptedCommunityKey(context: Context?): String? {
        if (context == null) return null
        val enabled = CommunityPrefs.uploadsEnabled(context)
        // Do not mint a key merely because an opt-out code path asks for a
        // header. A forced one-scan share may already have adopted one, though.
        if (!enabled && adoptedAuthorKeyHash == null) return null
        val keyHash = CommunityIdentity.keyHash(context)
        if (keyHash == adoptedAuthorKeyHash) return keyHash
        if (!enabled || !ensureCommunityAuthorAdopted(context)) return null
        return keyHash
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
        scanDao.deleteAll()
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
        val cutoff = System.currentTimeMillis() - olderThanMs
        val stale = scanDao.getAllScans()
            .filter { it.timestamp < cutoff && isAbstainedScan(it) }
        for (s in stale) scanDao.deleteById(s.id)
        stale.size
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
     * [CommunityUploader.uploadIfEnabled] which handles the
     * opt-out pref + thumbnail downscale before reaching this hop.
     */
    suspend fun uploadCommunityScan(
        scanId: String,
        userPseudoId: String,
        country: String? = null,
        region: String = "",
        lat: Double? = null,
        lon: Double? = null,
        disease: String,
        confidence: Float? = null,
        species: String? = null,
        speciesScore: Float? = null,
        locale: String? = null,
        nickname: String? = null,
        thumbnail: File? = null,
        context: Context? = null,
    ): Result<com.plantdiseases.app.data.model.CommunityScanUploadResponse> =
        withContext(Dispatchers.IO) {
            val authorKey = adoptedCommunityKey(context)
            try {
                val response = apiClient.httpClient.post("api/community/scan") {
                    authorKey?.let { header("X-Community-Author-Key", it) }
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
                                lat?.let { append("lat", it.toString()) }
                                lon?.let { append("lon", it.toString()) }
                                confidence?.let { append("confidence", it.toString()) }
                                species?.takeIf { it.isNotBlank() }?.let { append("species", it) }
                                speciesScore?.let { append("species_score", it.toString()) }
                                locale?.takeIf { it.isNotBlank() }?.let { append("locale", it) }
                                nickname?.takeIf { it.isNotBlank() }?.let { append("nickname", it) }
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
        lat: Double? = null,
        lon: Double? = null,
        disease: String?,
        species: String? = null,
        userPseudoId: String?,
        limit: Int = 12,
        scope: String? = null,
        ranking: String? = null,
        cursor: String? = null,
        context: Context? = null,
    ): Result<com.plantdiseases.app.data.model.CommunityScansResponse> =
        withContext(Dispatchers.IO) {
            val authorKey = adoptedCommunityKey(context)
            try {
                val response = apiClient.httpClient.get("api/community/nearby") {
                    authorKey?.let { header("X-Community-Author-Key", it) }
                    country?.takeIf { it.isNotBlank() }?.let { parameter("country", it) }
                    region?.takeIf { it.isNotBlank() }?.let { parameter("region", it) }
                    lat?.let { parameter("lat", it) }
                    lon?.let { parameter("lon", it) }
                    disease?.takeIf { it.isNotBlank() }?.let { parameter("disease", it) }
                    species?.takeIf { it.isNotBlank() }?.let { parameter("species", it) }
                    userPseudoId?.takeIf { it.isNotBlank() }?.let {
                        header(USER_PSEUDO_ID_HEADER, it)
                    }
                    scope?.takeIf { it.isNotBlank() }?.let { parameter("scope", it) }
                    ranking?.takeIf { it.isNotBlank() }?.let { parameter("ranking", it) }
                    cursor?.takeIf { it.isNotBlank() }?.let { parameter("cursor", it) }
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

    /** Individual community scans inside a map bbox (zoomed-in markers). */
    suspend fun communityMap(
        minLat: Double,
        minLon: Double,
        maxLat: Double,
        maxLon: Double,
        disease: String? = null,
        days: Int = 30,
        userPseudoId: String? = null,
        limit: Int = 300,
        context: Context? = null,
    ): Result<com.plantdiseases.app.data.model.CommunityScansResponse> =
        withContext(Dispatchers.IO) {
            val authorKey = adoptedCommunityKey(context)
            try {
                val response = apiClient.httpClient.get("api/community/map") {
                    authorKey?.let { header("X-Community-Author-Key", it) }
                    parameter("min_lat", minLat)
                    parameter("min_lon", minLon)
                    parameter("max_lat", maxLat)
                    parameter("max_lon", maxLon)
                    disease?.takeIf { it.isNotBlank() }?.let { parameter("disease", it) }
                    parameter("days", days)
                    userPseudoId?.takeIf { it.isNotBlank() }?.let {
                        header(USER_PSEUDO_ID_HEADER, it)
                    }
                    parameter("limit", limit)
                }
                when (response.status.value) {
                    in 200..299 -> Result.success(
                        response.body<com.plantdiseases.app.data.model.CommunityScansResponse>(),
                    )
                    else -> Result.failure(Exception("Community map failed: ${response.status.value}"))
                }
            } catch (e: Exception) {
                Result.failure(e)
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

    // v3: matches the server's v3 generation, which retired answers whose
    // prompt carried a caller's unkeyed common name — a cached v2 answer here
    // could still be one of those for up to a week.
    private fun speciesDiseasesCacheKey(name: String, locale: String): String =
        "species_diseases:v3:${name.trim().lowercase()}|$locale"

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

    // v5: matches the server's v5 profile generation, which retired profiles
    // whose prompt carried a caller's unkeyed common name.
    private fun speciesProfileCacheKey(name: String): String =
        "species_profile:v5:${name.trim().lowercase(java.util.Locale.ROOT)}"

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
        userPseudoId: String?,
        limit: Int = 12,
        context: Context? = null,
    ): Result<com.plantdiseases.app.data.model.CommunityScansResponse> =
        withContext(Dispatchers.IO) {
            val authorKey = adoptedCommunityKey(context)
            try {
                val response = apiClient.httpClient.get("api/community/by-species") {
                    authorKey?.let { header("X-Community-Author-Key", it) }
                    parameter("species", species)
                    parameter("limit", limit)
                    userPseudoId?.takeIf { it.isNotBlank() }?.let {
                        header(USER_PSEUDO_ID_HEADER, it)
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
}
