package com.plantdiseases.app.util

import android.content.Context
import com.google.gson.Gson
import java.io.File

/**
 * Tiny persistent disk cache for deterministic server responses that are
 * expensive to regenerate (LLM care plans) or are stable metadata lookups
 * (species info). Keyed by the request inputs and stored as JSON under
 * ``filesDir/api_cache``, so it survives process death and app restarts:
 * once a key is fetched it is never requested from the server again.
 *
 * NOT used for per-image / per-scan-unique calls (analyze, identify, feedback)
 * — those aren't repeatable. Remote image BYTES are cached separately by
 * Glide's own disk cache (DiskCacheStrategy.ALL at the call sites).
 *
 * Best-effort: when [init] hasn't run (e.g. in unit tests) [get] returns null
 * and [put] is a no-op, so callers transparently fall back to the network.
 */
object ResponseCache {
    private const val DIR = "api_cache"
    private val gson = Gson()

    @Volatile
    private var dir: File? = null

    /** Wire up the cache directory once, from Application.onCreate. */
    fun init(context: Context) {
        dir = File(context.applicationContext.filesDir, DIR).apply { mkdirs() }
    }

    private fun fileFor(key: String): File? {
        val d = dir ?: return null
        // Filesystem-safe, stable name: hash for uniqueness + a readable tail.
        val name = Integer.toHexString(key.hashCode()) +
            "_" + key.filter { it.isLetterOrDigit() }.takeLast(48)
        return File(d, "$name.json")
    }

    /** Cached value for [key], or null on miss / parse error / not-initialised. */
    fun <T> get(key: String, clazz: Class<T>): T? = try {
        fileFor(key)?.takeIf { it.isFile }?.readText()?.let { gson.fromJson(it, clazz) }
    } catch (_: Exception) {
        null
    }

    /** Store [value] under [key]. Failures are swallowed — the cache must
     *  never break the request path. */
    fun put(key: String, value: Any) {
        try {
            fileFor(key)?.writeText(gson.toJson(value))
        } catch (_: Exception) {
            // best-effort
        }
    }

    /** Wipe everything (used by "clear cache" / data-reset flows). */
    fun clear() {
        try {
            dir?.listFiles()?.forEach { it.delete() }
        } catch (_: Exception) {
            // best-effort
        }
    }
}
