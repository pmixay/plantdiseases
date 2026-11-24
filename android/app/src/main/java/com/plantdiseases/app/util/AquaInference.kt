package com.plantdiseases.app.util

import com.plantdiseases.app.data.model.AnalysisResponse
import com.plantdiseases.app.data.model.AnalysisAbstentionReasons
import com.plantdiseases.app.data.model.TopKEntry

/**
 * Turns on-device [AquaClassifier] probabilities into an [AnalysisResponse]
 * so the rest of the app (saveScan, ResultActivity) renders an Aqua scan
 * unchanged.
 *
 * Offline we have the verdict, confidence and bilingual display name;
 * description / treatment / prevention come from the
 * local bilingual fallback table ([OfflineScanContent]) instead of the
 * server's richer copy. Detection/regions and species are server-only and
 * stay null. The `offline_aqua` warning flags the result so the UI can
 * caveat it and skip server-only calls.
 */
object AquaInference {

    private const val HEALTHY = "healthy"

    /** Honest pre-inference result when the existing local blur QA fails. */
    fun buildImageQualityAbstention(): AnalysisResponse = AnalysisResponse(
        classKey = "unknown",
        family = ModelFamily.AQUA.id,
        diseaseName = "Unidentified condition",
        diseaseNameRu = "Не удалось определить",
        confidence = 0f,
        isHealthy = false,
        severity = "low",
        allProbs = emptyMap(),
        topK = emptyList(),
        warnings = listOf("offline_aqua", "blurry_image"),
        abstained = true,
        abstentionReason = AnalysisAbstentionReasons.IMAGE_QUALITY,
        verdictStatus = "abstained",
    )

    /**
     * [outOfDistribution]: the sidecar's MaxLogit floor ([AquaOodGate]) refused
     * the frame. Like the server's OOD rejection, the class/probability fields
     * stay as raw diagnostics while every accepted-diagnosis claim (name, care,
     * health) is neutralised. A natural `not_a_plant` argmax keeps its own copy.
     */
    fun buildResponse(probs: Map<String, Float>, outOfDistribution: Boolean = false): AnalysisResponse {
        require(probs.isNotEmpty()) { "Aqua probability map is empty" }
        require(probs.keys.all { it.isNotBlank() }) { "Aqua probability map has a blank class key" }
        require(probs.values.all { it.isFinite() && it >= 0f }) {
            "Aqua probabilities must be finite and non-negative"
        }
        val sum = probs.values.sum()
        require(sum.isFinite() && sum > 0f) { "Aqua probability mass must be positive" }
        // AquaClassifier already returns a normalised softmax. Renormalise only
        // when a caller supplies meaningfully off-scale values, preserving the
        // exact map on the normal inference path.
        val safeProbs = if (kotlin.math.abs(sum - 1f) <= 1e-4f) {
            probs
        } else {
            probs.mapValues { (_, value) -> value / sum }
        }
        val ranked = safeProbs.entries.sortedByDescending { it.value }
        val top = ranked.firstOrNull()
        val topKey = top?.key ?: "unknown"
        val topConf = top?.value ?: 0f
        val isHealthy = topKey == HEALTHY

        val topK = ranked.take(3).map { (key, conf) ->
            TopKEntry(
                className = key,
                confidence = conf,
                nameEn = DiseaseLabels.displayName(key, isRu = false) ?: prettify(key),
                nameRu = DiseaseLabels.displayName(key, isRu = true) ?: prettify(key),
                isHealthy = key == HEALTHY,
            )
        }

        // Local bilingual fallback content — the server isn't around to
        // supply description / treatment / prevention for an offline scan.
        val card = OfflineScanContent.careCard(topKey)
        val naturalNap = topKey == "not_a_plant"
        if (outOfDistribution && !naturalNap) {
            return AnalysisResponse(
                classKey = topKey,
                family = ModelFamily.AQUA.id,
                diseaseName = "Unidentified condition",
                diseaseNameRu = "Не удалось определить",
                confidence = topConf,
                isHealthy = false,
                severity = "low",
                allProbs = safeProbs,
                topK = topK,
                warnings = listOf("offline_aqua", "aqua_ood_gated"),
                abstained = true,
                abstentionReason = AnalysisAbstentionReasons.OOD,
                verdictStatus = "abstained",
            )
        }

        return AnalysisResponse(
            classKey = topKey,
            family = ModelFamily.AQUA.id,
            diseaseName = DiseaseLabels.displayName(topKey, isRu = false) ?: prettify(topKey),
            diseaseNameRu = DiseaseLabels.displayName(topKey, isRu = true) ?: prettify(topKey),
            description = card?.descriptionEn ?: "",
            descriptionRu = card?.descriptionRu ?: "",
            treatment = card?.treatmentEn ?: emptyList(),
            treatmentRu = card?.treatmentRu ?: emptyList(),
            prevention = card?.preventionEn ?: emptyList(),
            preventionRu = card?.preventionRu ?: emptyList(),
            confidence = topConf,
            isHealthy = isHealthy,
            // Aqua predicts a condition class, not lesion extent. Confidence
            // is certainty about that class, not disease severity, so keep the
            // API's conservative placeholder until severity-labelled data (or
            // a lesion model) exists. This matches the server contract.
            severity = "low",
            allProbs = safeProbs,
            topK = topK,
            // Flag so the result screen can hint that offline advice is thinner
            // and the cloud model can give fuller guidance.
            warnings = listOf("offline_aqua"),
            abstained = naturalNap,
            abstentionReason = if (naturalNap) AnalysisAbstentionReasons.NOT_A_PLANT else null,
            verdictStatus = if (naturalNap) "abstained" else "accepted",
        )
    }

    private fun prettify(key: String): String =
        key.split('_').joinToString(" ") { it.replaceFirstChar { c -> c.uppercase() } }
}
