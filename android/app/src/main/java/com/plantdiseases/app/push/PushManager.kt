package com.plantdiseases.app.push

import android.content.Context
import android.util.Log
import com.google.firebase.FirebaseApp
import com.google.firebase.FirebaseOptions
import com.google.firebase.messaging.FirebaseMessaging
import com.plantdiseases.app.BuildConfig
import com.plantdiseases.app.PlantDiseasesApp
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.util.CommunityPrefs
import com.plantdiseases.app.util.TelemetryIds
import kotlinx.coroutines.launch
import org.koin.core.component.KoinComponent
import org.koin.core.component.inject

/**
 * Firebase Cloud Messaging bootstrap + token registration.
 *
 * We deliberately don't ship google-services.json / the google-services
 * Gradle plugin, so Firebase is initialised manually from BuildConfig.FCM_*
 * (set at build time from your Firebase project). When those values are blank
 * — an unconfigured build — everything here no-ops and FCM stays dormant; the
 * WorkManager opinion poll remains the fallback.
 */
object PushManager : KoinComponent {
    private const val TAG = "PushManager"

    private val scanRepository: ScanRepository by inject()

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
        } catch (e: Exception) {
            Log.w(TAG, "Firebase init failed: ${e.message}")
            false
        }
    }

    /** Fetch the current FCM token and register it with the server, scoped to
     *  the community opt-in (no point registering a user who hasn't joined).
     *  Safe to call on every app start / opt-in — the server upserts by token. */
    fun registerIfEnabled(context: Context) {
        val ctx = context.applicationContext
        if (!CommunityPrefs.uploadsEnabled(ctx)) return
        if (!ensureFirebase(ctx)) return
        // The Application is still needed for appScope (an app-lifecycle scope).
        val app = ctx as PlantDiseasesApp
        FirebaseMessaging.getInstance().token
            .addOnSuccessListener { token ->
                if (token.isNullOrBlank()) return@addOnSuccessListener
                app.appScope.launch {
                    runCatching {
                        scanRepository.registerPushToken(
                            TelemetryIds.userPseudoId(ctx),
                            token,
                        )
                    }
                }
            }
            .addOnFailureListener { e -> Log.w(TAG, "FCM token fetch failed: ${e.message}") }
    }
}
