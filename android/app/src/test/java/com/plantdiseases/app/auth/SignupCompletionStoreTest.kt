package com.plantdiseases.app.auth

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.data.account.AuthProvider
import java.util.UUID
import org.junit.Assert.assertThrows
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class SignupCompletionStoreTest {
    private val context: Context = ApplicationProvider.getApplicationContext()

    @Test
    fun inMemoryContractKeepsTheExactLogicalOperationAcrossRepositoryInstances() {
        val store = InMemorySignupCompletionStore()
        val operation = operation()

        store.write(operation)

        assertThat(store.read()).isEqualTo(operation)
        store.clear()
        assertThat(store.read()).isNull()
    }

    @Test
    fun malformedIssuerScopedCiphertextFailsClosedAndIsErased() {
        val scope = AccountEndpointScope.from(
            "https://example.invalid/${UUID.randomUUID()}/id/",
        )
        val store = AndroidKeystoreSignupCompletionStore(context, scope)
        assertThat(store.read()).isNull()
        val prefs = context.getSharedPreferences(
            "account_signup_v2_${scope.storageKey}",
            Context.MODE_PRIVATE,
        )
        prefs.edit()
            .putString("issuer", scope.issuer)
            .putString("ciphertext", "AA==")
            .commit()

        assertThrows(CredentialUnreadableException::class.java) { store.read() }
        assertThat(prefs.all).isEmpty()
    }

    private fun operation() = PendingSignupOperation(
        provider = AuthProvider.YANDEX,
        pendingSignupToken = "pending-proof",
        completionId = "00000000-0000-4000-8000-000000000011",
        installationId = "00000000-0000-4000-8000-000000000012",
        expiresInSeconds = 600,
        nickname = "Retry Fern",
        confirmationAttempted = true,
        remoteTerminationConfirmed = true,
    )
}
