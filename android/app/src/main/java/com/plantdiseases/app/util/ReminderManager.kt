package com.plantdiseases.app.util

import android.content.Context
import androidx.work.Data
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import com.plantdiseases.app.worker.PlantReminderWorker
import java.util.Calendar
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
        val inputData = Data.Builder()
            .putLong(PlantReminderWorker.KEY_PLANT_ID, plantId)
            .putLong(PlantReminderWorker.KEY_REMINDER_ID, reminderId)
            .putString(PlantReminderWorker.KEY_REMINDER_TYPE, type)
            .putString(PlantReminderWorker.KEY_PLANT_NAME, plantName)
            .build()

        val initialDelay = computeInitialDelayMs(hour, minute)

        val request = PeriodicWorkRequestBuilder<PlantReminderWorker>(
            intervalDays.toLong(), TimeUnit.DAYS,
        )
            .setInputData(inputData)
            .setInitialDelay(initialDelay, TimeUnit.MILLISECONDS)
            .build()

        WorkManager.getInstance(context).enqueueUniquePeriodicWork(
            uniqueName(plantId, type),
            ExistingPeriodicWorkPolicy.UPDATE,
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

    /**
     * Compute how many milliseconds until the next occurrence of [hour]:[minute].
     * If the time has already passed today, schedules for tomorrow.
     */
    private fun computeInitialDelayMs(hour: Int, minute: Int): Long {
        val now = Calendar.getInstance()
        val target = Calendar.getInstance().apply {
            set(Calendar.HOUR_OF_DAY, hour)
            set(Calendar.MINUTE, minute)
            set(Calendar.SECOND, 0)
            set(Calendar.MILLISECOND, 0)
        }
        if (target.before(now)) {
            target.add(Calendar.DAY_OF_MONTH, 1)
        }
        return (target.timeInMillis - now.timeInMillis).coerceAtLeast(0L)
    }
}
