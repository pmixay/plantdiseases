package com.plantdiseases.app.data.model

import com.google.common.truth.Truth.assertThat
import com.google.gson.Gson
import com.plantdiseases.app.util.ResolvedRegion
import org.junit.Test

/**
 * Pure-JVM tests for the wire models. The most important contract here is
 * that a partial / older server response degrades gracefully into safe
 * defaults rather than NPE-ing or showing 0% — Gson ignores Kotlin
 * non-null types, so every always-expected field carries a default.
 */
class ModelsTest {

    private val gson = Gson()

    @Test
    fun analysisResponse_missingFields_useSafeDefaults() {
        val r = gson.fromJson("""{"disease_name":"Leaf Spot"}""", AnalysisResponse::class.java)
        assertThat(r.diseaseName).isEqualTo("Leaf Spot")
        assertThat(r.diseaseNameRu).isEqualTo("")
        assertThat(r.confidence).isEqualTo(0f)
        assertThat(r.isHealthy).isFalse()
        assertThat(r.treatment).isEmpty()
        assertThat(r.prevention).isEmpty()
        assertThat(r.toxicSpeciesList).isEmpty()
        assertThat(r.toxicitySource).isEqualTo("unknown")
        assertThat(r.toxicToPets).isFalse()
        assertThat(r.isRepeat).isFalse()
    }

    @Test
    fun analysisResponse_fullParse() {
        val json = """
            {"class_name":"leaf_spot","disease_name":"Leaf Spot","confidence":0.83,
             "is_healthy":false,"severity":"high","scan_id":"abc-123",
             "all_probs":{"leaf_spot":0.83,"healthy":0.1},
             "species":{"available":true,
               "best":{"scientific_name":"Ficus elastica","common_name":"Rubber Plant","score":0.7}}}
        """.trimIndent()
        val r = gson.fromJson(json, AnalysisResponse::class.java)
        assertThat(r.classKey).isEqualTo("leaf_spot")
        assertThat(r.confidence).isWithin(1e-5f).of(0.83f)
        assertThat(r.severity).isEqualTo("high")
        assertThat(r.scanId).isEqualTo("abc-123")
        assertThat(r.allProbs!!["leaf_spot"]!!).isWithin(1e-4f).of(0.83f)
        assertThat(r.species?.best?.scientificName).isEqualTo("Ficus elastica")
        assertThat(r.species?.best?.displayName).isEqualTo("Rubber Plant")
    }

    private fun carePlan(species: String = "Ficus", watering: Any? = null) = CarePlanResponse(
        species = species,
        disease = "leaf_spot",
        lightTier = "bright_indirect",
        wateringDaysRaw = watering,
    )

    @Test
    fun carePlan_wateringDays_handlesIntStringAndAsNeeded() {
        assertThat(carePlan(watering = 7.0).wateringDaysInt).isEqualTo(7)
        assertThat(carePlan(watering = "5").wateringDaysInt).isEqualTo(5)
        assertThat(carePlan(watering = "as_needed").wateringDaysInt).isNull()
        assertThat(carePlan(watering = null).wateringDaysInt).isNull()
    }

    @Test
    fun carePlan_asNeededFlag() {
        assertThat(carePlan(watering = "as_needed").wateringDaysIsAsNeeded).isTrue()
        assertThat(carePlan(watering = "AS_NEEDED").wateringDaysIsAsNeeded).isTrue()
        assertThat(carePlan(watering = 7.0).wateringDaysIsAsNeeded).isFalse()
    }

    @Test
    fun carePlan_hasSpecies() {
        assertThat(carePlan(species = "Ficus elastica").hasSpecies).isTrue()
        assertThat(carePlan(species = "").hasSpecies).isFalse()
        assertThat(carePlan(species = "unknown").hasSpecies).isFalse()
        assertThat(carePlan(species = "UNKNOWN").hasSpecies).isFalse()
    }

    @Test
    fun carePlan_wateringDays_parsedFromGsonNumber() {
        // Gson deserialises a bare JSON number into a Double for Any?.
        val plan = gson.fromJson(
            """{"species":"Ficus","disease":"leaf_spot","light_tier":"t","watering_days":3}""",
            CarePlanResponse::class.java,
        )
        assertThat(plan.wateringDaysInt).isEqualTo(3)
        assertThat(plan.wateringDaysIsAsNeeded).isFalse()
    }

    @Test
    fun speciesMatch_displayName_prefersCommonThenScientific() {
        assertThat(SpeciesMatch("Ficus elastica", "Rubber Plant", 0.5f).displayName)
            .isEqualTo("Rubber Plant")
        assertThat(SpeciesMatch("Ficus elastica", null, 0.5f).displayName)
            .isEqualTo("Ficus elastica")
        assertThat(SpeciesMatch("Ficus elastica", "  ", 0.5f).displayName)
            .isEqualTo("Ficus elastica")
        assertThat(SpeciesMatch(null, null, 0f).displayName).isNull()
    }

    @Test
    fun speciesCandidate_displayCommonName_firstNonBlank() {
        assertThat(SpeciesCandidate("Ficus", listOf("Rubber Plant", "Rubber Tree")).displayCommonName)
            .isEqualTo("Rubber Plant")
        assertThat(SpeciesCandidate("Ficus", emptyList()).displayCommonName).isNull()
        assertThat(SpeciesCandidate("Ficus", listOf("")).displayCommonName).isNull()
    }

    @Test
    fun resolvedRegion_displayLabel() {
        assertThat(ResolvedRegion("US", "Boston").displayLabel).isEqualTo("Boston, US")
        assertThat(ResolvedRegion("US", null).displayLabel).isEqualTo("US")
        assertThat(ResolvedRegion("US", "").displayLabel).isEqualTo("US")
    }
}
