package com.plantdiseases.app.data.account

import com.plantdiseases.app.auth.AccountEndpointScope
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
    private val accountIssuer = AccountEndpointScope.from(baseUrl).issuer
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
        // Some request bodies carry one-use provider/reauth credentials. A
        // transport failure after the server commits is ambiguous, so OkHttp
        // must not replay them on a replacement connection by itself.
        .retryOnConnectionFailure(false)
        // Every account request can carry a bearer, provider, refresh, or
        // one-use reauthentication credential. Never replay any of them to a
        // redirect target; the release account origin is fixed at build time.
        .followRedirects(false)
        .followSslRedirects(false)
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
        // Ktor can process redirects after the engine returns them. Keep this
        // disabled here as well as in OkHttp so account credentials never
        // leave the fixed release origin through a Location response.
        followRedirects = false
        expectSuccess = false
        // OkHttp redirect handling is disabled above, but Ktor also has a
        // client-level redirect plugin which can replay a completed engine
        // response. Disable both layers so no credential-bearing request can
        // leave the configured account issuer.
        followRedirects = false
        install(ContentNegotiation) { json(json) }
        defaultRequest { url(accountIssuer) }
    }

    override suspend fun exchange(
        provider: AuthProvider,
        providerAccessToken: String,
        installationId: String,
    ): ExchangeResult = client.post("v1/auth/${provider.wireName}/exchange") {
        contentType(ContentType.Application.Json)
        setBody(ExchangeRequest(providerAccessToken, installationId))
    }.decodeExchange()

    override suspend fun registerEmail(
        email: String,
        password: String,
        installationId: String,
    ): ExchangeResult.ProfileRequired = client.post("v1/auth/email/register") {
        contentType(ContentType.Application.Json)
        setBody(EmailCredentialRequest(email, password, installationId))
    }.decodeEmailRegistration()

    override suspend fun loginEmail(
        email: String,
        password: String,
        installationId: String,
    ): ExchangeResult = client.post("v1/auth/email/login") {
        contentType(ContentType.Application.Json)
        setBody(EmailCredentialRequest(email, password, installationId))
    }.decodeExchange()

    override suspend fun completeSignup(
        pendingSignupToken: String,
        completionId: String,
        nickname: String,
        installationId: String,
    ): ExchangeResult.Authenticated = client.post("v1/auth/signup/complete") {
        contentType(ContentType.Application.Json)
        setBody(
            CompleteSignupRequest(
                pendingSignupToken,
                completionId,
                nickname,
                installationId,
            ),
        )
    }.decodeSignupCompletion()

    override suspend fun confirmSignup(
        pendingSignupToken: String,
        completionId: String,
        installationId: String,
    ) {
        client.post("v1/auth/signup/confirm") {
            contentType(ContentType.Application.Json)
            setBody(ConfirmSignupRequest(pendingSignupToken, completionId, installationId))
        }.requireSuccess()
    }

    override suspend fun cancelSignup(
        pendingSignupToken: String,
        completionId: String,
        installationId: String,
    ) {
        client.post("v1/auth/signup/cancel") {
            contentType(ContentType.Application.Json)
            setBody(CancelSignupRequest(pendingSignupToken, completionId, installationId))
        }.requireSuccess()
    }

    override suspend fun refresh(
        refreshToken: String,
        rotationId: String,
    ): ExchangeResult.Authenticated =
        client.post("v1/auth/refresh") {
            contentType(ContentType.Application.Json)
            setBody(RefreshRequest(refreshToken, rotationId))
        }.decodeAuthenticated()

    override suspend fun logout(refreshToken: String) {
        client.post("v1/auth/logout") {
            contentType(ContentType.Application.Json)
            setBody(LogoutRequest(refreshToken))
        }.requireSuccess()
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

    override suspend fun reauthenticateEmail(
        accessToken: String,
        password: String,
        scope: String,
    ): ReauthResponse = client.post("v1/auth/email/reauth") {
        bearer(accessToken)
        contentType(ContentType.Application.Json)
        setBody(EmailReauthRequest(password, scope))
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

    override suspend fun entitlements(accessToken: String): EntitlementsResponse =
        client.get("v1/entitlements") { bearer(accessToken) }.decode()

    override suspend fun verifyRustorePurchase(
        accessToken: String,
        productId: String,
        purchaseId: String,
        sandbox: Boolean,
    ): EntitlementsResponse = client.post("v1/billing/rustore/verify") {
        bearer(accessToken)
        contentType(ContentType.Application.Json)
        setBody(RustoreVerificationRequest(productId, purchaseId, sandbox))
    }.decode()

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
            setBody(PushDeviceRequest(fcmToken, platform = "android"))
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

    private suspend fun HttpResponse.decodeExchange(): ExchangeResult {
        if (!status.isSuccess()) throw asException()
        val payload = body<AuthResponse>()
        return when (payload.status) {
            "authenticated" -> payload.requireAuthenticated()
            "profile_required" -> ExchangeResult.ProfileRequired(
                provider = AuthProvider.entries.firstOrNull { it.wireName == payload.provider }
                    ?: invalidAuthResponse("invalid_provider_response"),
                pendingSignupToken = requireNotNull(payload.pendingSignupToken),
                expiresIn = payload.expiresIn,
            )
            else -> invalidAuthResponse("invalid_auth_response")
        }
    }

    private suspend fun HttpResponse.decodeEmailRegistration(): ExchangeResult.ProfileRequired {
        val result = decodeExchange()
        if (result !is ExchangeResult.ProfileRequired || result.provider != AuthProvider.EMAIL) {
            invalidAuthResponse("invalid_email_registration_response")
        }
        return result
    }

    private suspend fun HttpResponse.decodeSignupCompletion(): ExchangeResult.Authenticated {
        if (!status.isSuccess()) throw asException()
        val payload = body<AuthResponse>()
        if (payload.status != "confirmation_required") {
            invalidAuthResponse("invalid_signup_completion_response")
        }
        return payload.requireAuthenticated()
    }

    private suspend fun HttpResponse.decodeAuthenticated(): ExchangeResult.Authenticated {
        if (!status.isSuccess()) throw asException()
        val payload = body<AuthResponse>()
        if (payload.status != "authenticated") invalidAuthResponse("invalid_auth_response")
        return payload.requireAuthenticated()
    }

    private fun AuthResponse.requireAuthenticated() = ExchangeResult.Authenticated(
        accessToken = requireNotNull(accessToken),
        refreshToken = requireNotNull(refreshToken),
        expiresIn = expiresIn,
        profile = requireNotNull(profile),
        session = requireNotNull(session),
    )

    private fun invalidAuthResponse(errorCode: String): Nothing =
        throw AccountApiException(500, errorCode)

    private suspend fun HttpResponse.asException(): AccountApiException {
        val code = runCatching {
            json.decodeFromString<ErrorEnvelope>(bodyAsText()).error.code
        }.getOrDefault("request_failed")
        return AccountApiException(status.value, code)
    }
}
