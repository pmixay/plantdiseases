package com.plantdiseases.app.util

import android.content.Context

/**
 * Community membership flag (sharing on).
 *
 * Default is OFF: a fresh install neither contributes nor reads area-scoped
 * community data. There is no separate sharing switch: joining the community
 * (`ui/community/CommunityJoin`, offered by the map and by nearby scans on a
 * result) grants coarse location and turns this on, so the user's accepted
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
        // Receiving community opinions is part of participating: it's the replies
        // on YOUR shared scans. They move in lockstep so there's a single
        // "community" switch (the standalone opinions toggle was removed). The
        // POST_NOTIFICATIONS permission + delivery scheduling are handled where
        // the user requests a second opinion (not at opt-in — joining the
        // community must not fire a notification prompt); this just keeps the
        // preference coherent.
        OpinionPrefs.setNotificationsEnabled(context, enabled)
    }

    /** Turns membership on; [com.plantdiseases.app.ui.community.CommunityJoin] is the caller. */
    fun enable(context: Context) = setUploadsEnabled(context, true)

    private fun prefs(context: Context) = context.applicationContext
        .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
}
