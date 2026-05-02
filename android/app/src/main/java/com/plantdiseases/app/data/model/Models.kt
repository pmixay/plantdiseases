package com.plantdiseases.app.data.model

import com.google.gson.annotations.SerializedName

/** Single bounding box returned by the YOLOv8 detector (Stage 1). */
data class DetectionRegion(
    @SerializedName("x") val x: Int,
    @SerializedName("y") val y: Int,
    @SerializedName("width") val width: Int,
    @SerializedName("height") val height: Int,
    @SerializedName("class") val className: String? = null,
    @SerializedName("confidence") val confidence: Float? = null
)

/** Stage 1 detection result — list of boxes plus the one Stage 2 used. */
data class Detection(
    @SerializedName("is_diseased") val isDiseased: Boolean,
    @SerializedName("detector_confidence") val detectorConfidence: Float,
    @SerializedName("regions") val regions: List<DetectionRegion> = emptyList(),
    @SerializedName("primary_region") val primaryRegion: DetectionRegion? = null
)

/** Single entry in the server-provided top-k candidate list. */
data class TopKEntry(
    @SerializedName("class") val className: String,
    @SerializedName("confidence") val confidence: Float,
    @SerializedName("name_en") val nameEn: String? = null,
    @SerializedName("name_ru") val nameRu: String? = null,
    @SerializedName("is_healthy") val isHealthy: Boolean? = null
)

/** Response from the server after analyzing an image */
data class AnalysisResponse(
    @SerializedName("disease_name") val diseaseName: String,
    @SerializedName("disease_name_ru") val diseaseNameRu: String,
    @SerializedName("confidence") val confidence: Float,
    @SerializedName("description") val description: String,
    @SerializedName("description_ru") val descriptionRu: String,
    @SerializedName("treatment") val treatment: List<String>,
    @SerializedName("treatment_ru") val treatmentRu: List<String>,
    @SerializedName("prevention") val prevention: List<String>,
    @SerializedName("prevention_ru") val preventionRu: List<String>,
    @SerializedName("is_healthy") val isHealthy: Boolean,
    @SerializedName("detection") val detection: Detection? = null,
    @SerializedName("all_probs") val allProbs: Map<String, Float>? = null,
    @SerializedName("top_k") val topK: List<TopKEntry>? = null,
    @SerializedName("warnings") val warnings: List<String>? = null,
    @SerializedName("pipeline_mode") val pipelineMode: String? = null,
    @SerializedName("elapsed_ms") val elapsedMs: Float? = null
)

/** Guide article about plant care */
data class GuideItem(
    val id: String,
    val titleEn: String,
    val titleRu: String,
    val descriptionEn: String,
    val descriptionRu: String,
    val contentEn: String,
    val contentRu: String,
    val iconRes: Int,
    val category: GuideCategory
)

enum class GuideCategory {
    COMMON_DISEASES,
    CARE_TIPS,
    WATERING,
    LIGHTING,
    PESTS
}

/** Scan history item for display */
data class ScanHistoryItem(
    val id: Long,
    val imagePath: String,
    val diseaseName: String,
    val diseaseNameRu: String,
    val confidence: Float,
    val isHealthy: Boolean,
    /**
     * True when the scan was classified as `not_a_plant`. Mutually
     * exclusive with `isHealthy`; `isHealthy=false && isNotAPlant=false`
     * means a real disease was diagnosed.
     */
    val isNotAPlant: Boolean,
    val timestamp: Long
)

/** AI Doctor chat request */
data class ChatRequest(
    @SerializedName("session_id") val sessionId: String,
    @SerializedName("message") val message: String,
    @SerializedName("context") val context: ChatContext? = null,
)

data class ChatContext(
    @SerializedName("disease") val disease: String?,
    @SerializedName("plant_name") val plantName: String,
)

data class ChatResponse(
    @SerializedName("reply") val reply: String,
    @SerializedName("suggested_questions") val suggestedQuestions: List<String>? = null,
)

/** PlantNet species candidate from /api/identify. */
data class SpeciesCandidate(
    @SerializedName("scientific_name") val scientificName: String?,
    @SerializedName("common_names") val commonNames: List<String> = emptyList(),
    @SerializedName("family") val family: String? = null,
    @SerializedName("genus") val genus: String? = null,
    @SerializedName("score") val score: Float = 0f,
) {
    val displayCommonName: String?
        get() = commonNames.firstOrNull()?.takeIf { it.isNotBlank() }
}

/**
 * /api/identify response. `available=false` is a graceful state when the
 * server has no PlantNet key, the quota is exhausted, or PlantNet errored —
 * the UI should fall back to manual species entry, not show an error toast.
 */
data class IdentifyResponse(
    @SerializedName("available") val available: Boolean,
    @SerializedName("results") val results: List<SpeciesCandidate> = emptyList(),
    @SerializedName("source") val source: String? = null,
    @SerializedName("organ") val organ: String? = null,
    @SerializedName("remaining_requests") val remainingRequests: Int? = null,
    @SerializedName("cached") val cached: Boolean? = null,
)

/** /api/care-plan request body. */
data class CarePlanRequest(
    @SerializedName("disease") val disease: String,
    @SerializedName("species") val species: String? = null,
    @SerializedName("light") val light: String = "bright_indirect",
    @SerializedName("locale") val locale: String = "en",
)

/**
 * /api/care-plan response. `wateringDaysRaw` is `Any?` because the server
 * may return either an integer interval ("every N days") or the string
 * `"as_needed"` for diseases where a fixed cadence is unsafe. Callers
 * should prefer the `wateringDaysInt` / `wateringDaysIsAsNeeded` helpers.
 */
data class CarePlanResponse(
    @SerializedName("species") val species: String,
    @SerializedName("disease") val disease: String,
    @SerializedName("light_tier") val lightTier: String,
    @SerializedName("watering_days") val wateringDaysRaw: Any? = null,
    @SerializedName("fertilise_weeks") val fertiliseWeeks: Int = 0,
    @SerializedName("humidity_target") val humidityTarget: String = "",
    @SerializedName("key_action") val keyAction: String = "",
    @SerializedName("follow_up_days") val followUpDays: Int = 14,
    @SerializedName("summary_md") val summaryMd: String? = null,
) {
    val wateringDaysInt: Int?
        get() = when (val v = wateringDaysRaw) {
            is Number -> v.toInt()
            is String -> v.toIntOrNull()
            else -> null
        }

    val wateringDaysIsAsNeeded: Boolean
        get() = (wateringDaysRaw as? String)?.equals("as_needed", ignoreCase = true) == true

    val hasSpecies: Boolean
        get() = species.isNotBlank() && !species.equals("unknown", ignoreCase = true)
}

/** Server health check response */
data class HealthResponse(
    @SerializedName("status") val status: String,
    @SerializedName("version") val version: String,
    @SerializedName("pipeline_mode") val pipelineMode: String,
    @SerializedName("detector_loaded") val detectorLoaded: Boolean,
    @SerializedName("classifier_loaded") val classifierLoaded: Boolean,
    @SerializedName("num_classes") val numClasses: Int,
    @SerializedName("uptime_seconds") val uptimeSeconds: Double? = null,
    @SerializedName("total_requests") val totalRequests: Int? = null
)
