package com.plantdiseases.app.di

import com.plantdiseases.app.data.local.AppDatabase
import com.plantdiseases.app.data.remote.PlantApiClient
import com.plantdiseases.app.data.repository.CommunityUploader
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.data.repository.ScanRepository
import org.koin.android.ext.koin.androidContext
import org.koin.dsl.module

/**
 * Koin graph for the app-scoped singletons that used to live as `by lazy`
 * properties on [com.plantdiseases.app.PlantDiseasesApp]. Each `single`
 * mirrors one former `by lazy` 1:1 — same constructor, same wiring — so the
 * object graph is byte-for-byte equivalent; only the *owner* of the lifetime
 * moves from the Application to Koin.
 *
 * The Application keeps the same public `database` / `apiClient` /
 * `scanRepository` / `plantRepository` / `communityUploader` accessors, but
 * they now resolve through Koin via `by inject()` (the safety net), so the
 * ~54 existing `application as PlantDiseasesApp` call sites compile and behave
 * unchanged until they are migrated to direct injection in a later step.
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
