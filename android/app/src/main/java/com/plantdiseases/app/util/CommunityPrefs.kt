package com.plantdiseases.app.util

import android.content.Context

/**
 * Opt-in state for the community scan-corpus.
 *
 * Default is OFF: a fresh install neither contributes nor pulls community
 * data until the user explicitly opts in IN CONTEXT — by tapping "Enable" on
 * the result screen's "similar scans near you" card or on the Nearby screen.
 * Enabling either one flips this on (see [enable]); there is no Profile
 * switch. Delete-my-contributions still clears any rows already shared.
 */
object CommunityPrefs {
    private const val PREFS_NAME = "plantdiseases_community"
    private const val KEY_ENABLED = "uploads_enabled"

    fun uploadsEnabled(context: Context): Boolean =
        prefs(context).getBoolean(KEY_ENABLED, false)

    fun setUploadsEnabled(context: Context, enabled: Boolean) {
        prefs(context).edit().putBoolean(KEY_ENABLED, enabled).apply()
    }

    /** Opt the user into community features from an in-context "Enable" tap. */
    fun enable(context: Context) = setUploadsEnabled(context, true)

    private fun prefs(context: Context) = context.applicationContext
        .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
}
