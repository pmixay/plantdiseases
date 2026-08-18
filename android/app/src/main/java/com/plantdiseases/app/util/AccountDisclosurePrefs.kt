package com.plantdiseases.app.util

import android.content.Context

/** Versioned acknowledgement shown before the first account-provider exchange. */
class AccountDisclosurePrefs(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    val acknowledged: Boolean
        get() = prefs.getString(KEY_VERSION, null) == ACCOUNT_DISCLOSURE_VERSION

    fun acknowledge() {
        check(
            prefs.edit().putString(KEY_VERSION, ACCOUNT_DISCLOSURE_VERSION).commit(),
        ) { "Unable to persist account disclosure acknowledgement" }
    }

    companion object {
        const val ACCOUNT_DISCLOSURE_VERSION = "account-identity-v1"
        private const val PREFS = "account_disclosure_v1"
        private const val KEY_VERSION = "acknowledged_version"
    }
}
