package com.plantdiseases.app.util

import com.google.common.truth.Truth.assertWithMessage
import org.junit.Test

/**
 * The bilingual tables that stay in Kotlin on purpose: they produce both
 * languages at once (an offline scan is saved with its English and Russian
 * text, so it reads right after a language switch) inside plain-JVM code. Each
 * Russian entry must be Russian, each English entry must not be, and both
 * follow the app's plain punctuation (no middle dot or long dash).
 */
class BilingualTablesTest {

    private val pairs: List<Triple<String, String, String>> = buildList {
        DISEASE_KEYS.forEach { key ->
            add(Triple("DiseaseLabels.$key", name(key, ru = false), name(key, ru = true)))
        }
        OfflineScanContent.SUPPORTED_CLASSES.forEach { key ->
            val card = requireNotNull(OfflineScanContent.careCard(key))
            add(Triple("offline $key description", card.descriptionEn, card.descriptionRu))
            card.treatmentEn.zip(card.treatmentRu).forEachIndexed { i, (en, ru) ->
                add(Triple("offline $key treatment $i", en, ru))
            }
            card.preventionEn.zip(card.preventionRu).forEachIndexed { i, (en, ru) ->
                add(Triple("offline $key prevention $i", en, ru))
            }
            val rules = OfflineScanContent.careRules(key)
            add(Triple("offline $key key action", rules.keyActionEn, rules.keyActionRu))
        }
        SpeciesCatalog.popular.forEach { add(Triple("species ${it.scientific}", it.commonEn, it.commonRu)) }
    }

    @Test
    fun russianEntriesAreRussianAndEnglishOnesAreNot() {
        val wrong = pairs.flatMap { (where, en, ru) ->
            listOfNotNull(
                "$where (EN has Cyrillic): $en".takeIf { CYRILLIC.containsMatchIn(en) },
                "$where (RU has no Cyrillic): $ru".takeUnless { CYRILLIC.containsMatchIn(ru) },
            )
        }
        assertWithMessage("Entries in the wrong language").that(wrong).isEmpty()
    }

    @Test
    fun entriesUsePlainPunctuation() {
        val offenders = pairs.flatMap { (where, en, ru) -> listOf(where to en, where to ru) }
            .filter { (_, text) -> FORBIDDEN.any { it in text } }
            .map { (where, text) -> "$where: $text" }
        assertWithMessage("Use '-' or ',' instead of a middle dot or long dash").that(offenders).isEmpty()
    }

    private fun name(key: String, ru: Boolean): String =
        requireNotNull(DiseaseLabels.displayName(key, isRu = ru)) { key }

    private companion object {
        val CYRILLIC = Regex("[\\u0400-\\u04FF]")
        val FORBIDDEN = listOf("·", "—", "–")
        val DISEASE_KEYS = listOf(
            "healthy", "powdery_mildew", "gray_mold_botrytis", "leaf_spot", "pests",
            "leaf_spot_fungal", "bacterial_leaf_spot", "mealybugs", "scale", "viral_disease",
            "edema", "sunburn_or_scorch", "leaf_scorch_underwatering", "nutrient_deficiency", "not_a_plant",
        )
    }
}
