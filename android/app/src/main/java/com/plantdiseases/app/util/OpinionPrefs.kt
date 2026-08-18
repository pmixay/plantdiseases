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
    private const val KEY_NOTIFICATIONS_ENABLED = "notifications_enabled"
    internal const val DEFAULT_NOTIFICATIONS_ENABLED = false
    private const val KEY_PREFIX = "seen_"
    private const val KEY_VIEWED_PREFIX = "viewed_"

    fun isInitialized(context: Context): Boolean =
        prefs(context).getBoolean(KEY_INITIALIZED, false)

    fun markInitialized(context: Context) {
        prefs(context).edit().putBoolean(KEY_INITIALIZED, true).apply()
    }

    fun notificationsEnabled(context: Context): Boolean =
        prefs(context).getBoolean(KEY_NOTIFICATIONS_ENABLED, DEFAULT_NOTIFICATIONS_ENABLED)

    fun setNotificationsEnabled(context: Context, enabled: Boolean) {
        prefs(context).edit().putBoolean(KEY_NOTIFICATIONS_ENABLED, enabled).apply()
    }

    fun lastSeen(context: Context, scanId: String): Int =
        prefs(context).getInt(KEY_PREFIX + scanId, 0)

    fun setSeen(context: Context, scanId: String, count: Int) {
        prefs(context).edit().putInt(KEY_PREFIX + scanId, count).apply()
    }

    /**
     * The opinion count the user has actually *viewed* on a scan — advanced
     * only when they open that scan's result, NOT by the background poll. The
     * Gallery uses this to show a red "+N" badge and highlight scans that have
     * opinions the user hasn't looked at yet. Kept separate from [lastSeen]
     * (the notification baseline) so a fired notification doesn't clear the
     * Gallery badge before the user has opened the scan.
     */
    fun viewedOpinions(context: Context, scanId: String): Int =
        prefs(context).getInt(KEY_VIEWED_PREFIX + scanId, 0)

    fun markViewedOpinions(context: Context, scanId: String, count: Int) {
        prefs(context).edit().putInt(KEY_VIEWED_PREFIX + scanId, count).apply()
    }

    private fun prefs(context: Context) = context.applicationContext
        .getSharedPreferences(PREFS, Context.MODE_PRIVATE)
}
