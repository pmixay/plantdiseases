package com.plantdiseases.app.data.model

import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.util.AppJson
import kotlinx.serialization.json.Json
import org.junit.Test

/**
 * Pure-JVM tests for the wire models. The most important contract here is
 * that partial / older JSON decodes into safe values rather than NPE-ing, and
 * that the explicit diagnostic contract then prevents those defaults from
 * being accepted or saved as a real HTTP diagnosis.
 *
 * Two decoders are exercised on purpose. [ktxJson] mirrors the app's Ktor
 * config and is the production HTTP path. [AppJson] is the *persistence* path:
 * the types it decodes here (care plans, popular bundles, community feeds)
 * are the ones written to util/ResponseCache, so files already on disk must
 * keep decoding into them.
 */
class ModelsTest {

    private fun canonicalProbs(vararg values: Pair<String, Float>): Map<String, Float> =
        linkedMapOf(
            "gray_mold_botrytis" to 0f,
            "healthy" to 0f,
            "leaf_scorch_underwatering" to 0f,
            "leaf_spot" to 0f,
            "not_a_plant" to 0f,
            "nutrient_deficiency" to 0f,
            "pests" to 0f,
            "powdery_mildew" to 0f,
            "viral_disease" to 0f,
        ).apply { putAll(values) }

    @Test
    fun analysisResponse_missingFields_useSafeDefaults() {
        // AnalysisResponse never goes through the cache in production — it is
        // decoded by Ktor + kotlinx.serialization (and built directly by
        // AquaInference on-device), so assert the missing-field tolerance
        // against the decoder the app actually uses.
        val r = ktxJson.decodeFromString(
            AnalysisResponse.serializer(),
            """{"disease_name":"Leaf Spot"}""",
        )
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
        assertThat(r.available).isTrue()
        assertThat(r.isEffectivelyAbstained()).isFalse()
        assertThat(r.diagnosticContractViolation()).isEqualTo("class_name is missing")
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
        val r = ktxJson.decodeFromString(AnalysisResponse.serializer(), json)
        assertThat(r.classKey).isEqualTo("leaf_spot")
        assertThat(r.confidence).isWithin(1e-5f).of(0.83f)
        assertThat(r.severity).isEqualTo("high")
        assertThat(r.scanId).isEqualTo("abc-123")
        assertThat(r.allProbs!!["leaf_spot"]!!).isWithin(1e-4f).of(0.83f)
        assertThat(r.species?.best?.scientificName).isEqualTo("Ficus elastica")
        assertThat(r.species?.best?.displayName).isEqualTo("Rubber Plant")
    }

    @Test
    fun analysisResponse_explicitAbstention_preservesDiagnosticClass() {
        val json = """
            {"class_name":"leaf_spot","confidence":0.83,
             "all_probs":{"leaf_spot":0.83,"healthy":0.17},
             "abstained":true,"abstention_reason":"ood",
             "verdict_status":"abstained"}
        """.trimIndent()
        val r = ktxJson.decodeFromString(AnalysisResponse.serializer(), json)
        assertThat(r.classKey).isEqualTo("leaf_spot")
        assertThat(r.allProbs!!["leaf_spot"]).isWithin(1e-5f).of(0.83f)
        assertThat(r.isEffectivelyAbstained()).isTrue()
        assertThat(r.effectiveAbstentionReason()).isEqualTo(AnalysisAbstentionReasons.OOD)
    }

    @Test
    fun analysisResponse_legacyWarningsAndNaturalNap_failClosed() {
        val legacyOod = AnalysisResponse(
            classKey = "leaf_spot",
            warnings = listOf("nap_ood_gated"),
        )
        val legacyBlur = AnalysisResponse(
            classKey = "not_a_plant",
            warnings = listOf("blurry_image"),
        )
        val naturalNap = AnalysisResponse(classKey = "not_a_plant")

        assertThat(legacyOod.effectiveAbstentionReason())
            .isEqualTo(AnalysisAbstentionReasons.OOD)
        assertThat(legacyBlur.effectiveAbstentionReason())
            .isEqualTo(AnalysisAbstentionReasons.IMAGE_QUALITY)
        assertThat(naturalNap.effectiveAbstentionReason())
            .isEqualTo(AnalysisAbstentionReasons.NOT_A_PLANT)
    }

