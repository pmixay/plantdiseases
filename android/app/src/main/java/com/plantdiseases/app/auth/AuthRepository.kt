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
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import java.util.UUID

fun interface ProviderSessionCleaner {
    suspend fun clear()
}

fun interface LogoutRetryScheduler {
    fun schedule()

    fun workerStarted(): Long = 0L

    fun workerDrained(startGeneration: Long): Boolean = true
}

/** One encrypted issuer-scoped queue paired only with its matching API origin. */
data class LogoutRevocationTarget(
    val api: AccountApi,
    val store: LogoutRevocationStore,
    /** A previous issuer's active vault, migrated to this target's queue only. */
    val previousIssuerVault: RefreshTokenVault? = null,
    /** The same previous issuer's pseudonymous installation identity. */
    val previousIssuerInstallationId: InstallationIdProvider? = null,
    /** Pending two-phase signup state from that exact previous issuer only. */
    val previousIssuerSignupStore: SignupCompletionStore? = null,
)

data class AdditionalLogoutRevocationTargets(
    val values: List<LogoutRevocationTarget>,
)

class AuthRepository(
    private val api: AccountApi,
    private val vault: RefreshTokenVault,
    private val installationId: InstallationIdProvider,
    private val providerSessionCleaner: ProviderSessionCleaner = ProviderSessionCleaner {},
    private val logoutRevocations: LogoutRevocationStore = NoOpLogoutRevocationStore,
    private val logoutRetryScheduler: LogoutRetryScheduler = LogoutRetryScheduler {},
    additionalLogoutTargets: List<LogoutRevocationTarget> = emptyList(),
    private val signupCompletions: SignupCompletionStore = InMemorySignupCompletionStore(),
    private val accountUiEnabled: Boolean = true,
) {
    private val mutableState = MutableStateFlow<AuthState>(AuthState.Restoring)
    val state: StateFlow<AuthState> = mutableState.asStateFlow()

    // Every operation that can replace or clear credentials shares one mutex.
    // This prevents a slow process restore/refresh from completing after a
    // logout or a new sign-in and resurrecting stale account state.
    private val sessionMutex = Mutex()

    // Registration and opt-out must reach the server in caller order. Without
    // this, an in-flight token registration can commit after disablePush and
    // silently re-enable notifications the user just turned off.
    private val pushMutationMutex = Mutex()

    // Startup, recovery, and foreground retry triggers may overlap. One queue
    // consumer avoids duplicate network attempts while preserving FIFO order.
    private val logoutRetryMutex = Mutex()

    private val primaryLogoutTarget = LogoutRevocationTarget(api, logoutRevocations)
    private val logoutTargets = listOf(primaryLogoutTarget) + additionalLogoutTargets

    @Volatile
    private var accessToken: String? = null

    @Suppress("ThrowsCount") // cancellation and definitive auth failures must remain distinct
    suspend fun restore() {
        retryLogoutsBeforeRestore()
        sessionMutex.withLock {
            val previous = when (val current = mutableState.value) {
                is AuthState.Authenticated -> current
                is AuthState.Unavailable -> current.lastAuthenticated
                else -> null
            }
            mutableState.value = AuthState.Restoring
            val pendingSignup = try {
                signupCompletions.read()
            } catch (failure: CancellationException) {
                mutableState.value = AuthState.Unavailable(previous)
                throw failure
            } catch (_: Exception) {
                // The encrypted pending bearer cannot be recovered. Destroy
                // any provisional refresh credential as well; a fresh valid
                // provider proof can atomically roll back the server state.
                abandonSignupLocally()
                return@withLock
            }
            if (pendingSignup != null) {
                showPendingSignup(pendingSignup)
                if (pendingSignup.nickname == null) return@withLock
                try {
                    finishSignup(pendingSignup)
                    return@withLock
                } catch (failure: CancellationException) {
                    showPendingSignup(pendingSignup)
                    throw failure
                } catch (failure: AccountApiException) {
                    when (failure.errorCode) {
                        in EDITABLE_NICKNAME_REJECTIONS -> {
                            try {
                                reopenNicknameForEditing(pendingSignup, failure)
                            } catch (_: Exception) {
                                mutableState.value = AuthState.Unavailable(previous)
                            }
                            return@withLock
                        }
                        in AMBIGUOUS_EXPIRED_SIGNUP_ERRORS -> {
                            try {
                                when (reconcileExpiredSignup(pendingSignup)) {
                                    SignupReconciliation.AUTHENTICATED -> return@withLock
                                    SignupReconciliation.NO_LIVE_CREDENTIAL -> {
                                        abandonSignupLocally()
                                        return@withLock
                                    }
                                    SignupReconciliation.CONFIRMED_REAUTH_REQUIRED -> {
                                        discardUnrecoverableConfirmedSignupLocally()
                                        mutableState.value = AuthState.Unavailable(previous)
                                        return@withLock
                                    }
                                }
                            } catch (reconciliationCancellation: CancellationException) {
                                showPendingSignup(pendingSignup)
                                throw reconciliationCancellation
                            } catch (_: Exception) {
                                showPendingSignup(pendingSignup)
                                return@withLock
                            }
                        }
                        in DEFINITIVE_ABANDONED_SIGNUP_ERRORS,
                        "signup_completion_conflict",
                        -> {
                            abandonSignupLocally()
                            return@withLock
                        }
                        else -> {
                            showPendingSignup(pendingSignup)
                            return@withLock
                        }
                    }
                } catch (_: Exception) {
                    showPendingSignup(pendingSignup)
                    return@withLock
                }
            }
            val refresh = try {
                // The rotation id is durably stored with this token before
                // any network bytes are sent. A lost response or process
                // death can therefore replay the same logical rotation.
                vault.prepareRefresh()
            } catch (failure: CancellationException) {
                mutableState.value = AuthState.Unavailable(previous)
                throw failure
            } catch (_: Exception) {
                mutableState.value = AuthState.Unavailable(previous)
                return@withLock
            }
            if (refresh == null) {
                accessToken = null
                mutableState.value = AuthState.Guest
                return@withLock
            }
            val logoutPending = try {
                logoutRevocations.isPending(refresh.refreshToken)
            } catch (failure: Exception) {
                mutableState.value = AuthState.Unavailable(previous)
                return@withLock
            }
            if (logoutPending) {
                // A previous logout could not revoke remotely and its local
                // vault clear failed. Never turn that credential back into an
                // authenticated session while its revocation is pending.
                if (!clearLocal()) mutableState.value = AuthState.Unavailable(previous)
                // The encrypted revocation proof is already durable, so a
                // shared device must not reuse this account's pseudonymous id.
                installationId.rotate()
                return@withLock
            }
            val authenticated = try {
                api.refresh(refresh.refreshToken, refresh.rotationId)
            } catch (failure: CancellationException) {
                mutableState.value = AuthState.Unavailable(previous)
                throw failure
            } catch (failure: AccountApiException) {
                if (failure.isDefinitiveRefreshFailure()) {
                    clearDefinitiveLocal()
                } else {
                    markUnavailable(previous)
                }
                return@withLock
            } catch (_: Exception) {
                markUnavailable(previous)
                return@withLock
            }
            try {
                acceptAuthenticated(authenticated, refresh.refreshToken)
            } catch (_: Exception) {
                // acceptAuthenticated already failed closed and removed the
                // old credential; startup should remain usable as Guest.
            }
        }
    }

    private suspend fun retryLogoutsBeforeRestore() {
        try {
            retryPendingLogouts()
        } catch (failure: CancellationException) {
            // restore() is also called from a lifecycle-owned retry action. If
            // that scope is cancelled before the vault is examined, do not
            // leave the process-wide repository in its startup-only state.
            withContext(NonCancellable) {
                sessionMutex.withLock {
                    if (mutableState.value is AuthState.Restoring) {
                        accessToken = null
                        mutableState.value = AuthState.Unavailable()
                    }
                }
            }
            throw failure
        }
    }

    /** Retries revocation proofs without restoring an account credential. */
    suspend fun retryPendingLogoutRevocations(): Boolean = retryPendingLogouts()

    /** WorkManager already owns retry/backoff; avoid enqueueing successors on each attempt. */
    internal suspend fun retryPendingLogoutRevocationsFromWorker(): Boolean {
        val currentSignupTerminated = if (accountUiEnabled) {
            true
        } else {
            // Only an account-disabled recovery build may let background work
            // consume the active issuer's profile flow. In normal builds the
            // same worker must never silently cancel an interactive signup.
            sessionMutex.withLock {
                terminateSignupTarget(
                    api = api,
                    store = signupCompletions,
                    refreshVault = vault,
                    scopedInstallationId = installationId,
                )
            }
        }
        val providerSessionCleared = if (!accountUiEnabled && currentSignupTerminated) {
            runCatching { providerSessionCleaner.clear() }.isSuccess
        } else {
            currentSignupTerminated
        }
        val queuesDrained = retryPendingLogouts(scheduleOnFailure = false)
        return currentSignupTerminated && providerSessionCleared && queuesDrained
    }

    /**
     * Account-disabled recovery is a real logout, not merely hidden UI. It
     * destroys/queues the active endpoint-scoped credential and drains every
     * approved issuer queue before exposing ordinary Guest semantics.
     */
    suspend fun enterAccountDisabledMode() {
        var firstFailure: Exception? = null
        withContext(NonCancellable) {
            val signupTerminated = sessionMutex.withLock {
                terminateSignupTarget(
                    api = api,
                    store = signupCompletions,
                    refreshVault = vault,
                    scopedInstallationId = installationId,
                )
            }
            if (!signupTerminated) {
                val notDurable = LogoutNotDurableException()
                firstFailure = notDurable
                try {
                    scheduleLogoutRetry()
                } catch (failure: Exception) {
                    notDurable.addSuppressed(failure)
                }
            }
            if (signupTerminated) {
                try {
                    logout()
                } catch (failure: Exception) {
                    firstFailure?.addSuppressed(failure) ?: run { firstFailure = failure }
                }
            }
            try {
                retryPendingLogouts()
            } catch (failure: Exception) {
                firstFailure?.addSuppressed(failure) ?: run { firstFailure = failure }
            } finally {
                // Even cancellation of an application-owned retry must not strand
                // public/community UI in the startup-only Restoring state.
                sessionMutex.withLock {
                    accessToken = null
                    mutableState.value = AuthState.Guest
                }
            }
        }
        firstFailure?.let { throw it }
    }

    suspend fun signIn(provider: AuthProvider, providerAccessToken: String) =
        beginAuthentication { currentInstallationId ->
            api.exchange(
                provider,
                providerAccessToken,
                currentInstallationId,
            )
        }

    suspend fun signInWithEmail(email: String, password: String) =
        beginAuthentication { currentInstallationId ->
            api.loginEmail(email, password, currentInstallationId)
        }

    suspend fun registerWithEmail(email: String, password: String) =
        beginAuthentication { currentInstallationId ->
            api.registerEmail(email, password, currentInstallationId)
        }

    /**
     * OAuth and private-password entry points deliberately converge before
     * touching any local credential. This keeps account switching, remote
     * revocation and crash-safe two-phase profile completion identical for
     * every sign-in method without ever persisting an email password.
     */
    @Suppress("ThrowsCount") // fail closed at each persistence/network boundary
    private suspend fun beginAuthentication(
        request: suspend (installationId: String) -> ExchangeResult,
    ) {
        pushMutationMutex.withLock {
            sessionMutex.withLock {
                // Treat an existing refresh credential as an account switch, even
                // if UI state was lost. Move its proof out of the active vault and
                // attempt the same idempotent remote revocation as explicit logout
                // before accepting credentials for another account.
                val displaced = prepareLocalLogout()
                val displacedRevocation = completePreparedRevocation(displaced)
                rotateAfterCredentialTermination(displaced, displacedRevocation)
                if (!logoutIsDurable(displaced, displacedRevocation)) {
                    throw LogoutNotDurableException()
                }
                displacedRevocation.cancellation?.let { throw it }
                currentCoroutineContext().ensureActive()
                if (signupCompletions.read() != null) {
                    throw SignupCompletionInProgressException()
                }
                val currentInstallationId = installationId.get()
                when (val result = request(currentInstallationId)) {
                    is ExchangeResult.Authenticated -> acceptAuthenticated(result)
                    is ExchangeResult.ProfileRequired -> {
                        val operation = PendingSignupOperation(
                            provider = result.provider,
                            pendingSignupToken = result.pendingSignupToken,
                            completionId = UUID.randomUUID().toString(),
                            installationId = currentInstallationId,
                            expiresInSeconds = result.expiresIn,
                        )
                        try {
                            signupCompletions.write(operation)
                        } catch (failure: Exception) {
                            withContext(NonCancellable) {
                                runCatching {
                                    api.cancelSignup(
                                        operation.pendingSignupToken,
                                        operation.completionId,
                                        operation.installationId,
                                    )
                                }
                            }
                            throw failure
                        }
                        mutableState.value = AuthState.ProfileRequired(
                            provider = result.provider,
                            pendingSignupToken = result.pendingSignupToken,
                            expiresInSeconds = result.expiresIn,
                        )
                    }
                }
            }
        }
    }

    @Suppress("ThrowsCount") // preserve cancellation and exact-retry failures separately
    suspend fun completeProfile(nickname: String) {
        sessionMutex.withLock {
            val pending = mutableState.value as? AuthState.ProfileRequired
                ?: throw AuthenticationRequiredException()
            val stored = signupCompletions.read()
                ?: throw AuthenticationUnavailableException()
            if (stored.pendingSignupToken != pending.pendingSignupToken) {
                throw SignupCompletionConflictException()
            }
            val prepared = if (stored.nickname == null) {
                stored.copy(nickname = nickname).also(signupCompletions::write)
            } else {
                // Once any bytes may have reached the server, the nickname is
                // part of the immutable HMAC-bound operation. UI text is only
                // a retry trigger; always reuse the durable exact value.
                stored
            }
            try {
                finishSignup(prepared)
            } catch (failure: CancellationException) {
                showPendingSignup(prepared)
                throw failure
            } catch (failure: AccountApiException) {
                if (failure.errorCode in EDITABLE_NICKNAME_REJECTIONS) {
                    reopenNicknameForEditing(prepared, failure)
                } else if (failure.errorCode in AMBIGUOUS_EXPIRED_SIGNUP_ERRORS) {
                    try {
                        when (reconcileExpiredSignup(prepared)) {
                            SignupReconciliation.AUTHENTICATED ->
                                throw SignupAlreadyConfirmedException()
                            SignupReconciliation.NO_LIVE_CREDENTIAL ->
                                abandonSignupLocally()
                            SignupReconciliation.CONFIRMED_REAUTH_REQUIRED -> {
                                discardUnrecoverableConfirmedSignupLocally()
                                throw SignupAlreadyConfirmedException()
                            }
                        }
                    } catch (reconciliationFailure: SignupAlreadyConfirmedException) {
                        throw reconciliationFailure
                    } catch (reconciliationFailure: CancellationException) {
                        showPendingSignup(prepared)
                        throw reconciliationFailure
                    } catch (reconciliationFailure: Exception) {
                        showPendingSignup(prepared)
                        throw reconciliationFailure
                    }
                } else if (failure.isDefinitiveSignupFailure()) {
                    abandonSignupLocally()
                } else {
                    showPendingSignup(prepared)
                }
                throw failure
            } catch (failure: Exception) {
                showPendingSignup(prepared)
                throw failure
            }
        }
    }

    @Suppress("ThrowsCount") // confirmed recovery is deliberately not cancellation success
    suspend fun cancelProfile() {
        sessionMutex.withLock {
            val pending = mutableState.value as? AuthState.ProfileRequired
                ?: throw AuthenticationRequiredException()
            val operation = signupCompletions.read()
                ?: throw AuthenticationUnavailableException()
            if (operation.pendingSignupToken != pending.pendingSignupToken) {
                throw SignupCompletionConflictException()
            }
            try {
                api.cancelSignup(
                    operation.pendingSignupToken,
                    operation.completionId,
                    operation.installationId,
                )
            } catch (failure: AccountApiException) {
                if (failure.errorCode in DEFINITIVE_ABANDONED_SIGNUP_ERRORS) {
                    abandonSignupLocally()
                    return@withLock
                }
                if (failure.errorCode in AMBIGUOUS_EXPIRED_SIGNUP_ERRORS) {
                    try {
                        when (reconcileExpiredSignup(operation)) {
                            SignupReconciliation.AUTHENTICATED ->
                                throw SignupAlreadyConfirmedException()
                            SignupReconciliation.NO_LIVE_CREDENTIAL -> {
                                abandonSignupLocally()
                                return@withLock
                            }
                            SignupReconciliation.CONFIRMED_REAUTH_REQUIRED -> {
                                discardUnrecoverableConfirmedSignupLocally()
                                throw SignupAlreadyConfirmedException()
                            }
                        }
                    } catch (reconciliationFailure: SignupAlreadyConfirmedException) {
                        throw reconciliationFailure
                    } catch (reconciliationFailure: CancellationException) {
                        showPendingSignup(operation)
                        throw reconciliationFailure
                    } catch (reconciliationFailure: Exception) {
                        showPendingSignup(operation)
                        throw reconciliationFailure
                    }
                }
                if (failure.errorCode == "signup_completion_conflict") {
                    abandonSignupLocally()
                    throw failure
                }
                if (failure.errorCode != "signup_already_confirmed") {
                    throw failure
                }
                // Confirmation committed but its response was lost. Recover
                // the same session and visibly become Authenticated; never
                // report this as a successful profile cancellation.
                when (reconcileExpiredSignup(operation)) {
                    SignupReconciliation.AUTHENTICATED -> Unit
                    SignupReconciliation.NO_LIVE_CREDENTIAL -> {
                        if (operation.nickname == null) throw failure
                        finishSignup(operation)
                    }
                    SignupReconciliation.CONFIRMED_REAUTH_REQUIRED -> {
                        discardUnrecoverableConfirmedSignupLocally()
                    }
                }
                throw SignupAlreadyConfirmedException()
            }
            val installationRotated = runCatching { installationId.rotate() }.isSuccess
            val refreshDestroyed = clearLocal()
            val pendingDestroyed = if (installationRotated && refreshDestroyed) {
                runCatching { signupCompletions.clear() }.isSuccess
            } else {
                false
            }
            if (!installationRotated || !refreshDestroyed || !pendingDestroyed) {
                mutableState.value = AuthState.Unavailable()
                throw LogoutNotDurableException()
            }
            providerSessionCleaner.clear()
        }
    }

    /**
     * Makes local logout visible before any provider/network suspension. The
     * refresh credential is moved to a separate encrypted revocation queue and
     * can never be read by the normal refresh path.
     */
    suspend fun logout() {
        pushMutationMutex.withLock {
            val local = sessionMutex.withLock {
                prepareLocalLogout()
            }
            // Once active credentials have been removed, caller cancellation must
            // not skip the only remote attempt when secure queue persistence also
            // failed. Finish the short, bounded durability sequence first, then
            // faithfully rethrow cancellation.
            val completion = withContext(NonCancellable) {
                var providerCancellation: CancellationException? = null
                try {
                    providerSessionCleaner.clear()
                } catch (failure: CancellationException) {
                    providerCancellation = failure
                } catch (_: Exception) {
                    // Local and server account sessions are independent of the
                    // provider SDK cache; failure here must not resurrect login state.
                }
                LogoutCompletion(
                    revocation = completePreparedRevocation(local).also { revocation ->
                        rotateAfterCredentialTermination(local, revocation)
                    },
                    cancellation = providerCancellation,
                )
            }
            if (!logoutIsDurable(local, completion.revocation)) {
                throw LogoutNotDurableException()
            }
            (completion.cancellation ?: completion.revocation.cancellation)?.let { throw it }
            currentCoroutineContext().ensureActive()
        }
    }

    suspend fun sessions(): Result<List<AccountSession>> = authenticatedCall(api::sessions)

    suspend fun revokeSession(sessionId: String): Result<Unit> = authenticatedCall { token ->
        api.revokeSession(token, sessionId)
    }

    suspend fun me(): Result<AccountProfile> = authenticatedCall(api::me)

    suspend fun registerPush(
        fcmToken: String,
        expectedSessionId: String? = null,
        registrationAllowed: () -> Boolean = { true },
    ): Result<Unit> = pushMutationMutex.withLock {
        // Re-check after waiting for any in-flight push mutation. A user can
        // opt out in the tiny gap between an async FCM callback's UI check and
        // this mutex; a stale callback must not register after disablePush.
        if (!registrationAllowed()) return@withLock Result.success(Unit)
        authenticatedCall(expectedSessionId) { token ->
            api.registerPush(token, fcmToken)
        }
    }

    suspend fun disablePush(): Result<Unit> = pushMutationMutex.withLock {
        authenticatedCall(api::disablePush)
    }

    suspend fun reauthenticate(
        provider: AuthProvider,
        providerAccessToken: String,
        scope: String,
    ): Result<ReauthResponse> = authenticatedCall { token ->
        api.reauthenticate(token, provider, providerAccessToken, scope)
    }

    suspend fun reauthenticateWithEmail(
        password: String,
        scope: String,
    ): Result<ReauthResponse> = authenticatedCall { token ->
        api.reauthenticateEmail(token, password, scope)
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
            ?: return unavailableOrAuthenticationFailure()
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

    suspend fun deleteAccount(reauthToken: String): Result<Unit> = pushMutationMutex.withLock {
        val expectedSessionId = currentSessionId()
            ?: return@withLock unavailableOrAuthenticationFailure()
        val result = authenticatedCall(expectedSessionId) { token ->
            api.deleteAccount(token, reauthToken)
        }.map { Unit }
        if (result.isSuccess) {
            val cleared = sessionMutex.withLock {
                val current = mutableState.value as? AuthState.Authenticated
                if (current?.session?.sessionId == expectedSessionId) {
                    clearLocal()
                    // A successful deletion response definitively invalidates
                    // the old account even if secure local deletion later fails.
                    installationId.rotate()
                    true
                } else {
                    false
                }
            }
            if (cleared) providerSessionCleaner.clear()
        }
        result
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
        } ?: return unavailableOrAuthenticationFailure()
        val observedSessionId = snapshot.sessionId
        val firstToken = snapshot.accessToken
        return try {
            Result.success(block(firstToken))
        } catch (first: AccountApiException) {
            if (first.statusCode != 401) return Result.failure(first)
            val nextToken = try {
                refreshAfterUnauthorized(firstToken, observedSessionId)
            } catch (failure: CancellationException) {
                rethrowCancellation(failure)
            } catch (failure: Exception) {
                return Result.failure(failure)
            }
            try {
                Result.success(block(nextToken))
            } catch (second: CancellationException) {
                rethrowCancellation(second)
            } catch (second: AccountApiException) {
                if (second.statusCode == 401) {
                    sessionMutex.withLock {
                        val current = mutableState.value as? AuthState.Authenticated
                        if (
                            current?.session?.sessionId == observedSessionId &&
                            accessToken == nextToken
                        ) {
                            clearDefinitiveLocal()
                        }
                    }
                }
                Result.failure(second)
            } catch (second: Exception) {
                Result.failure(second)
            }
        } catch (failure: CancellationException) {
            rethrowCancellation(failure)
        } catch (failure: Exception) {
            Result.failure(failure)
        }
    }

    private suspend fun currentSessionId(): String? = sessionMutex.withLock {
        (mutableState.value as? AuthState.Authenticated)?.session?.sessionId
    }

    private suspend fun refreshAfterUnauthorized(
        observedToken: String,
        observedSessionId: String,
    ): String = sessionMutex.withLock {
        val current = mutableState.value as? AuthState.Authenticated
            ?: throw AuthenticationUnavailableException()
        // A new sign-in must never inherit and automatically replay an action
        // that started under the previous account.
        if (current.session.sessionId != observedSessionId) {
            throw AuthenticationRequiredException()
        }
        accessToken?.takeIf { it != observedToken }?.let { return@withLock it }
        val refresh = try {
            vault.prepareRefresh()
        } catch (failure: CancellationException) {
            throw failure
        } catch (failure: Exception) {
            markUnavailable(current)
            throw failure
        } ?: run {
            clearDefinitiveLocal()
            throw AuthenticationRequiredException()
        }
        val authenticated = try {
            api.refresh(refresh.refreshToken, refresh.rotationId)
        } catch (failure: CancellationException) {
            throw failure
        } catch (failure: AccountApiException) {
            if (failure.isDefinitiveRefreshFailure()) {
                clearDefinitiveLocal()
                throw AuthenticationRequiredException()
            }
            markUnavailable(current)
            throw failure
        } catch (failure: Exception) {
            markUnavailable(current)
            throw failure
        }
        acceptAuthenticated(authenticated, refresh.refreshToken)
        authenticated.accessToken
    }

    private suspend fun retryPendingLogouts(
        scheduleOnFailure: Boolean = true,
    ): Boolean = logoutRetryMutex.withLock {
        var allDrained = true
        logoutTargets.forEach { target ->
            val previousSignupStore = target.previousIssuerSignupStore
            if (previousSignupStore != null) {
                val previousVault = target.previousIssuerVault
                val previousInstallationId = target.previousIssuerInstallationId
                if (previousVault == null || previousInstallationId == null) {
                    allDrained = false
                } else {
                    val previousSignupTerminated = terminateSignupTarget(
                        api = target.api,
                        store = previousSignupStore,
                        refreshVault = previousVault,
                        scopedInstallationId = previousInstallationId,
                    )
                    if (!previousSignupTerminated) allDrained = false
                }
            }
            val previousIssuer = preparePreviousIssuerRevocation(target)
            if (!previousIssuer.complete) {
                allDrained = false
            }
            if (!previousIssuer.allowQueueDrain) {
                return@forEach
            }
            val attempted = mutableSetOf<String>()
            while (true) {
                val next = runCatching { target.store.next() }
                if (next.isFailure) {
                    allDrained = false
                    break
                }
                val pending = next.getOrNull() ?: break
                // A broken persistence implementation must not trap startup in
                // an endless loop after a nominal server success.
                if (!attempted.add(pending.refreshToken)) {
                    allDrained = false
                    break
                }
                if (!attemptLogoutRevocation(target, pending.refreshToken)) {
                    allDrained = false
                    break
                }
            }
        }
        if (!allDrained && scheduleOnFailure) scheduleLogoutRetry()
        allDrained
    }

    /**
     * Cancel an unconfirmed operation or revoke its recovered confirmed
     * session. Every credential is sent only to the API paired with its
     * issuer-scoped store/vault by dependency injection.
     */
    @Suppress("ThrowsCount") // preserve cancellation at each issuer-scoped network boundary
    private suspend fun terminateSignupTarget(
        api: AccountApi,
        store: SignupCompletionStore,
        refreshVault: RefreshTokenVault,
        scopedInstallationId: InstallationIdProvider,
    ): Boolean {
        val operation = try {
            store.read()
        } catch (_: Exception) {
            return false
        } ?: return true
        val remoteTerminated = if (operation.remoteTerminationConfirmed) {
            true
        } else {
            try {
                api.cancelSignup(
                    operation.pendingSignupToken,
                    operation.completionId,
                    operation.installationId,
                )
                true
            } catch (failure: CancellationException) {
                throw failure
            } catch (failure: AccountApiException) {
                when (failure.errorCode) {
                    "signup_already_confirmed" -> {
                        val durableRefresh = try {
                            refreshVault.read()
                        } catch (_: Exception) {
                            return false
                        }
                        if (durableRefresh != null) {
                            try {
                                api.logout(durableRefresh)
                                true
                            } catch (failure: CancellationException) {
                                throw failure
                            } catch (_: Exception) {
                                false
                            }
                        } else {
                            val nickname = operation.nickname ?: return false
                            val recovered = try {
                                api.completeSignup(
                                    operation.pendingSignupToken,
                                    operation.completionId,
                                    nickname,
                                    operation.installationId,
                                )
                            } catch (failure: CancellationException) {
                                throw failure
                            } catch (_: Exception) {
                                return false
                            }
                            try {
                                api.logout(recovered.refreshToken)
                                true
                            } catch (failure: CancellationException) {
                                throw failure
                            } catch (_: Exception) {
                                false
                            }
                        }
                    }
                    in DEFINITIVE_ABANDONED_SIGNUP_ERRORS ->
                        revokeStoredSignupRefresh(
                            api,
                            refreshVault,
                            missingIsSuccess = true,
                        )
                    in AMBIGUOUS_EXPIRED_SIGNUP_ERRORS,
                    "signup_completion_conflict",
                    -> revokeStoredSignupRefresh(
                        api,
                        refreshVault,
                        missingIsSuccess = !operation.confirmationAttempted,
                    )
                    else -> false
                }
            } catch (_: Exception) {
                false
            }
        }
        if (!remoteTerminated) return false
        if (
            !operation.remoteTerminationConfirmed &&
            runCatching {
                store.write(operation.copy(remoteTerminationConfirmed = true))
            }.isFailure
        ) {
            return false
        }
        // Keep the encrypted operation marker until last. If rotation or
        // vault erasure fails, its exact proof remains available for another
        // idempotent teardown attempt after process death.
        if (runCatching { scopedInstallationId.rotate() }.isFailure) return false
        if (runCatching { refreshVault.clear() }.isFailure) return false
        return runCatching { store.clear() }.isSuccess
    }

    private suspend fun revokeStoredSignupRefresh(
        api: AccountApi,
        refreshVault: RefreshTokenVault,
        missingIsSuccess: Boolean,
    ): Boolean {
        val refresh = try {
            refreshVault.read()
        } catch (_: Exception) {
            return false
        } ?: return missingIsSuccess
        return try {
            api.logout(refresh)
            true
        } catch (failure: CancellationException) {
            throw failure
        } catch (_: Exception) {
            false
        }
    }

    /**
     * One-way update migration: a production build may destroy a dormant
     * staging credential, but can never restore it or send it to production.
     * The matching encrypted revocation proof is committed and read back
     * before cryptographic vault erasure, so a crash cannot lose both copies.
     */
    private fun preparePreviousIssuerRevocation(
        target: LogoutRevocationTarget,
    ): PreviousIssuerPreparation {
        val previousVault = target.previousIssuerVault ?: return PreviousIssuerPreparation.READY
        val refreshToken = try {
            previousVault.read()
        } catch (_: Exception) {
            // The vault implementation already attempts cryptographic erasure
            // when bytes are unreadable. Existing durable proofs for the same
            // issuer can still drain safely while the vault retry is scheduled.
            runCatching { target.previousIssuerInstallationId?.rotate() }
            return PreviousIssuerPreparation(allowQueueDrain = true, complete = false)
        } ?: return PreviousIssuerPreparation.READY
        val queued = runCatching {
            target.store.enqueue(refreshToken)
            target.store.isPending(refreshToken)
        }.getOrDefault(false)
        if (!queued) return PreviousIssuerPreparation.BLOCKED
        if (runCatching { target.previousIssuerInstallationId?.rotate() }.isFailure) {
            return PreviousIssuerPreparation.BLOCKED
        }
        return if (runCatching { previousVault.clear() }.isSuccess) {
            PreviousIssuerPreparation.READY
        } else {
            PreviousIssuerPreparation.BLOCKED
        }
    }

    private suspend fun attemptLogoutRevocation(
        target: LogoutRevocationTarget,
        refreshToken: String,
    ): Boolean {
        val succeeded = try {
            withTimeoutOrNull(LOGOUT_REVOCATION_TIMEOUT_MILLIS) {
                target.api.logout(refreshToken)
                true
            } ?: false
        } catch (failure: CancellationException) {
            throw failure
        } catch (_: Exception) {
            false
        }
        val queueUpdated = runCatching {
            if (succeeded) {
                target.store.complete(refreshToken)
            } else {
                target.store.recordFailure(refreshToken)
            }
        }.isSuccess
        // A 204 is not enough to advance the queue if removing its proof did
        // not persist: doing so would spin forever here and could allow an
        // identical surviving vault credential through the restore path.
        return succeeded && queueUpdated
    }

    /** Must be called while [sessionMutex] is held. */
    private fun prepareLocalLogout(): LogoutPreparation {
        val activeAccessToken = accessToken
        val activeSessionId = (mutableState.value as? AuthState.Authenticated)?.session?.sessionId
        val storedResult = runCatching { vault.read() }
        val stored = storedResult.getOrNull()
        val hadStoredCredential =
            stored != null || storedResult.exceptionOrNull() is CredentialUnreadableException
        val hadCredential =
            hadStoredCredential || accessToken != null || mutableState.value is AuthState.Authenticated
        val queued = stored != null && enqueueLogoutProof(stored)
        return LogoutPreparation(
            refreshToken = stored,
            credentialRead = storedResult.isSuccess,
            queued = queued,
            credentialDestroyed = clearLocal(),
            hadCredential = hadCredential,
            activeAccessToken = activeAccessToken,
            activeSessionId = activeSessionId,
        )
    }

    private suspend fun completePreparedRevocation(
        local: LogoutPreparation,
    ): RevocationCompletion {
        var revocationCancellation: CancellationException? = null
        val revoked = try {
            when {
                local.refreshToken != null ->
                    attemptLogoutRevocation(primaryLogoutTarget, local.refreshToken)
                !local.credentialRead &&
                    local.activeAccessToken != null &&
                    local.activeSessionId != null ->
                    attemptAccessSessionRevocation(
                        local.activeAccessToken,
                        local.activeSessionId,
                    )
                else -> local.credentialRead
            }
        } catch (failure: CancellationException) {
            local.refreshToken?.let { runCatching { logoutRevocations.recordFailure(it) } }
            revocationCancellation = failure
            false
        }
        val queued = if (!revoked && !local.queued && local.refreshToken != null) {
            // A first secure-storage write can fail transiently. Try once more
            // after the bounded network attempt before reporting that logout
            // could not be made durable.
            enqueueLogoutProof(local.refreshToken)
        } else {
            local.queued
        }
        return RevocationCompletion(revoked, queued, revocationCancellation)
    }

    private suspend fun attemptAccessSessionRevocation(
        activeAccessToken: String,
        activeSessionId: String,
    ): Boolean = try {
        withTimeoutOrNull(LOGOUT_REVOCATION_TIMEOUT_MILLIS) {
            api.revokeSession(activeAccessToken, activeSessionId)
            true
        } ?: false
    } catch (failure: CancellationException) {
        throw failure
    } catch (_: Exception) {
        false
    }

    private fun enqueueLogoutProof(refreshToken: String): Boolean {
        val queued = runCatching {
            logoutRevocations.enqueue(refreshToken)
            // A no-op or failed persistence implementation must not count as a
            // durable fallback merely because its enqueue method returned.
            logoutRevocations.isPending(refreshToken)
        }.getOrDefault(false)
        if (queued) scheduleLogoutRetry()
        return queued
    }

    private fun scheduleLogoutRetry() {
        // WorkManager scheduling is an additional eventual-delivery path. A
        // transient scheduler failure must not invalidate the encrypted proof;
        // startup and foreground drains remain available as a fallback.
        runCatching { logoutRetryScheduler.schedule() }
    }

    private fun logoutIsDurable(
        local: LogoutPreparation,
        completion: RevocationCompletion,
    ): Boolean {
        if (!local.credentialRead) return completion.revoked
        if (local.refreshToken == null) return true
        return completion.revoked || (completion.queued && local.credentialDestroyed)
    }

    private fun rotateAfterCredentialTermination(
        local: LogoutPreparation,
        completion: RevocationCompletion,
    ) {
        val credentialTerminated =
            local.credentialDestroyed || completion.queued || completion.revoked
        if (local.hadCredential && credentialTerminated) {
            installationId.rotate()
        }
    }

    /** Must be called while [sessionMutex] is held. */
    private suspend fun finishSignup(operation: PendingSignupOperation) {
        val nickname = operation.nickname ?: throw SignupCompletionInProgressException()
        val auth = api.completeSignup(
            operation.pendingSignupToken,
            operation.completionId,
            nickname,
            operation.installationId,
        )
        // Confirmation is deliberately last: until the refresh credential is
        // durable, the server account remains unusable and safely cancellable.
        vault.write(auth.refreshToken)
        val confirming = if (operation.confirmationAttempted) {
            operation
        } else {
            operation.copy(confirmationAttempted = true).also(signupCompletions::write)
        }
        api.confirmSignup(
            confirming.pendingSignupToken,
            confirming.completionId,
            confirming.installationId,
        )
        // A failed clear leaves the operation recoverable and unexposed. A
        // restart repeats complete/confirm and receives the same token family.
        signupCompletions.clear()
        accessToken = auth.accessToken
        mutableState.value = AuthState.Authenticated(auth.profile, auth.session)
    }

    /** Must be called while [sessionMutex] is held. */
    private fun reopenNicknameForEditing(
        operation: PendingSignupOperation,
        rejection: AccountApiException,
    ) {
        if (operation.confirmationAttempted) {
            showPendingSignup(operation)
            throw rejection
        }
        // These errors are returned before any completion can commit. Keep the
        // same token/completion/installation, but durably unfreeze only the
        // nickname so a correction does not create a second logical signup.
        val editable = operation.copy(nickname = null)
        try {
            signupCompletions.write(editable)
        } catch (persistenceFailure: Exception) {
            showPendingSignup(operation)
            persistenceFailure.addSuppressed(rejection)
            throw persistenceFailure
        }
        showPendingSignup(editable)
    }

    /**
     * Resolve an expired/missing completion marker without guessing whether a
     * lost confirmation committed. The signup marker remains durable until a
     * refresh proves an active confirmed session and its rotated credential is
     * stored, or the server definitively rejects the credential.
     */
    private suspend fun reconcileExpiredSignup(
        operation: PendingSignupOperation,
    ): SignupReconciliation {
        fun noLiveCredential(): SignupReconciliation =
            if (operation.confirmationAttempted) {
                SignupReconciliation.CONFIRMED_REAUTH_REQUIRED
            } else {
                SignupReconciliation.NO_LIVE_CREDENTIAL
            }

        val refresh = vault.prepareRefresh() ?: return noLiveCredential()
        if (logoutRevocations.isPending(refresh.refreshToken)) {
            return noLiveCredential()
        }
        val authenticated = try {
            api.refresh(refresh.refreshToken, refresh.rotationId)
        } catch (failure: CancellationException) {
            throw failure
        } catch (failure: AccountApiException) {
            if (failure.isDefinitiveRefreshFailure()) {
                return noLiveCredential()
            }
            throw failure
        }
        // Store the idempotent refresh result before removing the retry marker;
        // a failure leaves both enough proof and the exact operation to retry.
        vault.write(authenticated.refreshToken)
        signupCompletions.clear()
        accessToken = authenticated.accessToken
        mutableState.value = AuthState.Authenticated(
            authenticated.profile,
            authenticated.session,
        )
        return SignupReconciliation.AUTHENTICATED
    }

    private fun showPendingSignup(operation: PendingSignupOperation) {
        accessToken = null
        mutableState.value = AuthState.ProfileRequired(
            provider = operation.provider,
            pendingSignupToken = operation.pendingSignupToken,
            expiresInSeconds = operation.expiresInSeconds,
            pendingNickname = operation.nickname,
        )
    }

    /** Must be called while [sessionMutex] is held. */
    private suspend fun abandonSignupLocally() {
        val installationRotated = runCatching { installationId.rotate() }.isSuccess
        val refreshDestroyed = clearLocal()
        val pendingDestroyed = if (installationRotated && refreshDestroyed) {
            runCatching { signupCompletions.clear() }.isSuccess
        } else {
            false
        }
        if (!installationRotated || !refreshDestroyed || !pendingDestroyed) {
            accessToken = null
            mutableState.value = AuthState.Unavailable()
            return
        }
        try {
            providerSessionCleaner.clear()
        } catch (failure: Exception) {
            mutableState.value = AuthState.Unavailable()
            throw failure
        }
    }

    /** The confirmed account remains; local proof is dead, so provider sign-in is required. */
    private suspend fun discardUnrecoverableConfirmedSignupLocally() {
        abandonSignupLocally()
    }

    private suspend fun acceptAuthenticated(
        auth: ExchangeResult.Authenticated,
        revocationProof: String = auth.refreshToken,
    ) {
        try {
            vault.write(auth.refreshToken)
        } catch (failure: Exception) {
            // The server already created/rotated this remote session. If its
            // credential cannot be made durable locally, queue any token from
            // the same family before discarding local state so the failed
            // authentication cannot leave an orphaned live session.
            val queued = enqueueLogoutProof(revocationProof)
            val revoked = if (queued) {
                false
            } else {
                attemptUnqueuedLogoutRevocation(revocationProof)
            }
            val credentialDestroyed = clearLocal()
            if (queued || revoked || credentialDestroyed) installationId.rotate()
            if (!queued && !revoked) throw LogoutNotDurableException(failure)
            throw failure
        }
        accessToken = auth.accessToken
        mutableState.value = AuthState.Authenticated(auth.profile, auth.session)
    }

    private suspend fun attemptUnqueuedLogoutRevocation(refreshToken: String): Boolean =
        withContext(NonCancellable) {
            try {
                withTimeoutOrNull(LOGOUT_REVOCATION_TIMEOUT_MILLIS) {
                    api.logout(refreshToken)
                    true
                } ?: false
            } catch (_: Exception) {
                false
            }
        }

    private fun markUnavailable(previous: AuthState.Authenticated?) {
        accessToken = null
        mutableState.value = AuthState.Unavailable(previous)
    }

    private fun clearLocal(): Boolean {
        accessToken = null
        val credentialDestroyed = runCatching { vault.clear() }.isSuccess
        mutableState.value = AuthState.Guest
        return credentialDestroyed
    }

    private fun clearDefinitiveLocal() {
        clearLocal()
        installationId.rotate()
    }

    private fun <T> unavailableOrAuthenticationFailure(): Result<T> = Result.failure(
        if (mutableState.value is AuthState.Unavailable || mutableState.value is AuthState.Restoring) {
            AuthenticationUnavailableException()
        } else {
            AuthenticationRequiredException()
        },
    )

    private fun AccountApiException.isDefinitiveRefreshFailure(): Boolean =
        statusCode == 401 ||
            (
                statusCode == 409 &&
                    errorCode in setOf("deletion_pending", "signup_confirmation_required")
                )

    private fun AccountApiException.isDefinitiveSignupFailure(): Boolean =
        errorCode in DEFINITIVE_ABANDONED_SIGNUP_ERRORS ||
            errorCode == "signup_completion_conflict"

    private data class AuthenticatedSnapshot(
        val sessionId: String,
        val accessToken: String,
    )

    private data class LogoutPreparation(
        val refreshToken: String?,
        val credentialRead: Boolean,
        val queued: Boolean,
        val credentialDestroyed: Boolean,
        val hadCredential: Boolean,
        val activeAccessToken: String?,
        val activeSessionId: String?,
    )

    private data class RevocationCompletion(
        val revoked: Boolean,
        val queued: Boolean,
        val cancellation: CancellationException?,
    )

    private data class PreviousIssuerPreparation(
        val allowQueueDrain: Boolean,
        val complete: Boolean,
    ) {
        companion object {
            val READY = PreviousIssuerPreparation(allowQueueDrain = true, complete = true)
            val BLOCKED = PreviousIssuerPreparation(allowQueueDrain = false, complete = false)
        }
    }

    private data class LogoutCompletion(
        val revocation: RevocationCompletion,
        val cancellation: CancellationException?,
    )

    private enum class SignupReconciliation {
        AUTHENTICATED,
        NO_LIVE_CREDENTIAL,
        CONFIRMED_REAUTH_REQUIRED,
    }

    private companion object {
        const val LOGOUT_REVOCATION_TIMEOUT_MILLIS = 3_000L
        val AMBIGUOUS_EXPIRED_SIGNUP_ERRORS = setOf(
            "pending_signup_expired",
            "signup_completion_expired",
        )
        val DEFINITIVE_ABANDONED_SIGNUP_ERRORS = setOf(
            "pending_signup_replayed",
            "signup_completion_cancelled",
        )
        val EDITABLE_NICKNAME_REJECTIONS = setOf(
            "nickname_invalid",
            "nickname_taken",
        )
    }
}

private fun rethrowCancellation(failure: CancellationException): Nothing = throw failure
