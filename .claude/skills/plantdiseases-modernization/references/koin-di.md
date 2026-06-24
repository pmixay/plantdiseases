# Koin DI — replacing the service locator

> **STATUS: graph already wired.** `di/AppModule.kt` (`appModule`) + `startKoin{}` in
> `PlantDiseasesApp` exist, Koin deps are in the build (koin-bom 4.0.0), and the Application
> keeps its accessors as `by inject()` (the safety net). **The only work left: migrate the ~54
> `application as PlantDiseasesApp` call sites to direct injection, then delete the accessors**
> — jump to "Migrating the ~54 call sites". The build/module/startKoin sections below document
> what's already in place; don't redo them.

The app already does manual DI via a service locator: `PlantDiseasesApp` holds `by lazy`
singletons and ~54 consumers reach them with `application as PlantDiseasesApp`. Koin replaces
that with declarative modules + injection, with almost 1:1 mapping. Koin is **runtime** DI
(no KSP/codegen), which suits this codebase.

## Dependencies (`app/build.gradle.kts`)
```kotlin
// Koin 4.x — verify the latest stable before pinning
implementation("io.insert-koin:koin-android:4.0.0")
implementation("io.insert-koin:koin-androidx-workmanager:4.0.0") // app uses WorkManager
testImplementation("io.insert-koin:koin-test:4.0.0")
testImplementation("io.insert-koin:koin-test-junit4:4.0.0")
```
No Gradle plugin and no KSP needed for Koin.

## Map the service locator → a Koin module

Each `by lazy` in `PlantDiseasesApp` becomes a `single { }`. Mirror the exact construction:

```kotlin
// di/AppModule.kt
val appModule = module {
    // Room
    single { AppDatabase.getInstance(androidContext()) }
    single { get<AppDatabase>().scanDao() }
    single { get<AppDatabase>().scanChatMessageDao() }
    single { get<AppDatabase>().plantDao() }
    single { get<AppDatabase>().plantJournalDao() }
    single { get<AppDatabase>().plantReminderDao() }
    single { get<AppDatabase>().plantReminderEventDao() }
    single { get<AppDatabase>().chatMessageDao() }

    // Networking (unchanged interface; becomes Ktor in step 2 — only THIS line changes then)
    single { PlantApiClient() }

    // Repositories — same constructor args as the Application's lazies
    single { ScanRepository(get(), get(), get()) }            // scanDao, apiClient, scanChatMessageDao
    single { PlantRepository(get(), get(), get(), get(), get(), get()) }
    single { CommunityUploader(androidContext(), get()) }
}
```
> Pin the named DAOs explicitly if two DAOs share a type, or use `named(...)` qualifiers.
> Match the real constructor arg order in each repository — verify against the current
> `PlantDiseasesApp` lazy initializers.

The app-scoped `appScope`/`launchCommunityUpload`/WorkManager scheduling helpers can stay as
methods on `PlantDiseasesApp` (they're app lifecycle, not DI) — or move `appScope` into a
`single { CoroutineScope(SupervisorJob() + Dispatchers.IO) }`.

## Start Koin in the Application
```kotlin
class PlantDiseasesApp : Application() {
    override fun onCreate() {
        super.onCreate()
        startKoin {
            androidContext(this@PlantDiseasesApp)
            workManagerFactory()          // from koin-androidx-workmanager
            modules(appModule)
        }
        LocaleHelper.applyLocale(this)
        // ... rest unchanged (theme, notif channels, ResponseCache, crop fix)
        // NB: keep the existing "no WorkManager in onCreate" rule.
    }
}
```

## Migrating the ~54 call sites
Find them:
```bash
grep -rn "application as PlantDiseasesApp" android/app/src/main/java
```
Replace by consumer type:
- **Activity / Fragment:** `by inject<ScanRepository>()` (Koin Android KTX) instead of
  `(application as PlantDiseasesApp).scanRepository`.
- **ViewModel:** declare it in a module with `viewModel { ScanViewModel(get()) }` and obtain it
  with `by viewModel()` — this removes the existing `ViewModelProvider.Factory` boilerplate.
- **Worker:** Koin's `workManagerFactory()` + `KoinComponent`/constructor injection in the
  Worker; or `by inject()` via `KoinComponent` for the OpinionCheckWorker.
- **Plain classes:** implement `KoinComponent` and `by inject()`, or (preferred) pass deps via
  constructor and declare them in a module.

### Incremental safety net (do this to avoid a 54-edit big bang)
Keep the Application's properties temporarily as Koin look-ups so old call sites still compile
while you migrate them in batches:
```kotlin
class PlantDiseasesApp : Application(), KoinComponent {
    val scanRepository: ScanRepository by inject()   // delegates to Koin now
    val plantRepository: PlantRepository by inject()
    // ...
}
```
Then convert call sites file-by-file, run `testDebugUnitTest`, and delete each Application
property once nothing references it.

## Tests with Koin
- Existing tests that construct objects directly (e.g. `ScanRepository(FakeScanDao(),
  PlantApiClient(server.url(...)))`) **keep working unchanged** — they don't go through Koin.
  Don't force them onto Koin.
- For tests that exercise injection, use `KoinTestRule`/`startKoin` with a test module that
  binds fakes, and `checkModules()` to verify the graph resolves.

## Definition of done
- [ ] `startKoin` configured; `appModule` mirrors every former `by lazy` exactly.
- [ ] All `application as PlantDiseasesApp` call sites migrated (grep returns nothing), and the
      temporary Application delegate properties removed.
- [ ] ViewModel factories deleted in favor of `viewModel { }` where applicable.
- [ ] `OpinionCheckWorker` resolves via `workManagerFactory()`.
- [ ] `testDebugUnitTest` green; `assembleRelease` (R8) builds. Koin needs no special R8 rules.
