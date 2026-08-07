package com.plantdiseases.app.testutil

import com.plantdiseases.app.data.account.AccountApi
import com.plantdiseases.app.data.account.AccountProfile
import com.plantdiseases.app.data.account.AccountSession
import com.plantdiseases.app.data.account.AuthProvider
import com.plantdiseases.app.data.account.DeletionResponse
import com.plantdiseases.app.data.account.ExchangeResult
import com.plantdiseases.app.data.account.LinkResponse
import com.plantdiseases.app.data.account.NicknameResponse
import com.plantdiseases.app.data.account.ReauthResponse
import com.plantdiseases.app.data.account.TokenProfile

open class FakeAccountApi : AccountApi {
    var exchangeResult: ExchangeResult = authenticated()
    var refreshResult: ExchangeResult.Authenticated = authenticated()
    var refreshCalls = 0
    var logoutCalls = 0
    var registerPushCalls = 0
    var disablePushCalls = 0
    var lastPushToken: String? = null
    var cancelCalls = 0
    var logoutFailure: Exception? = null
    var onRefresh: suspend () -> Unit = {}

    override suspend fun exchange(
        provider: AuthProvider,
        providerAccessToken: String,
        installationId: String,
    ): ExchangeResult = exchangeResult

    override suspend fun completeSignup(
        pendingSignupToken: String,
        nickname: String,
        installationId: String,
    ): ExchangeResult.Authenticated = authenticated(profile = profile(nickname))

    override suspend fun cancelSignup(pendingSignupToken: String) {
        cancelCalls++
    }

    override suspend fun refresh(refreshToken: String): ExchangeResult.Authenticated {
        refreshCalls++
        onRefresh()
        return refreshResult
    }

    override suspend fun logout(accessToken: String) {
        logoutCalls++
        logoutFailure?.let { throw it }
    }

    override suspend fun sessions(accessToken: String): List<AccountSession> = listOf(session())

    override suspend fun revokeSession(accessToken: String, sessionId: String) = Unit

    override suspend fun reauthenticate(
        accessToken: String,
        provider: AuthProvider,
        providerAccessToken: String,
        scope: String,
    ): ReauthResponse = ReauthResponse("reauth", scope, 300)

    override suspend fun linkProvider(
        accessToken: String,
        provider: AuthProvider,
        providerAccessToken: String,
        reauthToken: String,
    ): LinkResponse = LinkResponse("linked", provider.wireName)

    override suspend fun me(accessToken: String): AccountProfile = error("not used")

    override suspend fun changeNickname(accessToken: String, nickname: String): NicknameResponse =
        NicknameResponse(nickname, "new-access", "Bearer", 300)

    override suspend fun deleteAccount(
        accessToken: String,
        reauthToken: String,
    ): DeletionResponse = DeletionResponse("deletion_pending")

    override suspend fun registerPush(accessToken: String, fcmToken: String) {
        registerPushCalls++
        lastPushToken = fcmToken
    }

    override suspend fun disablePush(accessToken: String) {
        disablePushCalls++
    }

    companion object {
        fun profile(nickname: String = "Beta Gardener") = TokenProfile(nickname, ACTOR_ID)

        fun session() = AccountSession(
            sessionId = SESSION_ID,
            installationId = INSTALLATION_ID,
            createdAt = "2026-01-01T00:00:00Z",
            lastUsedAt = "2026-01-01T00:00:00Z",
            idleExpiresAt = "2026-02-01T00:00:00Z",
            absoluteExpiresAt = "2026-03-01T00:00:00Z",
            current = true,
        )

        fun authenticated(
            accessToken: String = "access-1",
            refreshToken: String = "refresh-1",
            profile: TokenProfile = profile(),
            session: AccountSession = session(),
        ) = ExchangeResult.Authenticated(
            accessToken = accessToken,
            refreshToken = refreshToken,
            expiresIn = 300,
            profile = profile,
            session = session,
        )

        const val INSTALLATION_ID = "00000000-0000-4000-8000-000000000001"
        const val SESSION_ID = "00000000-0000-4000-8000-000000000002"
        const val ACTOR_ID = "00000000-0000-4000-8000-000000000003"
    }
}
