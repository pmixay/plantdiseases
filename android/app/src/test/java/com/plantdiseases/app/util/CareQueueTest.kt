package com.plantdiseases.app.util

import com.google.common.truth.Truth.assertThat
import org.junit.Test
import java.util.Calendar

/** Pure-Kotlin — no Robolectric needed; matches CareScheduleTest's style. */
class CareQueueTest {

    private fun cal(year: Int, month: Int, day: Int, hour: Int = 0, minute: Int = 0): Calendar =
        Calendar.getInstance().apply {
            set(year, month, day, hour, minute, 0)
            set(Calendar.MILLISECOND, 0)
        }

    private fun watering(
        anchorMs: Long,
        intervalDays: Int = 7,
        hour: Int = 9,
        minute: Int = 0,
        reminderId: Long = 1,
        plantId: Long = 1,
    ) = CareQueue.ReminderInput(
        reminderId = reminderId,
        plantId = plantId,
        type = "watering",
        title = "Water",
        intervalDays = intervalDays,
        hour = hour,
        minute = minute,
        enabled = true,
        anchorMs = anchorMs,
    )

    @Test
    fun project_sevenDayReminderAnchoredTenDaysAgo_emitsOverdueThenScheduledOccurrences() {
        // now is deliberately at the reminder's own hour (9:00) so the window
        // boundaries below land on exact occurrence instants instead of an
        // arbitrary time mid-way through a day.
        val now = cal(2026, Calendar.AUGUST, 15, 9, 0).timeInMillis
        val anchor = now - 10L * CareSchedule.DAY_MS
        val reminder = watering(anchor)

        // Occurrences land on anchor + 0/7/14/21 days = Aug5, Aug12, Aug19, Aug26
        // (all at 09:00). A window of [Aug4, Aug20) keeps exactly the first
        // three — Aug26 falls on/after the toMs cutoff and is excluded.
        val tasks = CareQueue.project(
            reminders = listOf(reminder),
            steps = emptyList(),
            oneOffs = emptyList(),
            doneEvents = emptyList(),
            fromMs = now - 11L * CareSchedule.DAY_MS,
            toMs = now + 5L * CareSchedule.DAY_MS,
        )

        assertThat(tasks).hasSize(3)
        val sorted = tasks.sortedBy { it.dueAt }
        assertThat(sorted[0].status).isEqualTo(CareQueue.CareTask.Status.PENDING)
        assertThat(sorted[0].dueAt).isLessThan(now)
        assertThat(sorted[2].dueAt).isGreaterThan(now)

        val cal0 = Calendar.getInstance().apply { timeInMillis = sorted[0].dueAt }
        assertThat(cal0.get(Calendar.HOUR_OF_DAY)).isEqualTo(9)
        assertThat(cal0.get(Calendar.MINUTE)).isEqualTo(0)
    }

    @Test
    fun project_anchorMovedToToday_pushesNextOccurrenceOutByOneInterval() {
        val now = cal(2026, Calendar.AUGUST, 15, 9, 0).timeInMillis
        // The reminder is exactly due right now under the OLD schedule
        // (anchored one full interval back) — occurrence 1 of that schedule
        // lands on `now` exactly.
        val staleAnchor = now - 7L * CareSchedule.DAY_MS
        // Logging today re-anchors to `now` — its occurrence 1 (the first
        // one AFTER today's log) lands on now + 7 days.
        val freshAnchor = now

        val window = (now - 1L * CareSchedule.DAY_MS) to (now + 10L * CareSchedule.DAY_MS)
        val before = CareQueue.project(
            listOf(watering(staleAnchor)), emptyList(), emptyList(), emptyList(),
            window.first, window.second,
        )
        val after = CareQueue.project(
            listOf(watering(freshAnchor)), emptyList(), emptyList(), emptyList(),
            window.first, window.second,
        )

        // The old schedule's due-right-now occurrence, exactly at `now`.
        assertThat(before.map { it.dueAt }).contains(now)
        // The new schedule's next occurrence after today's log, one full
        // interval later — not `now` itself (that's today's own log).
        assertThat(after.map { it.dueAt }).contains(now + 7L * CareSchedule.DAY_MS)
    }

