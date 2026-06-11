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

data class LabelStatsResponse(
    @SerializedName("per_class") val perClass: Map<String, LabelClassStat> = emptyMap(),
    val totals: Map<String, Int> = emptyMap(),
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
