package com.plantdiseases.app.auth

import android.content.Context
import java.util.UUID

interface InstallationIdProvider {
    fun get(): String
}

/** Random per-install identifier; never derived from Android or hardware identifiers. */
class InstallationIdStore(context: Context) : InstallationIdProvider {
    private val prefs = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    override fun get(): String {
        prefs.getString(KEY_ID, null)?.let { stored ->
            runCatching { UUID.fromString(stored) }.getOrNull()?.let { return it.toString() }
        }
        val fresh = UUID.randomUUID().toString()
        check(prefs.edit().putString(KEY_ID, fresh).commit())
        return fresh
    }

    private companion object {
        const val PREFS = "account_installation_v1"
        const val KEY_ID = "installation_id"
    }
}
