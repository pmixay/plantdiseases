package com.plantdiseases.app.util

import android.content.Context

/**
 * Sharing state for the community scan corpus.
 *
 * Default is ON so fresh installs contribute anonymised scans without asking
 * Android for device location. The Profile switch can turn uploads off, and
 * Delete-my-contributions still clears any rows already shared.
 */
object CommunityPrefs {
    private const val PREFS_NAME = "plantdiseases_community"
    private const val KEY_ENABLED = "uploads_enabled"
    internal const val DEFAULT_UPLOADS_ENABLED = true

    fun uploadsEnabled(context: Context): Boolean =
        prefs(context).getBoolean(KEY_ENABLED, DEFAULT_UPLOADS_ENABLED)

    fun setUploadsEnabled(context: Context, enabled: Boolean) {
        prefs(context).edit().putBoolean(KEY_ENABLED, enabled).apply()
    }

    /** Preserve the old call site shape for code that explicitly enables sharing. */
    fun enable(context: Context) = setUploadsEnabled(context, true)

    private fun prefs(context: Context) = context.applicationContext
        .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
}
