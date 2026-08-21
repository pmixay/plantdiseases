package com.plantdiseases.app.util

/**
 * Aggregates recurring reminders and treatment-plan steps into one dated
 * queue — the "Today" care hub ROADMAP §Garden refactor
 * describes. Pure Kotlin: no Room entities, no Android imports. The
 * repository layer (CareRepository) maps DAO rows into the *Input types
 * below and back out again; this object only does the projection math,
 * mirroring the existing CareSchedule.kt precedent.
 */
object CareQueue {

    const val MAX_OCCURRENCES_PER_REMINDER = 400

    data class ReminderInput(
        val reminderId: Long,
        val plantId: Long,
        /** ReminderPresets key: "watering" / "fertilize" / "check" / … */
        val type: String,
        val title: String,
        val intervalDays: Int,
        val hour: Int,
        val minute: Int,
        val enabled: Boolean,
        /** Care anchor — the most recent matching-type journal entry's
         *  timestamp, or the reminder's createdAt. Occurrence 0 is one full
         *  interval after this anchor. Computed by the
         *  caller exactly as PlantRepository.soonestReminderDueAt does. */
        val anchorMs: Long,
    )

    data class StepInput(
        val stepId: Long,
        val plantId: Long,
        val planId: Long,
        val title: String,
        val dueAtMs: Long,
        val status: String, // "pending" | "done" | "skipped"
        val stepIndex: Int,
        val stepCount: Int,
    )

    /** A completed occurrence of a recurring reminder, identified by the
     *  reminder it belongs to plus the calendar day (in the device's default
     *  time zone) it was done on. Both halves are load-bearing: a plant may
     *  hold several reminders of the same type — MIGRATION_3_4 dropped the
     *  (plant_id, type) unique index precisely so it can — so keying on type
     *  alone would let ticking one of them mark all the others done. */
    data class DoneEvent(
        val plantId: Long,
        val reminderId: Long,
        val type: String,
        val epochDay: Long,
    )

    data class CareTask(
        val id: TaskId,
        val plantId: Long,
        val type: String,
        val title: String,
        val dueAt: Long,
        val status: Status,
        val stepIndex: Int? = null,
        val stepCount: Int? = null,
    ) {
        enum class Status { PENDING, DONE, SKIPPED }

        sealed interface TaskId {
            data class Recurring(val reminderId: Long, val epochDay: Long) : TaskId
            data class Step(val stepId: Long) : TaskId
        }
    }

    data class DayState(val pending: Int, val overdue: Int, val done: Int)

    /** Projects every source into a flat, dated task list covering
     *  [fromMs, toMs). Deterministic and side-effect free — the same
     *  inputs always produce the same list, so the calendar and the day
     *  view can call this independently without drifting apart. */
    fun project(
        reminders: List<ReminderInput>,
        steps: List<StepInput>,
        doneEvents: List<DoneEvent>,
        fromMs: Long,
        toMs: Long,
    ): List<CareTask> {
        val tasks = mutableListOf<CareTask>()

        for (reminder in reminders) {
            if (!reminder.enabled) continue
            tasks += projectReminder(reminder, doneEvents, fromMs, toMs)
        }

        for (step in steps) {
            if (step.dueAtMs < fromMs || step.dueAtMs >= toMs) continue
            tasks += CareTask(
                id = CareTask.TaskId.Step(step.stepId),
                plantId = step.plantId,
                type = "treatment",
                title = step.title,
                dueAt = step.dueAtMs,
                status = statusOf(step.status),
                stepIndex = step.stepIndex,
                stepCount = step.stepCount,
            )
        }

        return tasks
    }

    private fun statusOf(raw: String): CareTask.Status = when (raw) {
        "done" -> CareTask.Status.DONE
        "skipped" -> CareTask.Status.SKIPPED
        else -> CareTask.Status.PENDING
    }

