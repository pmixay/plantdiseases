package com.plantdiseases.app.util

import com.google.common.truth.Truth.assertThat
import org.junit.Test

/** Verifies the locale-aware species-name picker ([localizedSpeciesName]). */
class SpeciesNameTest {

    @Test
    fun en_prefers_common_name() {
        assertThat(localizedSpeciesName("Sansevieria trifasciata", "Snake plant", isRu = false))
            .isEqualTo("Snake plant")
    }

    @Test
    fun en_falls_back_to_scientific() {
        assertThat(localizedSpeciesName("Sansevieria trifasciata", null, isRu = false))
            .isEqualTo("Sansevieria trifasciata")
    }

    @Test
    fun ru_keeps_a_cyrillic_common_name() {
        assertThat(localizedSpeciesName("Sansevieria trifasciata", "Сансевиерия", isRu = true))
            .isEqualTo("Сансевиерия")
    }

    @Test
    fun ru_drops_english_common_name_to_latin() {
        // A Russian user must never be shown an English-only common name.
        assertThat(localizedSpeciesName("Sansevieria trifasciata", "Snake plant", isRu = true))
            .isEqualTo("Sansevieria trifasciata")
    }

    @Test
    fun ru_keeps_english_common_when_no_scientific() {
        assertThat(localizedSpeciesName(null, "Snake plant", isRu = true))
            .isEqualTo("Snake plant")
    }

    @Test
    fun blank_inputs_return_null() {
        assertThat(localizedSpeciesName("  ", "", isRu = false)).isNull()
    }
}
