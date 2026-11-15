package com.plantdiseases.app.util

import android.content.Context

/** Independent, versioned opt-ins for anonymous model improvement and telemetry. */
class PrivacyConsentPrefs(context: Context) {
    private val preferences = context.applicationContext.getSharedPreferences(
        PREFS_NAME,
        Context.MODE_PRIVATE,
    )

    val trainingEnabled: Boolean
        get() = preferences.getBoolean(KEY_TRAINING_ENABLED, false) &&
            preferences.getString(KEY_TRAINING_VERSION, null) == TRAINING_CONSENT_VERSION

    val telemetryEnabled: Boolean
        get() = preferences.getBoolean(KEY_TELEMETRY_ENABLED, false) &&
            preferences.getString(KEY_TELEMETRY_VERSION, null) == TELEMETRY_CONSENT_VERSION

    /** True until the user has answered the error-report pop-up (or chose in an older build). */
    val telemetryChoicePending: Boolean
        get() = !preferences.contains(KEY_TELEMETRY_ENABLED)

    val trainingConsentVersion: String?
        get() = TRAINING_CONSENT_VERSION.takeIf { trainingEnabled }

    fun setTrainingEnabled(enabled: Boolean) {
        preferences.edit().apply {
            putBoolean(KEY_TRAINING_ENABLED, enabled)
            if (enabled) {
                putString(KEY_TRAINING_VERSION, TRAINING_CONSENT_VERSION)
            } else {
                remove(KEY_TRAINING_VERSION)
            }
        }.apply()
    }

    fun setTelemetryEnabled(enabled: Boolean) {
        preferences.edit().apply {
            putBoolean(KEY_TELEMETRY_ENABLED, enabled)
            if (enabled) {
                putString(KEY_TELEMETRY_VERSION, TELEMETRY_CONSENT_VERSION)
            } else {
                remove(KEY_TELEMETRY_VERSION)
            }
        }.apply()
    }

    companion object {
        const val TRAINING_CONSENT_VERSION = "model-improvement-v1"
        const val TELEMETRY_CONSENT_VERSION = "telemetry-errors-v1"

        internal const val PREFS_NAME = "privacy_consents_v1"
        internal const val KEY_TRAINING_ENABLED = "training_enabled"
        internal const val KEY_TRAINING_VERSION = "training_version"
        internal const val KEY_TELEMETRY_ENABLED = "telemetry_enabled"
        internal const val KEY_TELEMETRY_VERSION = "telemetry_version"
    }
}