    @Test
    fun analysisResponse_diagnosticContract_acceptsConsistentArgmaxAndNaturalNap() {
        val accepted = AnalysisResponse(
            classKey = "leaf_spot",
            diseaseName = "Leaf Spot",
            confidence = 0.8004f,
            allProbs = canonicalProbs("leaf_spot" to 0.8f, "healthy" to 0.2f),
            topK = listOf(
                TopKEntry("leaf_spot", 0.8f),
                TopKEntry("healthy", 0.2f),
            ),
        )
        val naturalNap = AnalysisResponse(
            classKey = "not_a_plant",
            diseaseName = "Not a plant",
            confidence = 0.7f,
            allProbs = canonicalProbs("not_a_plant" to 0.7f, "healthy" to 0.3f),
            topK = listOf(
                TopKEntry("not_a_plant", 0.7f),
                TopKEntry("healthy", 0.3f),
            ),
        )

        assertThat(accepted.diagnosticContractViolation()).isNull()
        assertThat(naturalNap.diagnosticContractViolation()).isNull()
    }

    @Test
    fun analysisResponse_diagnosticContract_acceptsPreInferenceQualityRejection() {
        val qualityRejected = AnalysisResponse(
            classKey = "unknown",
            confidence = 0f,
            allProbs = emptyMap(),
            topK = emptyList(),
            warnings = listOf("blurry_image"),
            abstained = true,
            abstentionReason = AnalysisAbstentionReasons.IMAGE_QUALITY,
            verdictStatus = "abstained",
        )

        assertThat(qualityRejected.diagnosticContractViolation()).isNull()
    }

