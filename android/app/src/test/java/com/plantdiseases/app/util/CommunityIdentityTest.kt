package com.plantdiseases.app.util

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.security.MessageDigest

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
        val prefs = ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        assertThat(prefs.getString(KEY_SECRET, null)).isNotNull()
        val second = CommunityIdentity.keyHash(ctx)
        assertThat(second).isEqualTo(first)
    }

    @Test
    fun keyHash_isSha256OfStoredSecret_notTheSecretItself() {
        // The stored secret is 32 random bytes rendered as hex — i.e. ALSO 64
        // lowercase hex chars — so "stable", "well-formed" and "persistent"
        // all hold identically for the raw secret. Only this test fails if
        // keyHash() is ever "simplified" to return secretHex(), which would
        // ship the raw device secret in the X-Community-Author-Key header
        // (the server validates length and hex, nothing more).
        val hash = CommunityIdentity.keyHash(ctx)
        val secret = ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .getString(KEY_SECRET, null)
        assertThat(secret).isNotNull()
        assertThat(hash).isNotEqualTo(secret)
        val expected = MessageDigest.getInstance("SHA-256")
            .digest(secret!!.toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it) }
        assertThat(hash).isEqualTo(expected)
    }

    @Test
    fun keyHash_isPerDevice() {
        // Wiping the prefs is what a different install looks like from here.
        // This is also the only test that reaches the freshly-provisioned
        // branch (the SecureRandom mint), which every other test skips
        // because an earlier call already seeded the secret.
        val first = CommunityIdentity.keyHash(ctx)
        ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit().clear().commit()
        assertThat(CommunityIdentity.keyHash(ctx)).isNotEqualTo(first)
    }

    @Test
    fun clear_mintsANewIdentityOnTheNextCall() {
        val first = CommunityIdentity.keyHash(ctx)
        CommunityIdentity.clear(ctx)
        assertThat(
            ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                .getString(KEY_SECRET, null),
        ).isNull()
        assertThat(CommunityIdentity.keyHash(ctx)).isNotEqualTo(first)
    }

    private companion object {
        const val PREFS_NAME = "plantdiseases_community_identity"
        const val KEY_SECRET = "device_secret"
    }
}
