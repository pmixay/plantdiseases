package com.plantdiseases.app.data.repository

import com.plantdiseases.app.auth.AuthRepository
import com.plantdiseases.app.auth.AuthState
import com.plantdiseases.app.auth.AuthenticationRequiredException
import com.plantdiseases.app.data.account.AccountApiException
import io.ktor.client.call.body
import io.ktor.client.request.HttpRequestBuilder
import io.ktor.client.request.header
import io.ktor.client.statement.HttpResponse
import io.ktor.http.HttpHeaders
import io.ktor.http.isSuccess
import java.net.SocketTimeoutException
import java.net.UnknownHostException

/**
 * Request plumbing the app-API repositories share: calls made as the signed-in
 * account (with [AuthRepository]'s one-time refresh on a 401), metered calls
 * that fall back to a guest, public reads that carry a token when there is one,
 * and the mapping of quota denials and failed protected calls to errors.
 */
internal class AppApiCalls(private val authRepository: AuthRepository?) {

    suspend fun <T> authenticatedAppCall(
        block: suspend (accessToken: String) -> T,
    ): Result<T> {
        val result = authRepository?.authenticatedCall(block)
            ?: return Result.failure(AuthenticationRequiredException())
        return result.fold(
            onSuccess = { Result.success(it) },
            onFailure = { error ->
                Result.failure(
                    when (error) {
                        is SocketTimeoutException -> ServerTimeoutException()
                        is UnknownHostException -> NoNetworkException()
                        is java.net.ConnectException -> ServerTimeoutException()
                        else -> error
                    },
                )
            },
        )
    }

    /**
     * Run one metered app request as the signed-in account when possible, or as
     * a guest otherwise. A signed-in 401 is converted to the account client's
     * refresh signal, so [AuthRepository] retries the block exactly once with a
     * fresh Bearer token. Callers create their idempotency UUID outside this
     * block; the refresh retry therefore reuses the same logical operation id.
     */
    suspend fun optionalMeteredCall(
        request: suspend (accessToken: String?) -> HttpResponse,
    ): Result<HttpResponse> {
        if (authRepository?.state?.value is AuthState.Authenticated) {
            return authenticatedAppCall { token ->
                val response = request(token)
                if (response.status.value == 401) {
                    throw AccountApiException(401, "auth_required")
                }
                response
            }
        }
        return try {
            Result.success(request(null))
        } catch (_: SocketTimeoutException) {
            Result.failure(ServerTimeoutException())
        } catch (_: UnknownHostException) {
            Result.failure(NoNetworkException())
        } catch (_: java.net.ConnectException) {
            Result.failure(ServerTimeoutException())
        } catch (failure: Exception) {
            Result.failure(failure)
        }
    }

    /** Public community reads carry an account token when one is available so
     * server-side block filtering follows the user across devices. Guests keep
     * the same public read path. A signed-in 401 gets the standard one-time
     * refresh/retry semantics through [authenticatedAppCall]. */
    suspend inline fun <reified T> optionalAccountGet(
        crossinline request: suspend (accessToken: String?) -> HttpResponse,
    ): Result<T> {
        if (authRepository?.state?.value is AuthState.Authenticated) {
            return authenticatedAppCall { token ->
                request(token).protectedBody<T>()
            }
        }
        return try {
            val response = request(null)
            if (!response.status.isSuccess()) {
                throw CommunityRequestException(response.status.value)
            }
            Result.success(response.body<T>())
        } catch (_: SocketTimeoutException) {
            Result.failure(ServerTimeoutException())
        } catch (_: UnknownHostException) {
            Result.failure(NoNetworkException())
        } catch (failure: Exception) {
            Result.failure(failure)
        }
    }
}

internal fun HttpRequestBuilder.bearer(token: String) {
    header(HttpHeaders.Authorization, "Bearer $token")
}

internal fun HttpRequestBuilder.metered(
    token: String?,
    requestId: String,
) {
    token?.let { bearer(it) }
    header(HEADER_IDEMPOTENCY_KEY, requestId)
}

/** Only quota denials carry the complete quota-header tuple. An ordinary
 * anti-abuse 429 deliberately stays a [RateLimitException]. [feature] names
 * the allowance so the limit dialog can say which one ran out; a request
 * sent without a Bearer token was a guest's. */
internal fun HttpResponse.meteredDenialOrNull(feature: MeteredFeature): Exception? {
    val plan = headers[HEADER_PLAN]?.trim()?.takeIf { it.isNotEmpty() }
    val limit = headers[HEADER_QUOTA_LIMIT]?.toIntOrNull()
    val remaining = headers[HEADER_QUOTA_REMAINING]?.toIntOrNull()
    val resetAt = headers[HEADER_QUOTA_RESET_AT]?.trim()?.takeIf { it.isNotEmpty() }
    val guest = call.request.headers[HttpHeaders.Authorization] == null
    return when {
        status.value == 402 -> ProRequiredException(plan, limit, remaining, resetAt, feature, guest)
        status.value == 429 &&
            plan != null &&
            limit != null &&
            remaining != null &&
            resetAt != null -> QuotaExhaustedException(plan, limit, remaining, resetAt, feature, guest)
        else -> null
    }
}

internal suspend inline fun <reified T> HttpResponse.protectedBody(): T {
    if (status.value == 401) throw AccountApiException(401, "auth_required")
    if (!status.isSuccess()) throw CommunityRequestException(status.value)
    return body()
}

internal suspend fun HttpResponse.requireProtectedSuccess() {
    if (status.value == 401) throw AccountApiException(401, "auth_required")
    if (!status.isSuccess()) throw CommunityRequestException(status.value)
}

private const val HEADER_IDEMPOTENCY_KEY = "X-Idempotency-Key"
private const val HEADER_PLAN = "X-PlantDiseases-Plan"
private const val HEADER_QUOTA_LIMIT = "X-Quota-Limit"
private const val HEADER_QUOTA_REMAINING = "X-Quota-Remaining"
private const val HEADER_QUOTA_RESET_AT = "X-Quota-Reset-At"
