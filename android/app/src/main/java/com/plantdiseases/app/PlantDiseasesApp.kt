package com.plantdiseases.app

import android.app.Activity
import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.os.Build
import android.os.Bundle
import android.view.ViewGroup
import com.canhub.cropper.CropImageActivity
import com.plantdiseases.app.data.repository.CommunityUploader
import com.plantdiseases.app.di.appModule
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.OpinionPrefs
import com.plantdiseases.app.util.ThemeHelper
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import org.koin.android.ext.android.inject
import org.koin.android.ext.koin.androidContext
import org.koin.android.ext.koin.androidLogger
import org.koin.core.context.GlobalContext
import org.koin.core.context.startKoin

class PlantDiseasesApp : Application() {

    // All app-scoped singletons now live in Koin (see di/AppModule.kt) and are
    // obtained at the call sites via direct injection. The Application only
    // injects what it uses itself — the community uploader for
    // [launchCommunityUpload]. `inject()` is lazy, so nothing resolves before
    // startKoin() runs below.
    private val communityUploader: CommunityUploader by inject()

    /**
     * Application-scoped CoroutineScope for fire-and-forget work that
     * must survive the originating activity finishing — e.g. the
     * community-scan upload after the user navigates from the
     * analysis screen to the result screen. SupervisorJob isolates
     * each upload so one failure can't tear the scope down.
     */
    val appScope: CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    /**
     * Launch a community-scan upload outside the activity scope so it
     * survives the navigation to ResultActivity. Exceptions are
     * swallowed inside CommunityUploader; we only guard against
     * coroutine cancellation here.
     */
    fun launchCommunityUpload(
        scan: com.plantdiseases.app.data.local.ScanEntity,
        analysis: com.plantdiseases.app.data.model.AnalysisResponse,
    ) {
        appScope.launch {
            runCatching { communityUploader.uploadIfEnabled(scan, analysis) }
        }
    }

    override fun onCreate() {
        super.onCreate()
        // Start Koin before anything resolves an injected singleton. Guarded
        // with getOrNull() because Robolectric reinstantiates this Application
        // across test classes in one JVM, and a second startKoin() on the
        // process-wide GlobalContext would throw.
        //
        // NB: do NOT add workManagerFactory() here. It calls
        // WorkManager.initialize(), but the default androidx.startup
        // WorkManagerInitializer (still enabled — the manifest never removed it)
        // already initialised WorkManager from the InitializationProvider that
        // runs *before* Application.onCreate. The second init throws
        // "WorkManager is already initialized" and crashes every launch on a
        // real device. Robolectric doesn't run the startup provider, so the
        // double-init never fired in unit tests — it only crashed on-device.
        // Today's workers (OpinionCheckWorker, PlantReminderWorker) use the
        // default WorkerFactory and resolve their deps as KoinComponents
        // (`by inject()` off the global Koin), not Koin constructor injection,
        // so the Koin WorkerFactory isn't needed. If a future worker needs
        // constructor injection, re-add workManagerFactory() *and* remove the
        // default WorkManagerInitializer in AndroidManifest.xml so only one
        // party initialises WorkManager.
        if (GlobalContext.getOrNull() == null) {
            startKoin {
                androidLogger()
                androidContext(this@PlantDiseasesApp)
                modules(appModule)
            }
        }
        LocaleHelper.applyLocale(this)
        ThemeHelper.applySavedTheme(this)
        createNotificationChannels()
        // Persistent disk cache for repeatable server responses (care plans,
        // species info) — "fetched once, never fetched again".
        com.plantdiseases.app.util.ResponseCache.init(this)
        registerCropImageInsetFix()
        // NB: do NOT touch WorkManager here. The default androidx.startup
        // WorkManagerInitializer initialises it before onCreate, but calling
        // WorkManager.getInstance()/initialize() from Application.onCreate
        // breaks every Robolectric test that instantiates the app (the startup
        // provider doesn't run under Robolectric). Scheduling is kicked off
        // from MainActivity instead — the same on-demand pattern ReminderManager
        // uses.
    }

