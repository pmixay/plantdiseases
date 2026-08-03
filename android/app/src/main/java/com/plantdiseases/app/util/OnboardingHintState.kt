package com.plantdiseases.app.util

import android.content.Context
import android.content.SharedPreferences

/**
 * Persistent, independent once-only gates for the two in-app onboarding hints.
 *
 * A hint is claimed immediately before it is shown. SharedPreferences updates
 * its in-memory value synchronously, so a rotation or a second activity cannot
 * race in and show the same hint again while the disk write completes.
 */
class OnboardingHintState internal constructor(
    private val preferences: SharedPreferences,
) {

    constructor(context: Context) : this(
        context.applicationContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE),
    )

    fun claimScanTabHint(): Boolean = claim(KEY_SCAN_TAB_HINT_SHOWN)

    fun claimResultHint(): Boolean = claim(KEY_RESULT_HINT_SHOWN)

    private fun claim(key: String): Boolean = synchronized(preferences) {
        if (preferences.getBoolean(key, false)) {
            false
        } else {
            preferences.edit().putBoolean(key, true).apply()
            true
        }
    }

    companion object {
        internal const val PREFS_NAME = "plantdiseases_onboarding_hints"
        private const val KEY_SCAN_TAB_HINT_SHOWN = "scan_tab_hint_shown"
        private const val KEY_RESULT_HINT_SHOWN = "result_hint_shown"
    }
}
