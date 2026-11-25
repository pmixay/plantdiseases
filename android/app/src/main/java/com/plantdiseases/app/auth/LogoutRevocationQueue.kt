package com.plantdiseases.app.auth

import com.plantdiseases.app.data.account.AccountApi
import com.plantdiseases.app.data.account.AccountApiException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withTimeoutOrNull

/** Bound on one logout revocation request, so a dead network cannot stall logout. */
internal const val LOGOUT_REVOCATION_TIMEOUT_MILLIS = 3_000L

/** Signup errors after which the server may or may not have confirmed the account. */
internal val AMBIGUOUS_EXPIRED_SIGNUP_ERRORS = setOf(
    "pending_signup_expired",
    "signup_completion_expired",
)

/** Signup errors that prove the pending signup was abandoned. */
internal val DEFINITIVE_ABANDONED_SIGNUP_ERRORS = setOf(
    "pending_signup_replayed",
    "signup_completion_cancelled",
)

/**
 * The encrypted logout-revocation queues, one per issuer: [primary] is this
 * build's account service, [additional] are earlier issuers whose credentials
 * may only ever be revoked, never restored. Split out of [AuthRepository],
 * which owns the session; this class owns getting refresh tokens revoked.
 */
internal class LogoutRevocationQueue(
    private val primary: LogoutRevocationTarget,
    additional: List<LogoutRevocationTarget>,
    private val scheduler: LogoutRetryScheduler,
) {
    private val targets = listOf(primary) + additional

    // Startup, recovery, and foreground retry triggers may overlap. One queue
    // consumer avoids duplicate network attempts while preserving FIFO order.
    private val mutex = Mutex()

    suspend fun drain(
        scheduleOnFailure: Boolean = true,
    ): Boolean = mutex.withLock {
        var allDrained = true
        targets.forEach { target ->
            val previousSignupStore = target.previousIssuerSignupStore
            if (previousSignupStore != null) {
                val previousVault = target.previousIssuerVault
                val previousInstallationId = target.previousIssuerInstallationId
                if (previousVault == null || previousInstallationId == null) {
                    allDrained = false
                } else {
                    val previousSignupTerminated = terminateSignup(
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
                if (!attempt(target, pending.refreshToken)) {
                    allDrained = false
                    break
                }
            }
        }
        if (!allDrained && scheduleOnFailure) scheduleRetry()
        allDrained
    }

    /** Revoke [refreshToken] at this build's own issuer; true once its proof is gone. */
    suspend fun attemptPrimary(refreshToken: String): Boolean = attempt(primary, refreshToken)

    fun enqueue(refreshToken: String): Boolean {
        val queued = runCatching {
            primary.store.enqueue(refreshToken)
            // A no-op or failed persistence implementation must not count as a
            // durable fallback merely because its enqueue method returned.
            primary.store.isPending(refreshToken)
        }.getOrDefault(false)
        if (queued) scheduleRetry()
        return queued
    }

    fun scheduleRetry() {
        // WorkManager scheduling is an additional eventual-delivery path. A
        // transient scheduler failure must not invalidate the encrypted proof;
        // startup and foreground drains remain available as a fallback.
        runCatching { scheduler.schedule() }
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

    private suspend fun attempt(
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

    private data class PreviousIssuerPreparation(
        val allowQueueDrain: Boolean,
        val complete: Boolean,
    ) {
        companion object {
            val READY = PreviousIssuerPreparation(allowQueueDrain = true, complete = true)
            val BLOCKED = PreviousIssuerPreparation(allowQueueDrain = false, complete = false)
        }
    }
}

/**
 * Cancel an unconfirmed operation or revoke its recovered confirmed
 * session. Every credential is sent only to the API paired with its
 * issuer-scoped store/vault by dependency injection.
 */
@Suppress("ThrowsCount") // preserve cancellation at each issuer-scoped network boundary
internal suspend fun terminateSignup(
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
