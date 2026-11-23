package com.plantdiseases.app.data.repository

import com.plantdiseases.app.auth.AuthRepository
import com.plantdiseases.app.data.local.ScanChatMessageDao
import com.plantdiseases.app.data.local.ScanChatMessageEntity
import com.plantdiseases.app.data.model.CarePlanRequest
import com.plantdiseases.app.data.model.CarePlanResponse
import com.plantdiseases.app.data.model.ChatRequest
import com.plantdiseases.app.data.model.ChatResponse
import com.plantdiseases.app.data.model.ProgressionRequest
import com.plantdiseases.app.data.model.ProgressionResponse
import com.plantdiseases.app.data.remote.PlantApiClient
import io.ktor.client.call.body
import io.ktor.client.request.get
import io.ktor.client.request.post
import io.ktor.client.request.setBody
import io.ktor.http.ContentType
import io.ktor.http.contentType
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import java.util.UUID
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * The assistant features on the app API: care plans (cached), symptom triage,
 * AI Plant Doctor turns with their scan-keyed transcripts, and progression
 * between two scans. Care plans and chat are metered.
 */
class AssistantRepository(
    private val apiClient: PlantApiClient,
    authRepository: AuthRepository? = null,
    // Nullable so network tests need no Room database; transcript calls
    // quietly no-op without it.
    private val scanChatMessageDao: ScanChatMessageDao? = null,
) {
    private val calls = AppApiCalls(authRepository)

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
            //
            // A species-page plan is the same plan as a healthy scan's, so the
            // two share an entry. A checklist plan has no AI summary: it reuses
            // a cached full plan but is stored apart, so it never stands in for
            // the full plan on the result or plant screens.
            val planKey = "careplan:v3:${request.disease}|${request.species}" +
                "|${request.light}|${request.locale}|${request.season ?: ""}"
            val checklist = request.purpose == CarePlanRequest.PURPOSE_CHECKLIST
            val cacheKey = if (checklist) "$planKey|checklist" else planKey
            com.plantdiseases.app.util.ResponseCache
                .get(planKey, CarePlanResponse::class.java)
                ?.let { return@withContext Result.success(it) }
            if (checklist) {
                com.plantdiseases.app.util.ResponseCache
                    .get(cacheKey, CarePlanResponse::class.java)
                    ?.let { return@withContext Result.success(it) }
            }
            try {
                val requestId = UUID.randomUUID().toString()
                val response = calls.optionalMeteredCall { token ->
                    apiClient.httpClient.post("api/care-plan") {
                        metered(token, requestId)
                        contentType(ContentType.Application.Json)
                        setBody(request)
                    }
                }.getOrElse { return@withContext Result.failure(it) }
                response.meteredDenialOrNull(MeteredFeature.CARE_PLAN)?.let {
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
                val response = calls.optionalMeteredCall { token ->
                    apiClient.httpClient.post("api/chat") {
                        metered(token, requestId)
                        contentType(ContentType.Application.Json)
                        setBody(request)
                    }
                }.getOrElse { return@withContext Result.failure(it) }
                response.meteredDenialOrNull(MeteredFeature.CHAT)?.let {
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
}
