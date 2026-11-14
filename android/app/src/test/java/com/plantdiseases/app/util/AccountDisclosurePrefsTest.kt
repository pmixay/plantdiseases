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
class AccountDisclosurePrefsTest {
    private val context: Context get() = ApplicationProvider.getApplicationContext()

    @Before
    fun clearPreferences() {
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE).edit().clear().commit()
    }

    @Test
    fun acknowledgementIsRequiredAndVersionBound() {
        val preferences = AccountDisclosurePrefs(context)
        assertThat(preferences.acknowledged).isFalse()

        preferences.acknowledge()
        assertThat(AccountDisclosurePrefs(context).acknowledged).isTrue()

        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_VERSION, "account-identity-v0")
            .commit()
        assertThat(AccountDisclosurePrefs(context).acknowledged).isFalse()
    }

    private companion object {
        const val PREFS_NAME = "account_disclosure_v1"
        const val KEY_VERSION = "acknowledged_version"
    }
}
