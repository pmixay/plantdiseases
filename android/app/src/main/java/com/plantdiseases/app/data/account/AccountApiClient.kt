package com.plantdiseases.app.data.account

import com.plantdiseases.app.util.ServerConfig
import io.ktor.client.HttpClient
import io.ktor.client.call.body
import io.ktor.client.engine.okhttp.OkHttp
import io.ktor.client.plugins.contentnegotiation.ContentNegotiation
import io.ktor.client.plugins.defaultRequest
import io.ktor.client.request.delete
import io.ktor.client.request.get
import io.ktor.client.request.header
import io.ktor.client.request.patch
import io.ktor.client.request.post
import io.ktor.client.request.put
import io.ktor.client.request.setBody
import io.ktor.client.request.url
import io.ktor.client.statement.HttpResponse
import io.ktor.client.statement.bodyAsText
import io.ktor.http.ContentType
import io.ktor.http.HttpHeaders
import io.ktor.http.contentType
import io.ktor.http.isSuccess
import io.ktor.serialization.kotlinx.json.json
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
import java.util.concurrent.TimeUnit

/** Private account client. Bearer credentials are attached per protected call. */
class AccountApiClient(
    baseUrl: String = ServerConfig.accountBaseUrl,
) : AccountApi {
    private val json = Json {
        ignoreUnknownKeys = true
        coerceInputValues = true
    }

    // Account mutations are never transport-retried automatically: a response
    // may be lost after the server committed a one-use signup, link, nickname,
    // or deletion action. AuthRepository owns the sole, bounded 401 refresh
    // retry for calls made with an application access token.
    private val okHttp = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .addInterceptor { chain ->
            chain.proceed(
                chain.request().newBuilder()
                    .header("User-Agent", "PlantDiseases-Android")
                    .build(),
            )
        }
        .build()

    private val client = HttpClient(OkHttp) {
        engine { preconfigured = okHttp }
        expectSuccess = false
        install(ContentNegotiation) { json(json) }
        defaultRequest { url(baseUrl) }
    }

    override suspend fun exchange(
        provider: AuthProvider,
        providerAccessToken: String,
        installationId: String,
    ): ExchangeResult = client.post("v1/auth/${provider.wireName}/exchange") {
        contentType(ContentType.Application.Json)
        setBody(ExchangeRequest(providerAccessToken, installationId))
    }.decodeAuth()

    override suspend fun completeSignup(
        pendingSignupToken: String,
        nickname: String,
        installationId: String,
    ): ExchangeResult.Authenticated = client.post("v1/auth/signup/complete") {
        contentType(ContentType.Application.Json)
        setBody(CompleteSignupRequest(pendingSignupToken, nickname, installationId))
    }.decodeAuth().requireAuthenticated()

    override suspend fun cancelSignup(pendingSignupToken: String) {
        client.post("v1/auth/signup/cancel") {
            contentType(ContentType.Application.Json)
            setBody(CancelSignupRequest(pendingSignupToken))
        }.requireSuccess()
    }

    override suspend fun refresh(refreshToken: String): ExchangeResult.Authenticated =
        client.post("v1/auth/refresh") {
            contentType(ContentType.Application.Json)
            setBody(RefreshRequest(refreshToken))
        }.decodeAuth().requireAuthenticated()

    override suspend fun logout(accessToken: String) {
        client.post("v1/auth/logout") { bearer(accessToken) }.requireSuccess()
    }

    override suspend fun sessions(accessToken: String): List<AccountSession> =
        client.get("v1/auth/sessions") { bearer(accessToken) }
            .decode<SessionsResponse>().sessions

    override suspend fun revokeSession(accessToken: String, sessionId: String) {
        client.delete("v1/auth/sessions/$sessionId") { bearer(accessToken) }.requireSuccess()
    }

    override suspend fun reauthenticate(
        accessToken: String,
        provider: AuthProvider,
        providerAccessToken: String,
        scope: String,
    ): ReauthResponse = client.post("v1/auth/reauth") {
        bearer(accessToken)
        contentType(ContentType.Application.Json)
        setBody(ReauthRequest(provider.wireName, providerAccessToken, scope))
    }.decode()

    override suspend fun linkProvider(
        accessToken: String,
        provider: AuthProvider,
        providerAccessToken: String,
        reauthToken: String,
    ): LinkResponse = client.post("v1/auth/${provider.wireName}/link") {
        bearer(accessToken)
        contentType(ContentType.Application.Json)
        setBody(LinkRequest(providerAccessToken, reauthToken))
    }.decode()

    override suspend fun me(accessToken: String): AccountProfile =
        client.get("v1/me") { bearer(accessToken) }.decode()

    override suspend fun changeNickname(accessToken: String, nickname: String): NicknameResponse =
        client.patch("v1/me/nickname") {
            bearer(accessToken)
            contentType(ContentType.Application.Json)
            setBody(NicknameRequest(nickname))
        }.decode()

    override suspend fun deleteAccount(accessToken: String, reauthToken: String): DeletionResponse =
        client.delete("v1/me") {
            bearer(accessToken)
            contentType(ContentType.Application.Json)
            setBody(DeletionRequest(reauthToken))
        }.decode()

    override suspend fun registerPush(accessToken: String, fcmToken: String) {
        client.put("v1/push/device") {
            bearer(accessToken)
            contentType(ContentType.Application.Json)
            setBody(PushDeviceRequest(fcmToken))
        }.requireSuccess()
    }

    override suspend fun disablePush(accessToken: String) {
        client.delete("v1/push/device") { bearer(accessToken) }.requireSuccess()
    }

    private fun io.ktor.client.request.HttpRequestBuilder.bearer(token: String) {
        header(HttpHeaders.Authorization, "Bearer $token")
    }

    private suspend inline fun <reified T> HttpResponse.decode(): T {
        if (!status.isSuccess()) throw asException()
        return body()
    }

    private suspend fun HttpResponse.requireSuccess() {
        if (!status.isSuccess()) throw asException()
    }

    private suspend fun HttpResponse.decodeAuth(): ExchangeResult {
        if (!status.isSuccess()) throw asException()
        val payload = body<AuthResponse>()
        return when (payload.status) {
            "authenticated" -> ExchangeResult.Authenticated(
                accessToken = requireNotNull(payload.accessToken),
                refreshToken = requireNotNull(payload.refreshToken),
                expiresIn = payload.expiresIn,
                profile = requireNotNull(payload.profile),
                session = requireNotNull(payload.session),
            )
            "profile_required" -> ExchangeResult.ProfileRequired(
                provider = AuthProvider.entries.firstOrNull { it.wireName == payload.provider }
                    ?: throw AccountApiException(500, "invalid_provider_response"),
                pendingSignupToken = requireNotNull(payload.pendingSignupToken),
                expiresIn = payload.expiresIn,
            )
            else -> throw AccountApiException(500, "invalid_auth_response")
        }
    }

    private fun ExchangeResult.requireAuthenticated(): ExchangeResult.Authenticated =
        this as? ExchangeResult.Authenticated
            ?: throw AccountApiException(500, "invalid_auth_response")

    private suspend fun HttpResponse.asException(): AccountApiException {
        val code = runCatching {
            json.decodeFromString<ErrorEnvelope>(bodyAsText()).error.code
        }.getOrDefault("request_failed")
        return AccountApiException(status.value, code)
    }
}
