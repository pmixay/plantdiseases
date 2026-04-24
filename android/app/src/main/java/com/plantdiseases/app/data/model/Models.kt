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
    val timestamp: Long
)

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