    @Test
    fun analysisResponse_diagnosticContract_rejectsContradictions() {
        val wrongArgmax = AnalysisResponse(
            classKey = "leaf_spot",
            diseaseName = "Leaf Spot",
            confidence = 0.2f,
            allProbs = canonicalProbs("healthy" to 0.8f, "leaf_spot" to 0.2f),
            topK = listOf(
                TopKEntry("healthy", 0.8f),
                TopKEntry("leaf_spot", 0.2f),
            ),
        )
        val fabricatedQualityDistribution = AnalysisResponse(
            classKey = "unknown",
            confidence = 0f,
            allProbs = mapOf("healthy" to 1f),
            topK = emptyList(),
            warnings = listOf("blurry_image"),
        )
        val novelButProbabilityConsistentClass = AnalysisResponse(
            classKey = "future_disease",
            diseaseName = "Future Disease",
            confidence = 0.8f,
            allProbs = linkedMapOf("future_disease" to 0.8f, "healthy" to 0.2f),
            topK = listOf(
                TopKEntry("future_disease", 0.8f),
                TopKEntry("healthy", 0.2f),
            ),
        )
        val partialProbabilityMap = AnalysisResponse(
            classKey = "leaf_spot",
            diseaseName = "Leaf Spot",
            confidence = 1f,
            allProbs = mapOf("leaf_spot" to 1f),
            topK = listOf(TopKEntry("leaf_spot", 1f)),
        )

        assertThat(wrongArgmax.diagnosticContractViolation())
            .isEqualTo("class_name is not the all_probs argmax")
        assertThat(fabricatedQualityDistribution.diagnosticContractViolation())
            .isEqualTo("image-quality rejection carries classifier probabilities")
        assertThat(novelButProbabilityConsistentClass.diagnosticContractViolation())
            .isEqualTo("class_name is not a known class key")
        assertThat(partialProbabilityMap.diagnosticContractViolation())
            .isEqualTo("all_probs keys do not match the canonical taxonomy")
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
    fun carePlan_wateringDays_parsedFromABareNumber() {
        val plan = AppJson.decodeFromString<CarePlanResponse>("""{"species":"Ficus","disease":"leaf_spot","light_tier":"t","watering_days":3}""")
        assertThat(plan.wateringDaysInt).isEqualTo(3)
        assertThat(plan.wateringDaysIsAsNeeded).isFalse()
    }

    @Test
    fun carePlan_missingProductsFields_staysNullNotACrash() {
        // Pre-Garden-refactor server response shape — no "products" or
        // "treatment_conditions" keys at all. Must degrade to null, the
        // same way every other v2-additive field already does.
        val plan = AppJson.decodeFromString<CarePlanResponse>("""{"species":"Ficus","disease":"leaf_spot","light_tier":"t"}""")
        assertThat(plan.products).isNull()
        assertThat(plan.treatmentConditions).isNull()
    }

    @Test
    fun carePlan_parsesProductsAndTreatmentConditions() {
        val json = """
            {"species":"Ficus","disease":"gray_mold_botrytis","light_tier":"t",
             "products":[{"key":"switch","kind":"fungicide","name":"Switch 62.5WG",
               "active_ingredient":"cyprodinil + fludioxonil","note":"Alternate modes of action."}],
             "treatment_conditions":{"best_time":"Morning.","temp_c":[15,25],"dry_hours":24}}
        """.trimIndent()
        val plan = AppJson.decodeFromString<CarePlanResponse>(json)
        assertThat(plan.products).hasSize(1)
        assertThat(plan.products!![0].activeIngredient).isEqualTo("cyprodinil + fludioxonil")
        assertThat(plan.treatmentConditions?.tempC).containsExactly(15, 25).inOrder()
        assertThat(plan.treatmentConditions?.dryHours).isEqualTo(24)
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
        val r = AppJson.decodeFromString<PopularResponse>(json)
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
        val r = AppJson.decodeFromString<PopularResponse>("""{"scope":"global"}""")
        assertThat(r.scope).isEqualTo("global")
        assertThat(r.items).isEmpty()
        // A bare item with no image still parses (client shows a placeholder).
        val item = AppJson.decodeFromString<PopularItem>("""{"scientific":"Aloe vera"}""")
        assertThat(item.scientific).isEqualTo("Aloe vera")
        assertThat(item.imageUrl).isNull()
    }

    @Test
    fun communityScansResponse_countryScopeFields_parse() {
        val r = AppJson.decodeFromString<CommunityScansResponse>("""{"scope":"country","country":"US","country_name_en":"United States","country_name_ru":"United States","summary":{"scope":"country","country":"US","country_name_en":"United States","total":3}}""")
        assertThat(r.scope).isEqualTo("country")
        assertThat(r.country).isEqualTo("US")
        assertThat(r.countryNameEn).isEqualTo("United States")
        val summary = requireNotNull(r.summary)
        assertThat(summary.scope).isEqualTo("country")
        assertThat(summary.countryNameEn).isEqualTo("United States")
        assertThat(summary.total).isEqualTo(3)
        // Absent flag defaults to false (no location prompt).
        assertThat(r.locationRequired).isFalse()
    }

    @Test
    fun communityScansResponse_locationRequiredFlag_parses() {
        val r = AppJson.decodeFromString<CommunityScansResponse>("""{"scope":"global","items":[],"location_required":true}""")
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
        val summary = requireNotNull(r.summary)
        assertThat(summary.country).isNull()
        assertThat(summary.countryNameEn).isEqualTo("Worldwide")
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
        val summary = requireNotNull(r.summary)
        assertThat(summary.total).isEqualTo(0)
        assertThat(summary.scope).isNull()
    }
}
