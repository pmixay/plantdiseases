package com.plantdiseases.app.util

import android.content.Context
import androidx.appcompat.app.AppCompatDelegate

/**
 * Persists the user's light/dark/system theme choice and applies it through
 * [AppCompatDelegate]'s night-mode. [applySavedTheme] is called once at app
 * start; [saveTheme] both stores and applies a new choice immediately.
 */
object ThemeHelper {

    private const val PREFS_NAME = "plantdiseases_prefs"
    private const val KEY_THEME = "app_theme"

    const val THEME_LIGHT = 0
    const val THEME_DARK = 1
    const val THEME_SYSTEM = 2

    /** Applies the persisted theme; call once during app startup. */
    fun applySavedTheme(context: Context) {
        val mode = getSavedTheme(context)
        applyTheme(mode)
    }

    /** Maps one of the `THEME_*` constants to the matching night-mode. */
    fun applyTheme(mode: Int) {
        val nightMode = when (mode) {
            THEME_LIGHT -> AppCompatDelegate.MODE_NIGHT_NO
            THEME_DARK -> AppCompatDelegate.MODE_NIGHT_YES
            else -> AppCompatDelegate.MODE_NIGHT_FOLLOW_SYSTEM
        }
        AppCompatDelegate.setDefaultNightMode(nightMode)
    }

    fun saveTheme(context: Context, mode: Int) {
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putInt(KEY_THEME, mode)
            .apply()
        applyTheme(mode)
    }

    fun getSavedTheme(context: Context): Int = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        .getInt(KEY_THEME, THEME_SYSTEM)
}
