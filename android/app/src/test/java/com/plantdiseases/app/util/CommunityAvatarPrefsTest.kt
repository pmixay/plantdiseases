package com.plantdiseases.app.util

import android.app.Application
import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
class CommunityAvatarPrefsTest {
    private val context: Context get() = ApplicationProvider.getApplicationContext()

    @Before
    fun clearPreferences() {
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE).edit().clear().commit()
    }

    @Test
    fun choiceIsUnsetUntilAnsweredAndPerAccount() {
        val prefs = CommunityAvatarPrefs(context)
        assertThat(prefs.choice("actor-a")).isNull()

        prefs.setChoice("actor-a", show = true, photoFingerprint = "fp1")
        val reopened = CommunityAvatarPrefs(context)
        assertThat(reopened.choice("actor-a")).isTrue()
        assertThat(reopened.askedFingerprint("actor-a")).isEqualTo("fp1")
        assertThat(reopened.choice("actor-b")).isNull()

        reopened.setChoice("actor-a", show = false, photoFingerprint = null)
        assertThat(reopened.choice("actor-a")).isFalse()
        assertThat(reopened.askedFingerprint("actor-a")).isNull()
    }

    @Test
    fun publishedAndRejectedPhotosAreTracked() {
        val prefs = CommunityAvatarPrefs(context)
        prefs.setPublished("actor-a", "fp1")
        prefs.setRejected("actor-a", "fp2")
        assertThat(prefs.publishedFingerprint("actor-a")).isEqualTo("fp1")
        assertThat(prefs.rejectedFingerprint("actor-a")).isEqualTo("fp2")
        assertThat(prefs.publishedFingerprint("actor-b")).isNull()

        prefs.setPublished("actor-a", null)
        assertThat(prefs.publishedFingerprint("actor-a")).isNull()
    }

    @Test
    fun theAccountIdIsNeverStoredInClear() {
        CommunityAvatarPrefs(context).setChoice("actor-secret", show = true, photoFingerprint = "fp")
        val keys = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE).all.keys
        assertThat(keys).isNotEmpty()
        assertThat(keys.none { it.contains("actor-secret") }).isTrue()
    }

    private companion object {
        const val PREFS_NAME = "community_avatar_v1"
    }
}
