package com.plantdiseases.app.data.account

interface AccountApi {
    suspend fun exchange(
        provider: AuthProvider,
        providerAccessToken: String,
        installationId: String,
    ): ExchangeResult

    suspend fun completeSignup(
        pendingSignupToken: String,
        nickname: String,
        installationId: String,
    ): ExchangeResult.Authenticated

    suspend fun cancelSignup(pendingSignupToken: String)

    suspend fun refresh(refreshToken: String): ExchangeResult.Authenticated

    suspend fun logout(accessToken: String)

    suspend fun sessions(accessToken: String): List<AccountSession>

    suspend fun revokeSession(accessToken: String, sessionId: String)

    suspend fun reauthenticate(
        accessToken: String,
        provider: AuthProvider,
        providerAccessToken: String,
        scope: String,
    ): ReauthResponse

    suspend fun linkProvider(
        accessToken: String,
        provider: AuthProvider,
        providerAccessToken: String,
        reauthToken: String,
    ): LinkResponse

    suspend fun me(accessToken: String): AccountProfile

    suspend fun changeNickname(accessToken: String, nickname: String): NicknameResponse

    suspend fun deleteAccount(accessToken: String, reauthToken: String): DeletionResponse

    suspend fun registerPush(accessToken: String, fcmToken: String)

    suspend fun disablePush(accessToken: String)
}

class AccountApiException(
    val statusCode: Int,
    val errorCode: String,
) : Exception("Account request failed ($statusCode/$errorCode)")
