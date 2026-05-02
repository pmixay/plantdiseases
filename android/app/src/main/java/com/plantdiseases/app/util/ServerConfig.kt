package com.plantdiseases.app.util

import com.plantdiseases.app.BuildConfig

/** Server base URL is fixed at build time through [BuildConfig.API_BASE_URL]. */
object ServerConfig {
    val baseUrl: String get() = BuildConfig.API_BASE_URL
}
