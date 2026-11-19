package com.plantdiseases.app.util

import android.content.Context
import java.security.MessageDigest

/**
 * Per-account memory of whether the member shows their local profile photo in
 * Community, and which version of it the server holds.
 *
 * Keys are a SHA-256 of the account's community actor id, so the file never
 * stores the id itself and one account's choice never applies to another.
 * Photos are identified by a content fingerprint (see [CommunityAvatarSync]).
 */
class CommunityAvatarPrefs(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    /** The member's answer: `true` show, `false` keep on device, `null` never asked. */
    fun choice(accountId: String): Boolean? {
        val key = key(KEY_CHOICE, accountId)
        return if (prefs.contains(key)) prefs.getBoolean(key, false) else null
    }

    fun setChoice(accountId: String, show: Boolean, photoFingerprint: String?) {
        prefs.edit()
            .putBoolean(key(KEY_CHOICE, accountId), show)
            .putString(key(KEY_ASKED, accountId), photoFingerprint)
            .apply()
    }

    /** The photo the member last answered about; a different photo asks again. */
    fun askedFingerprint(accountId: String): String? = prefs.getString(key(KEY_ASKED, accountId), null)

    /** The photo the server holds for this account, or null when it holds none. */
    fun publishedFingerprint(accountId: String): String? = prefs.getString(key(KEY_PUBLISHED, accountId), null)

    fun setPublished(accountId: String, photoFingerprint: String?) {
        prefs.edit().putString(key(KEY_PUBLISHED, accountId), photoFingerprint).apply()
    }

    /** A photo the server refused (e.g. a banned picture); not retried. */
    fun rejectedFingerprint(accountId: String): String? = prefs.getString(key(KEY_REJECTED, accountId), null)

    fun setRejected(accountId: String, photoFingerprint: String?) {
        prefs.edit().putString(key(KEY_REJECTED, accountId), photoFingerprint).apply()
    }

    private fun key(prefix: String, accountId: String): String {
        val digest = MessageDigest.getInstance("SHA-256")
            .digest(accountId.toByteArray(Charsets.UTF_8))
            .joinToString("") { byte -> "%02x".format(byte.toInt() and 0xff) }
        return "${prefix}_$digest"
    }

    companion object {
        private const val PREFS = "community_avatar_v1"
        private const val KEY_CHOICE = "show"
        private const val KEY_ASKED = "asked"
        private const val KEY_PUBLISHED = "published"
        private const val KEY_REJECTED = "rejected"
    }
}
