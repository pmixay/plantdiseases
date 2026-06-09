package com.plantdiseases.app.util

import android.content.Context

/**
 * Opt-in state for autosaving captured scan photos to the device gallery.
 *
 * Default is OFF: a fresh install keeps every scan inside the app's private
 * storage only. The user turns this on from the Profile screen; on Android
 * 9 and below that toggle also requests WRITE_EXTERNAL_STORAGE (scoped
 * storage on 10+ needs no permission for MediaStore inserts).
 *
 * Only photos snapped with the in-app camera are saved — images imported
 * from the gallery are already in the album, so re-saving them would just
 * create duplicates (see CameraFragment).
 */
object AutosavePrefs {
    // Reuse the app-wide prefs file so the toggle sits next to the other
    // scan-screen preferences (grid overlay, tips seen).
    private const val PREFS_NAME = "plantdiseases_prefs"
    private const val KEY_ENABLED = "autosave_to_album"

    fun enabled(context: Context): Boolean =
        prefs(context).getBoolean(KEY_ENABLED, false)

    fun setEnabled(context: Context, enabled: Boolean) {
        prefs(context).edit().putBoolean(KEY_ENABLED, enabled).apply()
    }

    private fun prefs(context: Context) = context.applicationContext
        .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
}
