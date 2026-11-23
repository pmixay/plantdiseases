package com.plantdiseases.app.data.repository

import com.plantdiseases.app.auth.AuthRepository
import com.plantdiseases.app.data.remote.PlantApiClient
import com.plantdiseases.app.util.ResolvedRegion
import com.plantdiseases.app.util.SanitizedImage
import io.ktor.client.call.body
import io.ktor.client.request.delete
import io.ktor.client.request.forms.MultiPartFormDataContent
import io.ktor.client.request.forms.formData
import io.ktor.client.request.get
import io.ktor.client.request.parameter
import io.ktor.client.request.post
import io.ktor.client.request.setBody
import io.ktor.http.ContentType
import io.ktor.http.Headers
import io.ktor.http.HttpHeaders
import io.ktor.http.contentType
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Shared scans on the app API: publishing a scan, nearby and per-species
 * strips, the community map and its heat layer, view counts, and the
 * blocked-authors list. Reads work for guests; writes need an account.
 */
class CommunityScanRepository(
    private val apiClient: PlantApiClient,
    authRepository: AuthRepository? = null,
) {
    private val calls = AppApiCalls(authRepository)

    suspend fun communityBlocks(): Result<List<com.plantdiseases.app.data.model.CommunityBlockItem>> =
        withContext(Dispatchers.IO) {
            calls.authenticatedAppCall { token ->
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
            calls.authenticatedAppCall { token ->
                val response = apiClient.httpClient.delete(
                    "api/community/safety/blocks/$blockId",
                ) {
                    bearer(token)
                }
                response.requireProtectedSuccess()
            }
        }

    /** Record an authenticated view. Guests browse without creating a row. */
    suspend fun recordCommunityView(scanId: String): Result<Unit> =
        withContext(Dispatchers.IO) {
            calls.authenticatedAppCall { token ->
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
            calls.authenticatedAppCall { token ->
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
            calls.optionalAccountGet { token ->
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
            calls.optionalAccountGet { token ->
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

    /** Community scans of one species. A member's coarse location orders them
     *  nearest first (server-side); without it they span all regions. */
    suspend fun communityScansBySpecies(
        species: String,
        limit: Int = 12,
        region: ResolvedRegion? = null,
    ): Result<com.plantdiseases.app.data.model.CommunityScansResponse> =
        withContext(Dispatchers.IO) {
            calls.optionalAccountGet { token ->
                apiClient.httpClient.get("api/community/by-species") {
                    token?.let { bearer(it) }
                    parameter("species", species)
                    parameter("limit", limit)
                    region?.country?.takeIf { it.isNotBlank() }?.let { parameter("country", it) }
                    region?.locality?.let { parameter("region", it) }
                    region?.lat?.let { parameter("lat", it) }
                    region?.lon?.let { parameter("lon", it) }
                }
            }
        }
}
