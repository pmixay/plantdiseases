package com.plantdiseases.app.data.repository

// Failures the app-API repositories return. Screens map them to error cards
// and limit dialogs, whichever repository the call went through.

class ServerTimeoutException : Exception("Server is not responding")
class NoNetworkException : Exception("No network connection")
class RateLimitException : Exception("Server is busy, please wait")

/** Which daily allowance a metered request spends. */
enum class MeteredFeature { ANALYSIS, FRONTIER_ANALYSIS, IDENTIFICATION, CHAT, CARE_PLAN, OTHER }

class ProRequiredException(
    val plan: String?,
    val limit: Int?,
    val remaining: Int?,
    val resetAt: String?,
    val feature: MeteredFeature = MeteredFeature.OTHER,
    val guest: Boolean = false,
) : Exception("PlantDiseases PRO is required")

class QuotaExhaustedException(
    val plan: String,
    val limit: Int,
    val remaining: Int,
    val resetAt: String,
    val feature: MeteredFeature = MeteredFeature.OTHER,
    val guest: Boolean = false,
) : Exception("Daily $plan quota exhausted")

class ServerStartingException : Exception("Server is starting / pipeline unavailable")
class ModelUnavailableException(
    val family: String,
    val reason: String?,
    val serverMessage: String?,
) : Exception("Model family $family is unavailable${reason?.let { ": $it" }.orEmpty()}")
class InvalidAnalysisResponseException(
    val violation: String,
) : Exception("Analysis response violated the diagnostic contract: $violation")
class UnsupportedImageException : Exception("Unsupported image format")
class FeedbackReceiptException : Exception("Feedback receipt is unavailable")
class CommunityRequestException(val statusCode: Int) : Exception("Community request failed: $statusCode")
