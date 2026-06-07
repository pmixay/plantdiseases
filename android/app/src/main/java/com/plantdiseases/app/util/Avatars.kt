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
        0xFF16A34A.toInt(), 0xFF2563EB.toInt(), 0xFFD97706.toInt(), 0xFFDC2626.toInt(),
        0xFF7C3AED.toInt(), 0xFF0891B2.toInt(), 0xFFDB2777.toInt(), 0xFF65A30D.toInt(),
    )

    fun colorFor(token: String?): Int = PALETTE[stableHash(token) % PALETTE.size]

    /** A 2-letter monogram derived from the token (looks like initials, but is
     *  just stable hash letters — anonymous). */
    fun monogramFor(token: String?): String {
        val h = stableHash(token)
        val a = 'A' + (h % 26)
        val b = 'A' + ((h / 26) % 26)
        return "$a$b"
    }

    private fun stableHash(token: String?): Int {
        if (token.isNullOrEmpty()) return 0
        var h = 0
        for (c in token) h = (h * 31 + c.code) and 0x7FFFFFFF
        return h
    }
}
