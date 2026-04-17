package com.plantdiseases.app.util

import android.content.Context
import android.content.res.Configuration
import java.util.Locale

object LocaleHelper {

    private const val PREFS_NAME = "plantdiseases_prefs"
    private const val KEY_LANGUAGE = "app_language"

    @Volatile
    private var cachedLanguage: String? = null

    fun applyLocale(context: Context): Context {
        val lang = getSavedLanguage(context)
        return setLocale(context, lang)
    }

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
        val value = prefs.getString(KEY_LANGUAGE, "en") ?: "en"
        cachedLanguage = value
        return value
    }

    private fun saveLanguage(context: Context, language: String) {
        cachedLanguage = language
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_LANGUAGE, language)
            .apply()
    }

    fun isRussian(context: Context): Boolean = getSavedLanguage(context) == "ru"

    fun toggleLanguage(context: Context): String {
        val current = getSavedLanguage(context)
        val newLang = if (current == "ru") "en" else "ru"
        setLocale(context, newLang)
        return newLang
    }
}
