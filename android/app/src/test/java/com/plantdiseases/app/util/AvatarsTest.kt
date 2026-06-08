package com.plantdiseases.app.util

import com.google.common.truth.Truth.assertThat
import org.junit.Test

/**
 * Pure-JVM coverage of [Avatars] — the deterministic anonymous-author
 * avatars in the community feed. The whole point is that the SAME token
 * always renders the SAME colour + monogram (so two posts read as "same
 * anonymous person"), while staying within a fixed palette and a valid
 * 2-letter monogram. A regression here would either de-anonymise authors
 * (colours drifting per render) or break the "same person" visual cue.
 */
class AvatarsTest {

    @Test
    fun sameTokenIsStableAcrossCalls() {
        val token = "author-7f3a9c"
        assertThat(Avatars.colorFor(token)).isEqualTo(Avatars.colorFor(token))
        assertThat(Avatars.monogramFor(token)).isEqualTo(Avatars.monogramFor(token))
    }

    @Test
    fun nullAndEmptyTokensShareTheStableFallback() {
        assertThat(Avatars.colorFor(null)).isEqualTo(Avatars.colorFor(""))
        assertThat(Avatars.monogramFor(null)).isEqualTo("AA")
        assertThat(Avatars.monogramFor("")).isEqualTo("AA")
    }

    @Test
    fun colorIsAlwaysOpaqueFromThePalette() {
        val palette = listOf(
            0xFF16A34A, 0xFF2563EB, 0xFFD97706, 0xFFDC2626,
            0xFF7C3AED, 0xFF0891B2, 0xFFDB2777, 0xFF65A30D,
        ).map { it.toInt() }
        for (t in listOf("a", "bb", "author-1", "author-2", "ζ-token", "12345")) {
            val c = Avatars.colorFor(t)
            assertThat(palette).contains(c)
            // Fully opaque so it reads on both light and dark cards.
            assertThat(c ushr 24).isEqualTo(0xFF)
        }
    }

    @Test
    fun monogramIsAlwaysTwoUppercaseLetters() {
        for (t in listOf("a", "author-1", "ZZZ", "12345", "ζ")) {
            val m = Avatars.monogramFor(t)
            assertThat(m).hasLength(2)
            assertThat(m).matches("[A-Z]{2}")
        }
    }

    @Test
    fun distinctTokensSpreadAcrossColoursAndMonograms() {
        val tokens = (0 until 50).map { "author-$it" }
        // Not a hash-quality assertion — just a sanity check that the mapping
        // isn't degenerate (everyone the same colour / same monogram).
        assertThat(tokens.map { Avatars.colorFor(it) }.toSet().size).isAtLeast(4)
        assertThat(tokens.map { Avatars.monogramFor(it) }.toSet().size).isAtLeast(10)
    }
}
