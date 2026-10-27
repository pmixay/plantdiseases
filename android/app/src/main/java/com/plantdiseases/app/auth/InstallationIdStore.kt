package com.plantdiseases.app.auth

import android.content.Context
import java.util.UUID

interface InstallationIdProvider {
    fun get(): String

    /** Replaces the account-scoped pseudonymous identifier with a durable fresh value. */
    fun rotate()
}

/**
 * Random identifier scoped to the normalized account issuer. It is never
 * derived from Android or hardware identifiers and cannot correlate staging
 * and production accounts even though both APKs intentionally share a package.
 */
class InstallationIdStore(
    context: Context,
    private val scope: AccountEndpointScope,
) : InstallationIdProvider {
    private val applicationContext = context.applicationContext
    private val prefs = applicationContext.getSharedPreferences(
        "account_installation_v2_${scope.storageKey}",
        Context.MODE_PRIVATE,
    )

    init {
        // The former package-global ID has no trustworthy issuer binding.
        check(
            applicationContext.getSharedPreferences(LEGACY_PREFS, Context.MODE_PRIVATE)
                .edit()
                .clear()
                .commit(),
        ) { "Unable to purge unscoped beta installation identity" }
    }

    @Synchronized
    override fun get(): String {
        if (prefs.getString(KEY_ISSUER, null) == scope.issuer) {
            prefs.getString(KEY_ID, null)?.let { stored ->
                runCatching { UUID.fromString(stored) }.getOrNull()?.let { return it.toString() }
            }
        }
        check(prefs.edit().clear().commit()) { "Unable to reset installation identity" }
        val fresh = UUID.randomUUID().toString()
        check(
            prefs.edit()
                .putString(KEY_ISSUER, scope.issuer)
                .putString(KEY_ID, fresh)
                .commit(),
        ) { "Unable to persist installation identity" }
        return fresh
    }

    @Synchronized
    override fun rotate() {
        val previous = prefs.getString(KEY_ID, null)
        var fresh: String
        do {
            fresh = UUID.randomUUID().toString()
        } while (fresh == previous)
        check(
            prefs.edit()
                .putString(KEY_ISSUER, scope.issuer)
                .putString(KEY_ID, fresh)
                .commit(),
        ) { "Unable to rotate installation identity" }
    }

    private companion object {
        const val LEGACY_PREFS = "account_installation_v1"
        const val KEY_ISSUER = "issuer"
        const val KEY_ID = "installation_id"
    }
}
