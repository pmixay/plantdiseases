package com.plantdiseases.app.util

import com.google.common.truth.Truth.assertThat
import org.junit.Test

/**
 * Pure-JVM coverage for the offline (Aqua) scan fallback content: the
 * bilingual care cards and the simplified local care-plan rules table.
 */
class OfflineScanContentTest {

    @Test
    fun every_supported_class_has_a_bilingual_care_card() {
        OfflineScanContent.SUPPORTED_CLASSES.forEach { key ->
            val card = OfflineScanContent.careCard(key)
            assertThat(card).isNotNull()
            card!!
            assertThat(card.descriptionEn).isNotEmpty()
            assertThat(card.descriptionRu).isNotEmpty()
            assertThat(card.treatmentEn).isNotEmpty()
            assertThat(card.treatmentRu).isNotEmpty()
            assertThat(card.preventionEn).isNotEmpty()
            assertThat(card.preventionRu).isNotEmpty()
            // EN and RU lists must stay in lock-step so the result screen
            // renders the same number of steps in both languages.
            assertThat(card.treatmentRu).hasSize(card.treatmentEn.size)
            assertThat(card.preventionRu).hasSize(card.preventionEn.size)
        }
    }

    @Test
    fun every_supported_class_has_sane_care_rules() {
        OfflineScanContent.SUPPORTED_CLASSES.forEach { key ->
            val r = OfflineScanContent.careRules(key)
            assertThat(r.wateringDays).isAtLeast(1)
            assertThat(r.wateringDays).isAtMost(30)
            assertThat(r.humidityMin).isLessThan(r.humidityMax)
            assertThat(r.humidityMin).isAtLeast(20)
            assertThat(r.humidityMax).isAtMost(90)
            assertThat(r.keyActionEn).isNotEmpty()
            assertThat(r.keyActionRu).isNotEmpty()
            assertThat(r.followUpDays).isAtLeast(1)
        }
    }

    @Test
    fun legacy_keys_normalise_to_canonical_content() {
        assertThat(OfflineScanContent.careCard("mealybugs"))
            .isEqualTo(OfflineScanContent.careCard("pests"))
        assertThat(OfflineScanContent.careCard("scale_insects"))
            .isEqualTo(OfflineScanContent.careCard("pests"))
        assertThat(OfflineScanContent.careCard("bacterial_leaf_spot"))
            .isEqualTo(OfflineScanContent.careCard("leaf_spot"))
    }

    @Test
    fun unknown_class_has_no_card_but_gets_default_rules() {
        assertThat(OfflineScanContent.careCard("not_a_plant")).isNull()
        val r = OfflineScanContent.careRules("totally_new_server_class")
        assertThat(r.wateringDays).isEqualTo(7)
        assertThat(r.keyActionEn).isNotEmpty()
        assertThat(r.keyActionRu).isNotEmpty()
    }

    @Test
    fun local_care_plan_maps_rules_and_locale() {
        val en = OfflineScanContent.localCarePlan("pests", isRu = false)
        val ru = OfflineScanContent.localCarePlan("pests", isRu = true)
        val rules = OfflineScanContent.careRules("pests")

        assertThat(en.wateringDaysInt).isEqualTo(rules.wateringDays)
        assertThat(en.lightTier).isEqualTo(rules.lightTier)
        assertThat(en.humidityTarget).contains("${rules.humidityMin}")
        assertThat(en.humidityTarget).contains("${rules.humidityMax}")
        assertThat(en.followUpDays).isEqualTo(rules.followUpDays)
        assertThat(en.keyAction).isEqualTo(rules.keyActionEn)
        assertThat(ru.keyAction).isEqualTo(rules.keyActionRu)
        // The offline plan must stay v1-only / clearly basic.
        assertThat(en.summaryMd).isNull()
        assertThat(en.treatment).isNull()
        assertThat(en.checkpoints).isNull()
    }

    @Test
    fun nutrient_deficiency_plan_feeds_more_often() {
        val plan = OfflineScanContent.localCarePlan("nutrient_deficiency", isRu = false)
        assertThat(plan.fertiliseWeeks).isEqualTo(2)
        val other = OfflineScanContent.localCarePlan("leaf_spot", isRu = false)
        assertThat(other.fertiliseWeeks).isEqualTo(4)
    }
}
