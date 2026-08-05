package com.plantdiseases.app.worker

import android.app.NotificationManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.util.ReminderManager
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import org.koin.core.component.KoinComponent
import org.koin.core.component.inject

/**
 * Handles the "Done" / "Snooze" action buttons on a reminder
 * notification. Both intents carry the plant_reminder_events row id
 * and the originating notification id; the receiver resolves the
 * event in the repository (which back-fills a journal entry for
 * "done") and then dismisses the notification.
 *
 * Runs on the system-managed broadcast thread; the actual repository
 * write hops onto Dispatchers.IO via [CoroutineScope].
 */
class ReminderActionReceiver : BroadcastReceiver(), KoinComponent {

    private val plantRepository: PlantRepository by inject()

    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.getStringExtra(EXTRA_ACTION) ?: return
        val eventId = intent.getLongExtra(EXTRA_EVENT_ID, -1L)
        val notificationId = intent.getIntExtra(EXTRA_NOTIFICATION_ID, -1)
        if (eventId < 0) return

        val resolution = when (action) {
            ACTION_DONE -> "done"
            ACTION_SNOOZE -> "snoozed"
            else -> return
        }
        // Dismiss the tapped notification immediately (instantaneous, no I/O).
        if (notificationId >= 0) {
            context.getSystemService(NotificationManager::class.java)
                ?.cancel(notificationId)
        }

        // "Snooze 1d": bring this reminder back in 24h (one-shot; does not
        // alter the periodic schedule). The re-fire data rides on the intent.
        if (action == ACTION_SNOOZE) {
            val plantId = intent.getLongExtra(EXTRA_PLANT_ID, -1L)
            val reminderId = intent.getLongExtra(EXTRA_REMINDER_ID, -1L)
            val type = intent.getStringExtra(EXTRA_REMINDER_TYPE)
            val plantName = intent.getStringExtra(EXTRA_PLANT_NAME)
            if (plantId > 0 && !type.isNullOrBlank() && !plantName.isNullOrBlank()) {
                ReminderManager.snooze(context, plantId, plantName, type, reminderId)
            }
        }

        // Persist off the main thread, but hold the broadcast open with
        // goAsync() so the OS does not kill the process before the write
        // (and, for "done", its journal back-fill) lands. A bare
        // fire-and-forget coroutine can be dropped when the app is in the
        // background, silently losing the user's Done/Snooze.
        val pending = goAsync()
        CoroutineScope(Dispatchers.IO).launch {
            try {
                plantRepository.resolveReminderEvent(eventId, resolution)
            } finally {
                pending.finish()
            }
        }
    }

    companion object {
        const val ACTION_DONE = "com.plantdiseases.action.REMINDER_DONE"
        const val ACTION_SNOOZE = "com.plantdiseases.action.REMINDER_SNOOZE"

        private const val EXTRA_ACTION = "extra_action"
        private const val EXTRA_EVENT_ID = "extra_event_id"
        private const val EXTRA_NOTIFICATION_ID = "extra_notification_id"
        private const val EXTRA_PLANT_ID = "extra_plant_id"
        private const val EXTRA_REMINDER_ID = "extra_reminder_id"
        private const val EXTRA_REMINDER_TYPE = "extra_reminder_type"
        private const val EXTRA_PLANT_NAME = "extra_plant_name"

        fun buildIntent(
            context: Context,
            eventId: Long,
            notificationId: Int,
            action: String,
            // Carried only for the Snooze action so the receiver can re-enqueue
            // the reminder; harmless (and ignored) for Done.
            plantId: Long = -1L,
            reminderId: Long = -1L,
            type: String? = null,
            plantName: String? = null,
        ): Intent = Intent(context, ReminderActionReceiver::class.java).apply {
            this.action = action
            putExtra(EXTRA_ACTION, action)
            putExtra(EXTRA_EVENT_ID, eventId)
            putExtra(EXTRA_NOTIFICATION_ID, notificationId)
            putExtra(EXTRA_PLANT_ID, plantId)
            putExtra(EXTRA_REMINDER_ID, reminderId)
            putExtra(EXTRA_REMINDER_TYPE, type)
            putExtra(EXTRA_PLANT_NAME, plantName)
        }
    }
}
