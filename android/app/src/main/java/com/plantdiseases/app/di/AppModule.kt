package com.plantdiseases.app.di

import com.plantdiseases.app.data.local.AppDatabase
import com.plantdiseases.app.data.remote.PlantApiClient
import com.plantdiseases.app.data.repository.CommunityUploader
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.util.ConnectivityObserver
import com.plantdiseases.app.util.OnboardingStateStore
import com.plantdiseases.app.util.ReachabilityReporter
import org.koin.android.ext.koin.androidContext
import org.koin.dsl.module

/**
 * Koin graph for the app-scoped singletons: the Room database, the Ktor
 * [PlantApiClient], the repositories over them, the community uploader and the
 * shared [ConnectivityObserver].
 * UI, workers and the Application resolve these via `inject()`/`get()`.
 */
val appModule = module {
    single { AppDatabase.getInstance(androidContext()) }

    // The reachability signal is wired as a separate singleton rather than
    // straight into the observer because the two ends need each other: the
    // HTTP client publishes call outcomes, and the observer's probe rides that
    // same client's connection pool. A standalone sink breaks the cycle.
    single { ReachabilityReporter() }

    single { PlantApiClient(reachabilityReporter = get()) }

    // One connectivity watcher for the whole app — the screens observe it
    // through OfflineNoticeView / enableWhileOnline instead of each polling
    // ConnectivityManager.
    single {
        ConnectivityObserver(
            context = androidContext(),
            probe = get<PlantApiClient>()::probeReachable,
            reports = get<ReachabilityReporter>().reports,
        )
    }

    single { OnboardingStateStore(androidContext()) }

    single {
        ScanRepository(
            get<AppDatabase>().scanDao(),
            get(),
            get<AppDatabase>().scanChatMessageDao(),
        )
    }

    single {
        PlantRepository(
            get<AppDatabase>().plantDao(),
            get<AppDatabase>().plantJournalDao(),
            get<AppDatabase>().scanDao(),
            get<AppDatabase>().plantReminderDao(),
            get<AppDatabase>().plantReminderEventDao(),
            get<AppDatabase>().chatMessageDao(),
        )
    }

    single { CommunityUploader(androidContext(), get()) }
}
