package com.plantdiseases.app.billing

import com.plantdiseases.app.auth.AuthRepository
import com.plantdiseases.app.auth.AuthState
import com.plantdiseases.app.auth.AuthenticationRequiredException
import com.plantdiseases.app.data.account.AccountApi
import com.plantdiseases.app.data.account.EntitlementsResponse
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * Process-wide, server-authoritative entitlement state. Store callbacks only
 * become access after [AccountApi.verifyRustorePurchase] succeeds.
 */
@Singleton
class EntitlementRepository @Inject constructor(
    private val authRepository: AuthRepository,
    private val accountApi: AccountApi,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val refreshMutex = Mutex()
    private val sessionState = SessionBoundEntitlementState()
    val state: StateFlow<EntitlementState> = sessionState.state

    init {
        scope.launch {
            authRepository.state.collectLatest { auth ->
                when (auth) {
                    AuthState.Guest,
                    is AuthState.ProfileRequired,
                    -> sessionState.clear()
                    AuthState.Restoring ->
                        sessionState.markRestoring()
                    is AuthState.Unavailable -> {
                        sessionState.markUnavailable(auth.lastAuthenticated?.session?.sessionId)
                    }
                    is AuthState.Authenticated -> {
                        sessionState.bind(auth.session.sessionId)
                        refresh()
                    }
                }
            }
        }
    }

    fun allows(feature: ProFeature): Boolean = EntitlementPolicy.allows(state.value, feature)

    fun currentSnapshot(): EntitlementSnapshot? = EntitlementPolicy.snapshot(state.value)

    /**
     * Refresh after a foreground transition without queuing duplicate network
     * calls when several activities resume together. Guest/profile-required
     * states are deliberately ignored; the auth-state collector owns them.
     */
    fun refreshInBackground() {
        if (authRepository.state.value !is AuthState.Authenticated) return
        scope.launch {
            if (!refreshMutex.tryLock()) return@launch
            try {
                refreshLocked()
            } finally {
                refreshMutex.unlock()
            }
        }
    }

    suspend fun refresh(): Result<EntitlementSnapshot> = refreshMutex.withLock {
        refreshLocked()
    }

    private suspend fun refreshLocked(): Result<EntitlementSnapshot> {
        val expectedSessionId = authenticatedSessionId()
            ?: return Result.failure(AuthenticationRequiredException())
        // The auth collector and foreground refreshes run on independent IO
        // coroutines. Begin the transition through the session-bound store so
        // a logout/account switch that already cleared or rebound the state
        // cannot be overwritten by this older refresh.
        val refresh = sessionState.beginRefresh(expectedSessionId)
            ?: return Result.failure(AuthenticationRequiredException())
        val previous = refresh.previous
        return authRepository.authenticatedCall { token -> accountApi.entitlements(token) }
            .map { response -> response.toEntitlementSnapshot() }
            .onSuccess {
                if (authenticatedSessionId() == expectedSessionId) {
                    sessionState.publish(expectedSessionId, EntitlementState.Ready(it))
                }
            }
            .onFailure {
                if (authenticatedSessionId() == expectedSessionId) {
                    sessionState.publish(
                        expectedSessionId,
                        EntitlementState.Unavailable(previous),
                    )
                }
            }
    }

    suspend fun verifyRustorePurchase(
        productId: String,
        purchaseId: String,
        sandbox: Boolean,
    ): Result<EntitlementSnapshot> {
        val expectedSessionId = authenticatedSessionId()
            ?: return Result.failure(AuthenticationRequiredException())
        val previous = sessionState.snapshotFor(expectedSessionId)
            ?.takeUnless { it == EntitlementSnapshot.FREE }
        return authRepository.authenticatedCall { token ->
            accountApi.verifyRustorePurchase(token, productId, purchaseId, sandbox)
        }.map { response -> response.toEntitlementSnapshot() }
            .onSuccess {
                if (authenticatedSessionId() == expectedSessionId) {
                    sessionState.publish(expectedSessionId, EntitlementState.Ready(it))
                }
            }
            // A failed canonical verification must also close the purchase
            // surface until a fresh server entitlement request succeeds.
            .onFailure {
                if (authenticatedSessionId() == expectedSessionId) {
                    sessionState.publish(
                        expectedSessionId,
                        EntitlementState.Unavailable(previous),
                    )
                }
            }
    }

    private fun authenticatedSessionId(): String? =
        (authRepository.state.value as? AuthState.Authenticated)?.session?.sessionId
}

/**
 * Couples every cached entitlement value to the account session that produced
 * it. Auth-state collection and network completions are deliberately
 * serialized here: whichever side observes a logout/session switch first wins,
 * and an older refresh can never publish over that newer binding.
 */
internal class SessionBoundEntitlementState {
    private val lock = Any()
    private var sessionId: String? = null
    private val mutableState = MutableStateFlow<EntitlementState>(EntitlementState.Checking())
    val state: StateFlow<EntitlementState> = mutableState.asStateFlow()

    fun clear() = synchronized(lock) {
        sessionId = null
        mutableState.value = EntitlementState.Free
    }

    fun markRestoring() = synchronized(lock) {
        mutableState.value = EntitlementState.Checking(snapshotLocked())
    }

    fun markUnavailable(lastAuthenticatedSessionId: String?) = synchronized(lock) {
        if (lastAuthenticatedSessionId == null) {
            sessionId = null
            mutableState.value = EntitlementState.Free
        } else {
            val previous = snapshotLocked().takeIf { sessionId == lastAuthenticatedSessionId }
            sessionId = lastAuthenticatedSessionId
            mutableState.value = EntitlementState.Unavailable(previous)
        }
    }

    /** Returns true when this is a different account session. */
    fun bind(authenticatedSessionId: String): Boolean = synchronized(lock) {
        val changed = sessionId != authenticatedSessionId
        sessionId = authenticatedSessionId
        if (changed) mutableState.value = EntitlementState.Checking()
        changed
    }

    /**
     * Atomically verifies ownership and enters Checking. A null result means a
     * newer auth-state transition already invalidated this refresh.
     */
    fun beginRefresh(expectedSessionId: String): RefreshStart? = synchronized(lock) {
        if (sessionId != expectedSessionId) return@synchronized null
        val previous = snapshotLocked()?.takeUnless { it == EntitlementSnapshot.FREE }
        mutableState.value = EntitlementState.Checking(previous)
        RefreshStart(previous)
    }

    fun snapshotFor(expectedSessionId: String): EntitlementSnapshot? = synchronized(lock) {
        snapshotLocked().takeIf { sessionId == expectedSessionId }
    }

    fun publish(expectedSessionId: String, value: EntitlementState): Boolean = synchronized(lock) {
        if (sessionId != expectedSessionId) return@synchronized false
        mutableState.value = value
        true
    }

    private fun snapshotLocked(): EntitlementSnapshot? = EntitlementPolicy.snapshot(mutableState.value)

    data class RefreshStart(val previous: EntitlementSnapshot?)
}

/** Preserve the complete server quota snapshot, including its policy version. */
internal fun EntitlementsResponse.toEntitlementSnapshot() = EntitlementSnapshot(
    plan = plan,
    status = status,
    validUntil = validUntil,
    autoRenewing = autoRenewing,
    billingAvailable = billingAvailable,
    billingExternalId = billingExternalId,
    products = products,
    quotaPolicyVersion = quotaPolicyVersion,
    quotas = quotas.associateBy { it.feature },
)
