package com.plantdiseases.app.util

/**
 * Aggregates recurring reminders, treatment-plan steps and one-off tasks
 * into one dated queue — the "Today" care hub ROADMAP §Garden refactor
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
        /** Anchor for occurrence 0 — the most recent matching-type journal
         *  entry's timestamp, or the plant's createdAt. Computed by the
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

    data class OneOffInput(
        val taskId: Long,
        val plantId: Long,
        val type: String,
        val title: String,
        val dueAtMs: Long,
        val status: String, // "pending" | "done"
    )

    /** A completed occurrence of a recurring reminder, identified by the
     *  calendar day (in the device's default time zone) it was done on —
     *  recurring reminders have no row of their own, so "done" is recorded
     *  per-day rather than per-occurrence-id. */
    data class DoneEvent(val plantId: Long, val type: String, val epochDay: Long)

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
            data class OneOff(val taskId: Long) : TaskId
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
        oneOffs: List<OneOffInput>,
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

        for (oneOff in oneOffs) {
            if (oneOff.dueAtMs < fromMs || oneOff.dueAtMs >= toMs) continue
            tasks += CareTask(
                id = CareTask.TaskId.OneOff(oneOff.taskId),
                plantId = oneOff.plantId,
                type = oneOff.type,
                title = oneOff.title,
                dueAt = oneOff.dueAtMs,
                status = statusOf(oneOff.status),
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
            .filter { it.plantId == reminder.plantId && it.type == reminder.type }
            .mapTo(mutableSetOf()) { it.epochDay }

        val results = mutableListOf<CareTask>()
        var occurrence = 0
        while (occurrence < MAX_OCCURRENCES_PER_REMINDER) {
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

    /** occurrence N's due instant: the anchor shifted forward N intervals,
     *  then re-based onto the reminder's hour:minute — matching
     *  CareSchedule.nextDueAt's anchor + interval*N day math, but landing
     *  on the configured time of day rather than the anchor's own time. */
    private fun occurrenceDueAt(reminder: ReminderInput, occurrence: Int): Long {
        val dayShifted = CareSchedule.nextDueAt(reminder.anchorMs, reminder.intervalDays * occurrence)
        val cal = java.util.Calendar.getInstance().apply {
            timeInMillis = dayShifted
            set(java.util.Calendar.HOUR_OF_DAY, reminder.hour)
            set(java.util.Calendar.MINUTE, reminder.minute)
            set(java.util.Calendar.SECOND, 0)
            set(java.util.Calendar.MILLISECOND, 0)
        }
        return cal.timeInMillis
    }

    private fun epochDayOf(ms: Long): Long {
        val cal = java.util.Calendar.getInstance().apply { timeInMillis = ms }
        // Days since epoch in LOCAL time — two timestamps on the same wall-clock
        // day (regardless of hour) must map to the same bucket, and this must
        // stay in lock-step with dayStartMs()/monthSummary() below, which build
        // their day boundaries the same way (set fields, don't just floor-divide
        // the UTC millis, which would drift by the zone offset).
        cal.set(java.util.Calendar.HOUR_OF_DAY, 0)
        cal.set(java.util.Calendar.MINUTE, 0)
        cal.set(java.util.Calendar.SECOND, 0)
        cal.set(java.util.Calendar.MILLISECOND, 0)
        return cal.timeInMillis / CareSchedule.DAY_MS
    }

    /** Tasks due on the calendar day containing [dayStartMs], sorted by
     *  time of day then title. */
    fun forDay(all: List<CareTask>, dayStartMs: Long): List<CareTask> {
        val end = dayStartMs + CareSchedule.DAY_MS
        return all.filter { it.dueAt >= dayStartMs && it.dueAt < end }
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
