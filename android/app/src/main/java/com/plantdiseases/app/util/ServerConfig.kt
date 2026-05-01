package com.plantdiseases.app.util

import android.content.Context
import com.plantdiseases.app.BuildConfig

/** Server base URL is fixed at build time through BuildConfig.API_BASE_URL. */
object ServerConfig {

    @Suppress("UNUSED_PARAMETER")
    fun getBaseUrl(context: Context): String = BuildConfig.API_BASE_URL
}
