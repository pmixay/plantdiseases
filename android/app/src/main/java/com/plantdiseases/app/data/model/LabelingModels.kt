package com.plantdiseases.app.data.model

import com.google.gson.annotations.SerializedName

/** DTOs for the admin data-labeling tool (the /api/label endpoints). */

data class LabelLoginRequest(val password: String)

data class LabelLoginResponse(
    val token: String,
    @SerializedName("expires_at") val expiresAt: Long = 0,
)

data class LabelCard(
    val id: Long,
    @SerializedName("class") val klass: String,
    @SerializedName("suggested_class") val suggestedClass: String? = null,
    val source: String? = null,
    @SerializedName("source_url") val sourceUrl: String? = null,
    @SerializedName("image_url") val imageUrl: String,
    @SerializedName("model_pred") val modelPred: String? = null,
    @SerializedName("model_conf") val modelConf: Double? = null,
    @SerializedName("model_set") val modelSet: List<String> = emptyList(),
    // User-feedback context (null for dataset/scan cards): true = thumbs-up,
    // false = thumbs-down ("user proposed X / model said Y"), plus the note.
    @SerializedName("scan_feedback") val scanFeedback: Boolean? = null,
    @SerializedName("feedback_note") val feedbackNote: String? = null,
    // true ⇒ spot-check of a confident model call (audit sampling).
    val audit: Boolean = false,
)

data class LabelNextResponse(
    val cards: List<LabelCard> = emptyList(),
    @SerializedName("lease_seconds") val leaseSeconds: Int = 0,
)

data class LabelVerdictItem(val id: Long, val verdict: String)

data class LabelVerdictBatch(val verdicts: List<LabelVerdictItem>)

data class LabelVerdictResponse(val applied: Int = 0)

data class LabelClassStat(
    val weight: Int = 0,
    val validated: Int = 0,
    val pending: Int = 0,
    val claimed: Int = 0,
    val no: Int = 0,
    @SerializedName("not_sure") val notSure: Int = 0,
    val satisfied: Boolean = false,
    val remaining: Int = 0,
)

data class LabelSourceStat(
    val pending: Int = 0,
    val claimed: Int = 0,
    val resolved: Int = 0,
)

data class LabelStatsResponse(
    @SerializedName("per_class") val perClass: Map<String, LabelClassStat> = emptyMap(),
    val totals: Map<String, Int> = emptyMap(),
    // Per-source-group counts ("feedback" / "dataset" / "scans"); empty when
    // talking to an older server (Gson default keeps back-compat).
    val sources: Map<String, LabelSourceStat> = emptyMap(),
)

data class LabelClueItem(
    @SerializedName("class") val klass: String,
    val name: String? = null,
    @SerializedName("photo_url") val photoUrl: String? = null,
)

data class LabelClueResponse(
    val suggested: LabelClueItem,
    @SerializedName("pass") val pass: List<String> = emptyList(),
    @SerializedName("drop") val drop: List<String> = emptyList(),
    val confusables: List<LabelClueItem> = emptyList(),
)

data class LabelExportResponse(
    val bundle: String,
    @SerializedName("download_url") val downloadUrl: String? = null,
    @SerializedName("kaggle_command") val kaggleCommand: String? = null,
)
