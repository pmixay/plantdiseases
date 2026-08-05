package com.plantdiseases.app.util

import com.google.common.truth.Truth.assertThat
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
    fun buildResponse_viral_always_escalates_to_high() {
        // Even at modest confidence, viral disease is escalated (mirrors the
        // server's always-escalate-viral rule).
        val r = AquaInference.buildResponse(mapOf("viral_disease" to 0.4f, "healthy" to 0.35f))
        assertThat(r.classKey).isEqualTo("viral_disease")
        assertThat(r.severity).isEqualTo("high")
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
}
