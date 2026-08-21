package com.plantdiseases.app.util

import com.google.common.truth.Truth.assertThat
import org.junit.Test
import java.time.LocalDate
import java.util.Calendar
import java.util.TimeZone

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

        // Occurrences land one full interval after the anchor: Aug12, Aug19,
        // Aug26… A window of [Aug4, Aug20) keeps the first two.
        val tasks = CareQueue.project(
            reminders = listOf(reminder),
            steps = emptyList(),
            doneEvents = emptyList(),
            fromMs = now - 11L * CareSchedule.DAY_MS,
            toMs = now + 5L * CareSchedule.DAY_MS,
        )

        assertThat(tasks).hasSize(2)
        val sorted = tasks.sortedBy { it.dueAt }
        assertThat(sorted[0].status).isEqualTo(CareQueue.CareTask.Status.PENDING)
        assertThat(sorted[0].dueAt).isLessThan(now)
        assertThat(sorted[1].dueAt).isGreaterThan(now)

        val cal0 = Calendar.getInstance().apply { timeInMillis = sorted[0].dueAt }
        assertThat(cal0.get(Calendar.HOUR_OF_DAY)).isEqualTo(9)
        assertThat(cal0.get(Calendar.MINUTE)).isEqualTo(0)
    }

    @Test
    fun project_anchorMovedToToday_pushesNextOccurrenceOutByOneInterval() {
        val now = cal(2026, Calendar.AUGUST, 15, 9, 0).timeInMillis
        // The reminder is exactly due right now under the OLD schedule
        // (anchored one full interval back) — occurrence 0 of that schedule
        // lands on `now` exactly.
        val staleAnchor = now - 7L * CareSchedule.DAY_MS
        // Logging today re-anchors to `now` — its occurrence 0 (the first
        // one AFTER today's log) lands on now + 7 days.
        val freshAnchor = now

        val window = (now - 1L * CareSchedule.DAY_MS) to (now + 10L * CareSchedule.DAY_MS)
        val before = CareQueue.project(
            listOf(watering(staleAnchor)),
            emptyList(),
            emptyList(),
            window.first,
            window.second,
        )
        val after = CareQueue.project(
            listOf(watering(freshAnchor)),
            emptyList(),
            emptyList(),
            window.first,
            window.second,
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
        val todayEpochDay = CareQueue.epochDayOf(cal(2026, Calendar.AUGUST, 15).timeInMillis)

        val tasks = CareQueue.project(
            reminders = listOf(reminder),
            steps = emptyList(),
            doneEvents = listOf(CareQueue.DoneEvent(plantId = 1, reminderId = 1, type = "watering", epochDay = todayEpochDay)),
            fromMs = now - 1L * CareSchedule.DAY_MS,
            toMs = now + 1L * CareSchedule.DAY_MS,
        )

        val today = tasks.first { it.dueAt in (now - 12L * 60 * 60 * 1000)..(now + 12L * 60 * 60 * 1000) }
        assertThat(today.status).isEqualTo(CareQueue.CareTask.Status.DONE)

        val summary = CareQueue.monthSummary(
            tasks,
            now - 1L * CareSchedule.DAY_MS,
            now + 1L * CareSchedule.DAY_MS,
            now,
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
            listOf(reminder),
            emptyList(),
            emptyList(),
            fromMs = now - 400L * CareSchedule.DAY_MS,
            toMs = now + 400L * CareSchedule.DAY_MS,
        )

        // isNotEmpty matters as much as the cap: the cap used to be counted
        // from occurrence 0, so a long-lived reminder could exhaust it before
        // the window even started and return nothing at all.
        assertThat(tasks).isNotEmpty()
        assertThat(tasks.size).isAtMost(CareQueue.MAX_OCCURRENCES_PER_REMINDER)
    }

    @Test
    fun project_dailyReminderAnchoredFiveHundredDaysBack_stillLandsInTodaysWindow() {
        // Regression: the occurrence walk started at 0 and stopped after 400
        // steps, so a plant created 500 days ago with a daily reminder
        // disappeared from the day view and the calendar entirely — while
        // WorkManager kept firing its notification.
        val now = cal(2026, Calendar.AUGUST, 15, 12, 0).timeInMillis
        val reminder = watering(anchorMs = now - 500L * CareSchedule.DAY_MS, intervalDays = 1, hour = 9)
        val dayStart = CareQueue.dayStartMs(CareQueue.epochDayOf(now))

        val tasks = CareQueue.project(
            listOf(reminder),
            emptyList(),
            emptyList(),
            fromMs = dayStart,
            toMs = dayStart + CareSchedule.DAY_MS,
        )

        assertThat(tasks).hasSize(1)
        assertThat(CareQueue.epochDayOf(tasks[0].dueAt)).isEqualTo(CareQueue.epochDayOf(now))
    }

    @Test
    fun project_lateCompletionReanchorsWithoutRespawningOnCompletionDay() {
        // Due Aug 10, ticked Aug 15. Completing writes a journal entry, which
        // re-anchors the reminder onto the completion. Occurrence 0 must land
        // one interval later, not back on Aug 15 (which used to respawn the
        // just-completed task and require a second tap).
        val originalDue = cal(2026, Calendar.AUGUST, 10, 9, 0).timeInMillis
        val completedAt = cal(2026, Calendar.AUGUST, 15, 18, 0).timeInMillis

        val tasks = CareQueue.project(
            reminders = listOf(watering(anchorMs = completedAt, hour = 9)),
            steps = emptyList(),
            doneEvents = listOf(
                CareQueue.DoneEvent(plantId = 1, reminderId = 1, type = "watering", epochDay = CareQueue.epochDayOf(originalDue)),
            ),
            fromMs = originalDue - CareSchedule.DAY_MS,
            toMs = completedAt + CareSchedule.DAY_MS,
        )

        val onCompletionDay = tasks.filter {
            CareQueue.epochDayOf(it.dueAt) == CareQueue.epochDayOf(completedAt)
        }
        assertThat(onCompletionDay).isEmpty()
    }

    @Test
    fun latestOccurrenceBefore_returnsOneCurrentOccurrenceWithoutLookbackCutoff() {
        val now = cal(2026, Calendar.AUGUST, 15, 12, 0).timeInMillis
        val oldAnchor = now - 1_000L * CareSchedule.DAY_MS
        val todayEnd = CareQueue.dayStartMs(CareQueue.epochDayOf(now) + 1L)

        val latest = CareQueue.latestOccurrenceBefore(
            watering(anchorMs = oldAnchor, intervalDays = 1, hour = 9),
            todayEnd,
        )

        assertThat(latest).isNotNull()
        assertThat(CareQueue.epochDayOf(latest!!.dueAt)).isEqualTo(CareQueue.epochDayOf(now))
    }

    @Test
    fun firstOccurrence_isOneFullIntervalAfterReminderAnchor() {
        val anchor = cal(2026, Calendar.AUGUST, 1, 18, 0).timeInMillis
        val expectedDay = CareQueue.epochDayOf(cal(2026, Calendar.AUGUST, 8, 9, 0).timeInMillis)
        val task = CareQueue.latestOccurrenceBefore(
            watering(anchorMs = anchor, intervalDays = 7, hour = 9),
            cal(2026, Calendar.AUGUST, 9, 0, 0).timeInMillis,
        )

        assertThat(task).isNotNull()
        assertThat(CareQueue.epochDayOf(task!!.dueAt)).isEqualTo(expectedDay)
    }

    @Test
    fun project_emptyInputs_returnsEmptyList() {
        val now = System.currentTimeMillis()
        val tasks = CareQueue.project(
            emptyList(),
            emptyList(),
            emptyList(),
            now,
            now + CareSchedule.DAY_MS,
        )
        assertThat(tasks).isEmpty()
    }

    @Test
    fun project_disabledReminder_emitsNothing() {
        val now = System.currentTimeMillis()
        val disabled = watering(anchorMs = now - 100L * CareSchedule.DAY_MS).copy(enabled = false)
        val tasks = CareQueue.project(
            listOf(disabled),
            emptyList(),
            emptyList(),
            now - 30L * CareSchedule.DAY_MS,
            now + 30L * CareSchedule.DAY_MS,
        )
        assertThat(tasks).isEmpty()
    }

    @Test
    fun monthSummary_lastDayOfMonthAt2359_bucketsIntoThatMonthNotTheNext() {
        val monthStart = cal(2026, Calendar.AUGUST, 1, 0, 0).timeInMillis
        val monthEnd = cal(2026, Calendar.SEPTEMBER, 1, 0, 0).timeInMillis
        val lastDayLate = cal(2026, Calendar.AUGUST, 31, 23, 59).timeInMillis

        val task = CareQueue.CareTask(
            id = CareQueue.CareTask.TaskId.Step(1),
            plantId = 1,
            type = "check",
            title = "Check",
            dueAt = lastDayLate,
            status = CareQueue.CareTask.Status.PENDING,
        )

        val summary = CareQueue.monthSummary(listOf(task), monthStart, monthEnd, nowMs = monthStart)
        val lastDayEpoch = CareQueue.epochDayOf(cal(2026, Calendar.AUGUST, 31).timeInMillis)
        val firstDayNextMonthEpoch = CareQueue.epochDayOf(cal(2026, Calendar.SEPTEMBER, 1).timeInMillis)

        assertThat(summary).containsKey(lastDayEpoch)
        assertThat(summary).doesNotContainKey(firstDayNextMonthEpoch)
    }

    @Test
    fun epochDayOf_positiveUtcOffsetTimezone_matchesLocalCalendarDate() {
        // Regression: epochDayOf() used to floor-divide the raw UTC instant
        // of local midnight, which lands on the PREVIOUS UTC day in any zone
        // ahead of UTC — the "tap day 15, open day 14" bug reported from
        // Moscow (UTC+3, no DST). LocalDate.toEpochDay() is the
        // timezone-independent oracle for what "days since epoch" a calendar
        // date should map to.
        val original = TimeZone.getDefault()
        try {
            TimeZone.setDefault(TimeZone.getTimeZone("Europe/Moscow"))
            val localMidnightAug15 = cal(2026, Calendar.AUGUST, 15).timeInMillis
            val expected = LocalDate.of(2026, 8, 15).toEpochDay()
            assertThat(CareQueue.epochDayOf(localMidnightAug15)).isEqualTo(expected)
        } finally {
            TimeZone.setDefault(original)
        }
    }

    @Test
    fun dayStartMs_isTheExactInverseOfEpochDayOf_eastAndWestOfUtc() {
        // The calendar tap handler used to invert epochDayOf with a plain
        // `* DAY_MS`, which reconstructs UTC midnight — a different 24-hour
        // window in every zone but UTC, so the day view opened the wrong day
        // and its "is this today?" check silently failed.
        val original = TimeZone.getDefault()
        try {
            for (zone in listOf("America/New_York", "Europe/Moscow")) {
                TimeZone.setDefault(TimeZone.getTimeZone(zone))
                val epochDay = LocalDate.of(2026, 8, 20).toEpochDay()
                val start = CareQueue.dayStartMs(epochDay)

                assertThat(CareQueue.epochDayOf(start)).isEqualTo(epochDay)
                val cal = Calendar.getInstance().apply { timeInMillis = start }
                assertThat(cal.get(Calendar.HOUR_OF_DAY)).isEqualTo(0)
                assertThat(cal.get(Calendar.DAY_OF_MONTH)).isEqualTo(20)
            }
        } finally {
            TimeZone.setDefault(original)
        }
    }

    @Test
    fun forDay_sortsByTimeOfDayThenTitle() {
        val dayStart = cal(2026, Calendar.AUGUST, 15, 0, 0).timeInMillis
        val late = CareQueue.CareTask(
            CareQueue.CareTask.TaskId.Step(1),
            1,
            "check",
            "Zed",
            dayStart + 18L * 60 * 60 * 1000,
            CareQueue.CareTask.Status.PENDING,
        )
        val early = CareQueue.CareTask(
            CareQueue.CareTask.TaskId.Step(2),
            1,
            "watering",
            "Alpha",
            dayStart + 8L * 60 * 60 * 1000,
            CareQueue.CareTask.Status.PENDING,
        )
        val outside = CareQueue.CareTask(
            CareQueue.CareTask.TaskId.Step(3),
            1,
            "watering",
            "Tomorrow",
            dayStart + CareSchedule.DAY_MS + 1000,
            CareQueue.CareTask.Status.PENDING,
        )

        val result = CareQueue.forDay(listOf(late, early, outside), dayStart)

        assertThat(result).containsExactly(early, late).inOrder()
    }

    @Test
    fun project_twoSameTypeRemindersOnOnePlant_completingOneLeavesTheOtherPending() {
        // Regression: DoneEvent used to be keyed on (plantId, type, day), but a
        // plant may hold several reminders of the SAME type — MIGRATION_3_4
        // dropped the (plant_id, type) unique index precisely so it can. Ticking
        // one of them therefore marked every same-type reminder done that day.
        val now = cal(2026, Calendar.AUGUST, 15, 12, 0).timeInMillis
        val anchor = now - 7L * CareSchedule.DAY_MS // occurrence 1 due today for both
        val first = watering(anchor, hour = 9, reminderId = 1)
        val second = watering(anchor, hour = 9, reminderId = 2)
        val todayEpochDay = CareQueue.epochDayOf(cal(2026, Calendar.AUGUST, 15).timeInMillis)

        val tasks = CareQueue.project(
            reminders = listOf(first, second),
            steps = emptyList(),
            // Only reminder 1 was ticked.
            doneEvents = listOf(
                CareQueue.DoneEvent(plantId = 1, reminderId = 1, type = "watering", epochDay = todayEpochDay),
            ),
            fromMs = now - 1L * CareSchedule.DAY_MS,
            toMs = now + 1L * CareSchedule.DAY_MS,
        )

        val byReminder = tasks
            .mapNotNull { it.id as? CareQueue.CareTask.TaskId.Recurring }
            .associateWith { id -> tasks.first { it.id == id } }
        val done = byReminder.entries.first { it.key.reminderId == 1L }.value
        val untouched = byReminder.entries.first { it.key.reminderId == 2L }.value

        assertThat(done.status).isEqualTo(CareQueue.CareTask.Status.DONE)
        assertThat(untouched.status).isEqualTo(CareQueue.CareTask.Status.PENDING)
    }
}
