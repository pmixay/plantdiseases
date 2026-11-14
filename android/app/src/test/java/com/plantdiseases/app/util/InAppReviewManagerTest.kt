package com.plantdiseases.app.util

import android.app.Application
import android.content.Context
import android.os.Looper
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.util.concurrent.TimeUnit

/**
 * Robolectric coverage of [InAppReviewManager]: the long prompt throttle, and
 * — the property every call site leans on — that a device without the RuStore
 * app treats a review request as a silent no-op instead of crashing the
 * caller's flow (Robolectric has no store app installed, so the SDK fails
 * exactly the way a sideloaded production install would).
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
class InAppReviewManagerTest {

    private val context: Context get() = ApplicationProvider.getApplicationContext()

    private fun newManager() = InAppReviewManager(context)

    private val sixtyDays = TimeUnit.DAYS.toMillis(60)

    /** A plausible wall-clock "now". The throttle compares against a stamp
     *  that defaults to 0 (the epoch), so tests must use realistic epoch
     *  millis — a tiny fake value like 123L would sit inside the 60-day
     *  window measured from 1970 and read as "just prompted". */
    private val now = 1_753_000_000_000L

    // ── prompt throttle ──────────────────────────────────────────────

    @Test
    fun promptThrottle_opensAfterTheWindow_onClockRollback_andPersists() {
        val manager = newManager()
        // First ever check: nothing stamped yet.
        assertThat(manager.shouldPromptNow(now)).isTrue()

        manager.markPrompted(now)
        assertThat(manager.shouldPromptNow(now)).isFalse()
        assertThat(manager.shouldPromptNow(now + sixtyDays - 1)).isFalse()
        assertThat(manager.shouldPromptNow(now + sixtyDays)).isTrue()
        // Device clock rolled back behind the stored stamp: prompt rather than
        // stay silent until the clock catches back up.
        assertThat(manager.shouldPromptNow(now - 2 * sixtyDays)).isTrue()
        // A fresh instance (new controller / next launch) reads the same prefs.
        assertThat(newManager().shouldPromptNow(now + 1)).isFalse()
    }

    // ── graceful degradation without RuStore ─────────────────────────

    @Test
    fun requestWithoutRuStoreIsASilentNoOp() {
        val manager = newManager()
        manager.maybeRequestReview()
        shadowOf(Looper.getMainLooper()).idle()
        // No crash, and no prompt was recorded — the request failed inside the
        // SDK before ever reaching the review-flow launch.
        assertThat(manager.shouldPromptNow(now)).isTrue()
    }
}