    @Test
    fun project_dayWithDoneEvent_rendersDoneNotPending() {
        val now = cal(2026, Calendar.AUGUST, 15, 12, 0).timeInMillis
        val anchor = now - 7L * CareSchedule.DAY_MS // occurrence 1 due today
        val reminder = watering(anchor, hour = 9)
        val todayEpochDay = cal(2026, Calendar.AUGUST, 15).timeInMillis / CareSchedule.DAY_MS

        val tasks = CareQueue.project(
            reminders = listOf(reminder),
            steps = emptyList(),
            oneOffs = emptyList(),
            doneEvents = listOf(CareQueue.DoneEvent(plantId = 1, type = "watering", epochDay = todayEpochDay)),
            fromMs = now - 1L * CareSchedule.DAY_MS,
            toMs = now + 1L * CareSchedule.DAY_MS,
        )

        val today = tasks.first { it.dueAt in (now - 12L * 60 * 60 * 1000)..(now + 12L * 60 * 60 * 1000) }
        assertThat(today.status).isEqualTo(CareQueue.CareTask.Status.DONE)

        val summary = CareQueue.monthSummary(
            tasks, now - 1L * CareSchedule.DAY_MS, now + 1L * CareSchedule.DAY_MS, now,
        )
        val todayState = summary[todayEpochDay]!!
        assertThat(todayState.done).isEqualTo(1)
        assertThat(todayState.pending).isEqualTo(0)
        assertThat(todayState.overdue).isEqualTo(0)
    }

    @Test
    fun project_oneDayIntervalOverOneYearWindow_isCappedAndDoesNotHang() {
        val now = cal(2026, Calendar.AUGUST, 15, 12, 0).timeInMillis
        val reminder = watering(anchorMs = now - 365L * CareSchedule.DAY_MS, intervalDays = 1)

        val tasks = CareQueue.project(
            listOf(reminder), emptyList(), emptyList(), emptyList(),
            fromMs = now - 400L * CareSchedule.DAY_MS,
            toMs = now + 400L * CareSchedule.DAY_MS,
        )

        assertThat(tasks.size).isAtMost(CareQueue.MAX_OCCURRENCES_PER_REMINDER)
    }

    @Test
    fun project_emptyInputs_returnsEmptyList() {
        val now = System.currentTimeMillis()
        val tasks = CareQueue.project(
            emptyList(), emptyList(), emptyList(), emptyList(),
            now, now + CareSchedule.DAY_MS,
        )
        assertThat(tasks).isEmpty()
    }

    @Test
    fun project_disabledReminder_emitsNothing() {
        val now = System.currentTimeMillis()
        val disabled = watering(anchorMs = now - 100L * CareSchedule.DAY_MS).copy(enabled = false)
        val tasks = CareQueue.project(
            listOf(disabled), emptyList(), emptyList(), emptyList(),
            now - 30L * CareSchedule.DAY_MS, now + 30L * CareSchedule.DAY_MS,
        )
        assertThat(tasks).isEmpty()
    }

    @Test
    fun monthSummary_lastDayOfMonthAt2359_bucketsIntoThatMonthNotTheNext() {
        val monthStart = cal(2026, Calendar.AUGUST, 1, 0, 0).timeInMillis
        val monthEnd = cal(2026, Calendar.SEPTEMBER, 1, 0, 0).timeInMillis
        val lastDayLate = cal(2026, Calendar.AUGUST, 31, 23, 59).timeInMillis

        val task = CareQueue.CareTask(
            id = CareQueue.CareTask.TaskId.OneOff(1),
            plantId = 1,
            type = "check",
            title = "Check",
            dueAt = lastDayLate,
            status = CareQueue.CareTask.Status.PENDING,
        )

        val summary = CareQueue.monthSummary(listOf(task), monthStart, monthEnd, nowMs = monthStart)
        val lastDayEpoch = cal(2026, Calendar.AUGUST, 31).timeInMillis / CareSchedule.DAY_MS
        val firstDayNextMonthEpoch = cal(2026, Calendar.SEPTEMBER, 1).timeInMillis / CareSchedule.DAY_MS

        assertThat(summary).containsKey(lastDayEpoch)
        assertThat(summary).doesNotContainKey(firstDayNextMonthEpoch)
    }

    @Test
    fun forDay_sortsByTimeOfDayThenTitle() {
        val dayStart = cal(2026, Calendar.AUGUST, 15, 0, 0).timeInMillis
        val late = CareQueue.CareTask(
            CareQueue.CareTask.TaskId.OneOff(1), 1, "check", "Zed",
            dayStart + 18L * 60 * 60 * 1000, CareQueue.CareTask.Status.PENDING,
        )
        val early = CareQueue.CareTask(
            CareQueue.CareTask.TaskId.OneOff(2), 1, "watering", "Alpha",
            dayStart + 8L * 60 * 60 * 1000, CareQueue.CareTask.Status.PENDING,
        )
        val outside = CareQueue.CareTask(
            CareQueue.CareTask.TaskId.OneOff(3), 1, "watering", "Tomorrow",
            dayStart + CareSchedule.DAY_MS + 1000, CareQueue.CareTask.Status.PENDING,
        )

        val result = CareQueue.forDay(listOf(late, early, outside), dayStart)

        assertThat(result).containsExactly(early, late).inOrder()
    }
}
