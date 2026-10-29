package com.plantdiseases.app.worker

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import androidx.work.ExistingWorkPolicy
import com.google.common.truth.Truth.assertThat
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.util.UUID

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class LogoutRetrySchedulerTest {
    private val context: Context = ApplicationProvider.getApplicationContext()

    @Test
    fun repeatedOfflineLaunchSchedulesStayBoundedByKeepPolicy() {
        val enqueuer = CountingUniqueWorkEnqueuer()
        val scheduler = scheduler(enqueuer)

        repeat(100) { scheduler.schedule() }

        assertThat(enqueuer.unfinishedWorkCount).isEqualTo(1)
        assertThat(enqueuer.policies).containsExactly(ExistingWorkPolicy.KEEP)
    }

    @Test
    fun enqueueDuringFinalDrainEitherRetriesCurrentWorkerOrAppendsSuccessor() {
        val enqueuer = CountingUniqueWorkEnqueuer()
        val scheduler = scheduler(enqueuer)
        scheduler.schedule()
        val firstGeneration = scheduler.workerStarted()

        scheduler.schedule()

        assertThat(scheduler.workerDrained(firstGeneration)).isFalse()
        val retryGeneration = scheduler.workerStarted()
        assertThat(scheduler.workerDrained(retryGeneration)).isTrue()

        scheduler.schedule()

        assertThat(enqueuer.policies).containsExactly(
            ExistingWorkPolicy.KEEP,
            ExistingWorkPolicy.APPEND_OR_REPLACE,
        ).inOrder()
        assertThat(enqueuer.unfinishedWorkCount).isEqualTo(2)
    }

    private fun scheduler(enqueuer: LogoutRetryWorkEnqueuer) =
        WorkManagerLogoutRetryScheduler(
            context,
            enqueuer,
            "account_logout_work_test_${UUID.randomUUID()}",
        )

    private class CountingUniqueWorkEnqueuer : LogoutRetryWorkEnqueuer {
        val policies = linkedSetOf<ExistingWorkPolicy>()
        var unfinishedWorkCount = 0

        override fun enqueue(policy: ExistingWorkPolicy) {
            policies += policy
            when (policy) {
                ExistingWorkPolicy.KEEP -> if (unfinishedWorkCount == 0) unfinishedWorkCount = 1
                ExistingWorkPolicy.APPEND,
                ExistingWorkPolicy.APPEND_OR_REPLACE,
                -> unfinishedWorkCount++
                ExistingWorkPolicy.REPLACE -> unfinishedWorkCount = 1
            }
        }
    }
}
