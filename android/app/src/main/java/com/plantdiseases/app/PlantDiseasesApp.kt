package com.plantdiseases.app

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.os.Build
import com.plantdiseases.app.data.local.AppDatabase
import com.plantdiseases.app.data.remote.PlantApiClient
import com.plantdiseases.app.data.repository.CommunityUploader
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.ThemeHelper
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

class PlantDiseasesApp : Application() {

    val database: AppDatabase by lazy { AppDatabase.getInstance(this) }
    val apiClient: PlantApiClient by lazy { PlantApiClient() }
    val scanRepository: ScanRepository by lazy {
        ScanRepository(database.scanDao(), apiClient)
    }
    val plantRepository: PlantRepository by lazy {
        PlantRepository(
            database.plantDao(),
            database.plantJournalDao(),
            database.scanDao(),
            database.plantReminderDao(),
            database.plantReminderEventDao(),
            database.chatMessageDao(),
        )
    }
    val communityUploader: CommunityUploader by lazy {
        CommunityUploader(this, scanRepository)
    }

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
        LocaleHelper.applyLocale(this)
        ThemeHelper.applySavedTheme(this)
        createNotificationChannels()
    }

    private fun createNotificationChannels() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_PLANT_REMINDERS,
                "Plant Reminders",
                NotificationManager.IMPORTANCE_HIGH,
            ).apply {
                description = "Care schedule reminders for your plants"
            }
            getSystemService(NotificationManager::class.java)
                .createNotificationChannel(channel)
        }
    }

    companion object {
        const val CHANNEL_PLANT_REMINDERS = "plant_reminders"
    }
}
