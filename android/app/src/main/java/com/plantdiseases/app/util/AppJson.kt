package com.plantdiseases.app.util

import kotlinx.serialization.json.Json

/**
 * The app's JSON for everything that is not the network wire: Room fields
 * (lists, maps and detection boxes stored as JSON text), the response cache
 * and the Aqua sidecar. Lenient on read, so rows and cache files written by
 * the Gson-era app still decode: unknown keys are ignored, a missing nullable
 * field reads as null, and an explicit null falls back to the default.
 */
val AppJson: Json = Json {
    ignoreUnknownKeys = true
    coerceInputValues = true
    explicitNulls = false
    encodeDefaults = true
}
