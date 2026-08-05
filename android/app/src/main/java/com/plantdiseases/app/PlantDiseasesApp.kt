package com.plantdiseases.app

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
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
    private val scanRepository:
        com.plantdiseases.app.data.repository.ScanRepository by inject()

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

    /**
     * Wire up error/problem telemetry: register the (consent-gated, best-effort)
     * sender, flush any crash persisted from a previous run, then install the
     * uncaught-exception handler. Errors-only — nothing is sent on the happy
     * path. Reuses the same data consent as the rest of model improvement.
     */
    private fun setupTelemetry() {
        com.plantdiseases.app.util.TelemetryReporter.init(
            sender = { report ->
                appScope.launch { runCatching { scanRepository.reportError(report) } }
            },
            consent = {
                com.plantdiseases.app.ui.onboarding.OnboardingActivity.isDataConsentGiven(this)
            },
        )
        flushPendingCrash()
        installCrashHandler()
    }

    /** Persist a fatal crash (with its breadcrumb trail) so the NEXT launch can
     *  report it — the process is dying, so a network call here is unreliable. */
    private fun installCrashHandler() {
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, error ->
            runCatching {
                getSharedPreferences(TELEMETRY_PREFS, MODE_PRIVATE).edit()
                    .putString(KEY_CRASH_KIND, error.javaClass.simpleName)
                    .putString(KEY_CRASH_MESSAGE, error.stackTraceToString().take(2000))
                    .putString(
                        KEY_CRASH_CRUMBS,
                        com.plantdiseases.app.util.TelemetryReporter.breadcrumbs()
                            .joinToString("\n"),
                    )
                    .commit() // sync — the process is about to exit
            }
            previous?.uncaughtException(thread, error)
        }
    }

    private fun flushPendingCrash() {
        val prefs = getSharedPreferences(TELEMETRY_PREFS, MODE_PRIVATE)
        val kind = prefs.getString(KEY_CRASH_KIND, null) ?: return
        val message = prefs.getString(KEY_CRASH_MESSAGE, null)
        val crumbs = prefs.getString(KEY_CRASH_CRUMBS, null)
            ?.split('\n')?.filter { it.isNotBlank() }
        prefs.edit()
            .remove(KEY_CRASH_KIND).remove(KEY_CRASH_MESSAGE).remove(KEY_CRASH_CRUMBS)
            .apply()
        if (!com.plantdiseases.app.ui.onboarding.OnboardingActivity.isDataConsentGiven(this)) {
            return
        }
        appScope.launch {
            runCatching {
                scanRepository.reportError(
                    com.plantdiseases.app.data.model.ErrorReport(
                        stage = "crash",
                        kind = kind,
                        fatal = true,
                        appVersion = com.plantdiseases.app.BuildConfig.VERSION_NAME,
                        androidApi = android.os.Build.VERSION.SDK_INT,
                        message = message,
                        breadcrumbs = crumbs,
                    ),
                )
            }
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
        setupTelemetry()
        purgeStaleRejectedScans()
        // NB: do NOT touch WorkManager here. The default androidx.startup
        // WorkManagerInitializer initialises it before onCreate, but calling
        // WorkManager.getInstance()/initialize() from Application.onCreate
        // breaks every Robolectric test that instantiates the app (the startup
        // provider doesn't run under Robolectric). Scheduling is kicked off
        // from MainActivity instead — the same on-demand pattern ReminderManager
        // uses.
    }

    /**
     * Sweep stale `not_a_plant` / blur-reject rows on every launch. NAP
     * verdicts DO get a full result screen (so the user can flag "this is
     * a plant"), and the persisted row backs that screen — but NAP scans
     * are hidden from the gallery and stats, so once the moment passes the
     * row is unreachable dead weight. Rows older than 48 h are deleted;
     * the table is small, so the sweep is cheap.
     */
    private fun purgeStaleRejectedScans() {
        appScope.launch {
            runCatching { scanRepository.purgeNotAPlantScans() }
        }
    }

    private fun createNotificationChannels() {
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

    /** Warm Glide's disk cache with the guide/disease example photos so they
     *  render offline after first launch online. One-time + CONNECTED
     *  constraint; KEEP so a launch during an in-flight prefetch doesn't stack
     *  duplicates. Called from MainActivity (not Application.onCreate — same
     *  WorkManager-timing note as the opinion poll). */
    fun prefetchGuidePhotos() {
        val request = androidx.work.OneTimeWorkRequestBuilder<
            com.plantdiseases.app.worker.GuidePhotoPrefetchWorker
            >()
            .setConstraints(
                androidx.work.Constraints.Builder()
                    .setRequiredNetworkType(androidx.work.NetworkType.CONNECTED)
                    .build(),
            )
            .build()
        androidx.work.WorkManager.getInstance(this).enqueueUniqueWork(
            "guide_photo_prefetch",
            androidx.work.ExistingWorkPolicy.KEEP,
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

        // Where a fatal crash is stashed for reporting on the next launch.
        private const val TELEMETRY_PREFS = "plantdiseases_telemetry"
        private const val KEY_CRASH_KIND = "crash_kind"
        private const val KEY_CRASH_MESSAGE = "crash_message"
        private const val KEY_CRASH_CRUMBS = "crash_crumbs"
    }
}
