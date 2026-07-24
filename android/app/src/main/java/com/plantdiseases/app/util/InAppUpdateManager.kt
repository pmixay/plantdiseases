package com.plantdiseases.app.util

import android.content.Context
import android.util.Log
import android.view.View
import com.google.android.material.snackbar.Snackbar
import com.plantdiseases.app.R
import ru.rustore.sdk.appupdate.listener.InstallStateUpdateListener
import ru.rustore.sdk.appupdate.manager.RuStoreAppUpdateManager
import ru.rustore.sdk.appupdate.manager.factory.RuStoreAppUpdateManagerFactory
import ru.rustore.sdk.appupdate.model.AppUpdateInfo
import ru.rustore.sdk.appupdate.model.AppUpdateOptions
import ru.rustore.sdk.appupdate.model.AppUpdateType
import ru.rustore.sdk.appupdate.model.InstallState
import ru.rustore.sdk.appupdate.model.InstallStatus
import ru.rustore.sdk.appupdate.model.UpdateAvailability
import java.util.concurrent.TimeUnit

/**
 * RuStore in-app updates, flexible flow (rustore.ru/help/guides/sdk-in-app-updates).
 *
 * On installs that came from RuStore, [checkForUpdates] asks the store app
 * whether a newer build is published and, at most once per
 * [PROMPT_INTERVAL_MS], hands off to RuStore's own update dialog. The APK
 * downloads in the background (the store shows its own progress UI); once it
 * lands — this launch or a later one — a snackbar offers to install, and
 * [RuStoreAppUpdateManager.completeUpdate] closes the app and applies it.
 *
 * Everywhere else — sideloads, other stores, devices without the RuStore app,
 * an unauthorized store user — the SDK reports failure and the launch carries
 * on untouched, matching the app's graceful-degradation pattern for optional
 * integrations. The SDK talks to the store app over IPC and can misbehave on
 * exotic firmware, so even manager *creation* stays inside the runCatching:
 * the update check must never be able to take a launch down.
 */
class InAppUpdateManager(
    context: Context,
    private val hostView: View,
    private val snackbarAnchor: View? = null,
) {
    private val appContext = context.applicationContext
    private val prefs = appContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    /** Created lazily by [checkForUpdates] so a broken store binding surfaces
     *  there (and is swallowed there) instead of in the constructor. */
    private var manager: RuStoreAppUpdateManager? = null

    // An object (not a SAM lambda) so register/unregister see one identity.
    private val installListener = object : InstallStateUpdateListener {
        override fun onStateUpdated(installState: InstallState) {
            when (installState.installStatus) {
                InstallStatus.DOWNLOADED -> showInstallSnackbar()
                InstallStatus.FAILED -> Log.w(
                    TAG,
                    "Update download failed (code ${installState.installErrorCode})",
                )
                else -> Unit
            }
        }
    }

    /** Fire-and-forget update check; call once per launch of the main screen. */
    fun checkForUpdates() {
        runCatching {
            val mgr = manager ?: RuStoreAppUpdateManagerFactory.create(appContext).also {
                manager = it
                it.registerListener(installListener)
            }
            mgr.getAppUpdateInfo()
                .addOnSuccessListener { info -> onUpdateInfo(mgr, info) }
                // Expected on every non-RuStore install (RuStoreNotInstalledException
                // & friends) — debug-log and move on.
                .addOnFailureListener { e -> Log.d(TAG, "Update info unavailable", e) }
        }.onFailure { e -> Log.w(TAG, "RuStore appupdate SDK unavailable", e) }
    }

    /** Drop the store-app listener; call from the host activity's onDestroy. */
    fun release() {
        runCatching { manager?.unregisterListener(installListener) }
        manager = null
    }

    private fun onUpdateInfo(mgr: RuStoreAppUpdateManager, info: AppUpdateInfo) {
        when {
            // A download finished in a previous session but was never applied —
            // skip straight to the install offer (never throttled: no dialog,
            // and the alternative is running a stale build forever).
            info.installStatus == InstallStatus.DOWNLOADED -> showInstallSnackbar()

            info.updateAvailability == UpdateAvailability.UPDATE_AVAILABLE &&
                shouldPromptNow() -> startUpdateFlow(mgr, info)

            else -> Unit
        }
    }

    private fun startUpdateFlow(mgr: RuStoreAppUpdateManager, info: AppUpdateInfo) {
        // Marked before the result comes back: a decline ("later") in RuStore's
        // dialog must count as a prompt, or the user would be re-asked on the
        // very next launch.
        markPrompted()
        mgr.startUpdateFlow(
            info,
            AppUpdateOptions.Builder().appUpdateType(AppUpdateType.FLEXIBLE).build(),
        ).addOnFailureListener { e -> Log.w(TAG, "startUpdateFlow failed", e) }
        // Success just means the dialog resolved (RESULT_OK = user accepted,
        // RESULT_CANCELED = declined); the download itself reports through
        // installListener.
    }

    private fun showInstallSnackbar() {
        Snackbar.make(hostView, R.string.update_ready, Snackbar.LENGTH_INDEFINITE)
            .setAction(R.string.update_ready_action) { completeUpdate() }
            .also { bar -> snackbarAnchor?.let(bar::setAnchorView) }
            .show()
    }

    private fun completeUpdate() {
        manager?.completeUpdate(
            AppUpdateOptions.Builder().appUpdateType(AppUpdateType.FLEXIBLE).build(),
        )?.addOnFailureListener { e -> Log.w(TAG, "completeUpdate failed", e) }
        // On success RuStore closes the app and installs the downloaded APK.
    }

    /** True when the last flexible-update offer is over [PROMPT_INTERVAL_MS]
     *  old. A clock rolled back past the stored stamp also prompts — otherwise
     *  a device time change could silence update offers for years. */
    internal fun shouldPromptNow(now: Long = System.currentTimeMillis()): Boolean {
        val elapsed = now - prefs.getLong(KEY_LAST_PROMPT, 0L)
        return elapsed < 0 || elapsed >= PROMPT_INTERVAL_MS
    }

    internal fun markPrompted(now: Long = System.currentTimeMillis()) {
        prefs.edit().putLong(KEY_LAST_PROMPT, now).apply()
    }

    companion object {
        private const val TAG = "InAppUpdateManager"
        private const val PREFS_NAME = "plantdiseases_prefs"
        private const val KEY_LAST_PROMPT = "rustore_update_last_prompt_ms"
        private val PROMPT_INTERVAL_MS = TimeUnit.HOURS.toMillis(24)
    }
}
