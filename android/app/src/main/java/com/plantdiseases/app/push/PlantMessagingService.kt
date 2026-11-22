package com.plantdiseases.app.push

import com.google.firebase.messaging.FirebaseMessagingService
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.auth.AuthRepository
import com.plantdiseases.app.auth.AuthState
import com.plantdiseases.app.util.ForumNotificationPrefs
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * Optional future FCM receiver. Builds ship with FCM configuration empty and
 * poll instead (community answers: ForumAnswerCheckWorker); this path stays
 * fail-closed until a durable server sink is implemented. It registers the
 * token so a future server can reach the device, and handles no message type
 * yet: a future one must be a DATA message bound to the currently
 * authenticated actor ([PushManager.notificationBelongsToAuthenticatedActor])
 * before anything is shown.
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
        if (!ForumNotificationPrefs.answersEnabled(ctx)) return
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
}
