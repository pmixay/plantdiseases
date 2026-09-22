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
import com.plantdiseases.app.di.appEntryPoint
import com.plantdiseases.app.ui.garden.PlantDetailActivity
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.NotificationCenter

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
 *
 * Resolves its repository via [appEntryPoint] rather than Hilt-assisted
 * construction (`HiltWorkerFactory`) — see the comment on
 * [com.plantdiseases.app.di.AppEntryPoint] for why.
 */
class PlantReminderWorker(
    private val context: Context,
    params: WorkerParameters,
) : CoroutineWorker(context, params) {

    private val plantRepository = appEntryPoint(context).plantRepository()

    @SuppressLint("MissingPermission") // permission checked immediately before notify() below
    override suspend fun doWork(): Result {
        val reminderId = inputData.getLong(KEY_REMINDER_ID, -1L)
        val plantId = inputData.getLong(KEY_PLANT_ID, -1L)
        val reminderType = inputData.getString(KEY_REMINDER_TYPE) ?: return Result.failure()
        val plantName = inputData.getString(KEY_PLANT_NAME) ?: return Result.failure()
        val existingEventId = inputData.getLong(KEY_EXISTING_EVENT_ID, -1L)

        if (plantId == -1L) return Result.failure()

        // Deliberately NOT gated on POST_NOTIFICATIONS up here: the firing must
        // still be journalled and recorded in the in-app notification centre for
        // a user who denied the permission — that is exactly the case the centre
        // exists for (see NotificationCenter's kdoc). The permission is checked
        // immediately before notify(), at the bottom of this method.

        // Strings are resolved through a locale-wrapped context: `context` here
        // is the Worker's application context, which — unlike an Activity's — is
        // never wrapped by LocaleHelper (applyLocale's result is only ever
        // attached via Activity.attachBaseContext), so getString() would fall
        // back to the SYSTEM locale and disagree with a Russian-in-app /
        // English-system user. Same workaround OpinionNotifier documents.
        val localizedCtx = LocaleHelper.setLocale(context, LocaleHelper.getSavedLanguage(context))

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
        if (reminderId > 0L && reminder == null) return Result.success()
        val eventId = if (existingEventId > 0L) {
            if (reminder == null) return Result.success()
            plantRepository.reactivateSnoozedReminderEvent(existingEventId) ?: return Result.success()
        } else if (reminder != null) {
            // A worker retry, or an in-app Done racing this firing, may have
            // already materialised today's occurrence. The repository returns
            // null in that case; do not post a stale duplicate notification.
            plantRepository.recordReminderFire(reminder) ?: return Result.success()
        } else {
            -1L
        }

        // Custom-named reminders title with their own name; legacy rows
        // without a custom label keep the friendlier verb phrasing. The
        // body prefers the user's description when present.
        val customLabel = reminder?.label?.trim()?.takeIf { it.isNotEmpty() }
        val title = when {
            customLabel != null ->
                localizedCtx.getString(R.string.reminder_notif_generic, customLabel, plantName)
            reminderType == TYPE_WATERING ->
                localizedCtx.getString(R.string.reminder_notif_watering, plantName)
            reminderType == TYPE_CHECK ->
                localizedCtx.getString(R.string.reminder_notif_check, plantName)
            reminderType == TYPE_FERTILIZE ->
                localizedCtx.getString(R.string.reminder_notif_fertilize, plantName)
            else -> localizedCtx.getString(
                R.string.reminder_notif_generic,
                com.plantdiseases.app.util.ReminderPresets.displayLabel(null, reminderType) {
                    localizedCtx.getString(it)
                },
                plantName,
            )
        }
        val body = reminder?.description?.trim()?.takeIf { it.isNotEmpty() }
            ?: com.plantdiseases.app.util.ReminderPresets.displayLabel(
                customLabel,
                reminderType,
            ) { localizedCtx.getString(it) }

        val contentIntent = PendingIntent.getActivity(
            context,
            notificationId,
            Intent(context, PlantDetailActivity::class.java).apply {
                putExtra(PlantDetailActivity.EXTRA_PLANT_ID, plantId)
            },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

        val builder = NotificationCompat.Builder(localizedCtx, PlantDiseasesApp.CHANNEL_PLANT_REMINDERS)
            .setSmallIcon(R.drawable.ic_care)
            .setContentTitle(title)
            .setContentText(body)
            .setContentIntent(contentIntent)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)

        // Done + Snooze actions are only useful when we know which
        // event to resolve. Legacy workers with no reminder row id keep a
        // plain notification; positive ids were authoritatively re-read above,
        // so deleted/disabled rows never reach this point.
        if (eventId > 0) {
            val doneIntent = ReminderActionReceiver.buildIntent(
                context,
                eventId,
                notificationId,
                ReminderActionReceiver.ACTION_DONE,
                plantId = plantId,
                reminderId = reminderId,
                type = reminderType,
                plantName = plantName,
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
                localizedCtx.getString(R.string.reminder_action_done),
                donePending,
            )
            builder.addAction(
                R.drawable.ic_clock,
                localizedCtx.getString(R.string.reminder_action_snooze),
                snoozePending,
            )
        }

        NotificationCenter.record(
            context,
            type = "reminder",
            title = title,
            body = body,
            deeplink = "plant:$plantId",
        )

        // On Android 13+ notify() silently no-ops without POST_NOTIFICATIONS.
        // Everything above (the pending event + the notification-centre row) has
        // already run on purpose, so a user who denied the permission still sees
        // the firing in-app. Return success so WorkManager doesn't keep retrying,
        // but log loudly so "reminders silently dropped" shows up in adb logcat
        // instead of looking like a missing alarm.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            android.util.Log.w(
                "PlantReminderWorker",
                "POST_NOTIFICATIONS missing — recorded the reminder in the " +
                    "notification centre but skipped the system notification " +
                    "for plant_id=$plantId type=$reminderType. Ask the user " +
                    "to re-grant the notification permission in Settings.",
            )
            return Result.success()
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
        const val KEY_EXISTING_EVENT_ID = "existing_event_id"

        const val TYPE_WATERING = "watering"
        const val TYPE_CHECK = "check"
        const val TYPE_FERTILIZE = "fertilize"
    }
}
