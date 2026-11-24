package com.plantdiseases.app.data.repository

import com.plantdiseases.app.data.model.SpeciesDiseasesResponse
import com.plantdiseases.app.data.model.SpeciesInfoResponse
import com.plantdiseases.app.data.model.SpeciesProfileResponse
import com.plantdiseases.app.data.remote.PlantApiClient
import com.plantdiseases.app.util.ResponseCache
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** Species metadata lookups and their persistent cache policy. */
class SpeciesRepository(private val apiClient: PlantApiClient) {
    /** Localized Wikipedia blurb + lead image for a scientific name. */
    suspend fun getSpeciesInfo(
        name: String,
        locale: String,
    ): Result<SpeciesInfoResponse> =
        withContext(Dispatchers.IO) {
            // Wikipedia blurb + lead image are stable per (name, locale) — cache
            // once available so the result screen doesn't re-fetch every open.
            // v2: image_url/images are now server-proxied (api/plant-photo).
            // v3: those proxied URLs now point at a card-sized Wikimedia render
            // instead of the multi-megabyte original, which is what made lead
            // photos time out and fall back to a placeholder.
            // v4: entries cached before the server learned the new thumbnail
            // host and the Wikidata/iNaturalist fallbacks are photo-less
            // misses that would otherwise stick for a week.
            val cacheKey = "species:v4:${name.trim().lowercase()}|$locale"
            ResponseCache
                .get<SpeciesInfoResponse>(cacheKey)
                ?.let { return@withContext Result.success(it) }
            try {
                val body = apiClient.speciesInfo(name, locale)
                if (body != null) {
                    // Don't cache a transient "unavailable" (e.g. Wikipedia
                    // miss / rate-limit) — only a real hit is worth keeping.
                    // A genus-level or photo-less hit is kept only briefly so
                    // the complete answer replaces it once the server has it.
                    if (body.available) {
                        val complete = !body.imageUrl.isNullOrBlank() && body.level != "genus"
                        ResponseCache.put(
                            cacheKey,
                            body,
                            if (complete) {
                                ResponseCache.TTL_SPECIES_MS
                            } else {
                                ResponseCache.TTL_SPECIES_DEGRADED_MS
                            },
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
    ): SpeciesDiseasesResponse? =
        withContext(Dispatchers.IO) {
            ResponseCache.get<SpeciesDiseasesResponse>(speciesDiseasesCacheKey(name, locale))
        }

    /** Conditions a species is commonly affected by (AI, best-effort). The
     *  answer is stable per (name, locale), and the LLM call is slow, so cache
     *  a real (`available`) result — reopening the species no longer re-hits
     *  the model and re-renders the list from scratch. */
    suspend fun fetchSpeciesDiseases(
        name: String,
        common: String?,
        locale: String,
    ): Result<SpeciesDiseasesResponse> =
        withContext(Dispatchers.IO) {
            val cacheKey = speciesDiseasesCacheKey(name, locale)
            ResponseCache
                .get<SpeciesDiseasesResponse>(cacheKey)
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
                    ResponseCache.put(
                        cacheKey,
                        body,
                        if (body.available) {
                            ResponseCache.TTL_SPECIES_MS
                        } else {
                            ResponseCache.TTL_SPECIES_MISS_MS
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
    ): Result<SpeciesProfileResponse> =
        withContext(Dispatchers.IO) {
            // The canonical profile contains both EN/RU text copies, so one cached response is
            // safe to reuse after an offline language switch.
            val cacheKey = speciesProfileCacheKey(name)
            ResponseCache
                .get<SpeciesProfileResponse>(cacheKey)
                ?.let { return@withContext Result.success(it) }
            try {
                val body = apiClient.speciesProfile(name, common, locale)
                if (body != null) {
                    ResponseCache.put(
                        cacheKey,
                        body,
                        if (body.available) {
                            ResponseCache.TTL_SPECIES_MS
                        } else {
                            ResponseCache.TTL_SPECIES_MISS_MS
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
}