    private fun projectReminder(
        reminder: ReminderInput,
        doneEvents: List<DoneEvent>,
        fromMs: Long,
        toMs: Long,
    ): List<CareTask> {
        val intervalDays = reminder.intervalDays.coerceAtLeast(1)
        val doneDays = doneEvents
            .filter { it.plantId == reminder.plantId && it.reminderId == reminder.reminderId }
            .mapTo(mutableSetOf()) { it.epochDay }

        val results = mutableListOf<CareTask>()
        val stepMs = intervalDays.toLong() * CareSchedule.DAY_MS
        val first = occurrenceDueAt(reminder, 0)
        // Jump straight to the first occurrence that can land in the window;
        // step back one so a DST-shifted occurrence can't be skipped, and
        // clamp for windows starting before occurrence 0 (CareDayActivity
        // queries dayStart - 30d).
        val startIndex = if (fromMs <= first) {
            0
        } else {
            (((fromMs - first) / stepMs) - 1).coerceAtLeast(0L).toInt()
        }
        // The cap is relative to the WINDOW, not to occurrence 0: an absolute
        // cap silently drops long-lived reminders (a plant created 500 days
        // ago with a daily reminder exhausts it before the window starts and
        // vanishes from the calendar entirely).
        val maxIndex = startIndex + ((toMs - fromMs) / stepMs).toInt() + 2
        var occurrence = startIndex
        while (occurrence <= maxIndex && occurrence - startIndex < MAX_OCCURRENCES_PER_REMINDER) {
            val dueAt = occurrenceDueAt(reminder, occurrence)
            if (dueAt >= toMs) break
            if (dueAt >= fromMs) {
                val epochDay = epochDayOf(dueAt)
                val isDone = epochDay in doneDays
                results += CareTask(
                    id = CareTask.TaskId.Recurring(reminder.reminderId, epochDay),
                    plantId = reminder.plantId,
                    type = reminder.type,
                    title = reminder.title,
                    dueAt = dueAt,
                    status = if (isDone) CareTask.Status.DONE else CareTask.Status.PENDING,
                )
            }
            occurrence++
        }
        return results
    }

    /** occurrence N's due instant: the anchor shifted forward N + 1 intervals,
     *  then re-based onto the reminder's hour:minute using the same wall-clock
     *  day arithmetic as WorkManager's first schedule. */
    private fun occurrenceDueAt(reminder: ReminderInput, occurrence: Int): Long {
        val intervalCount = occurrence.toLong() + 1L
        val days = (reminder.intervalDays.coerceAtLeast(1).toLong() * intervalCount)
            .coerceAtMost(Int.MAX_VALUE.toLong())
            .toInt()
        return CareSchedule.scheduledDueAt(
            anchorMs = reminder.anchorMs,
            intervalDays = days,
            hour = reminder.hour,
            minute = reminder.minute,
        )
    }

    /** Most recent occurrence strictly before [cutoffExclusive], or null when
     * occurrence zero has not happened yet. This is the home-screen Today
     * projection: it gives one current action per recurring reminder instead
     * of expanding years of missed daily occurrences (and then arbitrarily
     * cutting them off at 30/400 days). The small correction loops account
     * for DST while the initial index estimate stays O(1). */
    fun latestOccurrenceBefore(
        reminder: ReminderInput,
        cutoffExclusive: Long,
    ): CareTask? {
        val first = occurrenceDueAt(reminder, 0)
        if (!reminder.enabled || first >= cutoffExclusive) return null

        val stepMs = reminder.intervalDays.coerceAtLeast(1).toLong() * CareSchedule.DAY_MS
        var occurrence = ((cutoffExclusive - 1L - first) / stepMs)
            .coerceAtLeast(0L)
            .coerceAtMost(Int.MAX_VALUE.toLong())
            .toInt()
        while (occurrence > 0 && occurrenceDueAt(reminder, occurrence) >= cutoffExclusive) {
            occurrence--
        }
        while (occurrence < Int.MAX_VALUE) {
            val next = occurrenceDueAt(reminder, occurrence + 1)
            if (next >= cutoffExclusive) break
            occurrence++
        }
        val dueAt = occurrenceDueAt(reminder, occurrence)
        return CareTask(
            id = CareTask.TaskId.Recurring(reminder.reminderId, epochDayOf(dueAt)),
            plantId = reminder.plantId,
            type = reminder.type,
            title = reminder.title,
            dueAt = dueAt,
            status = CareTask.Status.PENDING,
        )
    }

