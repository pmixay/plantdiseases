package com.plantdiseases.app.util

import android.content.Context

/**
 * Tracks the last-seen second-opinion count per shared scan so
 * [com.plantdiseases.app.worker.OpinionCheckWorker] only notifies on NEW
 * opinions, and never re-notifies for ones the user already saw.
 *
 * The first successful poll seeds the baseline WITHOUT notifying (otherwise
 * every pre-existing opinion would fire a notification at once) — see
 * [isInitialized] / [markInitialized].
 */
object OpinionPrefs {
    private const val PREFS = "plantdiseases_opinions"
    private const val KEY_INITIALIZED = "baseline_initialized"
    private const val KEY_PREFIX = "seen_"

    fun isInitialized(context: Context): Boolean =
        prefs(context).getBoolean(KEY_INITIALIZED, false)

    fun markInitialized(context: Context) {
        prefs(context).edit().putBoolean(KEY_INITIALIZED, true).apply()
    }

    fun lastSeen(context: Context, scanId: String): Int =
        prefs(context).getInt(KEY_PREFIX + scanId, 0)

    fun setSeen(context: Context, scanId: String, count: Int) {
        prefs(context).edit().putInt(KEY_PREFIX + scanId, count).apply()
    }

    private fun prefs(context: Context) = context.applicationContext
        .getSharedPreferences(PREFS, Context.MODE_PRIVATE)
}
