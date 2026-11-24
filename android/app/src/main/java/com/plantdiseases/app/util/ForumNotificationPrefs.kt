package com.plantdiseases.app.util

import android.content.Context

/**
 * Notifications for answers on the user's community posts: the Profile switch
 * that turns them off, and the alert times behind the hourly limit
 * ([com.plantdiseases.app.push.ForumAnswerNotifier]). On by default; the
 * in-app notification centre keeps every answer either way.
 */
object ForumNotificationPrefs {
    private const val PREFS = "forum_answer_notifications"
    private const val KEY_ENABLED = "answers_enabled"
    private const val KEY_ALERTS = "recent_alerts"
    private const val HOUR_S = 60 * 60L

    fun answersEnabled(context: Context): Boolean = prefs(context).getBoolean(KEY_ENABLED, true)

    fun setAnswersEnabled(context: Context, enabled: Boolean) {
        prefs(context).edit().putBoolean(KEY_ENABLED, enabled).apply()
    }

    /** Alert times (seconds) within the last hour. */
    fun recentAlerts(context: Context): List<Long> =
        prefs(context).getString(KEY_ALERTS, "").orEmpty()
            .split(',')
            .mapNotNull { it.trim().toLongOrNull() }

    fun recordAlerts(context: Context, nowS: Long, count: Int) {
        val kept = recentAlerts(context).filter { nowS - it < HOUR_S } + List(count) { nowS }
        prefs(context).edit().putString(KEY_ALERTS, kept.joinToString(",")).apply()
    }

    private fun prefs(context: Context) =
        context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
}