    /** Public so CareRepository can bucket a `plant_reminder_events.fired_at`
     *  / `done_at` timestamp into the same day-buckets this object uses
     *  internally when building [DoneEvent]s — duplicating the calculation
     *  risks the two drifting out of sync. */
    fun epochDayOf(ms: Long): Long {
        val cal = java.util.Calendar.getInstance().apply { timeInMillis = ms }
        // Days since epoch in LOCAL time — two timestamps on the same wall-clock
        // day (regardless of hour) must map to the same bucket, and this must
        // stay in lock-step with [dayStartMs] (its inverse) and monthSummary()
        // below.
        cal.set(java.util.Calendar.HOUR_OF_DAY, 0)
        cal.set(java.util.Calendar.MINUTE, 0)
        cal.set(java.util.Calendar.SECOND, 0)
        cal.set(java.util.Calendar.MILLISECOND, 0)
        // cal.timeInMillis is still an absolute UTC instant (local midnight,
        // expressed as an instant) — floor-dividing THAT by DAY_MS buckets by
        // UTC day, not local day. East of UTC, local midnight instants still
        // fall late on the PREVIOUS UTC day, so the naive division returns the
        // previous day's index (this was the "tap day 15, open day 14" bug in
        // any zone ahead of UTC, e.g. Moscow at UTC+3). Adding the zone+DST
        // offset shifts the instant so the division lands on the correct
        // local-day boundary regardless of timezone.
        val offsetMs = cal.get(java.util.Calendar.ZONE_OFFSET) + cal.get(java.util.Calendar.DST_OFFSET)
        return (cal.timeInMillis + offsetMs) / CareSchedule.DAY_MS
    }

    /** Inverse of [epochDayOf]: LOCAL midnight of [epochDay] as an absolute
     *  instant. Plain `epochDay * DAY_MS` reconstructs UTC midnight, which
     *  is a different day-window in every zone but UTC — the calendar tap
     *  handler and the day view must not disagree about which day they
     *  opened. atStartOfDay is what makes this correct across a DST
     *  transition: on a day whose local midnight does not exist it returns
     *  the first instant that does, where subtracting a zone offset read at
     *  some other instant would land on the wrong side of the switch. */
    fun dayStartMs(epochDay: Long): Long =
        java.time.LocalDate.ofEpochDay(epochDay)
            .atStartOfDay(java.time.ZoneId.systemDefault())
            .toInstant()
            .toEpochMilli()

    /** Tasks due on the calendar day containing [dayStartMs], sorted by
     *  time of day then title. */
    fun forDay(all: List<CareTask>, dayStart: Long): List<CareTask> {
        val end = dayStartMs(epochDayOf(dayStart) + 1L)
        return all.filter { it.dueAt >= dayStart && it.dueAt < end }
            .sortedWith(compareBy({ it.dueAt }, { it.title }))
    }

    /** Per-epoch-day roll-up for the month grid. Only days with at least
     *  one task appear in the map — an absent key means nothing scheduled. */
    fun monthSummary(all: List<CareTask>, monthStartMs: Long, monthEndMs: Long, nowMs: Long): Map<Long, DayState> {
        val byDay = all
            .filter { it.dueAt >= monthStartMs && it.dueAt < monthEndMs }
            .groupBy { epochDayOf(it.dueAt) }
        return byDay.mapValues { (_, dayTasks) ->
            var pending = 0
            var overdue = 0
            var done = 0
            for (t in dayTasks) {
                when (t.status) {
                    CareTask.Status.DONE -> done++
                    CareTask.Status.SKIPPED -> Unit
                    CareTask.Status.PENDING -> if (t.dueAt <= nowMs) overdue++ else pending++
                }
            }
            DayState(pending = pending, overdue = overdue, done = done)
        }
    }
}
