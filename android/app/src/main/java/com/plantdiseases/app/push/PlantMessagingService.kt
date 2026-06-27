package com.plantdiseases.app.push

import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.util.OpinionPrefs
import com.plantdiseases.app.util.TelemetryIds
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import org.koin.android.ext.android.inject

/**
 * Receives FCM pushes — the primary delivery path for "someone left a second
 * opinion on your scan", working with the app closed / phone idle. The server
 * sends a high-priority DATA message (not a notification message) so this runs
 * in every app state and we build the localized notification ourselves.
 *
 * Expected data payload: { type: "opinion", scan_id: <clientScanId>,
 * disease: <class_key> }.
 */
class PlantMessagingService : FirebaseMessagingService() {

    private val scanRepository: ScanRepository by inject()

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
        // onMessageReceived already runs off the main thread; the scan lookup +
        // notify is quick and well within FCM's processing window.
        runBlocking {
            OpinionNotifier.notifyNewOpinion(applicationContext, scanId, disease, note)
        }
    }
}
