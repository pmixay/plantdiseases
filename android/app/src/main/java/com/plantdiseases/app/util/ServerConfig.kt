package com.plantdiseases.app.util

import com.plantdiseases.app.BuildConfig

/** Build-time endpoints. No user-controlled or runtime URL override exists. */
object ServerConfig {
    val appBaseUrl: String get() = BuildConfig.APP_API_BASE_URL
    val accountBaseUrl: String get() = BuildConfig.ACCOUNT_API_BASE_URL

    /**
     * Resolve a photo URL for loading. The server now returns plant photos as
     * relative proxy paths (`api/plant-photo?u=…`) so they load from our own
     * domain — reliable where Wikimedia is blocked. Absolute URLs (older cached
     * responses, or anything already http) are passed through unchanged.
     */
    fun photoUrl(raw: String): String =
        if (raw.isBlank() || raw.startsWith("http", ignoreCase = true)) raw else appBaseUrl + raw
}
