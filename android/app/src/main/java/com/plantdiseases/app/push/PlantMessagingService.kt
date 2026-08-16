package com.plantdiseases.app.push

import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.util.OpinionPrefs
import com.plantdiseases.app.util.TelemetryIds
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import javax.inject.Inject

/**
 * Receives FCM pushes — the primary delivery path for "someone left a second
 * opinion on your scan", working with the app closed / phone idle. The server
 * sends a high-priority DATA message (not a notification message) so this runs
 * in every app state and we build the localized notification ourselves.
 *
 * Expected data payload: { type: "opinion", scan_id: <clientScanId>,
 * disease: <class_key>, note: <text>, nickname: <responder handle> }.
 */
@AndroidEntryPoint
class PlantMessagingService : FirebaseMessagingService() {

    @Inject
    lateinit var scanRepository: ScanRepository

    override fun onNewToken(token: String) {
        val ctx = applicationContext
        if (!OpinionPrefs.notificationsEnabled(ctx)) return
        // The Application is still needed for appScope (an app-lifecycle scope).
        val app = ctx as PlantDiseasesApp
        app.appScope.launch {
            runCatching {
                scanRepository.registerPushToken(TelemetryIds.userPseudoId(ctx), token)
            }
        }
    }

    override fun onMessageReceived(message: RemoteMessage) {
        val data = message.data
        if (data["type"] != "opinion") return
        if (!OpinionPrefs.notificationsEnabled(applicationContext)) return
        val scanId = data["scan_id"].orEmpty()
        if (scanId.isBlank()) return
        val disease = data["disease"].orEmpty()
        val note = data["note"].orEmpty()
        val nickname = data["nickname"].orEmpty()
        // onMessageReceived already runs off the main thread; the scan lookup +
        // notify is quick and well within FCM's processing window.
        runBlocking {
            OpinionNotifier.notifyNewOpinion(applicationContext, scanId, disease, note, nickname)
            // Advance the poll baseline, exactly as OpinionCheckWorker does after
            // it notifies. Without this the next poll still sees
            // opinionCount > lastSeen and records a SECOND notification-centre row
            // for the same opinion (NotificationRepository.record is a bare
            // @Insert with no dedupe key). The push payload carries no count, so
            // an increment is the only option — and it self-heals, because the
            // next poll rewrites the baseline to the scan's true opinionCount.
            OpinionPrefs.setSeen(
                applicationContext,
                scanId,
                OpinionPrefs.lastSeen(applicationContext, scanId) + 1,
            )
        }
    }
}
