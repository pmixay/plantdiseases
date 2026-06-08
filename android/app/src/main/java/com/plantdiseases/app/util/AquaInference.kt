package com.plantdiseases.app.util

import com.plantdiseases.app.data.model.AnalysisResponse
import com.plantdiseases.app.data.model.TopKEntry

/**
 * Turns on-device [AquaClassifier] probabilities into a *minimal*
 * [AnalysisResponse] so the rest of the app (saveScan, ResultActivity) renders
 * an Aqua scan unchanged.
 *
 * "Minimal" by design: offline we have the verdict, confidence, bilingual
 * display name and a severity heuristic, but NOT the rich treatment/prevention
 * text (that lives server-side in diseases_data.py). Those fields stay empty
 * offline; if the user later goes online the server model fills them in on a
 * re-scan. Detection/regions and species are server-only and stay null.
 */
object AquaInference {

    private const val HEALTHY = "healthy"
    private val VIRAL = setOf("viral_disease")

    fun buildResponse(probs: Map<String, Float>): AnalysisResponse {
        val ranked = probs.entries.sortedByDescending { it.value }
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

        return AnalysisResponse(
            classKey = topKey,
            family = ModelFamily.AQUA.id,
            diseaseName = DiseaseLabels.displayName(topKey, isRu = false) ?: prettify(topKey),
            diseaseNameRu = DiseaseLabels.displayName(topKey, isRu = true) ?: prettify(topKey),
            confidence = topConf,
            isHealthy = isHealthy,
            severity = severityFor(topKey, topConf, isHealthy),
            allProbs = probs,
            topK = topK,
            // Flag so the result screen can hint that offline advice is thinner
            // and the cloud model can give fuller guidance.
            warnings = listOf("offline_aqua"),
        )
    }

    /** Server severity comes from bbox area × confidence margin. Offline we
     *  approximate from class + top-1 confidence; viral is always escalated
     *  (matches the server's always-escalate-viral rule). */
    private fun severityFor(key: String, conf: Float, isHealthy: Boolean): String = when {
        isHealthy -> "low"
        key in VIRAL -> "high"
        conf >= 0.80f -> "high"
        conf >= 0.55f -> "medium"
        else -> "low"
    }

    private fun prettify(key: String): String =
        key.split('_').joinToString(" ") { it.replaceFirstChar { c -> c.uppercase() } }
}
