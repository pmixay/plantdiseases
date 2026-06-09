package com.plantdiseases.app.util

import android.content.Context
import android.content.res.Configuration
import java.util.Locale

/**
 * Persists the user's chosen UI language ("en"/"ru") and wraps a [Context]
 * with the matching [Locale] so every screen renders in that language
 * regardless of the device locale. Activities call [applyLocale] from
 * `attachBaseContext`; the choice is cached in-process and mirrored in
 * SharedPreferences so it survives restarts.
 */
object LocaleHelper {

    private const val PREFS_NAME = "plantdiseases_prefs"
    private const val KEY_LANGUAGE = "app_language"

    @Volatile
    private var cachedLanguage: String? = null

    /** Wraps [context] in the persisted language, used from `attachBaseContext`. */
    fun applyLocale(context: Context): Context {
        val lang = getSavedLanguage(context)
        return setLocale(context, lang)
    }

    /** Persists [language] and returns a [context] configured with that locale. */
    fun setLocale(context: Context, language: String): Context {
        saveLanguage(context, language)
        val locale = Locale(language)
        Locale.setDefault(locale)

        val config = Configuration(context.resources.configuration)
        config.setLocale(locale)
        config.setLayoutDirection(locale)

        return context.createConfigurationContext(config)
    }

    fun getSavedLanguage(context: Context): String {
        cachedLanguage?.let { return it }
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        // First launch (no explicit choice yet): follow the device language so
        // a Russian phone opens in Russian and everything else in English —
        // the two supported UI languages. Once the user picks a language in
        // Profile it is persisted and takes over.
        val value = prefs.getString(KEY_LANGUAGE, null) ?: defaultFromDeviceLocale(context)
        cachedLanguage = value
        return value
    }

    /** Maps the device locale to a supported UI language: "ru" for a Russian
     *  device, "en" for everything else. Reads the locale off the context's
     *  configuration so it reflects the real system setting even after we've
     *  overridden the process-wide default via [setLocale]. */
    private fun defaultFromDeviceLocale(context: Context): String {
        val locales = context.resources.configuration.locales
        val lang = (if (!locales.isEmpty) locales.get(0) else Locale.getDefault())
            .language.lowercase()
        return if (lang == "ru") "ru" else "en"
    }

    private fun saveLanguage(context: Context, language: String) {
        cachedLanguage = language
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_LANGUAGE, language)
            .apply()
    }

    /** True when the persisted language is Russian — used to pick RU vs EN copy. */
    fun isRussian(context: Context): Boolean = getSavedLanguage(context) == "ru"

    /** Flips between "en" and "ru", persists it, and returns the new language. */
    fun toggleLanguage(context: Context): String {
        val current = getSavedLanguage(context)
        val newLang = if (current == "ru") "en" else "ru"
        setLocale(context, newLang)
        return newLang
    }
}
