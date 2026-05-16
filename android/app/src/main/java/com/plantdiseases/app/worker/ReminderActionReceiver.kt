package com.plantdiseases.app.worker

import android.app.NotificationManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.plantdiseases.app.PlantDiseasesApp
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

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
class ReminderActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.getStringExtra(EXTRA_ACTION) ?: return
        val eventId = intent.getLongExtra(EXTRA_EVENT_ID, -1L)
        val notificationId = intent.getIntExtra(EXTRA_NOTIFICATION_ID, -1)
        if (eventId < 0) return

        val app = context.applicationContext as PlantDiseasesApp
        val resolution = when (action) {
            ACTION_DONE -> "done"
            ACTION_SNOOZE -> "snoozed"
            else -> return
        }
        // Receivers run on the main thread; persist on IO. ``goAsync``
        // is the textbook pattern but adds bookkeeping — for a single
        // 5-ms SQL write a fire-and-forget coroutine is cheaper and
        // doesn't risk holding the broadcast slot.
        CoroutineScope(Dispatchers.IO).launch {
            app.plantRepository.resolveReminderEvent(eventId, resolution)
        }

        if (notificationId >= 0) {
            context.getSystemService(NotificationManager::class.java)
                ?.cancel(notificationId)
        }
    }

    companion object {
        const val ACTION_DONE = "com.plantdiseases.action.REMINDER_DONE"
        const val ACTION_SNOOZE = "com.plantdiseases.action.REMINDER_SNOOZE"

        private const val EXTRA_ACTION = "extra_action"
        private const val EXTRA_EVENT_ID = "extra_event_id"
        private const val EXTRA_NOTIFICATION_ID = "extra_notification_id"

        fun buildIntent(
            context: Context,
            eventId: Long,
            notificationId: Int,
            action: String,
        ): Intent = Intent(context, ReminderActionReceiver::class.java).apply {
            this.action = action
            putExtra(EXTRA_ACTION, action)
            putExtra(EXTRA_EVENT_ID, eventId)
            putExtra(EXTRA_NOTIFICATION_ID, notificationId)
        }
    }
}
