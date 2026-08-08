package com.plantdiseases.app.auth

import com.plantdiseases.app.data.account.AuthProvider
import java.util.UUID

/** Builds mock-provider credentials only for local synthetic staging tests. */
object SyntheticProviderToken {
    fun forInstallation(provider: AuthProvider, installationId: String): String {
        val fixtureId = UUID.fromString(installationId).toString().replace("-", "")
        return "mock-${provider.wireName}-generated-$fixtureId"
    }
}
