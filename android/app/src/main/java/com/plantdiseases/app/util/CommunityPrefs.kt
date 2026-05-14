package com.plantdiseases.app.util

import android.content.Context

/**
 * Toggle + first-run consent state for the community scan-corpus.
 *
 * Default is ON *after* the user has explicitly acknowledged the
 * first-run sheet — until then `uploadsEnabled` returns false so a
 * brand-new install never silently uploads. The Profile screen
 * exposes a switch that flips this back to off at any time.
 */
object CommunityPrefs {
    private const val PREFS_NAME = "plantdiseases_community"
    private const val KEY_ACKNOWLEDGED = "first_run_acknowledged"
    private const val KEY_ENABLED = "uploads_enabled"

    fun hasAcknowledged(context: Context): Boolean =
        prefs(context).getBoolean(KEY_ACKNOWLEDGED, false)

    fun setAcknowledged(context: Context, enabled: Boolean) {
        prefs(context).edit()
            .putBoolean(KEY_ACKNOWLEDGED, true)
            .putBoolean(KEY_ENABLED, enabled)
            .apply()
    }

    fun uploadsEnabled(context: Context): Boolean =
        hasAcknowledged(context) && prefs(context).getBoolean(KEY_ENABLED, true)

    fun setUploadsEnabled(context: Context, enabled: Boolean) {
        prefs(context).edit().putBoolean(KEY_ENABLED, enabled).apply()
    }

    private fun prefs(context: Context) = context.applicationContext
        .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
}
