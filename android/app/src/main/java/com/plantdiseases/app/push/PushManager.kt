package com.plantdiseases.app.push

import android.content.Context
import android.util.Log
import com.google.firebase.FirebaseApp
import com.google.firebase.FirebaseOptions
import com.google.firebase.messaging.FirebaseMessaging
import com.plantdiseases.app.BuildConfig
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.auth.AuthState
import com.plantdiseases.app.di.appEntryPoint
import com.plantdiseases.app.util.ForumNotificationPrefs
import kotlinx.coroutines.launch

/**
 * Firebase Cloud Messaging bootstrap + token registration.
 *
 * We deliberately don't ship google-services.json / the google-services
 * Gradle plugin, so Firebase is initialised manually from BuildConfig.FCM_*
 * (set at build time from your Firebase project). When those values are blank
 * — an unconfigured build — everything here no-ops and FCM stays dormant; the
 * WorkManager poll for community answers remains the fallback.
 *
 * A plain Kotlin `object` — not an Android framework component — so Hilt
 * can't field-inject it via `@AndroidEntryPoint`; it resolves its repository
 * via [com.plantdiseases.app.di.AppEntryPoint] instead, same as the workers.
 */
object PushManager {
    private const val TAG = "PushManager"

    private fun isConfigured(): Boolean =
        BuildConfig.FCM_APP_ID.isNotBlank() &&
            BuildConfig.FCM_API_KEY.isNotBlank() &&
            BuildConfig.FCM_PROJECT_ID.isNotBlank() &&
            BuildConfig.FCM_SENDER_ID.isNotBlank()

    /** Ensure the default FirebaseApp exists. Returns false when FCM isn't
     *  configured or init failed (the caller then relies on polling). */
    fun ensureFirebase(context: Context): Boolean {
        if (!isConfigured()) return false
        val ctx = context.applicationContext
        if (FirebaseApp.getApps(ctx).isNotEmpty()) return true
        return try {
            val options = FirebaseOptions.Builder()
                .setApplicationId(BuildConfig.FCM_APP_ID)
                .setApiKey(BuildConfig.FCM_API_KEY)
                .setProjectId(BuildConfig.FCM_PROJECT_ID)
                .setGcmSenderId(BuildConfig.FCM_SENDER_ID)
                .build()
            FirebaseApp.initializeApp(ctx, options)
            true
        } catch (_: Exception) {
            Log.w(TAG, "Firebase init failed")
            false
        }
    }

    /** Fetch the current FCM token and register it with the server, scoped to
     *  the community-answer notification switch (Profile).
     *  Safe to call on every app start / opt-in — the server upserts by token. */
    fun registerIfEnabled(context: Context) {
        val ctx = context.applicationContext
        if (!ForumNotificationPrefs.answersEnabled(ctx)) return
        val authRepository = appEntryPoint(ctx).authRepository()
        val expectedSessionId = (authRepository.state.value as? AuthState.Authenticated)
            ?.session
            ?.sessionId
            ?: return
        if (!ensureFirebase(ctx)) return
        // The Application is still needed for appScope (an app-lifecycle scope).
        val app = ctx as PlantDiseasesApp
        // Deprecated in favour of Firebase Installation IDs — see the note on
        // PlantMessagingService.onNewToken for why this stays on tokens.
        @Suppress("DEPRECATION")
        FirebaseMessaging.getInstance().token
            .addOnSuccessListener { token ->
                if (token.isNullOrBlank()) return@addOnSuccessListener
                if (!registrationStillAllowed(ctx, authRepository.state.value, expectedSessionId)) {
                    return@addOnSuccessListener
                }
                app.appScope.launch {
                    if (!registrationStillAllowed(ctx, authRepository.state.value, expectedSessionId)) {
                        return@launch
                    }
                    authRepository.registerPush(token, expectedSessionId) {
                        registrationStillAllowed(ctx, authRepository.state.value, expectedSessionId)
                    }
                }
            }
            .addOnFailureListener { Log.w(TAG, "FCM token fetch failed") }
    }

    internal fun registrationStillAllowed(
        context: Context,
        state: AuthState,
        expectedSessionId: String,
    ): Boolean = registrationStillAllowed(
        notificationsEnabled = ForumNotificationPrefs.answersEnabled(context),
        state = state,
        expectedSessionId = expectedSessionId,
    )

    internal fun registrationStillAllowed(
        notificationsEnabled: Boolean,
        state: AuthState,
        expectedSessionId: String,
    ): Boolean = notificationsEnabled &&
        (state as? AuthState.Authenticated)?.session?.sessionId == expectedSessionId

    internal fun notificationBelongsToAuthenticatedActor(
        targetCommunityActorId: String?,
        state: AuthState,
    ): Boolean {
        val currentActorId = (state as? AuthState.Authenticated)
            ?.profile
            ?.communityActorId
            ?: return false
        return !targetCommunityActorId.isNullOrBlank() && targetCommunityActorId == currentActorId
    }
}
