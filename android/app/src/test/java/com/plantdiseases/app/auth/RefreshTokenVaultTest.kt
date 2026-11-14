package com.plantdiseases.app.auth

import android.app.Application
import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import org.junit.Assert.assertThrows
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.util.UUID

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
class RefreshTokenVaultTest {
    private val context: Context = ApplicationProvider.getApplicationContext()

    @Test
    fun absentCredentialIsDistinctFromUnreadableCredential() {
        val scope = scope()
        val vault = AndroidKeystoreRefreshTokenVault(context, scope)

        assertThat(vault.read()).isNull()

        context.getSharedPreferences(
            "account_refresh_v2_${scope.storageKey}",
            Context.MODE_PRIVATE,
        ).edit()
            .putString("issuer", scope.issuer)
            .putString("ciphertext", "AA==")
            .commit()

        assertThrows(CredentialUnreadableException::class.java) { vault.read() }
    }

    private fun scope() = AccountEndpointScope.from(
        "https://example.invalid/${UUID.randomUUID()}/id/",
    )
}
