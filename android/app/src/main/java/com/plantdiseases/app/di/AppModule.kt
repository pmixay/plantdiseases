package com.plantdiseases.app.di

import com.plantdiseases.app.data.local.AppDatabase
import com.plantdiseases.app.data.remote.PlantApiClient
import com.plantdiseases.app.data.repository.CommunityUploader
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.data.repository.ScanRepository
import org.koin.android.ext.koin.androidContext
import org.koin.dsl.module

/**
 * Koin graph for the app-scoped singletons: the Room database, the Ktor
 * [PlantApiClient], the repositories over them, and the community uploader.
 * UI, workers and the Application resolve these via `inject()`/`get()`.
 */
val appModule = module {
    single { AppDatabase.getInstance(androidContext()) }

    single { PlantApiClient() }

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
