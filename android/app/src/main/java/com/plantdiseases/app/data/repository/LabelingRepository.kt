package com.plantdiseases.app.data.repository

import com.plantdiseases.app.data.model.LabelCard
import com.plantdiseases.app.data.model.LabelClueResponse
import com.plantdiseases.app.data.model.LabelExportResponse
import com.plantdiseases.app.data.model.LabelLoginRequest
import com.plantdiseases.app.data.model.LabelStatsResponse
import com.plantdiseases.app.data.model.LabelVerdictBatch
import com.plantdiseases.app.data.model.LabelVerdictItem
import com.plantdiseases.app.data.remote.PlantApiClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Thin wrapper over the /api/label endpoints for the admin labeling screen.
 * Returns [Result] like the other repositories. A 404 surfaces as
 * [LabelingUnavailableException] so the UI can show the graceful "not available
 * on this server" message (back-compat with older server builds).
 */
class LabelingUnavailableException : Exception("Labeling not available on this server")

class LabelingRepository(
    private val apiClient: PlantApiClient = PlantApiClient(),
) {
    private val api get() = apiClient.apiService

    suspend fun login(password: String): Result<String> = call {
        val r = api.labelLogin(LabelLoginRequest(password))
        if (r.code() == 404) throw LabelingUnavailableException()
        val token = r.body()?.token
        if (!r.isSuccessful || token.isNullOrBlank()) {
            throw Exception("login failed (${r.code()})")
        }
        token
    }

    suspend fun next(
        token: String,
        klass: String?,
        count: Int = 20,
        source: String? = null,
    ): Result<List<LabelCard>> = call {
        val r = api.labelNext(token, count, klass, source)
        if (r.code() == 404) throw LabelingUnavailableException()
        r.body()?.cards ?: throw Exception("next failed (${r.code()})")
    }

    suspend fun submit(token: String, verdicts: List<Pair<Long, String>>): Result<Int> = call {
        val batch = LabelVerdictBatch(verdicts.map { LabelVerdictItem(it.first, it.second) })
        val r = api.labelVerdict(token, batch)
        r.body()?.applied ?: throw Exception("verdict failed (${r.code()})")
    }

    suspend fun stats(token: String): Result<LabelStatsResponse> = call {
        val r = api.labelStats(token)
        r.body() ?: throw Exception("stats failed (${r.code()})")
    }

    suspend fun clue(token: String, klass: String, lang: String): Result<LabelClueResponse> = call {
        val r = api.labelClue(token, klass, lang)
        r.body() ?: throw Exception("clue failed (${r.code()})")
    }

    suspend fun export(token: String): Result<LabelExportResponse> = call {
        val r = api.labelExport(token)
        r.body() ?: throw Exception("export failed (${r.code()})")
    }

    private suspend fun <T> call(block: suspend () -> T): Result<T> =
        withContext(Dispatchers.IO) {
            try {
                Result.success(block())
            } catch (e: Exception) {
                Result.failure(e)
            }
        }
}
