package com.plantdiseases.app.util

import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.data.model.AnalysisAbstentionReasons
import com.plantdiseases.app.data.model.CANONICAL_ANALYSIS_CLASS_KEYS
import com.plantdiseases.app.data.model.diagnosticContractViolation
import com.plantdiseases.app.data.model.effectiveAbstentionReason
import org.junit.Assert.assertThrows
import org.junit.Test

/**
 * Pure-JVM coverage for the on-device Aqua family's probability → response
 * mapping and softmax. No Android deps, so these run without Robolectric.
 */
class AquaInferenceTest {

    @Test
    fun softmax_is_normalised_and_orders_preserved() {
        val out = AquaClassifier.softmax(floatArrayOf(2f, 1f, 0f))
        assertThat(out.sum()).isWithin(1e-5f).of(1f)
        assertThat(out[0]).isGreaterThan(out[1])
        assertThat(out[1]).isGreaterThan(out[2])
    }

    @Test
    fun softmax_handles_large_logits_without_overflow() {
        val out = AquaClassifier.softmax(floatArrayOf(1000f, 999f))
        assertThat(out.sum()).isWithin(1e-5f).of(1f)
        assertThat(out[0]).isGreaterThan(out[1])
    }

    @Test
    fun softmax_rejects_empty_or_nonfinite_model_output() {
        assertThrows(IllegalArgumentException::class.java) {
            AquaClassifier.softmax(floatArrayOf())
        }
        assertThrows(IllegalArgumentException::class.java) {
            AquaClassifier.softmax(floatArrayOf(1f, Float.NaN))
        }
    }

    @Test
    fun buildResponse_picks_argmax_and_tags_family() {
        val probs = mapOf("healthy" to 0.1f, "leaf_spot" to 0.7f, "rust" to 0.2f)
        val r = AquaInference.buildResponse(probs)
        assertThat(r.classKey).isEqualTo("leaf_spot")
        assertThat(r.confidence).isEqualTo(0.7f)
        assertThat(r.family).isEqualTo("aqua")
        assertThat(r.isHealthy).isFalse()
        assertThat(r.allProbs).isEqualTo(probs)
        // Offline contract: a warning flags the on-device origin so the UI
        // can caveat the result and skip server-only calls.
        assertThat(r.warnings).contains("offline_aqua")
    }

    @Test
    fun buildResponse_fills_bilingual_card_content_from_local_table() {
        val r = AquaInference.buildResponse(mapOf("powdery_mildew" to 0.8f, "healthy" to 0.2f))
        // Offline scans must not render blank: description / treatment /
        // prevention come from OfflineScanContent in both languages.
        assertThat(r.description).isNotEmpty()
        assertThat(r.descriptionRu).isNotEmpty()
        assertThat(r.treatment).isNotEmpty()
        assertThat(r.treatmentRu).isNotEmpty()
        assertThat(r.prevention).isNotEmpty()
        assertThat(r.preventionRu).isNotEmpty()
    }

    @Test
    fun buildResponse_healthy_is_low_severity() {
        val r = AquaInference.buildResponse(mapOf("healthy" to 0.9f, "rust" to 0.1f))
        assertThat(r.isHealthy).isTrue()
        assertThat(r.severity).isEqualTo("low")
    }

    @Test
    fun buildResponse_notAPlant_is_not_reported_as_high_severity() {
        val r = AquaInference.buildResponse(mapOf("not_a_plant" to 0.95f, "healthy" to 0.05f))
        assertThat(r.classKey).isEqualTo("not_a_plant")
        assertThat(r.severity).isEqualTo("low")
    }

