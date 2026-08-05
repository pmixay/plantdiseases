package com.plantdiseases.app.data.model

import com.google.common.truth.Truth.assertThat
import com.google.gson.Gson
import kotlinx.serialization.json.Json
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
    fun popularResponse_fullParse_mapsSnakeCaseFields() {
        val json = """
            {"scope":"country","country":"RU","locale":"ru",
             "items":[{"scientific":"Monstera deliciosa","common_en":"Swiss cheese plant",
                       "common_ru":"Монстера","image_url":"https://img/m.jpg"}]}
        """.trimIndent()
        val r = gson.fromJson(json, PopularResponse::class.java)
        assertThat(r.scope).isEqualTo("country")
        assertThat(r.country).isEqualTo("RU")
        assertThat(r.items).hasSize(1)
        val item = r.items[0]
        assertThat(item.scientific).isEqualTo("Monstera deliciosa")
        assertThat(item.commonEn).isEqualTo("Swiss cheese plant")
        assertThat(item.commonRu).isEqualTo("Монстера")
        assertThat(item.imageUrl).isEqualTo("https://img/m.jpg")
    }

    @Test
    fun popularResponse_missingFields_useSafeDefaults() {
        val r = gson.fromJson("""{"scope":"global"}""", PopularResponse::class.java)
        assertThat(r.scope).isEqualTo("global")
        assertThat(r.items).isEmpty()
        // A bare item with no image still parses (client shows a placeholder).
        val item = gson.fromJson("""{"scientific":"Aloe vera"}""", PopularItem::class.java)
        assertThat(item.scientific).isEqualTo("Aloe vera")
        assertThat(item.imageUrl).isNull()
    }

    @Test
    fun communityScansResponse_countryScopeFields_parse() {
        val r = gson.fromJson(
            """{"scope":"country","country":"US","country_name_en":"United States","country_name_ru":"United States","summary":{"scope":"country","country":"US","country_name_en":"United States","total":3}}""",
            CommunityScansResponse::class.java,
        )
        assertThat(r.scope).isEqualTo("country")
        assertThat(r.country).isEqualTo("US")
        assertThat(r.countryNameEn).isEqualTo("United States")
        assertThat(r.summary!!.scope).isEqualTo("country")
        assertThat(r.summary!!.countryNameEn).isEqualTo("United States")
        assertThat(r.summary!!.total).isEqualTo(3)
        // Absent flag defaults to false (no location prompt).
        assertThat(r.locationRequired).isFalse()
    }

    @Test
    fun communityScansResponse_locationRequiredFlag_parses() {
        val r = gson.fromJson(
            """{"scope":"global","items":[],"location_required":true}""",
            CommunityScansResponse::class.java,
        )
        assertThat(r.locationRequired).isTrue()
        assertThat(r.items).isEmpty()
    }

    // The PRODUCTION decode path is kotlinx.serialization (Ktor
    // ContentNegotiation), not Gson — and unlike Gson it enforces Kotlin
    // nullability. These tests mirror the app's exact Json config so they
    // catch the "explicit null on a strict field" crash the Gson tests can't.
    private val ktxJson = Json {
        ignoreUnknownKeys = true
        coerceInputValues = true
    }

    @Test
    fun communityScansResponse_nullCountry_decodesWithoutThrowing() {
        // Regression: a place with no ISO country (worldwide scope, or e.g.
        // Abkhazia) makes the server send `"country": null`. Strict kotlinx
        // decoding used to throw on that, surfacing as "Couldn't reach the
        // community". It must now decode cleanly with a null country.
        val json = """
            {"scope":"worldwide","country":null,"country_name_en":"Worldwide",
             "items":[],
             "summary":{"scope":"worldwide","country":null,
                        "country_name_en":"Worldwide","total":0}}
        """.trimIndent()
        val r = ktxJson.decodeFromString(CommunityScansResponse.serializer(), json)
        assertThat(r.scope).isEqualTo("worldwide")
        assertThat(r.summary!!.country).isNull()
        assertThat(r.summary!!.countryNameEn).isEqualTo("Worldwide")
    }

    @Test
    fun communityScansResponse_explicitNullOnDefaultedField_coercesToDefault() {
        // coerceInputValues: an explicit null on a field that has a default
        // (here the summary's non-null `total`) falls back to the default
        // instead of throwing — defence-in-depth against an evolving server.
        val json = """
            {"scope":"worldwide","items":[],
             "summary":{"country":null,"total":null,"scope":null}}
        """.trimIndent()
        val r = ktxJson.decodeFromString(CommunityScansResponse.serializer(), json)
        assertThat(r.summary!!.total).isEqualTo(0)
        assertThat(r.summary!!.scope).isNull()
    }
}
