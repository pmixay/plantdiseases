package com.plantdiseases.app.util

import android.content.Context
import com.google.gson.Gson
import com.google.gson.JsonElement
import java.io.File
import java.util.concurrent.TimeUnit

/**
 * Tiny persistent disk cache for deterministic server responses that are
 * expensive to regenerate (LLM care plans) or are stable metadata lookups
 * (species info). Keyed by the request inputs and stored as JSON under
 * ``filesDir/api_cache``, so it survives process death and app restarts: a
 * fetched key is served from disk until its per-entry TTL lapses (see the
 * TTL_* constants), after which the next read transparently re-fetches.
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

    // Per-entry TTLs. Policy: STABLE content (Wikipedia species blurbs,
    // LLM care plans) refreshes at 7d so server prompt/model/Wikipedia updates
    // eventually reach cached keys; the trending feed is short-lived (1h) so
    // "trending nearby" stays fresh while still sparing repeat onResume fetches.
    // A TTL of 0 (the [put] default) means "never expires" — for any future
    // entry whose value is truly immutable.
    val TTL_SPECIES_MS: Long = TimeUnit.DAYS.toMillis(7)
    val TTL_CARE_PLAN_MS: Long = TimeUnit.DAYS.toMillis(7)
    val TTL_TRENDING_MS: Long = TimeUnit.HOURS.toMillis(1)

    // Popular bundle refreshes every two hours server-side.
    val TTL_POPULAR_MS: Long = TimeUnit.HOURS.toMillis(2)

    /** On-disk envelope: the cached payload plus when it was stored and how
     *  long it stays fresh. */
    private data class Envelope(
        val savedAt: Long,
        val ttlMs: Long,
        val payload: JsonElement,
    )

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

    /** Cached value for [key], or null on miss / expiry / parse error /
     *  not-initialised. An expired entry is deleted on read so the next call
     *  re-fetches and re-stores a fresh copy. */
    fun <T> get(key: String, clazz: Class<T>): T? = try {
        val file = fileFor(key)?.takeIf { it.isFile }
        val env = file?.readText()?.let { gson.fromJson(it, Envelope::class.java) }
        when {
            env?.payload == null -> null
            env.ttlMs > 0L && System.currentTimeMillis() - env.savedAt > env.ttlMs -> {
                file.delete()
                null
            }
            else -> gson.fromJson(env.payload, clazz)
        }
    } catch (_: Exception) {
        null
    }

    /** Store [value] under [key] with an optional time-to-live ([ttlMs]; 0 =
     *  never expires). Failures are swallowed — the cache must never break the
     *  request path. */
    fun put(key: String, value: Any, ttlMs: Long = 0L) {
        try {
            val env = Envelope(System.currentTimeMillis(), ttlMs, gson.toJsonTree(value))
            fileFor(key)?.writeText(gson.toJson(env))
        } catch (_: Exception) {
            // best-effort
        }
    }
}
