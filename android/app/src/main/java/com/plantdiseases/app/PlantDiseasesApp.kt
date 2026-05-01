package com.plantdiseases.app

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.os.Build
import com.plantdiseases.app.data.local.AppDatabase
import com.plantdiseases.app.data.remote.PlantApiClient
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.ThemeHelper

class PlantDiseasesApp : Application() {

    val database: AppDatabase by lazy { AppDatabase.getInstance(this) }
    val apiClient: PlantApiClient by lazy { PlantApiClient(this) }
    val scanRepository: ScanRepository by lazy {
        ScanRepository(database.scanDao(), apiClient)
    }
    val plantRepository: PlantRepository by lazy {
        PlantRepository(
            database.plantDao(),
            database.plantJournalDao(),
            database.scanDao(),
            database.plantReminderDao(),
            database.chatMessageDao(),
        )
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
