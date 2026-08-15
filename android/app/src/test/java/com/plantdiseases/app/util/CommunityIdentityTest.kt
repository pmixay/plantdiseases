package com.plantdiseases.app.util

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The device secret never leaves this file — everything else in the app
 * only ever sees [CommunityIdentity.keyHash]. What matters here is that
 * the hash is stable (a rotating identity would defeat the whole point of
 * a durable one), well-formed (64 lowercase hex chars — the server rejects
 * anything else with a 422), and genuinely per-device.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class CommunityIdentityTest {

    private val ctx: Context = ApplicationProvider.getApplicationContext()

    @Test
    fun keyHash_isStableAcrossCalls() {
        val first = CommunityIdentity.keyHash(ctx)
        val second = CommunityIdentity.keyHash(ctx)
        assertThat(second).isEqualTo(first)
    }

    @Test
    fun keyHash_isSixtyFourLowercaseHexChars() {
        val hash = CommunityIdentity.keyHash(ctx)
        assertThat(hash).hasLength(64)
        assertThat(hash).matches("[0-9a-f]{64}")
    }

    @Test
    fun keyHash_persistsAcrossSeparatePrefsLookups() {
        // Simulates two separate onCreate() calls on the same device: a
        // fresh SharedPreferences handle to the same PREFS_NAME must see
        // the secret the first lookup generated, not mint a new one.
        val first = CommunityIdentity.keyHash(ctx)
        val prefs = ctx.getSharedPreferences("plantdiseases_community_identity", Context.MODE_PRIVATE)
        assertThat(prefs.getString("device_secret", null)).isNotNull()
        val second = CommunityIdentity.keyHash(ctx)
        assertThat(second).isEqualTo(first)
    }
}
