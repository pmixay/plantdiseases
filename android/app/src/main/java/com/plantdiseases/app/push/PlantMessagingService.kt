package com.plantdiseases.app.push

import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.auth.AuthRepository
import com.plantdiseases.app.auth.AuthState
import com.plantdiseases.app.util.OpinionPrefs
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import javax.inject.Inject

/**
 * Optional future FCM receiver. Version 1.9 ships with FCM configuration empty
 * and uses polling; this path stays fail-closed until a durable server sink is
 * implemented. A future server must send a DATA message so the client can bind
 * it to the currently authenticated actor before showing anything.
 *
 * Expected data payload: { type: "opinion", community_actor_id: <targetActor>,
 * scan_id: <clientScanId>,
 * disease: <class_key>, note: <text>, nickname: <responder handle> }.
 */
@AndroidEntryPoint
class PlantMessagingService : FirebaseMessagingService() {

    @Inject
    lateinit var authRepository: AuthRepository

    // Firebase deprecated onNewToken/getToken in firebase-messaging 25.1.0 in
    // favour of Firebase Installation IDs. Tokens still work and Google has
    // published no FID migration guide; moving would also mean changing
    // server/push.py (its push_tokens table is keyed by token) and the wire
    // format between them, so it is a coordinated change, not a lint fix.
    @Suppress("OVERRIDE_DEPRECATION")
    override fun onNewToken(token: String) {
        val ctx = applicationContext
        if (!OpinionPrefs.notificationsEnabled(ctx)) return
        val expectedSessionId = (authRepository.state.value as? AuthState.Authenticated)
            ?.session
            ?.sessionId
            ?: return
        // The Application is still needed for appScope (an app-lifecycle scope).
        val app = ctx as PlantDiseasesApp
        app.appScope.launch {
            if (!PushManager.registrationStillAllowed(ctx, authRepository.state.value, expectedSessionId)) {
                return@launch
            }
            authRepository.registerPush(token, expectedSessionId) {
                PushManager.registrationStillAllowed(ctx, authRepository.state.value, expectedSessionId)
            }
        }
    }

    override fun onMessageReceived(message: RemoteMessage) {
        val data = message.data
        if (data["type"] != "opinion") return
        // No notifications-enabled gate here: OpinionNotifier records this to
        // the notification centre regardless, and decides on its own whether
        // to also interrupt the user with a system notification. Still bind
        // every account-scoped push to the currently authenticated actor.
        if (
            !PushManager.notificationBelongsToAuthenticatedActor(
                data["community_actor_id"],
                authRepository.state.value,
            )
        ) {
            return
        }
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
