package com.plantdiseases.app.di

import android.content.Context
import com.plantdiseases.app.BuildConfig
import com.plantdiseases.app.auth.AccountEndpointScope
import com.plantdiseases.app.auth.AccountLogoutEndpointRegistry
import com.plantdiseases.app.auth.AdditionalLogoutRevocationTargets
import com.plantdiseases.app.auth.AndroidLogoutRevocationStore
import com.plantdiseases.app.auth.AndroidKeystoreRefreshTokenVault
import com.plantdiseases.app.auth.AndroidKeystoreSignupCompletionStore
import com.plantdiseases.app.auth.AuthRepository
import com.plantdiseases.app.auth.InstallationIdProvider
import com.plantdiseases.app.auth.InstallationIdStore
import com.plantdiseases.app.auth.LogoutRevocationStore
import com.plantdiseases.app.auth.LogoutRevocationTarget
import com.plantdiseases.app.auth.LogoutRetryScheduler
import com.plantdiseases.app.auth.OfficialProviderSessionCleaner
import com.plantdiseases.app.auth.ProviderSessionCleaner
import com.plantdiseases.app.auth.RefreshTokenVault
import com.plantdiseases.app.auth.SignupCompletionStore
import com.plantdiseases.app.billing.ProPaymentClient
import com.plantdiseases.app.billing.RuStorePayConfiguration
import com.plantdiseases.app.billing.RuStoreProPaymentClient
import com.plantdiseases.app.data.account.AccountApi
import com.plantdiseases.app.data.account.AccountApiClient
import com.plantdiseases.app.data.local.AppDatabase
import com.plantdiseases.app.data.remote.PlantApiClient
import com.plantdiseases.app.data.repository.CareRepository
import com.plantdiseases.app.data.repository.CommunityUploader
import com.plantdiseases.app.data.repository.NotificationRepository
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.data.repository.RoomRepository
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.util.AccountDisclosurePrefs
import com.plantdiseases.app.util.ConnectivityObserver
import com.plantdiseases.app.ui.onboarding.guidance.OnboardingStateStore
import com.plantdiseases.app.util.DefaultUploadImageSanitizer
import com.plantdiseases.app.util.FeedbackReceiptStore
import com.plantdiseases.app.util.PrivacyConsentPrefs
import com.plantdiseases.app.util.ReachabilityReporter
import com.plantdiseases.app.util.SharedPreferencesFeedbackReceiptStore
import com.plantdiseases.app.util.ServerConfig
import com.plantdiseases.app.util.UploadImageSanitizer
import com.plantdiseases.app.worker.WorkManagerLogoutRetryScheduler
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
    fun provideAccountEndpointScope(): AccountEndpointScope =
        AccountEndpointScope.from(ServerConfig.accountBaseUrl)

    @Provides
    @Singleton
    fun provideAccountApi(): AccountApi = AccountApiClient()

    @Provides
    @Singleton
    fun provideProPaymentClient(): ProPaymentClient = RuStoreProPaymentClient(
        RuStorePayConfiguration(
            consoleAppId = BuildConfig.RUSTORE_CONSOLE_APP_ID,
            monthlyProductId = BuildConfig.RUSTORE_PRO_MONTHLY_PRODUCT_ID,
            yearlyProductId = BuildConfig.RUSTORE_PRO_YEARLY_PRODUCT_ID,
            enabled = BuildConfig.RUSTORE_PAY_CONFIGURED,
        ),
    )

    @Provides
    @Singleton
    fun provideRefreshTokenVault(
        @ApplicationContext context: Context,
        accountEndpointScope: AccountEndpointScope,
    ): RefreshTokenVault = AndroidKeystoreRefreshTokenVault(context, accountEndpointScope)

    @Provides
    @Singleton
    fun provideSignupCompletionStore(
        @ApplicationContext context: Context,
        accountEndpointScope: AccountEndpointScope,
    ): SignupCompletionStore = AndroidKeystoreSignupCompletionStore(context, accountEndpointScope)

    @Provides
    @Singleton
    fun provideLogoutRevocationStore(
        @ApplicationContext context: Context,
        accountEndpointScope: AccountEndpointScope,
    ): LogoutRevocationStore = AndroidLogoutRevocationStore(context, accountEndpointScope)

    @Provides
    @Singleton
    fun provideInstallationId(
        @ApplicationContext context: Context,
        accountEndpointScope: AccountEndpointScope,
    ): InstallationIdProvider = InstallationIdStore(context, accountEndpointScope)

    @Provides
    @Singleton
    fun provideProviderSessionCleaner(): ProviderSessionCleaner = OfficialProviderSessionCleaner()

    @Provides
    @Singleton
    fun provideLogoutRetryScheduler(
        @ApplicationContext context: Context,
    ): LogoutRetryScheduler = WorkManagerLogoutRetryScheduler(context)

    @Provides
    @Singleton
    fun provideAdditionalLogoutTargets(
        @ApplicationContext context: Context,
    ): AdditionalLogoutRevocationTargets = AdditionalLogoutRevocationTargets(
        AccountLogoutEndpointRegistry.additionalEndpoints(ServerConfig.accountBaseUrl).map { endpoint ->
            val endpointScope = AccountEndpointScope.from(endpoint)
            LogoutRevocationTarget(
                api = AccountApiClient(endpoint),
                store = AndroidLogoutRevocationStore(
                    context,
                    endpointScope,
                ),
                previousIssuerVault = AndroidKeystoreRefreshTokenVault(context, endpointScope),
                previousIssuerInstallationId = InstallationIdStore(context, endpointScope),
                previousIssuerSignupStore = AndroidKeystoreSignupCompletionStore(
                    context,
                    endpointScope,
                ),
            )
        },
    )

    @Provides
    @Singleton
    fun provideAuthRepository(
        accountApi: AccountApi,
        refreshTokenVault: RefreshTokenVault,
        installationId: InstallationIdProvider,
        providerSessionCleaner: ProviderSessionCleaner,
        logoutRevocationStore: LogoutRevocationStore,
        logoutRetryScheduler: LogoutRetryScheduler,
        additionalLogoutTargets: AdditionalLogoutRevocationTargets,
        signupCompletionStore: SignupCompletionStore,
    ): AuthRepository = AuthRepository(
        accountApi,
        refreshTokenVault,
        installationId,
        providerSessionCleaner,
        logoutRevocationStore,
        logoutRetryScheduler,
        additionalLogoutTargets.values,
        signupCompletionStore,
        accountUiEnabled = BuildConfig.ACCOUNT_UI_ENABLED,
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
    fun provideAccountDisclosurePrefs(
        @ApplicationContext context: Context,
    ): AccountDisclosurePrefs = AccountDisclosurePrefs(context)

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
        database.roomDao(),
        database.treatmentPlanDao(),
        database.treatmentStepDao(),
        database,
    )

    @Provides
    @Singleton
    fun provideRoomRepository(database: AppDatabase): RoomRepository = RoomRepository(
        database,
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
        database = database,
        plantDao = database.plantDao(),
        scanDao = database.scanDao(),
        roomDao = database.roomDao(),
        reminderDao = database.plantReminderDao(),
        reminderEventDao = database.plantReminderEventDao(),
        journalDao = database.plantJournalDao(),
        treatmentPlanDao = database.treatmentPlanDao(),
        treatmentStepDao = database.treatmentStepDao(),
        plantRepository = plantRepository,
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

    @Provides
    @Singleton
    fun provideNotificationRepository(database: AppDatabase): NotificationRepository =
        NotificationRepository(database.notificationDao())
}
