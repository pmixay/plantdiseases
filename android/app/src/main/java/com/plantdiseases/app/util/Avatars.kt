package com.plantdiseases.app.util

/**
 * Deterministic anonymous avatars for community scans. The server sends a
 * stable, non-reversible per-author token; the same token always maps to the
 * same colour + monogram, so people can tell "these two scans are the same
 * (anonymous) person" without ever learning who that is.
 */
object Avatars {
    // Pleasant, distinct hues that read on both light and dark cards.
    private val PALETTE = intArrayOf(
        0xFF16A34A.toInt(),
        0xFF2563EB.toInt(),
        0xFFD97706.toInt(),
        0xFFDC2626.toInt(),
        0xFF7C3AED.toInt(),
        0xFF0891B2.toInt(),
        0xFFDB2777.toInt(),
        0xFF65A30D.toInt(),
    )

    /** Stable colour for any seed (a nickname or the legacy author token). */
    fun colorFor(seed: String?): Int = PALETTE[stableHash(seed) % PALETTE.size]

    /** A 2-letter monogram derived from a token (looks like initials, but is
     *  just stable hash letters — anonymous). */
    fun monogramFor(token: String?): String {
        val h = stableHash(token)
        val a = 'A' + (h % 26)
        val b = 'A' + ((h / 26) % 26)
        return "$a$b"
    }

    /** Initials from a display nickname — the first letters of up to two real
     *  words ("Томатный Король 42" -> "ТК", "Botanist of Fernwood" -> "BF").
     *  Falls back to the hashed monogram for a blank seed. */
    fun monogramForNick(nick: String?): String {
        if (nick.isNullOrBlank()) return monogramFor(nick)
        val words = nick.trim().split(Regex("\\s+"))
            .filter { it.length >= 3 && it.first().isLetter() }
        return when {
            words.size >= 2 -> "${words[0].first()}${words[1].first()}".uppercase()
            words.size == 1 -> words[0].take(2).uppercase()
            else -> monogramFor(nick)
        }
    }

    private fun stableHash(token: String?): Int {
        if (token.isNullOrEmpty()) return 0
        var h = 0
        for (c in token) h = (h * 31 + c.code) and 0x7FFFFFFF
        return h
    }
}
