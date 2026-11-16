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
class PrivacyConsentPrefsTest {
    private val context: Context get() = ApplicationProvider.getApplicationContext()

    @Before
    fun clearPreferences() {
        context.getSharedPreferences(PrivacyConsentPrefs.PREFS_NAME, Context.MODE_PRIVATE)
            .edit().clear().commit()
        context.getSharedPreferences("plantdiseases_prefs", Context.MODE_PRIVATE)
            .edit().clear().putBoolean("data_consent_given", true).commit()
    }

    @Test
    fun legacyCombinedConsentDoesNotEnableEitherIndependentPurpose() {
        val preferences = PrivacyConsentPrefs(context)

        assertThat(preferences.trainingEnabled).isFalse()
        assertThat(preferences.telemetryEnabled).isFalse()
        assertThat(preferences.trainingConsentVersion).isNull()
    }

    @Test
    fun choicesAreIndependentAndVersionBound() {
        val preferences = PrivacyConsentPrefs(context)

        preferences.setTrainingEnabled(true)
        assertThat(preferences.trainingEnabled).isTrue()
        assertThat(preferences.telemetryEnabled).isFalse()
        assertThat(preferences.trainingConsentVersion)
            .isEqualTo(PrivacyConsentPrefs.TRAINING_CONSENT_VERSION)

        preferences.setTelemetryEnabled(true)
        preferences.setTrainingEnabled(false)
        assertThat(preferences.trainingEnabled).isFalse()
        assertThat(preferences.telemetryEnabled).isTrue()
    }

    @Test
    fun staleDisclosureVersionFailsClosed() {
        context.getSharedPreferences(PrivacyConsentPrefs.PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putBoolean(PrivacyConsentPrefs.KEY_TELEMETRY_ENABLED, true)
            .putString(PrivacyConsentPrefs.KEY_TELEMETRY_VERSION, "telemetry-errors-v0")
            .commit()

        assertThat(PrivacyConsentPrefs(context).telemetryEnabled).isFalse()
    }

    @Test
    fun telemetryChoiceIsPendingUntilAnswered() {
        val preferences = PrivacyConsentPrefs(context)
        assertThat(preferences.telemetryChoicePending).isTrue()

        preferences.setTelemetryEnabled(false)
        assertThat(preferences.telemetryChoicePending).isFalse()
    }
}
