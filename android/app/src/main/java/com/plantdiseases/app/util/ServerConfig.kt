package com.plantdiseases.app.util

import com.plantdiseases.app.BuildConfig

/** Server base URL is fixed at build time through [BuildConfig.API_BASE_URL]. */
object ServerConfig {
    val baseUrl: String get() = BuildConfig.API_BASE_URL

    /**
     * Resolve a photo URL for loading. The server now returns plant photos as
     * relative proxy paths (`api/plant-photo?u=…`) so they load from our own
     * domain — reliable where Wikimedia is blocked. Absolute URLs (older cached
     * responses, or anything already http) are passed through unchanged.
     */
    fun photoUrl(raw: String): String =
        if (raw.isBlank() || raw.startsWith("http", ignoreCase = true)) raw else baseUrl + raw
}
