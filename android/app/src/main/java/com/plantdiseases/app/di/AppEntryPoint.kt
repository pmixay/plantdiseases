package com.plantdiseases.app.di

import android.content.Context
import com.plantdiseases.app.auth.AuthRepository
import com.plantdiseases.app.auth.InstallationIdProvider
import com.plantdiseases.app.auth.LogoutRetryScheduler
import com.plantdiseases.app.data.repository.ForumNotificationSync
import com.plantdiseases.app.data.repository.NotificationRepository
import com.plantdiseases.app.data.repository.PlantRepository
import com.plantdiseases.app.data.repository.ScanRepository
import com.plantdiseases.app.util.AccountDisclosurePrefs
import dagger.hilt.EntryPoint
import dagger.hilt.InstallIn
import dagger.hilt.android.EntryPointAccessors
import dagger.hilt.components.SingletonComponent

/**
 * Manual DI escape hatch for classes Hilt can't (or shouldn't) field-inject
 * via `@AndroidEntryPoint`:
 *   * `CoroutineWorker` subclasses (`ForumAnswerCheckWorker`, `PlantReminderWorker`)
 *     — see the note on `androidx.hilt:hilt-work` below.
 *   * the plain Kotlin `object` singletons in `push/`/`util/` that aren't
 *     Android framework components at all (`ForumAnswerNotifier`, `PushManager`,
 *     `NotificationCenter`).
 *   * [com.plantdiseases.app.ui.common.SpeciesInfoCardView] — a custom View
 *     that IS eligible for `@AndroidEntryPoint`, but `SpeciesInfoCardViewTest`
 *     constructs it directly against a plain (non-Hilt) `AppCompatActivity`
 *     host, which would crash a Hilt-injected View at construction time.
 *     Resolving lazily here — via a `by lazy` field on that class — keeps
 *     that test's non-network paths working unmodified, since the lazy is
 *     never forced unless a real Wikipedia enrichment fetch actually happens.
 *
 * (`ReminderActionReceiver` and `PlantMessagingService` ARE Android framework
 * components — a `BroadcastReceiver` and a `Service` — so they use
 * `@AndroidEntryPoint` + `@Inject lateinit var` directly and don't need this.)
 *
 * Deliberately NOT [androidx.hilt.work.HiltWorkerFactory] for the workers:
 * wiring one up requires a `Configuration.Provider` on the Application that
 * calls `WorkManager.initialize()`, which collides with the default
 * `androidx.startup` `WorkManagerInitializer` still declared in the manifest
 * (crash on launch — see `PlantDiseasesApp.onCreate` for the full story).
 * Resolving through `EntryPointAccessors.fromApplication` at the point of use
 * mirrors how these classes resolved their dependencies before this DI
 * migration — off a global container, lazily, on first use — and touches
 * nothing at Application startup.
 */
@EntryPoint
@InstallIn(SingletonComponent::class)
interface AppEntryPoint {
    fun authRepository(): AuthRepository

    fun logoutRetryScheduler(): LogoutRetryScheduler

    fun scanRepository(): ScanRepository

    fun plantRepository(): PlantRepository

    fun notificationRepository(): NotificationRepository

    fun accountDisclosurePrefs(): AccountDisclosurePrefs

    fun installationIdProvider(): InstallationIdProvider

    fun forumNotificationSync(): ForumNotificationSync
}

/** `EntryPointAccessors.fromApplication` against the given context's
 *  application, typed to [AppEntryPoint]. Call with `applicationContext` (or
 *  a `Context` this function itself unwraps) — the entry point lives on
 *  [dagger.hilt.components.SingletonComponent], not any Activity/Fragment
 *  component. */
fun appEntryPoint(context: Context): AppEntryPoint =
    EntryPointAccessors.fromApplication(context.applicationContext, AppEntryPoint::class.java)
