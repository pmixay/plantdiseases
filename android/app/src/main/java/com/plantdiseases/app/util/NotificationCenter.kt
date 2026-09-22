package com.plantdiseases.app.util

import android.content.Context
import com.plantdiseases.app.di.appEntryPoint

/**
 * Records one row in the in-app notification centre. Separate from whether
 * the system notification actually posted — a producer calls this whether
 * or not `notify()` ran, so a user who denied POST_NOTIFICATIONS (or is on
 * API < 33 without ever seeing the permission prompt) still sees the event
 * inside the app.
 *
 * Call this from every producer of a user-facing notification:
 *   • [com.plantdiseases.app.push.OpinionNotifier] — type "community"
 *   • [com.plantdiseases.app.worker.PlantReminderWorker] — type "reminder"
 *   • [com.plantdiseases.app.ui.garden.detail.DetailExtraCardsController]
 *     (progression alert) — type "garden"
 * A new producer must call [record] too — nothing else discovers it.
 */
object NotificationCenter {
    suspend fun record(context: Context, type: String, title: String, body: String, deeplink: String? = null) {
        appEntryPoint(context).notificationRepository().record(type, title, body, deeplink)
    }
}