    @Test
    fun buildResponse_rejects_invalid_mass_and_normalises_valid_scores() {
        for (invalid in listOf(
            emptyMap(),
            mapOf("healthy" to Float.NaN),
            mapOf("healthy" to -0.1f, "leaf_spot" to 1.1f),
            mapOf("healthy" to 0f, "leaf_spot" to 0f),
        )) {
            assertThrows(IllegalArgumentException::class.java) {
                AquaInference.buildResponse(invalid)
            }
        }
        val r = AquaInference.buildResponse(mapOf("leaf_spot" to 8f, "healthy" to 2f))
        assertThat(r.confidence).isWithin(1e-6f).of(0.8f)
        assertThat(checkNotNull(r.allProbs).values.sum()).isWithin(1e-6f).of(1f)
    }

    @Test
    fun buildResponse_diseaseSeverity_stays_unassessedAndConservative() {
        // The class head has no lesion-extent target, so confidence cannot be
        // converted into a clinical-looking severity level.
        val r = AquaInference.buildResponse(mapOf("viral_disease" to 0.4f, "healthy" to 0.35f))
        assertThat(r.classKey).isEqualTo("viral_disease")
        assertThat(r.severity).isEqualTo("low")
    }

    @Test
    fun buildResponse_topK_is_capped_and_localised() {
        val probs = mapOf(
            "leaf_spot" to 0.5f,
            "rust" to 0.25f,
            "healthy" to 0.15f,
            "aphids" to 0.10f,
        )
        val r = AquaInference.buildResponse(probs)
        val topK = r.topK!!
        assertThat(topK).hasSize(3)
        assertThat(topK[0].className).isEqualTo("leaf_spot")
        // Bilingual names are filled from DiseaseLabels.
        assertThat(topK[0].nameEn).isNotEmpty()
        assertThat(topK[0].nameRu).isNotEmpty()
    }

    @Test
    fun imageQualityFailure_isExplicitPreInferenceAbstention() {
        val response = AquaInference.buildImageQualityAbstention()

        assertThat(response.classKey).isEqualTo("unknown")
        assertThat(response.confidence).isEqualTo(0f)
        assertThat(response.allProbs).isEmpty()
        assertThat(response.topK).isEmpty()
        assertThat(response.abstained).isTrue()
        assertThat(response.effectiveAbstentionReason())
            .isEqualTo(AnalysisAbstentionReasons.IMAGE_QUALITY)
        assertThat(response.warnings).containsAtLeast("offline_aqua", "blurry_image")
        assertThat(response.diagnosticContractViolation()).isNull()
    }

    @Test
    fun nineClassDiseaseEnvelope_satisfiesDiagnosticContract() {
        val leftover = (1f - 0.82f) / (CANONICAL_ANALYSIS_CLASS_KEYS.size - 1)
        val probs = CANONICAL_ANALYSIS_CLASS_KEYS.associateWith { key ->
            if (key == "leaf_spot") 0.82f else leftover
        }
        val response = AquaInference.buildResponse(probs)
        assertThat(response.diagnosticContractViolation()).isNull()
        assertThat(response.abstained).isFalse()
        assertThat(response.verdictStatus).isEqualTo("accepted")
    }

    @Test
    fun notAPlantEnvelope_isExplicitAbstentionAndContractValid() {
        val leftover = (1f - 0.91f) / (CANONICAL_ANALYSIS_CLASS_KEYS.size - 1)
        val probs = CANONICAL_ANALYSIS_CLASS_KEYS.associateWith { key ->
            if (key == "not_a_plant") 0.91f else leftover
        }
        val response = AquaInference.buildResponse(probs)
        assertThat(response.abstained).isTrue()
        assertThat(response.effectiveAbstentionReason())
            .isEqualTo(AnalysisAbstentionReasons.NOT_A_PLANT)
        assertThat(response.diagnosticContractViolation()).isNull()
    }

    @Test
    fun partialProbabilityMap_isRejectedByDiagnosticContract() {
        val response = AquaInference.buildResponse(
            mapOf("healthy" to 0.1f, "leaf_spot" to 0.7f, "rust" to 0.2f),
        )
        assertThat(response.diagnosticContractViolation()).isNotNull()
    }
}
