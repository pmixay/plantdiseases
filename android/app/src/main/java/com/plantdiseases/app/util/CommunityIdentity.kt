package com.plantdiseases.app.util

import android.content.Context
import java.security.MessageDigest
import java.security.SecureRandom

/**
 * The forum's durable per-device identity (COMMUNITY_FORUM_PLAN.docx §3.1 /
 * §4). Distinct from [TelemetryIds.userPseudoId]: that one is documented as
 * telemetry-only and travels in a URL path today, so it must never also
 * authorise anything. This is a separate 32-byte secret, generated once and
 * persisted the same way TelemetryIds already is — plain SharedPreferences,
 * MODE_PRIVATE (Android's own per-app sandbox is this codebase's existing
 * bar for a device-generated identifier; there is no
 * androidx.security:security-crypto dependency here to add one for).
 *
 * The secret itself never leaves the device. [keyHash] — its SHA-256 —
 * is what actually travels, as the `X-Community-Author-Key` header on
 * every community/forum write. privacy.html promises exactly this: "не
 * покидает пределы приложения в открытом виде — сервер получает только
 * его отпечаток" / "it never leaves the app in readable form — the
 * server only ever receives a one-way hash of it".
 */
object CommunityIdentity {
    private const val PREFS_NAME = "plantdiseases_community_identity"
    private const val KEY_SECRET_HEX = "device_secret"
    private const val SECRET_BYTES = 32

    /** The secret itself, as a persisted hex string — 256 bits of
     *  SecureRandom entropy either way, so hashing this string's UTF-8
     *  bytes below is exactly as strong as hashing the raw ByteArray would
     *  be, with no decode-back-to-bytes step needed. */
    private fun secretHex(context: Context): String {
        val prefs = context.applicationContext
            .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        prefs.getString(KEY_SECRET_HEX, null)?.let { return it }
        val fresh = ByteArray(SECRET_BYTES).also { SecureRandom().nextBytes(it) }
            .joinToString("") { "%02x".format(it) }
        prefs.edit().putString(KEY_SECRET_HEX, fresh).apply()
        return fresh
    }

    /** SHA-256 of the device secret, lowercase hex — the only form of the
     *  secret that ever leaves the device. */
    fun keyHash(context: Context): String =
        MessageDigest.getInstance("SHA-256")
            .digest(secretHex(context).toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it) }
}
