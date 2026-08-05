package com.plantdiseases.app.util

import android.content.Context
import androidx.work.Data
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import com.plantdiseases.app.data.local.PlantReminderEntity
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
            context,
            plantId,
            plantName,
            type,
            intervalDays,
            reminderId,
            initialDelayMs = CareSchedule.initialDelayMs(hour, minute),
        )
    }

    /** Convenience overload for the generic-reminder list UI. */
    fun schedule(context: Context, reminder: PlantReminderEntity, plantName: String) =
        schedule(
            context,
            reminder.plantId,
            plantName,
            reminder.type,
            reminder.intervalDays,
            reminder.hour,
            reminder.minute,
            reminderId = reminder.id,
        )

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
            context,
            plantId,
            plantName,
            type,
            intervalDays,
            reminderId,
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
            intervalDays.toLong(),
            TimeUnit.DAYS,
        )
            .setInputData(inputData)
            .setInitialDelay(initialDelayMs, TimeUnit.MILLISECONDS)
            .build()

        WorkManager.getInstance(context).enqueueUniquePeriodicWork(
            uniqueName(plantId, type, reminderId),
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
            "snooze_${uniqueName(plantId, type, reminderId)}",
            ExistingWorkPolicy.REPLACE,
            request,
        )
    }

    fun cancel(context: Context, plantId: Long, type: String, reminderId: Long = 0L) {
        WorkManager.getInstance(context)
            .cancelUniqueWork(uniqueName(plantId, type, reminderId))
    }

    fun cancelReminder(context: Context, reminder: PlantReminderEntity) =
        cancel(context, reminder.plantId, reminder.type, reminder.id)

    /**
     * Cancel every scheduled reminder for a plant (plant deletion).
     * [reminders] is the plant's current reminder rows — needed because
     * generic reminders carry their row id in the unique work name. The
     * legacy three names are always cancelled too, covering rows that
     * were scheduled before the v4 generic-reminder migration.
     */
    fun cancelAll(
        context: Context,
        plantId: Long,
        reminders: List<PlantReminderEntity> = emptyList(),
    ) {
        ReminderPresets.LEGACY_TYPES.forEach { type ->
            cancel(context, plantId, type)
        }
        reminders.forEach { cancelReminder(context, it) }
    }

    /**
     * Unique periodic-work name. The legacy preset types keep the
     * historical ``reminder_{plant}_{type}`` shape so already-enqueued
     * workers from older app versions are still addressable (UPDATE /
     * cancel by the same name). Generic reminders may repeat a type
     * ("custom" twice on one plant), so their name carries the row id.
     */
    private fun uniqueName(plantId: Long, type: String, reminderId: Long = 0L): String =
        if (ReminderPresets.isLegacyType(type) || reminderId <= 0L) {
            "reminder_${plantId}_$type"
        } else {
            "reminder_${plantId}_${type}_$reminderId"
        }
}
