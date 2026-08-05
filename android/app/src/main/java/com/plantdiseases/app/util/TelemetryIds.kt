package com.plantdiseases.app.util

import android.content.Context
import java.util.UUID

/**
 * Per-install opaque identifiers used for feedback telemetry.
 *
 * `userPseudoId` is a random UUIDv4 generated once per install and
 * persisted in SharedPreferences. It is opaque to the server — we
 * never derive it from any platform identifier (no IMEI, no ANDROID_ID,
 * no advertising id) so it cannot be cross-referenced with anything
 * outside this app. Reinstalling the app rotates it.
 *
 * The id lets the retraining pipeline group multiple feedback rows
 * from one user (useful for accuracy-per-cohort stats) without
 * knowing who that user is. Deletion model: clearing app data
 * generates a fresh id on next launch.
 */
object TelemetryIds {
    private const val PREFS_NAME = "plantdiseases_telemetry"
    private const val KEY_USER_PSEUDO_ID = "user_pseudo_id"

    fun userPseudoId(context: Context): String {
        val prefs = context.applicationContext
            .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        prefs.getString(KEY_USER_PSEUDO_ID, null)?.let { return it }
        val fresh = UUID.randomUUID().toString()
        prefs.edit().putString(KEY_USER_PSEUDO_ID, fresh).apply()
        return fresh
    }
}