    /**
     * Give the cropper (com.canhub.cropper.CropImageActivity) a uniform inset
     * so the crop canvas never butts against the toolbar or the system nav bar.
     *
     * Why here and not in a layout/theme: the library launches CropImageActivity
     * via a hard-coded contract (no subclass hook), and its CropImageView ignores
     * its OWN padding when scaling the image (onLayout feeds applyImageMatrix the
     * full width/height). Padding the activity's android.R.id.content frame
     * instead shrinks the CropImageView itself, so the image is letterboxed
     * inside the inset and the bottom no longer touches the nav. This also makes
     * a tall portrait gallery image (which used to fill the full height) and a
     * 4:3 camera photo (already letterboxed) look consistent — both float in the
     * same padded frame.
     */
    private fun registerCropImageInsetFix() {
        registerActivityLifecycleCallbacks(object : ActivityLifecycleCallbacks {
            override fun onActivityCreated(activity: Activity, savedInstanceState: Bundle?) {
                if (activity !is CropImageActivity) return
                val content = activity.findViewById<ViewGroup>(android.R.id.content) ?: return
                val d = activity.resources.displayMetrics.density
                content.clipToPadding = true
                content.setPadding(
                    (16 * d).toInt(), // left
                    (12 * d).toInt(), // top (small gap under the toolbar)
                    (16 * d).toInt(), // right
                    (24 * d).toInt(), // bottom (clears the system nav bar)
                )
            }

            override fun onActivityStarted(activity: Activity) {}
            override fun onActivityResumed(activity: Activity) {}
            override fun onActivityPaused(activity: Activity) {}
            override fun onActivityStopped(activity: Activity) {}
            override fun onActivitySaveInstanceState(activity: Activity, outState: Bundle) {}
            override fun onActivityDestroyed(activity: Activity) {}
        })
    }

    private fun createNotificationChannels() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val nm = getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(
                NotificationChannel(
                    CHANNEL_PLANT_REMINDERS,
                    getString(R.string.channel_plant_reminders_name),
                    NotificationManager.IMPORTANCE_HIGH,
                ).apply { description = getString(R.string.channel_plant_reminders_desc) },
            )
            nm.createNotificationChannel(
                NotificationChannel(
                    CHANNEL_COMMUNITY,
                    getString(R.string.channel_community_name),
                    NotificationManager.IMPORTANCE_DEFAULT,
                ).apply { description = getString(R.string.channel_community_desc) },
            )
        }
    }

    /** Poll for new second opinions on shared scans roughly hourly. Called
     *  from MainActivity (not Application.onCreate — see note there).
     *  Without server push (FCM) this poll is the only way a reply reaches a
     *  closed app, so the interval is kept short with a flex window. UPDATE (not
     *  KEEP) so installs created under the old 6h schedule adopt the new one.
     *  Quiet: notifies only on a genuinely new opinion — see OpinionCheckWorker. */
    fun scheduleOpinionChecks() {
        if (!OpinionPrefs.notificationsEnabled(this)) return
        val request = androidx.work.PeriodicWorkRequestBuilder<
            com.plantdiseases.app.worker.OpinionCheckWorker
            >(
            1,
            java.util.concurrent.TimeUnit.HOURS,
            15,
            java.util.concurrent.TimeUnit.MINUTES,
        )
            .setConstraints(
                androidx.work.Constraints.Builder()
                    .setRequiredNetworkType(androidx.work.NetworkType.CONNECTED)
                    .build(),
            )
            .build()
        androidx.work.WorkManager.getInstance(this).enqueueUniquePeriodicWork(
            "opinion_check",
            androidx.work.ExistingPeriodicWorkPolicy.UPDATE,
            request,
        )
    }

    /** Fire an immediate, one-off opinion check — called when the app comes to
     *  the foreground (and right after the user asks for a second opinion) so a
     *  reply that landed while the app was closed surfaces promptly instead of
     *  waiting for the next periodic tick. Expedited, degrading to a normal job
     *  if the expedited quota is spent. */
    fun checkOpinionsNow() {
        if (!OpinionPrefs.notificationsEnabled(this)) return
        val request = androidx.work.OneTimeWorkRequestBuilder<
            com.plantdiseases.app.worker.OpinionCheckWorker
            >()
            .setConstraints(
                androidx.work.Constraints.Builder()
                    .setRequiredNetworkType(androidx.work.NetworkType.CONNECTED)
                    .build(),
            )
            .setExpedited(
                androidx.work.OutOfQuotaPolicy.RUN_AS_NON_EXPEDITED_WORK_REQUEST,
            )
            .build()
        androidx.work.WorkManager.getInstance(this).enqueueUniqueWork(
            "opinion_check_now",
            androidx.work.ExistingWorkPolicy.REPLACE,
            request,
        )
    }

    companion object {
        const val CHANNEL_PLANT_REMINDERS = "plant_reminders"
        const val CHANNEL_COMMUNITY = "community"
    }
}
