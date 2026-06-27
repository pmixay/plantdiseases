package com.plantdiseases.app.util

import android.content.Context

/**
 * Opt-in state for the community scan corpus.
 *
 * Default is OFF: a fresh install neither contributes nor pulls community data
 * until the user explicitly opts in IN CONTEXT — by tapping "Enable" on the
 * result screen's "similar scans" card or on the Nearby screen. Enabling
 * requests ``ACCESS_COARSE_LOCATION`` (so scans can be grouped by area) and
 * flips this on; nothing is shared before that. Delete-my-contributions still
 * clears any rows already shared.
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
        // POST_NOTIFICATIONS permission + delivery scheduling are handled at the
        // UI opt-in site; this just keeps the preference coherent.
        OpinionPrefs.setNotificationsEnabled(context, enabled)
    }

    /** Opt the user into community features from an in-context "Enable" tap. */
    fun enable(context: Context) = setUploadsEnabled(context, true)

    private fun prefs(context: Context) = context.applicationContext
        .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
}
