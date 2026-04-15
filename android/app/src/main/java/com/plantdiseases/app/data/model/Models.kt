package com.plantdiseases.app.data.model

import com.google.gson.annotations.SerializedName

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
    @SerializedName("is_healthy") val isHealthy: Boolean
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
