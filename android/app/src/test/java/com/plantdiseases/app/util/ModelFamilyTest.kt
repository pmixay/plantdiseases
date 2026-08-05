package com.plantdiseases.app.util

import com.google.common.truth.Truth.assertThat
import org.junit.Test

/**
 * Pure-JVM coverage of [ModelFamily.fromId] — the persisted-preference
 * resolver. A scan's model family is round-tripped through SharedPreferences
 * as a string id, so a bad/unknown id must resolve to a usable default rather
 * than crash or silently pick a server-gated tier.
 */
class ModelFamilyTest {

    @Test
    fun resolvesKnownIdsCaseInsensitively() {
        assertThat(ModelFamily.fromId("aqua")).isEqualTo(ModelFamily.AQUA)
        assertThat(ModelFamily.fromId("GAMMA")).isEqualTo(ModelFamily.GAMMA)
        assertThat(ModelFamily.fromId("Frontier")).isEqualTo(ModelFamily.FRONTIER)
    }

    @Test
    fun unknownOrNullIdFallsBackToDefault() {
        assertThat(ModelFamily.fromId(null)).isEqualTo(ModelFamily.DEFAULT)
        assertThat(ModelFamily.fromId("")).isEqualTo(ModelFamily.DEFAULT)
        assertThat(ModelFamily.fromId("omega")).isEqualTo(ModelFamily.DEFAULT)
    }

    @Test
    fun everyFamilyRoundTripsThroughItsId() {
        // Whatever id we persist must resolve back to the same family.
        for (family in ModelFamily.entries) {
            assertThat(ModelFamily.fromId(family.id)).isEqualTo(family)
        }
    }

    @Test
    fun onlyAquaRunsOnDevice() {
        // The picker's "Offline" badge keys off onDevice; pin the contract so
        // a new server-tier can't accidentally claim to run offline.
        assertThat(ModelFamily.AQUA.onDevice).isTrue()
        assertThat(ModelFamily.entries.filter { it.onDevice }).containsExactly(ModelFamily.AQUA)
    }
}
