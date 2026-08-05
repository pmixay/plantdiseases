package com.plantdiseases.app.util

import com.google.common.truth.Truth.assertThat
import org.junit.Test

/**
 * Pure-JVM coverage for [DiseaseLabels], the single source of truth that
 * turns a model class key into a localised label. Guards against a class key
 * leaking through untranslated in the probability list / alternatives — the
 * `pests` regression where the raw key showed in a Russian UI.
 */
class DiseaseLabelsTest {

    @Test
    fun pests_is_localised_in_both_languages() {
        val en = DiseaseLabels.displayName("pests", isRu = false)
        val ru = DiseaseLabels.displayName("pests", isRu = true)
        // Never null — otherwise callers fall back to the raw "pests" key.
        assertThat(en).isEqualTo("Sap-sucking Pests")
        assertThat(ru).isEqualTo("Сосущие вредители")
        // The RU label must not be the raw key or the English text.
        assertThat(ru).isNotEqualTo("pests")
        assertThat(ru).isNotEqualTo(en)
    }

    @Test
    fun legacy_pest_keys_are_localised() {
        for (key in listOf("mealybugs", "mealybug", "scale", "scale_insects")) {
            assertThat(DiseaseLabels.displayName(key, isRu = false)).isNotNull()
            assertThat(DiseaseLabels.displayName(key, isRu = true)).isNotNull()
        }
    }

    @Test
    fun every_production_class_key_resolves() {
        // Mirrors the classifier head (server/classifier.py) — none of these
        // may return null, or they would render as the raw key in the verdict.
        val productionKeys = listOf(
            "gray_mold_botrytis", "healthy", "leaf_scorch_underwatering",
            "leaf_spot", "not_a_plant", "nutrient_deficiency", "pests",
            "powdery_mildew", "viral_disease",
        )
        for (key in productionKeys) {
            assertThat(DiseaseLabels.displayName(key, isRu = false)).isNotNull()
            assertThat(DiseaseLabels.displayName(key, isRu = true)).isNotNull()
        }
    }

    @Test
    fun unknown_key_returns_null_so_caller_can_fall_back() {
        assertThat(DiseaseLabels.displayName("totally_new_server_class", isRu = false)).isNull()
        assertThat(DiseaseLabels.displayName("totally_new_server_class", isRu = true)).isNull()
    }
}
