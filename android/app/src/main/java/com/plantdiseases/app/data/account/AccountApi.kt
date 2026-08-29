package com.plantdiseases.app.data.account

interface AccountApi {
    suspend fun exchange(
        provider: AuthProvider,
        providerAccessToken: String,
        installationId: String,
    ): ExchangeResult

    suspend fun registerEmail(
        email: String,
        password: String,
        installationId: String,
    ): ExchangeResult.ProfileRequired

    suspend fun loginEmail(
        email: String,
        password: String,
        installationId: String,
    ): ExchangeResult

    suspend fun completeSignup(
        pendingSignupToken: String,
        completionId: String,
        nickname: String,
        installationId: String,
    ): ExchangeResult.Authenticated

    suspend fun confirmSignup(
        pendingSignupToken: String,
        completionId: String,
        installationId: String,
    )

    suspend fun cancelSignup(
        pendingSignupToken: String,
        completionId: String,
        installationId: String,
    )

    suspend fun refresh(
        refreshToken: String,
        rotationId: String,
    ): ExchangeResult.Authenticated

    /** Idempotently revokes the session family identified by this refresh credential. */
    suspend fun logout(refreshToken: String)

    suspend fun sessions(accessToken: String): List<AccountSession>

    suspend fun revokeSession(accessToken: String, sessionId: String)

    suspend fun reauthenticate(
        accessToken: String,
        provider: AuthProvider,
        providerAccessToken: String,
        scope: String,
    ): ReauthResponse

    suspend fun reauthenticateEmail(
        accessToken: String,
        password: String,
        scope: String,
    ): ReauthResponse

    suspend fun linkProvider(
        accessToken: String,
        provider: AuthProvider,
        providerAccessToken: String,
        reauthToken: String,
    ): LinkResponse

    suspend fun me(accessToken: String): AccountProfile

    /** Future account-server contract; entitlement truth never comes from a
     * client preference or an unverified store callback. */
    suspend fun entitlements(accessToken: String): EntitlementsResponse

    suspend fun verifyRustorePurchase(
        accessToken: String,
        productId: String,
        purchaseId: String,
        sandbox: Boolean,
    ): EntitlementsResponse

    suspend fun changeNickname(accessToken: String, nickname: String): NicknameResponse

    suspend fun deleteAccount(accessToken: String, reauthToken: String): DeletionResponse

    suspend fun registerPush(accessToken: String, fcmToken: String)

    suspend fun disablePush(accessToken: String)
}

class AccountApiException(
    val statusCode: Int,
    val errorCode: String,
) : Exception("Account request failed ($statusCode/$errorCode)")
