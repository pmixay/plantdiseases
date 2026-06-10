package com.plantdiseases.app.util

import android.content.Context
import androidx.work.Data
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import com.plantdiseases.app.worker.PlantReminderWorker
import java.util.concurrent.TimeUnit

object ReminderManager {

    /**
     * Schedule a periodic reminder for a plant.
     * The first trigger fires at [hour]:[minute] on the next available day.
     *
     * ``reminderId`` is the PlantReminderEntity row id — threaded
     * through to the worker so each firing can record an event in
     * plant_reminder_events with the reminder it came from. Pass
     * 0L if you don't have one (the worker will skip the
     * Done/Snooze action buttons in that case).
     */
    fun schedule(
        context: Context,
        plantId: Long,
        plantName: String,
        type: String,
        intervalDays: Int,
        hour: Int,
        minute: Int,
        reminderId: Long = 0L,
    ) {
        enqueuePeriodic(
            context, plantId, plantName, type, intervalDays, reminderId,
            initialDelayMs = CareSchedule.initialDelayMs(hour, minute),
        )
    }

    /**
     * Re-anchor a reminder's periodic schedule after the user just logged
     * the matching care action ("mark watered now"). The next firing lands
     * [intervalDays] days from now at the reminder's usual [hour]:[minute],
     * instead of nagging on the old cadence the morning after the user
     * already did the work. Subsequent firings keep the periodic interval.
     */
    fun rescheduleAfterCare(
        context: Context,
        plantId: Long,
        plantName: String,
        type: String,
        intervalDays: Int,
        hour: Int,
        minute: Int,
        reminderId: Long = 0L,
    ) {
        enqueuePeriodic(
            context, plantId, plantName, type, intervalDays, reminderId,
            initialDelayMs = CareSchedule.delayAfterCareMs(hour, minute, intervalDays),
        )
    }

    private fun enqueuePeriodic(
        context: Context,
        plantId: Long,
        plantName: String,
        type: String,
        intervalDays: Int,
        reminderId: Long,
        initialDelayMs: Long,
    ) {
        val inputData = Data.Builder()
            .putLong(PlantReminderWorker.KEY_PLANT_ID, plantId)
            .putLong(PlantReminderWorker.KEY_REMINDER_ID, reminderId)
            .putString(PlantReminderWorker.KEY_REMINDER_TYPE, type)
            .putString(PlantReminderWorker.KEY_PLANT_NAME, plantName)
            .build()

        val request = PeriodicWorkRequestBuilder<PlantReminderWorker>(
            intervalDays.toLong(), TimeUnit.DAYS,
        )
            .setInputData(inputData)
            .setInitialDelay(initialDelayMs, TimeUnit.MILLISECONDS)
            .build()

        WorkManager.getInstance(context).enqueueUniquePeriodicWork(
            uniqueName(plantId, type),
            ExistingPeriodicWorkPolicy.UPDATE,
            request,
        )
    }

    /**
     * Re-fire a single reminder one day from now — the notification
     * "Snooze 1d" action. One-shot: it does NOT touch the plant's periodic
     * schedule, it just brings this one reminder back tomorrow. Unique work
     * with REPLACE so a second snooze tap resets the 24h timer rather than
     * stacking duplicate firings.
     */
    fun snooze(
        context: Context,
        plantId: Long,
        plantName: String,
        type: String,
        reminderId: Long,
    ) {
        val inputData = Data.Builder()
            .putLong(PlantReminderWorker.KEY_PLANT_ID, plantId)
            .putLong(PlantReminderWorker.KEY_REMINDER_ID, reminderId)
            .putString(PlantReminderWorker.KEY_REMINDER_TYPE, type)
            .putString(PlantReminderWorker.KEY_PLANT_NAME, plantName)
            .build()

        val request = OneTimeWorkRequestBuilder<PlantReminderWorker>()
            .setInputData(inputData)
            .setInitialDelay(1, TimeUnit.DAYS)
            .build()

        WorkManager.getInstance(context).enqueueUniqueWork(
            "snooze_${plantId}_$type",
            ExistingWorkPolicy.REPLACE,
            request,
        )
    }

    fun cancel(context: Context, plantId: Long, type: String) {
        WorkManager.getInstance(context).cancelUniqueWork(uniqueName(plantId, type))
    }

    fun cancelAll(context: Context, plantId: Long) {
        listOf(
            PlantReminderWorker.TYPE_WATERING,
            PlantReminderWorker.TYPE_CHECK,
            PlantReminderWorker.TYPE_FERTILIZE,
        ).forEach { type ->
            cancel(context, plantId, type)
        }
    }

    private fun uniqueName(plantId: Long, type: String) = "reminder_${plantId}_$type"
}
