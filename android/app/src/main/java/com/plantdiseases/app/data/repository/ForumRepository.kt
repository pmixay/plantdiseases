package com.plantdiseases.app.data.repository

import com.plantdiseases.app.auth.AuthRepository
import com.plantdiseases.app.auth.AuthState
import com.plantdiseases.app.auth.AuthenticationRequiredException
import com.plantdiseases.app.data.account.AccountApiException
import com.plantdiseases.app.data.model.ForumAcceptRequest
import com.plantdiseases.app.data.model.ForumActivityResponse
import com.plantdiseases.app.data.model.ForumAnswer
import com.plantdiseases.app.data.model.ForumAnswerCreateRequest
import com.plantdiseases.app.data.model.ForumBlockRequest
import com.plantdiseases.app.data.model.ForumLikeRequest
import com.plantdiseases.app.data.model.ForumLikeResponse
import com.plantdiseases.app.data.model.ForumMediaUpload
import com.plantdiseases.app.data.model.ForumPage
import com.plantdiseases.app.data.model.ForumPost
import com.plantdiseases.app.data.model.ForumPostCreateRequest
import com.plantdiseases.app.data.model.ForumPostEditRequest
import com.plantdiseases.app.data.model.ForumReactionRequest
import com.plantdiseases.app.data.model.ForumReactionResponse
import com.plantdiseases.app.data.model.ForumReportRequest
import com.plantdiseases.app.data.model.ForumSpace
import com.plantdiseases.app.data.model.ForumSpacesResponse
import com.plantdiseases.app.data.model.HelperProfile
import com.plantdiseases.app.data.model.ModActionResult
import com.plantdiseases.app.data.model.ModAnswerActionRequest
import com.plantdiseases.app.data.model.ModAppealDecisionRequest
import com.plantdiseases.app.data.model.ModAppealRequest
import com.plantdiseases.app.data.model.ModDismissRequest
import com.plantdiseases.app.data.model.ModNotices
import com.plantdiseases.app.data.model.ModPostActionRequest
import com.plantdiseases.app.data.model.ModQueue
import com.plantdiseases.app.data.model.ModRewardFreezeRequest
import com.plantdiseases.app.data.model.ModSanctionRequest
import com.plantdiseases.app.data.model.ModVerifyRequest
import com.plantdiseases.app.data.model.StaffMe
import com.plantdiseases.app.data.remote.PlantApiClient
import io.ktor.client.call.body
import io.ktor.client.request.HttpRequestBuilder
import io.ktor.client.request.delete
import io.ktor.client.request.forms.MultiPartFormDataContent
import io.ktor.client.request.forms.formData
import io.ktor.client.request.get
import io.ktor.client.request.header
import io.ktor.client.request.parameter
import io.ktor.client.request.patch
import io.ktor.client.request.post
import io.ktor.client.request.setBody
import io.ktor.client.statement.HttpResponse
import io.ktor.client.statement.bodyAsText
import io.ktor.http.ContentType
import io.ktor.http.Headers
import io.ktor.http.HttpHeaders
import io.ktor.http.contentType
import io.ktor.http.isSuccess
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import java.net.SocketTimeoutException
import java.net.UnknownHostException

/**
 * The community forum API: feed, search, posts, answers, likes, photos,
 * achievements and moderation. Reads work as a guest and carry the account
 * token when signed in (so the server applies the reader's block list);
 * every write goes through [AuthRepository.authenticatedCall], which refreshes
 * an expired access token once.
 *
 * A refused request surfaces as [ForumException] with the server's stable
 * error code (`self_like`, `muted`, `level_required`, ...), which the screens
 * map to localized messages.
 */
