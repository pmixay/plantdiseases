package com.plantdiseases.app.data.repository

import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.model.AnalysisAbstentionReasons
import com.plantdiseases.app.util.AppJson
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.floatOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject

/**
 * One backward-compatible rejection policy for persisted scan rows.
 *
 * Room predates the public ``abstained`` fields, so new responses persist
 * those fields as warning markers while old cached rows may carry only
 * ``nap_ood_gated`` or ``blurry_image``. Garden, history, community and result
 * surfaces must all interpret those rows identically; keeping this parsing in
 * one place prevents a raw diagnostic argmax from resurfacing as a diagnosis.
 */
internal object PersistedScanAbstentionPolicy {
    fun reason(scan: ScanEntity): String? {
        val warnings = warningTokens(scan.warnings)
        if ("nap_ood_gated" in warnings) return AnalysisAbstentionReasons.OOD
        if ("blurry_image" in warnings) return AnalysisAbstentionReasons.IMAGE_QUALITY
        warnings.firstOrNull { it.startsWith("abstention_reason:") }
            ?.substringAfter(':')
            ?.takeIf { it.isNotBlank() }
            ?.let { return it }
        if (naturalNapClass(scan)) return AnalysisAbstentionReasons.NOT_A_PLANT
        if ("abstained" in warnings) return AnalysisAbstentionReasons.UNKNOWN
        return null
    }

    fun isAbstained(scan: ScanEntity): Boolean = reason(scan) != null

    private fun naturalNapClass(scan: ScanEntity): Boolean =
        diagnosticClassKey(scan) == AnalysisAbstentionReasons.NOT_A_PLANT ||
            scan.diseaseName.equals("Not a Plant", ignoreCase = true) ||
            scan.diseaseNameRu.equals("Не растение", ignoreCase = true)

    private fun diagnosticClassKey(scan: ScanEntity): String? =
        scan.displayedClassKey?.takeIf { it.isNotBlank() } ?: runCatching {
            AppJson.parseToJsonElement(scan.allProbs.orEmpty()).jsonObject.entries
                .mapNotNull { (key, value) ->
                    (value as? JsonPrimitive)?.floatOrNull?.let { key to it }
                }
                .maxByOrNull { it.second }
                ?.first
        }.getOrNull()

    private fun warningTokens(raw: String?): List<String> {
        if (raw.isNullOrBlank()) return emptyList()
        return runCatching {
            AppJson.parseToJsonElement(raw).jsonArray
                .mapNotNull { item ->
                    (item as? JsonPrimitive)?.takeIf { it.isString }
                        ?.content
                        ?.trim()
                        ?.lowercase()
                        ?.takeIf { it.isNotBlank() }
                }
        }.getOrDefault(emptyList())
    }
}
