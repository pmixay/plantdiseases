package com.plantdiseases.app.worker

import android.Manifest
import android.annotation.SuppressLint
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.content.ContextCompat
import androidx.core.app.NotificationCompat
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.R
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.ui.garden.PlantDetailActivity
import org.koin.core.component.KoinComponent
import org.koin.core.component.inject

/**
 * Fires when a reminder's WorkManager interval elapses. Persists a
 * ``pending`` event in plant_reminder_events and posts a notification
 * with two action buttons:
 *
 *   * Done   → ReminderActionReceiver records action="done" and
 *              auto-creates the matching journal entry.
 *   * Snooze → records action="snoozed" so the streak chip on plant
 *              detail still shows the firing.
 *
 * The event id is threaded through both pending-intents so the
 * receivers know which row to resolve.
 */
class PlantReminderWorker(
    private val context: Context,
    params: WorkerParameters,
) : CoroutineWorker(context, params), KoinComponent {

    private val plantRepository: PlantRepository by inject()

    @SuppressLint("MissingPermission") // permission checked at line below before notify()
    override suspend fun doWork(): Result {
        val reminderId = inputData.getLong(KEY_REMINDER_ID, -1L)
        val plantId = inputData.getLong(KEY_PLANT_ID, -1L)
        val reminderType = inputData.getString(KEY_REMINDER_TYPE) ?: return Result.failure()
        val plantName = inputData.getString(KEY_PLANT_NAME) ?: return Result.failure()

        if (plantId == -1L) return Result.failure()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            // Reminder fired but POST_NOTIFICATIONS was revoked / never
            // granted. Return success so WorkManager doesn't keep
            // retrying, but log loudly so "reminders silently dropped"
            // shows up in adb logcat instead of looking like a missing
            // alarm.
            android.util.Log.w(
                "PlantReminderWorker",
                "POST_NOTIFICATIONS missing — silently skipping reminder " +
                    "for plant_id=$plantId type=$reminderType. Ask the user " +
                    "to re-grant the notification permission in Settings.",
            )
            return Result.success()
        }

        // Stable per-(plant, legacy-type) ids keep the historical dedupe
        // behaviour; generic reminders dedupe on their row id instead
        // (offset so the two ranges can't collide).
        val notificationId = when (reminderType) {
            TYPE_WATERING -> (plantId * 10).toInt()
            TYPE_CHECK -> (plantId * 10 + 1).toInt()
            TYPE_FERTILIZE -> (plantId * 10 + 2).toInt()
            else ->
                if (reminderId > 0) {
                    (reminderId % 1_000_000L).toInt() + 10_000_000
                } else {
                    (plantId * 10 + 9).toInt()
                }
        }

        // Log the firing as a pending event BEFORE posting the notify
        // so the action receivers always have a row to resolve. If
        // resolution doesn't happen (user dismisses without tapping),
        // the row stays "pending" and the recent-events list shows it
        // — that's an audit signal, not a bug.
        val reminder = if (reminderId > 0) plantRepository.getReminderById(reminderId) else null
        val eventId = if (reminder != null) {
            plantRepository.recordReminderFire(reminder)
        } else {
            -1L
        }

        // Custom-named reminders title with their own name; legacy rows
        // without a custom label keep the friendlier verb phrasing. The
        // body prefers the user's description when present.
        val customLabel = reminder?.label?.trim()?.takeIf { it.isNotEmpty() }
        val title = when {
            customLabel != null ->
                context.getString(R.string.reminder_notif_generic, customLabel, plantName)
            reminderType == TYPE_WATERING ->
                context.getString(R.string.reminder_notif_watering, plantName)
            reminderType == TYPE_CHECK ->
                context.getString(R.string.reminder_notif_check, plantName)
            reminderType == TYPE_FERTILIZE ->
                context.getString(R.string.reminder_notif_fertilize, plantName)
            else -> context.getString(
                R.string.reminder_notif_generic,
                com.plantdiseases.app.util.ReminderPresets.displayLabel(null, reminderType) {
                    context.getString(it)
                },
                plantName,
            )
        }
        val body = reminder?.description?.trim()?.takeIf { it.isNotEmpty() }
            ?: com.plantdiseases.app.util.ReminderPresets.displayLabel(
                customLabel,
                reminderType,
            ) { context.getString(it) }

        val contentIntent = PendingIntent.getActivity(
            context,
            notificationId,
            Intent(context, PlantDetailActivity::class.java).apply {
                putExtra(PlantDetailActivity.EXTRA_PLANT_ID, plantId)
            },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

        val builder = NotificationCompat.Builder(context, PlantDiseasesApp.CHANNEL_PLANT_REMINDERS)
            .setSmallIcon(R.drawable.ic_care)
            .setContentTitle(title)
            .setContentText(body)
            .setContentIntent(contentIntent)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)

        // Done + Snooze actions are only useful when we know which
        // event to resolve; if recordReminderFire couldn't find the
        // PlantReminder row (e.g. the user deleted it but the worker
        // already enqueued), skip them silently — the notification
        // still shows.
        if (eventId > 0) {
            val doneIntent = ReminderActionReceiver.buildIntent(
                context,
                eventId,
                notificationId,
                ReminderActionReceiver.ACTION_DONE,
            )
            val donePending = PendingIntent.getBroadcast(
                context,
                (notificationId * 10) + 1,
                doneIntent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
            val snoozeIntent = ReminderActionReceiver.buildIntent(
                context,
                eventId,
                notificationId,
                ReminderActionReceiver.ACTION_SNOOZE,
                plantId = plantId,
                reminderId = reminderId,
                type = reminderType,
                plantName = plantName,
            )
            val snoozePending = PendingIntent.getBroadcast(
                context,
                (notificationId * 10) + 2,
                snoozeIntent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
            builder.addAction(
                R.drawable.ic_care,
                context.getString(R.string.reminder_action_done),
                donePending,
            )
            builder.addAction(
                R.drawable.ic_clock,
                context.getString(R.string.reminder_action_snooze),
                snoozePending,
            )
        }

        val nm = context.getSystemService(NotificationManager::class.java)
        nm.notify(notificationId, builder.build())

        return Result.success()
    }

    companion object {
        const val KEY_PLANT_ID = "plant_id"
        const val KEY_REMINDER_ID = "reminder_id"
        const val KEY_REMINDER_TYPE = "reminder_type"
        const val KEY_PLANT_NAME = "plant_name"

        const val TYPE_WATERING = "watering"
        const val TYPE_CHECK = "check"
        const val TYPE_FERTILIZE = "fertilize"
    }
}
