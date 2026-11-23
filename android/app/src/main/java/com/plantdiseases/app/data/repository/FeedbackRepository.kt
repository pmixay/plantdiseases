package com.plantdiseases.app.data.repository

import com.plantdiseases.app.data.local.ScanEntity
import com.plantdiseases.app.data.model.FeedbackRequest
import com.plantdiseases.app.data.model.FeedbackResponse
import com.plantdiseases.app.data.remote.PlantApiClient
import com.plantdiseases.app.util.DefaultUploadImageSanitizer
import com.plantdiseases.app.util.FeedbackReceiptStore
import com.plantdiseases.app.util.NoOpFeedbackReceiptStore
import com.plantdiseases.app.util.UploadImageSanitizer
import io.ktor.client.call.body
import io.ktor.client.request.forms.MultiPartFormDataContent
import io.ktor.client.request.forms.formData
import io.ktor.client.request.get
import io.ktor.client.request.post
import io.ktor.client.request.setBody
import io.ktor.http.ContentType
import io.ktor.http.Headers
import io.ktor.http.HttpHeaders
import io.ktor.http.contentType
import java.io.File
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Feedback on a diagnosis. Each saved scan keeps the server's one-use receipt
 * ([ScanRepository.saveScan] stores it); sending feedback spends it.
 */
class FeedbackRepository(
    private val apiClient: PlantApiClient,
    private val imageSanitizer: UploadImageSanitizer = DefaultUploadImageSanitizer,
    private val feedbackReceiptStore: FeedbackReceiptStore = NoOpFeedbackReceiptStore,
) {
    /**
     * Submit a thumbs-up / thumbs-down on a diagnosis.
     *
     * The server consumes a one-use receipt and updates the matching anonymous
     * training sample. The endpoint is rate-limited — if the client posts
     * faster than 1/sec the
     * request lands as a 429 and we surface a RateLimitException so
     * the UI can show a brief "try again in a moment" toast.
     */
    suspend fun submitFeedback(request: FeedbackRequest): Result<FeedbackResponse> =
        withContext(Dispatchers.IO) {
            try {
                val response = apiClient.httpClient.post("api/feedback") {
                    contentType(ContentType.Application.Json)
                    setBody(request)
                }
                when (response.status.value) {
                    in 200..299 -> Result.success(response.body<FeedbackResponse>())
                    429 -> Result.failure(RateLimitException())
                    503 -> Result.failure(ServerStartingException())
                    409 -> Result.failure(FeedbackReceiptException())
                    422 -> Result.failure(Exception("Invalid feedback payload"))
                    else -> Result.failure(Exception("Server error: ${response.status.value}"))
                }
            } catch (e: SocketTimeoutException) {
                Result.failure(ServerTimeoutException())
            } catch (e: UnknownHostException) {
                Result.failure(NoNetworkException())
            } catch (e: java.net.ConnectException) {
                Result.failure(ServerTimeoutException())
            } catch (e: Exception) {
                Result.failure(e)
            }
        }

    /**
     * Submit negative feedback and its sanitized image atomically. The receipt
     * is one-use, so this replaces (rather than follows) the JSON-only route.
     */
    suspend fun uploadFeedbackImage(
        request: FeedbackRequest,
        imageFile: java.io.File,
    ): Result<Unit> = withContext(Dispatchers.IO) {
        try {
            if (!imageFile.exists()) {
                return@withContext Result.failure(Exception("Image file missing"))
            }
            val image = imageSanitizer.sanitize(imageFile)
            val response = apiClient.httpClient.post("api/feedback/image") {
                setBody(
                    MultiPartFormDataContent(
                        formData {
                            append("feedback_receipt", request.feedbackReceipt)
                            append("correct", request.correct.toString())
                            request.correctedClass?.let { append("corrected_class", it) }
                            request.reason?.let { append("reason", it) }
                            append(
                                "image",
                                image.bytes,
                                Headers.build {
                                    append(HttpHeaders.ContentType, image.contentType)
                                    append(
                                        HttpHeaders.ContentDisposition,
                                        "filename=\"${image.filename}\"",
                                    )
                                },
                            )
                        },
                    ),
                )
            }
            if (response.status.value in 200..299) {
                Result.success(Unit)
            } else {
                when (response.status.value) {
                    429 -> Result.failure(RateLimitException())
                    503 -> Result.failure(ServerStartingException())
                    409 -> Result.failure(FeedbackReceiptException())
                    422 -> Result.failure(Exception("Invalid feedback payload"))
                    else -> Result.failure(Exception("Server error: ${response.status.value}"))
                }
            }
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    fun feedbackReceipt(scan: ScanEntity): String? = feedbackReceiptStore.get(scan.id.toString())

    fun consumeFeedbackReceipt(scan: ScanEntity) {
        feedbackReceiptStore.remove(scan.id.toString())
    }
}
