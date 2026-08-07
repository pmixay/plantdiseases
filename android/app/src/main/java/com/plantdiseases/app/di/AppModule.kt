package com.plantdiseases.app.di

import android.content.Context
import com.plantdiseases.app.auth.AndroidKeystoreRefreshTokenVault
import com.plantdiseases.app.auth.AuthRepository
import com.plantdiseases.app.auth.InstallationIdProvider
import com.plantdiseases.app.auth.InstallationIdStore
import com.plantdiseases.app.auth.OfficialProviderSessionCleaner
import com.plantdiseases.app.auth.ProviderSessionCleaner
import com.plantdiseases.app.auth.RefreshTokenVault
import com.plantdiseases.app.data.account.AccountApi
import com.plantdiseases.app.data.account.AccountApiClient
import com.plantdiseases.app.data.local.AppDatabase
import com.plantdiseases.app.data.remote.PlantApiClient
import com.plantdiseases.app.data.repository.CommunityUploader
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.util.ConnectivityObserver
import com.plantdiseases.app.util.OnboardingStateStore
import com.plantdiseases.app.util.PrivacyConsentPrefs
import com.plantdiseases.app.util.ReachabilityReporter
import com.plantdiseases.app.util.DefaultUploadImageSanitizer
import com.plantdiseases.app.util.FeedbackReceiptStore
import com.plantdiseases.app.util.SharedPreferencesFeedbackReceiptStore
import com.plantdiseases.app.util.UploadImageSanitizer
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
    fun provideAccountApi(): AccountApi = AccountApiClient()

    @Provides
    @Singleton
    fun provideRefreshTokenVault(@ApplicationContext context: Context): RefreshTokenVault =
        AndroidKeystoreRefreshTokenVault(context)

    @Provides
    @Singleton
    fun provideInstallationId(@ApplicationContext context: Context): InstallationIdProvider =
        InstallationIdStore(context)

    @Provides
    @Singleton
    fun provideProviderSessionCleaner(): ProviderSessionCleaner = OfficialProviderSessionCleaner()

    @Provides
    @Singleton
    fun provideAuthRepository(
        accountApi: AccountApi,
        refreshTokenVault: RefreshTokenVault,
        installationId: InstallationIdProvider,
        providerSessionCleaner: ProviderSessionCleaner,
    ): AuthRepository = AuthRepository(
        accountApi,
        refreshTokenVault,
        installationId,
        providerSessionCleaner,
    )

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
    fun providePrivacyConsentPrefs(@ApplicationContext context: Context): PrivacyConsentPrefs =
        PrivacyConsentPrefs(context)

    @Provides
    @Singleton
    fun provideUploadImageSanitizer(): UploadImageSanitizer = DefaultUploadImageSanitizer

    @Provides
    @Singleton
    fun provideFeedbackReceiptStore(@ApplicationContext context: Context): FeedbackReceiptStore =
        SharedPreferencesFeedbackReceiptStore(context)

    @Provides
    @Singleton
    fun provideScanRepository(
        database: AppDatabase,
        apiClient: PlantApiClient,
        imageSanitizer: UploadImageSanitizer,
        feedbackReceiptStore: FeedbackReceiptStore,
        authRepository: AuthRepository,
    ): ScanRepository = ScanRepository(
        database.scanDao(),
        apiClient,
        database.scanChatMessageDao(),
        imageSanitizer,
        feedbackReceiptStore,
        authRepository,
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
    )

    @Provides
    @Singleton
    fun provideCommunityUploader(
        @ApplicationContext context: Context,
        scanRepository: ScanRepository,
        authRepository: AuthRepository,
        imageSanitizer: UploadImageSanitizer,
    ): CommunityUploader = CommunityUploader(
        context,
        scanRepository,
        authRepository,
        imageSanitizer,
    )
}
