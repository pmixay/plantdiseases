package com.plantdiseases.app.util

import java.util.Calendar

/**
 * Pure date math for the plant-care reminder schedule. Shared by
 * [PlantRepository][com.plantdiseases.app.data.repository.PlantRepository]
 * (next-due computation for the garden "care due" chips),
 * [ReminderManager] (WorkManager initial delays) and the Garden UI
 * (overdue / due-in-N-days bucketing). No Android dependencies so it
 * is directly unit-testable.
 */
object CareSchedule {

    const val DAY_MS: Long = 24L * 60L * 60L * 1000L

    /** Absolute epoch-ms when a reminder next falls due, given the last
     *  matching care event (or the plant's creation) at [anchorMs]. */
    fun nextDueAt(anchorMs: Long, intervalDays: Int): Long =
        anchorMs + intervalDays.coerceAtLeast(0) * DAY_MS

    /** True when [dueAt] has genuinely passed. Integer-day truncation
     *  must NOT be used for this — anything due within the next 24 h
     *  would otherwise read as overdue before it arrived. */
    fun isOverdue(dueAt: Long, nowMs: Long): Boolean = dueAt <= nowMs

    /** Whole days (truncated) until [dueAt]; 0 when due within the next
     *  24 h, negative when overdue. */
    fun daysUntil(dueAt: Long, nowMs: Long): Int = ((dueAt - nowMs) / DAY_MS).toInt()

    /**
     * Milliseconds until the next wall-clock occurrence of [hour]:[minute].
     * If that time has already passed today, schedules for tomorrow.
     */
    fun initialDelayMs(hour: Int, minute: Int, now: Calendar = Calendar.getInstance()): Long {
        val target = (now.clone() as Calendar).apply {
            set(Calendar.HOUR_OF_DAY, hour)
            set(Calendar.MINUTE, minute)
            set(Calendar.SECOND, 0)
            set(Calendar.MILLISECOND, 0)
        }
        if (!target.after(now)) {
            target.add(Calendar.DAY_OF_MONTH, 1)
        }
        return (target.timeInMillis - now.timeInMillis).coerceAtLeast(0L)
    }

    /**
     * Milliseconds until the reminder should next fire after the user just
     * performed the care action "now": [intervalDays] days ahead, at the
     * reminder's [hour]:[minute]. Used to push the periodic schedule out
     * when the user waters/fertilises off-cycle, so the next nudge doesn't
     * arrive the morning after they already did the work.
     */
    fun delayAfterCareMs(
        hour: Int,
        minute: Int,
        intervalDays: Int,
        now: Calendar = Calendar.getInstance(),
    ): Long {
        val target = (now.clone() as Calendar).apply {
            add(Calendar.DAY_OF_MONTH, intervalDays.coerceAtLeast(1))
            set(Calendar.HOUR_OF_DAY, hour)
            set(Calendar.MINUTE, minute)
            set(Calendar.SECOND, 0)
            set(Calendar.MILLISECOND, 0)
        }
        if (!target.after(now)) {
            target.add(Calendar.DAY_OF_MONTH, 1)
        }
        return (target.timeInMillis - now.timeInMillis).coerceAtLeast(0L)
    }
}
