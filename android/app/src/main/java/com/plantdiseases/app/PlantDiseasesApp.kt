package com.plantdiseases.app

import android.app.Application
import com.plantdiseases.app.data.local.AppDatabase
import com.plantdiseases.app.data.remote.PlantApiClient
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.util.LocaleHelper
import com.plantdiseases.app.util.ThemeHelper

class PlantDiseasesApp : Application() {

    val database: AppDatabase by lazy { AppDatabase.getInstance(this) }
    val apiClient: PlantApiClient by lazy { PlantApiClient(this) }
    val scanRepository: ScanRepository by lazy {
        ScanRepository(database.scanDao(), apiClient)
    }

    override fun onCreate() {
        super.onCreate()
        LocaleHelper.applyLocale(this)
        ThemeHelper.applySavedTheme(this)
    }
}
