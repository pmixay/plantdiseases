package com.plantdiseases.app.util

import android.content.Context
import com.plantdiseases.app.BuildConfig

/**
 * Runtime-configurable server base URL.
 *
 * The app ships with `BuildConfig.API_BASE_URL` as the default (used the
 * first time the app launches, or after "Reset to default" in Profile).
 * Users can override the URL from the Profile screen; the new value is
 * persisted in SharedPreferences and picked up on the next API call.
 */
object ServerConfig {

    private const val PREFS_NAME = "plantdiseases_prefs"
    private const val KEY_SERVER_URL = "server_base_url"

    @Volatile
    private var cached: String? = null

    fun defaultUrl(): String = BuildConfig.API_BASE_URL

    fun getBaseUrl(context: Context): String {
        cached?.let { return it }
        val prefs = context.applicationContext
            .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        val stored = prefs.getString(KEY_SERVER_URL, null)
        val value = normalize(stored) ?: defaultUrl()
        cached = value
        return value
    }

    /** Persist a new URL. Returns the normalised value on success, null if invalid. */
    fun setBaseUrl(context: Context, rawUrl: String): String? {
        val normalised = normalize(rawUrl) ?: return null
        context.applicationContext
            .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_SERVER_URL, normalised)
            .apply()
        cached = normalised
        return normalised
    }

    fun resetToDefault(context: Context): String {
        context.applicationContext
            .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .remove(KEY_SERVER_URL)
            .apply()
        val value = defaultUrl()
        cached = value
        return value
    }

    /**
     * Accepts `host:port` or a full URL. Returns a string ending in `/`.
     * Returns null for empty / clearly-malformed input so callers can show
     * a validation message.
     */
    fun normalize(raw: String?): String? {
        val trimmed = raw?.trim().orEmpty()
        if (trimmed.isEmpty()) return null
        val withScheme = when {
            trimmed.startsWith("http://") || trimmed.startsWith("https://") -> trimmed
            else -> "http://$trimmed"
        }
        val candidate = if (withScheme.endsWith("/")) withScheme else "$withScheme/"
        // Parse to reject obvious garbage ("http://" by itself, spaces, etc.)
        return try {
            val url = java.net.URL(candidate)
            if (url.host.isNullOrBlank()) null else candidate
        } catch (e: java.net.MalformedURLException) {
            null
        }
    }
}
