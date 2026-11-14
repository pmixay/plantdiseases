package com.plantdiseases.app.auth

import android.app.Application
import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import java.util.UUID
import org.junit.Assert.assertThrows
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
class SignupCompletionStoreTest {
    private val context: Context = ApplicationProvider.getApplicationContext()

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
}
