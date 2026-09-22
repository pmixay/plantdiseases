package com.plantdiseases.app.ui.garden.detail

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.runBlocking
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class ProgressionAlertGateTest {

    private val context: Context = ApplicationProvider.getApplicationContext()

    @Before
    fun clearGate() {
        context.getSharedPreferences(ProgressionAlertGate.PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .clear()
            .commit()
    }

    @Test
    fun sameLatestScan_recordsOnlyOnce_butNewScanRecordsAgain() = runBlocking {
        val records = AtomicInteger()

        val first = ProgressionAlertGate.recordOnce(context, plantId = 7, latestScanId = 41) {
            records.incrementAndGet()
        }
        val duplicate = ProgressionAlertGate.recordOnce(context, plantId = 7, latestScanId = 41) {
            records.incrementAndGet()
        }
        val nextScan = ProgressionAlertGate.recordOnce(context, plantId = 7, latestScanId = 42) {
            records.incrementAndGet()
        }
        val olderAfterDelete = ProgressionAlertGate.recordOnce(context, plantId = 7, latestScanId = 41) {
            records.incrementAndGet()
        }

        assertThat(first).isTrue()
        assertThat(duplicate).isFalse()
        assertThat(nextScan).isTrue()
        assertThat(olderAfterDelete).isFalse()
        assertThat(records.get()).isEqualTo(2)
    }

    @Test
    fun concurrentDuplicateResponses_claimOneRecord() = runBlocking {
        val records = AtomicInteger()

        val results = coroutineScope {
            List(4) {
                async {
                    ProgressionAlertGate.recordOnce(context, plantId = 8, latestScanId = 51) {
                        records.incrementAndGet()
                    }
                }
            }.map { it.await() }
        }

        assertThat(results.count { it }).isEqualTo(1)
        assertThat(records.get()).isEqualTo(1)
    }

    @Test
    fun failedRecord_isRetried() = runBlocking {
        val firstAttempt = runCatching {
            ProgressionAlertGate.recordOnce(context, plantId = 9, latestScanId = 61) {
                error("database unavailable")
            }
        }
        assertThat(firstAttempt.isFailure).isTrue()
        var retried = false

        val recorded = ProgressionAlertGate.recordOnce(context, plantId = 9, latestScanId = 61) {
            retried = true
        }

        assertThat(recorded).isTrue()
        assertThat(retried).isTrue()
    }
}
