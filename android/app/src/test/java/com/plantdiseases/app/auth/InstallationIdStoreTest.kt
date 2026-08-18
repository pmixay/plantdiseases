package com.plantdiseases.app.auth

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.util.UUID

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class InstallationIdStoreTest {
    private val context: Context = ApplicationProvider.getApplicationContext()

    @Test
    fun identityIsStableOnlyWithinOneIssuerScope() {
        val suffix = UUID.randomUUID().toString()
        val production = AccountEndpointScope.from("https://example.invalid/$suffix/id/")
        val staging = AccountEndpointScope.from("https://example.invalid/$suffix/test/id/")

        val firstProduction = InstallationIdStore(context, production).get()
        val secondProduction = InstallationIdStore(context, production).get()
        val stagingId = InstallationIdStore(context, staging).get()

        assertThat(secondProduction).isEqualTo(firstProduction)
        assertThat(stagingId).isNotEqualTo(firstProduction)
    }

    @Test
    fun legacyUnscopedIdentityIsDestroyedRatherThanMigrated() {
        val legacy = UUID.randomUUID().toString()
        context.getSharedPreferences("account_installation_v1", Context.MODE_PRIVATE)
            .edit()
            .putString("installation_id", legacy)
            .commit()
        val scope = AccountEndpointScope.from(
            "https://example.invalid/${UUID.randomUUID()}/id/",
        )

        val scoped = InstallationIdStore(context, scope).get()

        assertThat(scoped).isNotEqualTo(legacy)
        assertThat(
            context.getSharedPreferences("account_installation_v1", Context.MODE_PRIVATE).all,
        ).isEmpty()
    }

    @Test
    fun rotateReplacesAndDurablyPersistsIdentityWithinIssuerScope() {
        val scope = AccountEndpointScope.from(
            "https://example.invalid/${UUID.randomUUID()}/id/",
        )
        val store = InstallationIdStore(context, scope)
        val before = store.get()

        store.rotate()
        val after = store.get()

        assertThat(after).isNotEqualTo(before)
        assertThat(InstallationIdStore(context, scope).get()).isEqualTo(after)
    }
}
