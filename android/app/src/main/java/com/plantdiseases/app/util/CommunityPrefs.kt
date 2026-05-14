package com.plantdiseases.app.util

import android.content.Context

/**
 * Toggle state for the community scan-corpus.
 *
 * Default is ON. A fresh install starts contributing to the corpus
 * immediately so the "similar scans near you" card can populate from
 * day one. The Profile screen exposes a switch that turns uploads
 * off (and Delete-my-contributions clears any rows already shared).
 */
object CommunityPrefs {
    private const val PREFS_NAME = "plantdiseases_community"
    private const val KEY_ENABLED = "uploads_enabled"

    fun uploadsEnabled(context: Context): Boolean =
        prefs(context).getBoolean(KEY_ENABLED, true)

    fun setUploadsEnabled(context: Context, enabled: Boolean) {
        prefs(context).edit().putBoolean(KEY_ENABLED, enabled).apply()
    }

    private fun prefs(context: Context) = context.applicationContext
        .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
}
