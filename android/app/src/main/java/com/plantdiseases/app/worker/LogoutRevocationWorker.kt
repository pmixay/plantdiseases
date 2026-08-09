package com.plantdiseases.app.worker

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.plantdiseases.app.auth.LogoutRetryScheduler
import com.plantdiseases.app.di.appEntryPoint
import kotlinx.coroutines.CancellationException
import java.util.concurrent.TimeUnit

/**
 * Retries only encrypted remote-logout proofs. It never reads the active
 * refresh-token vault or turns a revocation proof into an account credential.
 */
class LogoutRevocationWorker(
    context: Context,
    params: WorkerParameters,
) : CoroutineWorker(context, params) {
    private val dependencies = appEntryPoint(context)
    private val authRepository = dependencies.authRepository()
    private val retryScheduler = dependencies.logoutRetryScheduler()

    override suspend fun doWork(): Result = try {
        val startGeneration = retryScheduler.workerStarted()
        if (!authRepository.retryPendingLogoutRevocationsFromWorker()) {
            Result.retry()
        } else if (retryScheduler.workerDrained(startGeneration)) {
            Result.success()
        } else {
            // A proof was enqueued while this worker was running. KEEP may
            // have deduplicated that schedule against this work item, so keep
            // this item alive for one more pass instead of losing the wakeup.
            Result.retry()
        }
    } catch (failure: CancellationException) {
        throw failure
    } catch (_: Exception) {
        Result.retry()
    }
}

/** Persists a network-constrained retry even if the user never reopens the app. */
internal fun interface LogoutRetryWorkEnqueuer {
    fun enqueue(policy: ExistingWorkPolicy)
}

class WorkManagerLogoutRetryScheduler internal constructor(
    context: Context,
    private val workEnqueuer: LogoutRetryWorkEnqueuer = AndroidLogoutRetryWorkEnqueuer(context),
    statePreferencesName: String = STATE_PREFERENCES,
) : LogoutRetryScheduler {
    private val applicationContext = context.applicationContext
    private val state = applicationContext.getSharedPreferences(
        statePreferencesName,
        Context.MODE_PRIVATE,
    )

    @Synchronized
    override fun schedule() {
        val finishing = state.getBoolean(KEY_WORKER_FINISHING, false)
        val nextGeneration = state.getLong(KEY_GENERATION, 0L).let { current ->
            if (current == Long.MAX_VALUE) 0L else current + 1L
        }
        check(
            state.edit()
                .putLong(KEY_GENERATION, nextGeneration)
                .putBoolean(KEY_WORKER_FINISHING, false)
                .commit(),
        ) { "Unable to persist logout-retry handoff" }
        workEnqueuer.enqueue(
            if (finishing) ExistingWorkPolicy.APPEND_OR_REPLACE else ExistingWorkPolicy.KEEP,
        )
    }

    @Synchronized
    override fun workerStarted(): Long {
        check(state.edit().putBoolean(KEY_WORKER_FINISHING, false).commit()) {
            "Unable to start logout-retry handoff"
        }
        return state.getLong(KEY_GENERATION, 0L)
    }

    @Synchronized
    override fun workerDrained(startGeneration: Long): Boolean {
        if (state.getLong(KEY_GENERATION, 0L) != startGeneration) return false
        return state.edit().putBoolean(KEY_WORKER_FINISHING, true).commit()
    }

    private companion object {
        const val STATE_PREFERENCES = "account_logout_work_v1"
        const val KEY_GENERATION = "generation"
        const val KEY_WORKER_FINISHING = "worker_finishing"
    }
}

private class AndroidLogoutRetryWorkEnqueuer(context: Context) : LogoutRetryWorkEnqueuer {
    private val applicationContext = context.applicationContext

    override fun enqueue(policy: ExistingWorkPolicy) {
        val request = OneTimeWorkRequestBuilder<LogoutRevocationWorker>()
            .setConstraints(
                Constraints.Builder()
                    .setRequiredNetworkType(NetworkType.CONNECTED)
                    .build(),
            )
            .setBackoffCriteria(
                BackoffPolicy.EXPONENTIAL,
                INITIAL_BACKOFF_SECONDS,
                TimeUnit.SECONDS,
            )
            .build()
        WorkManager.getInstance(applicationContext).enqueueUniqueWork(
            UNIQUE_WORK_NAME,
            policy,
            request,
        )
    }

    private companion object {
        const val UNIQUE_WORK_NAME = "account_logout_revocation"
        const val INITIAL_BACKOFF_SECONDS = 30L
    }
}
