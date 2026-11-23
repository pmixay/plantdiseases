package com.plantdiseases.app.util

import android.content.Context

/**
 * Community membership flag (sharing on).
 *
 * Default is OFF: a fresh install neither contributes nor reads area-scoped
 * community data. There is no separate sharing switch: joining the community
 * (`ui/community/CommunityJoin`, offered by the map, by nearby scans on a
 * result or a species page, and by Home's popular section) grants coarse location and turns this on, so the user's accepted
 * scans are shared while signed in. "Leave" on the community map turns it off
 * and stops new uploads; delete-my-contributions still clears any rows already
 * shared.
 */
object CommunityPrefs {
    private const val PREFS_NAME = "plantdiseases_community"
    private const val KEY_ENABLED = "uploads_enabled"
    internal const val DEFAULT_UPLOADS_ENABLED = false

    fun uploadsEnabled(context: Context): Boolean =
        prefs(context).getBoolean(KEY_ENABLED, DEFAULT_UPLOADS_ENABLED)

    fun setUploadsEnabled(context: Context, enabled: Boolean) {
        prefs(context).edit().putBoolean(KEY_ENABLED, enabled).apply()
    }

    /** Turns membership on; [com.plantdiseases.app.ui.community.CommunityJoin] is the caller. */
    fun enable(context: Context) = setUploadsEnabled(context, true)

    private fun prefs(context: Context) = context.applicationContext
        .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
}
