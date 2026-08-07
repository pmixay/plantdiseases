package com.plantdiseases.app.auth

import com.plantdiseases.app.data.account.AccountApi
import com.plantdiseases.app.data.account.AccountApiException
import com.plantdiseases.app.data.account.AccountProfile
import com.plantdiseases.app.data.account.AccountSession
import com.plantdiseases.app.data.account.AuthProvider
import com.plantdiseases.app.data.account.ExchangeResult
import com.plantdiseases.app.data.account.LinkResponse
import com.plantdiseases.app.data.account.NicknameResponse
import com.plantdiseases.app.data.account.ReauthResponse
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

fun interface ProviderSessionCleaner {
    suspend fun clear()
}

class AuthRepository(
    private val api: AccountApi,
    private val vault: RefreshTokenVault,
    private val installationId: InstallationIdProvider,
    private val providerSessionCleaner: ProviderSessionCleaner = ProviderSessionCleaner {},
) {
    private val mutableState = MutableStateFlow<AuthState>(AuthState.Guest)
    val state: StateFlow<AuthState> = mutableState.asStateFlow()

    // Every operation that can replace or clear credentials shares one mutex.
    // This prevents a slow process restore/refresh from completing after a
    // logout or a new sign-in and resurrecting stale account state.
    private val sessionMutex = Mutex()

    @Volatile
    private var accessToken: String? = null

    suspend fun restore() {
        sessionMutex.withLock {
            val refresh = runCatching { vault.read() }.getOrNull()
            if (refresh == null) {
                clearLocal()
                return@withLock
            }
            runCatching { api.refresh(refresh) }
                .onSuccess(::acceptAuthenticated)
                .onFailure { clearLocal() }
        }
    }

    suspend fun signIn(provider: AuthProvider, providerAccessToken: String) {
        sessionMutex.withLock {
            clearLocal()
            when (val result = api.exchange(provider, providerAccessToken, installationId.get())) {
                is ExchangeResult.Authenticated -> acceptAuthenticated(result)
                is ExchangeResult.ProfileRequired -> {
                    mutableState.value = AuthState.ProfileRequired(
                        provider = result.provider,
                        pendingSignupToken = result.pendingSignupToken,
                        expiresInSeconds = result.expiresIn,
                    )
                }
            }
        }
    }

    suspend fun completeProfile(nickname: String) {
        sessionMutex.withLock {
            val pending = mutableState.value as? AuthState.ProfileRequired
                ?: throw AuthenticationRequiredException()
            acceptAuthenticated(
                api.completeSignup(pending.pendingSignupToken, nickname, installationId.get()),
            )
        }
    }

    suspend fun cancelProfile() {
        sessionMutex.withLock {
            val pending = mutableState.value as? AuthState.ProfileRequired
            try {
                if (pending != null) api.cancelSignup(pending.pendingSignupToken)
            } finally {
                clearLocal()
                providerSessionCleaner.clear()
            }
        }
    }

    suspend fun logout() {
        sessionMutex.withLock {
            val token = accessToken
            try {
                if (token != null) {
                    runCatching { api.disablePush(token) }
                    api.logout(token)
                }
            } finally {
                clearLocal()
                providerSessionCleaner.clear()
            }
        }
    }

    suspend fun sessions(): Result<List<AccountSession>> = authenticatedCall(api::sessions)

    suspend fun revokeSession(sessionId: String): Result<Unit> = authenticatedCall { token ->
        api.revokeSession(token, sessionId)
    }

    suspend fun me(): Result<AccountProfile> = authenticatedCall(api::me)

    suspend fun registerPush(fcmToken: String): Result<Unit> = authenticatedCall { token ->
        api.registerPush(token, fcmToken)
    }

    suspend fun disablePush(): Result<Unit> = authenticatedCall(api::disablePush)

    suspend fun reauthenticate(
        provider: AuthProvider,
        providerAccessToken: String,
        scope: String,
    ): Result<ReauthResponse> = authenticatedCall { token ->
        api.reauthenticate(token, provider, providerAccessToken, scope)
    }

    suspend fun linkProvider(
        provider: AuthProvider,
        providerAccessToken: String,
        reauthToken: String,
    ): Result<LinkResponse> = authenticatedCall { token ->
        api.linkProvider(token, provider, providerAccessToken, reauthToken)
    }

    suspend fun changeNickname(nickname: String): Result<NicknameResponse> {
        val expectedSessionId = currentSessionId()
            ?: return Result.failure(AuthenticationRequiredException())
        val result = authenticatedCall(expectedSessionId) { token ->
            api.changeNickname(token, nickname)
        }
        result.getOrNull()?.let { response ->
            sessionMutex.withLock {
                val current = mutableState.value as? AuthState.Authenticated
                if (
                    current != null &&
                    current.session.sessionId == expectedSessionId &&
                    accessToken != null
                ) {
                    accessToken = response.accessToken
                    mutableState.value = current.copy(
                        profile = current.profile.copy(nickname = response.nickname),
                    )
                }
            }
        }
        return result
    }

    suspend fun deleteAccount(reauthToken: String): Result<Unit> {
        val expectedSessionId = currentSessionId()
            ?: return Result.failure(AuthenticationRequiredException())
        val result = authenticatedCall(expectedSessionId) { token ->
            api.deleteAccount(token, reauthToken)
        }.map { Unit }
        if (result.isSuccess) {
            val cleared = sessionMutex.withLock {
                val current = mutableState.value as? AuthState.Authenticated
                if (current?.session?.sessionId == expectedSessionId) {
                    clearLocal()
                    true
                } else {
                    false
                }
            }
            if (cleared) providerSessionCleaner.clear()
        }
        return result
    }

    suspend fun <T> authenticatedCall(block: suspend (accessToken: String) -> T): Result<T> =
        authenticatedCall(expectedSessionId = null, block)

    private suspend fun <T> authenticatedCall(
        expectedSessionId: String?,
        block: suspend (accessToken: String) -> T,
    ): Result<T> {
        val snapshot = sessionMutex.withLock {
            val current = mutableState.value as? AuthState.Authenticated
                ?: return@withLock null
            val token = accessToken ?: return@withLock null
            if (expectedSessionId != null && current.session.sessionId != expectedSessionId) {
                return@withLock null
            }
            AuthenticatedSnapshot(current.session.sessionId, token)
        } ?: return Result.failure(AuthenticationRequiredException())
        val observedSessionId = snapshot.sessionId
        val firstToken = snapshot.accessToken
        return try {
            Result.success(block(firstToken))
        } catch (first: AccountApiException) {
            if (first.statusCode != 401) return Result.failure(first)
            val nextToken = refreshAfterUnauthorized(firstToken, observedSessionId)
                ?: return Result.failure(AuthenticationRequiredException())
            try {
                Result.success(block(nextToken))
            } catch (second: Exception) {
                if (second is CancellationException) throw second
                if (second is AccountApiException && second.statusCode == 401) {
                    sessionMutex.withLock {
                        val current = mutableState.value as? AuthState.Authenticated
                        if (
                            current?.session?.sessionId == observedSessionId &&
                            accessToken == nextToken
                        ) {
                            clearLocal()
                        }
                    }
                }
                Result.failure(second)
            }
        } catch (failure: Exception) {
            if (failure is CancellationException) throw failure
            Result.failure(failure)
        }
    }

    private suspend fun currentSessionId(): String? = sessionMutex.withLock {
        (mutableState.value as? AuthState.Authenticated)?.session?.sessionId
    }

    private suspend fun refreshAfterUnauthorized(
        observedToken: String,
        observedSessionId: String,
    ): String? =
        sessionMutex.withLock {
            val current = mutableState.value as? AuthState.Authenticated
                ?: return@withLock null
            // A new sign-in must never inherit and automatically replay an
            // action that started under the previous account.
            if (current.session.sessionId != observedSessionId) return@withLock null
            accessToken?.takeIf { it != observedToken }?.let { return@withLock it }
            val refresh = vault.read() ?: return@withLock clearLocal().let { null }
            try {
                api.refresh(refresh).also(::acceptAuthenticated).accessToken
            } catch (failure: Exception) {
                if (failure is CancellationException) throw failure
                clearLocal()
                null
            }
        }

    private fun acceptAuthenticated(auth: ExchangeResult.Authenticated) {
        try {
            vault.write(auth.refreshToken)
        } catch (failure: Exception) {
            clearLocal()
            throw failure
        }
        accessToken = auth.accessToken
        mutableState.value = AuthState.Authenticated(auth.profile, auth.session)
    }

    private fun clearLocal() {
        accessToken = null
        runCatching { vault.clear() }
        mutableState.value = AuthState.Guest
    }

    private data class AuthenticatedSnapshot(
        val sessionId: String,
        val accessToken: String,
    )
}
