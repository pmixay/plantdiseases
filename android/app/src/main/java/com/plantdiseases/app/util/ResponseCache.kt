package com.plantdiseases.app.util

import android.content.Context
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.decodeFromJsonElement
import kotlinx.serialization.json.encodeToJsonElement
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

    // A species whose AI "common problems" came back unavailable (no key /
    // quota / transient failure → the UI renders the bundled offline guide) is
    // cached briefly, so reopening the same species is instant instead of
    // re-hitting the network and flashing a spinner every time. The short
    // window means it still retries within the day, picking up the AI list
    // once it becomes available.
    val TTL_SPECIES_MISS_MS: Long = TimeUnit.HOURS.toMillis(6)

    // A species-info answer that is only genus-level or has no photo yet: the
    // server keeps retrying the species-level sources, so recheck soon rather
    // than pinning the degraded card for a week.
    val TTL_SPECIES_DEGRADED_MS: Long = TimeUnit.MINUTES.toMillis(30)

    // The popular bundle is a live community-scan aggregate, so cache it only
    // very briefly — a new nearby scan should surface near-live, not after
    // half an hour. The server invalidates its own bundle cache on every
    // upload + serves stale-while-revalidate, so this short client window just
    // avoids re-fetching on rapid re-opens. (Re-fetching is cheap: the static
    // per-species photos load via the long-cached by-name proxy + Glide.)
    val TTL_POPULAR_MS: Long = TimeUnit.MINUTES.toMillis(2)

    /** On-disk envelope: the cached payload plus when it was stored and how
     *  long it stays fresh. */
    // Field names are the file format (Gson-era files use the same ones).
    @Serializable
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
    /** A cached, unexpired [T], or null (missing, expired or unreadable). */
    inline fun <reified T> get(key: String): T? =
        payload(key, keepExpired = false)?.let { runCatching { AppJson.decodeFromJsonElement<T>(it.json) }.getOrNull() }

    data class Peek<T>(val value: T, val fresh: Boolean)

    /** The cached [T] even when expired, with whether it is still fresh. */
    inline fun <reified T> peek(key: String): Peek<T>? =
        payload(key, keepExpired = true)?.let { stored ->
            runCatching { Peek(AppJson.decodeFromJsonElement<T>(stored.json), stored.fresh) }.getOrNull()
        }

    /** Stores [value]; a [ttlMs] of 0 never expires. Best-effort. */
    inline fun <reified T : Any> put(key: String, value: T, ttlMs: Long = 0L) {
        runCatching { store(key, AppJson.encodeToJsonElement(value), ttlMs) }
    }

    @PublishedApi
    internal class Stored(val json: JsonElement, val fresh: Boolean)

    /** The stored payload; an expired entry is deleted unless [keepExpired]. */
    @PublishedApi
    internal fun payload(key: String, keepExpired: Boolean): Stored? = try {
        val file = fileFor(key)?.takeIf { it.isFile }
        val env = file?.readText()?.let { AppJson.decodeFromString(Envelope.serializer(), it) }
        val fresh = env != null && (env.ttlMs <= 0L || System.currentTimeMillis() - env.savedAt <= env.ttlMs)
        when {
            env == null -> null
            fresh || keepExpired -> Stored(env.payload, fresh)
            else -> {
                file.delete()
                null
            }
        }
    } catch (_: Exception) {
        null
    }

    @PublishedApi
    internal fun store(key: String, payload: JsonElement, ttlMs: Long) {
        val env = Envelope(System.currentTimeMillis(), ttlMs, payload)
        fileFor(key)?.writeText(AppJson.encodeToString(Envelope.serializer(), env))
    }
}
