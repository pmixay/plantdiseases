package com.plantdiseases.app.util

import com.google.common.truth.Truth.assertThat
import org.junit.Test
import java.util.Calendar

/**
 * Verifies the pure reminder-schedule math in [CareSchedule]: next-due
 * computation off a care anchor, overdue / days-until bucketing for the
 * garden chips, and the WorkManager initial-delay calculations (first
 * schedule + the "user just watered, push the next fire out" path).
 */
class CareScheduleTest {

    private fun calendarAt(hour: Int, minute: Int): Calendar =
        Calendar.getInstance().apply {
            set(2026, Calendar.JUNE, 10, hour, minute, 0)
            set(Calendar.MILLISECOND, 0)
        }

    // ── nextDueAt ────────────────────────────────────────────────────

    @Test
    fun nextDueAtAddsWholeDays() {
        val anchor = 1_000_000L
        assertThat(CareSchedule.nextDueAt(anchor, 7))
            .isEqualTo(anchor + 7 * CareSchedule.DAY_MS)
    }

    @Test
    fun nextDueAtClampsNegativeInterval() {
        val anchor = 5_000L
        assertThat(CareSchedule.nextDueAt(anchor, -3)).isEqualTo(anchor)
    }

    // ── isOverdue / daysUntil ────────────────────────────────────────

    @Test
    fun dueWithinNext24HoursIsNotOverdue() {
        val now = 100L * CareSchedule.DAY_MS
        val dueIn12h = now + CareSchedule.DAY_MS / 2
        assertThat(CareSchedule.isOverdue(dueIn12h, now)).isFalse()
        // Truncates to 0 days — the UI renders this as "tomorrow", never
        // as overdue.
        assertThat(CareSchedule.daysUntil(dueIn12h, now)).isEqualTo(0)
    }

    @Test
    fun pastDueIsOverdue() {
        val now = 100L * CareSchedule.DAY_MS
        assertThat(CareSchedule.isOverdue(now - 1, now)).isTrue()
        assertThat(CareSchedule.isOverdue(now, now)).isTrue()
        assertThat(CareSchedule.isOverdue(now + 1, now)).isFalse()
    }

    @Test
    fun daysUntilCountsWholeDays() {
        val now = 50L * CareSchedule.DAY_MS
        assertThat(CareSchedule.daysUntil(now + 3 * CareSchedule.DAY_MS, now)).isEqualTo(3)
        assertThat(CareSchedule.daysUntil(now + 3 * CareSchedule.DAY_MS + 1, now)).isEqualTo(3)
        assertThat(CareSchedule.daysUntil(now - 2 * CareSchedule.DAY_MS, now)).isEqualTo(-2)
    }

    // ── initialDelayMs ───────────────────────────────────────────────

    @Test
    fun initialDelayTargetsLaterToday() {
        val now = calendarAt(8, 0)
        val delay = CareSchedule.initialDelayMs(9, 30, now)
        assertThat(delay).isEqualTo(90L * 60L * 1000L) // 1h30m
    }

    @Test
    fun initialDelayRollsToTomorrowWhenTimePassed() {
        val now = calendarAt(10, 0)
        val delay = CareSchedule.initialDelayMs(9, 0, now)
        assertThat(delay).isEqualTo(23L * 60L * 60L * 1000L) // 9:00 tomorrow
    }

    @Test
    fun initialDelayExactlyNowRollsToTomorrow() {
        val now = calendarAt(9, 0)
        val delay = CareSchedule.initialDelayMs(9, 0, now)
        assertThat(delay).isEqualTo(CareSchedule.DAY_MS)
    }

    // ── delayAfterCareMs ─────────────────────────────────────────────

    @Test
    fun delayAfterCareLandsIntervalDaysOutAtReminderTime() {
        // Watered at 10:00 with a 3-day 9:00 reminder → next fire is in
        // 3 days at 9:00, i.e. 3 days minus 1 hour from now.
        val now = calendarAt(10, 0)
        val delay = CareSchedule.delayAfterCareMs(9, 0, 3, now)
        assertThat(delay).isEqualTo(3 * CareSchedule.DAY_MS - 60L * 60L * 1000L)
    }

    @Test
    fun delayAfterCareIsAlwaysAtLeastTheRestOfTheInterval() {
        // Watered at 8:00 with a 1-day 9:00 reminder → tomorrow 9:00 (25 h),
        // NOT today 9:00 (which would nag an hour after watering).
        val now = calendarAt(8, 0)
        val delay = CareSchedule.delayAfterCareMs(9, 0, 1, now)
        assertThat(delay).isEqualTo(CareSchedule.DAY_MS + 60L * 60L * 1000L)
    }

    @Test
    fun delayAfterCareClampsNonPositiveInterval() {
        val now = calendarAt(12, 0)
        // interval 0 is treated as 1 day — never an immediate re-fire.
        val delay = CareSchedule.delayAfterCareMs(9, 0, 0, now)
        assertThat(delay).isGreaterThan(0L)
        assertThat(delay).isAtMost(2 * CareSchedule.DAY_MS)
    }
}