class ForumRepository(
    private val apiClient: PlantApiClient,
    private val authRepository: AuthRepository?,
) {

    /** A request the server refused, with its HTTP status and error code. */
    class ForumException(val status: Int, val code: String) : Exception("Forum request failed: $status $code")

    private val errorJson = Json { ignoreUnknownKeys = true }

    val isSignedIn: Boolean
        get() = authRepository?.state?.value is AuthState.Authenticated

    // ── reading ───────────────────────────────────────────────────────────

    suspend fun spaces(): Result<List<ForumSpace>> = read<ForumSpacesResponse> { token ->
        apiClient.httpClient.get("api/community/spaces") { token?.let { bearer(it) } }
    }.map { it.items }

    suspend fun feed(
        tab: String,
        sort: String,
        space: String? = null,
        cursor: String? = null,
    ): Result<ForumPage> = read { token ->
        apiClient.httpClient.get("api/community/feed") {
            token?.let { bearer(it) }
            parameter("tab", tab)
            parameter("sort", sort)
            space?.let { parameter("space", it) }
            cursor?.let { parameter("cursor", it) }
        }
    }

    suspend fun search(query: SearchQuery, cursor: String? = null): Result<ForumPage> = read { token ->
        apiClient.httpClient.get("api/community/search") {
            token?.let { bearer(it) }
            query.text?.takeIf { it.isNotBlank() }?.let { parameter("q", it.trim()) }
            query.space?.let { parameter("space", it) }
            if (query.solvedOnly) parameter("solved", true)
            if (query.withPhotos) parameter("with_photos", true)
            query.condition?.let { parameter("condition", it) }
            query.species?.takeIf { it.isNotBlank() }?.let { parameter("species", it) }
            cursor?.let { parameter("cursor", it) }
        }
    }

    suspend fun post(postId: String): Result<ForumPost> = read { token ->
        apiClient.httpClient.get("api/community/posts/$postId") { token?.let { bearer(it) } }
    }

    suspend fun helperProfile(authorToken: String): Result<HelperProfile> = read { token ->
        apiClient.httpClient.get("api/community/helpers/$authorToken") { token?.let { bearer(it) } }
    }

    suspend fun myAchievements(): Result<HelperProfile> = write { token ->
        apiClient.httpClient.get("api/community/me/achievements") { bearer(token) }
    }

    suspend fun activity(since: Long): Result<ForumActivityResponse> = write { token ->
        apiClient.httpClient.get("api/community/activity") {
            bearer(token)
            parameter("since", since)
        }
    }

    /** URL of a forum photo, for Glide. Public while its post is visible. */
    fun mediaUrl(mediaId: String): String = "${apiClient.baseUrl}api/community/media/$mediaId"

    // ── writing ───────────────────────────────────────────────────────────

    /**
     * Upload one already-sanitized JPEG. With [scanToken] the bytes must be the
     * exact photo the server analysed for that token, so the post can carry the
     * AI verdict.
     */
    suspend fun uploadPhoto(jpeg: ByteArray, scanToken: String? = null): Result<ForumMediaUpload> = write { token ->
        apiClient.httpClient.post("api/community/media") {
            bearer(token)
            setBody(
                MultiPartFormDataContent(
                    formData {
                        scanToken?.takeIf { it.isNotBlank() }?.let { append("scan_token", it) }
                        append(
                            "image",
                            jpeg,
                            Headers.build {
                                append(HttpHeaders.ContentType, "image/jpeg")
                                append(HttpHeaders.ContentDisposition, "filename=\"photo.jpg\"")
                            },
                        )
                    },
                ),
            )
        }
    }

    suspend fun createPost(request: ForumPostCreateRequest): Result<ForumPost> = write { token ->
        apiClient.httpClient.post("api/community/posts") { json(token, request) }
    }

    suspend fun editPost(postId: String, request: ForumPostEditRequest): Result<ForumPost> = write { token ->
        apiClient.httpClient.patch("api/community/posts/$postId") { json(token, request) }
    }

    suspend fun deletePost(postId: String): Result<Unit> = writeUnit { token ->
        apiClient.httpClient.delete("api/community/posts/$postId") { bearer(token) }
    }

    suspend fun likePost(postId: String, liked: Boolean): Result<ForumLikeResponse> = write { token ->
        apiClient.httpClient.post("api/community/posts/$postId/like") {
            json(token, ForumLikeRequest(liked))
        }
    }

    suspend fun answer(postId: String, request: ForumAnswerCreateRequest): Result<ForumAnswer> = write { token ->
        apiClient.httpClient.post("api/community/posts/$postId/answers") { json(token, request) }
    }

    suspend fun deleteAnswer(answerId: String): Result<Unit> = writeUnit { token ->
        apiClient.httpClient.delete("api/community/answers/$answerId") { bearer(token) }
    }

    /** [reaction] is `like`, `not_helpful`, or null to clear. */
    suspend fun react(answerId: String, reaction: String?): Result<ForumReactionResponse> = write { token ->
        apiClient.httpClient.post("api/community/answers/$answerId/reaction") {
            json(token, ForumReactionRequest(reaction))
        }
    }

    suspend fun accept(postId: String, answerId: String?): Result<Unit> = writeUnit { token ->
        apiClient.httpClient.post("api/community/posts/$postId/accept") {
            json(token, ForumAcceptRequest(answerId))
        }
    }

    suspend fun report(targetType: String, targetId: String, reason: String): Result<Unit> = writeUnit { token ->
        apiClient.httpClient.post("api/community/forum/report") {
            json(token, ForumReportRequest(targetType, targetId, reason))
        }
    }

    suspend fun block(targetType: String, targetId: String): Result<Unit> = writeUnit { token ->
        apiClient.httpClient.post("api/community/forum/block") {
            json(token, ForumBlockRequest(targetType, targetId))
        }
    }

    // ── moderation ────────────────────────────────────────────────────────

    suspend fun staffMe(): Result<StaffMe> = write { token ->
        apiClient.httpClient.get("api/community/admin/me") { bearer(token) }
    }

    suspend fun moderationQueue(kind: String): Result<ModQueue> = write { token ->
        apiClient.httpClient.get("api/community/admin/queue") {
            bearer(token)
            parameter("kind", kind)
        }
    }

    suspend fun moderatePost(postId: String, request: ModPostActionRequest): Result<ModActionResult> = write { token ->
        apiClient.httpClient.post("api/community/admin/posts/$postId/action") { json(token, request) }
    }

    suspend fun moderateAnswer(answerId: String, request: ModAnswerActionRequest): Result<ModActionResult> = write { token ->
        apiClient.httpClient.post("api/community/admin/answers/$answerId/action") { json(token, request) }
    }

    suspend fun verifyAnswer(answerId: String, verified: Boolean): Result<Unit> = writeUnit { token ->
        apiClient.httpClient.post("api/community/admin/answers/$answerId/verify") {
            json(token, ModVerifyRequest(verified))
        }
    }

    suspend fun dismissReports(targetType: String, targetId: String): Result<Unit> = writeUnit { token ->
        apiClient.httpClient.post("api/community/admin/reports/dismiss") {
            json(token, ModDismissRequest(targetType, targetId))
        }
    }

    suspend fun sanction(authorToken: String, request: ModSanctionRequest): Result<ModActionResult> = write { token ->
        apiClient.httpClient.post("api/community/admin/actors/$authorToken/sanction") { json(token, request) }
    }

    suspend fun resolveAppeal(appealId: String, overturn: Boolean): Result<Unit> = writeUnit { token ->
        apiClient.httpClient.post("api/community/admin/appeals/$appealId/resolve") {
            json(token, ModAppealDecisionRequest(if (overturn) "overturn" else "uphold"))
        }
    }

    suspend fun setRewardFreeze(authorToken: String, frozen: Boolean): Result<Unit> = writeUnit { token ->
        apiClient.httpClient.post("api/community/admin/actors/$authorToken/rewards") {
            json(token, ModRewardFreezeRequest(frozen))
        }
    }

    suspend fun excludeFromTraining(postId: String): Result<Unit> = writeUnit { token ->
        apiClient.httpClient.post("api/community/admin/posts/$postId/exclude-training") { bearer(token) }
    }

    suspend fun notices(): Result<ModNotices> = write { token ->
        apiClient.httpClient.get("api/community/moderation/notices") { bearer(token) }
    }

    suspend fun appeal(actionId: String, text: String): Result<Unit> = writeUnit { token ->
        apiClient.httpClient.post("api/community/moderation/appeals") {
            json(token, ModAppealRequest(actionId, text))
        }
    }

    // ── plumbing ──────────────────────────────────────────────────────────

    /** Search filters; at least one of [text], [condition] or [species] is needed. */
    data class SearchQuery(
        val text: String? = null,
        val space: String? = null,
        val solvedOnly: Boolean = false,
        val withPhotos: Boolean = false,
        val condition: String? = null,
        val species: String? = null,
    )

    private fun HttpRequestBuilder.bearer(token: String) {
        header(HttpHeaders.Authorization, "Bearer $token")
    }

    /** Bearer + JSON body. Inline and reified: Ktor picks the serializer from
     *  the static type, so an `Any` parameter would fail at runtime. */
    private inline fun <reified B : Any> HttpRequestBuilder.json(token: String, body: B) {
        bearer(token)
        contentType(ContentType.Application.Json)
        setBody(body)
    }

    private suspend fun HttpResponse.failure(): Nothing {
        if (status.value == 401) throw AccountApiException(401, "auth_required")
        val code = runCatching {
            errorJson.parseToJsonElement(bodyAsText()).jsonObject["detail"]?.jsonPrimitive?.content
        }.getOrNull().orEmpty()
        throw ForumException(status.value, code.ifBlank { "http_${status.value}" })
    }

    private suspend inline fun <reified T> HttpResponse.parsed(): T {
        if (!status.isSuccess()) failure()
        return body()
    }

    private suspend inline fun <reified T> read(
        crossinline request: suspend (accessToken: String?) -> HttpResponse,
    ): Result<T> = withContext(Dispatchers.IO) {
        if (isSignedIn) {
            write<T> { token -> request(token) }
        } else {
            mapped { request(null).parsed<T>() }
        }
    }

    private suspend inline fun <reified T> write(
        crossinline request: suspend (accessToken: String) -> HttpResponse,
    ): Result<T> = withContext(Dispatchers.IO) {
        val auth = authRepository ?: return@withContext Result.failure(AuthenticationRequiredException())
        auth.authenticatedCall { token -> request(token).parsed<T>() }.recoverCatching { throw normalize(it) }
    }

    private suspend inline fun writeUnit(
        crossinline request: suspend (accessToken: String) -> HttpResponse,
    ): Result<Unit> = withContext(Dispatchers.IO) {
        val auth = authRepository ?: return@withContext Result.failure(AuthenticationRequiredException())
        auth.authenticatedCall { token ->
            val response = request(token)
            if (!response.status.isSuccess()) response.failure()
        }.recoverCatching { throw normalize(it) }
    }

    private inline fun <T> mapped(block: () -> T): Result<T> = try {
        Result.success(block())
    } catch (failure: Exception) {
        Result.failure(normalize(failure))
    }

    private fun normalize(error: Throwable): Throwable = when (error) {
        is SocketTimeoutException, is java.net.ConnectException -> ScanRepository.ServerTimeoutException()
        is UnknownHostException -> ScanRepository.NoNetworkException()
        else -> error
    }
}
