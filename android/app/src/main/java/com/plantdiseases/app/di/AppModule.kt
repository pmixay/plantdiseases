package com.plantdiseases.app.di

import android.content.Context
import com.plantdiseases.app.data.local.AppDatabase
import com.plantdiseases.app.data.remote.PlantApiClient
import com.plantdiseases.app.data.repository.CareRepository
import com.plantdiseases.app.data.repository.CommunityUploader
import com.plantdiseases.app.data.repository.NotificationRepository
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.data.repository.RoomRepository
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.util.ConnectivityObserver
import com.plantdiseases.app.util.OnboardingStateStore
import com.plantdiseases.app.util.ReachabilityReporter
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import javax.inject.Singleton

/**
 * Hilt graph for the app-scoped singletons: the Room database, the Ktor
 * [PlantApiClient], the repositories over them, the community uploader and the
 * shared [ConnectivityObserver].
 * UI resolves these via `@Inject lateinit var`; workers, the push-package
 * singleton objects and [com.plantdiseases.app.ui.common.SpeciesInfoCardView]
 * resolve them via [AppEntryPoint] instead (see that file for why).
 */
@Module
@InstallIn(SingletonComponent::class)
object AppModule {

    @Provides
    @Singleton
    fun provideAppDatabase(@ApplicationContext context: Context): AppDatabase =
        AppDatabase.getInstance(context)

    // The reachability signal is wired as a separate singleton rather than
    // straight into the observer because the two ends need each other: the
    // HTTP client publishes call outcomes, and the observer's probe rides that
    // same client's connection pool. A standalone sink breaks the cycle.
    @Provides
    @Singleton
    fun provideReachabilityReporter(): ReachabilityReporter = ReachabilityReporter()

    @Provides
    @Singleton
    fun providePlantApiClient(reachabilityReporter: ReachabilityReporter): PlantApiClient =
        PlantApiClient(reachabilityReporter = reachabilityReporter)

    // One connectivity watcher for the whole app — the screens observe it
    // through OfflineNoticeView / enableWhileOnline instead of each polling
    // ConnectivityManager.
    @Provides
    @Singleton
    fun provideConnectivityObserver(
        @ApplicationContext context: Context,
        apiClient: PlantApiClient,
        reachabilityReporter: ReachabilityReporter,
    ): ConnectivityObserver = ConnectivityObserver(
        context = context,
        probe = apiClient::probeReachable,
        reports = reachabilityReporter.reports,
    )

    @Provides
    @Singleton
    fun provideOnboardingStateStore(@ApplicationContext context: Context): OnboardingStateStore =
        OnboardingStateStore(context)

    @Provides
    @Singleton
    fun provideScanRepository(
        database: AppDatabase,
        apiClient: PlantApiClient,
    ): ScanRepository = ScanRepository(
        database.scanDao(),
        apiClient,
        database.scanChatMessageDao(),
    )

    @Provides
    @Singleton
    fun providePlantRepository(database: AppDatabase): PlantRepository = PlantRepository(
        database.plantDao(),
        database.plantJournalDao(),
        database.scanDao(),
        database.plantReminderDao(),
        database.plantReminderEventDao(),
        database.chatMessageDao(),
        database.roomDao(),
        database.treatmentPlanDao(),
    )

    @Provides
    @Singleton
    fun provideRoomRepository(database: AppDatabase): RoomRepository = RoomRepository(
        database.roomDao(),
        database.plantDao(),
        database.scanDao(),
    )

    @Provides
    @Singleton
    fun provideCareRepository(
        database: AppDatabase,
        plantRepository: PlantRepository,
    ): CareRepository = CareRepository(
        plantDao = database.plantDao(),
        roomDao = database.roomDao(),
        scanDao = database.scanDao(),
        reminderDao = database.plantReminderDao(),
        reminderEventDao = database.plantReminderEventDao(),
        journalDao = database.plantJournalDao(),
        treatmentPlanDao = database.treatmentPlanDao(),
        treatmentStepDao = database.treatmentStepDao(),
        careTaskDao = database.careTaskDao(),
        plantRepository = plantRepository,
    )

    @Provides
    @Singleton
    fun provideCommunityUploader(
        @ApplicationContext context: Context,
        scanRepository: ScanRepository,
    ): CommunityUploader = CommunityUploader(context, scanRepository)

    @Provides
    @Singleton
    fun provideNotificationRepository(database: AppDatabase): NotificationRepository =
        NotificationRepository(database.notificationDao())
}
