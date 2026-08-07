plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.serialization")
    id("com.google.devtools.ksp")
    id("com.google.dagger.hilt.android")
    id("org.jlleitschuh.gradle.ktlint")
    id("io.gitlab.arturbosch.detekt")
}

// Account-beta OAuth configuration can come from a Gradle property or an
// environment variable. Environment support keeps extractable mobile client
// values out of command lines and lets CI inject them without committing a
// local.properties/gradle.properties file.
fun configuredValue(name: String): String =
    providers.gradleProperty(name)
        .orElse(providers.environmentVariable(name))
        .orNull
        ?.trim()
        .orEmpty()

val yandexClientId = configuredValue("YANDEX_CLIENT_ID")
val vkClientId = configuredValue("VKID_CLIENT_ID")
val vkClientSecret = configuredValue("VKID_CLIENT_SECRET")
val accountBetaRealOAuthEnabled = when (
    val raw = configuredValue("ACCOUNT_BETA_REAL_OAUTH").lowercase()
) {
    "", "false" -> false
    "true" -> true
    else -> throw GradleException("ACCOUNT_BETA_REAL_OAUTH must be true or false")
}

if (accountBetaRealOAuthEnabled) {
    val missing = buildList {
        if (yandexClientId.isBlank()) add("YANDEX_CLIENT_ID")
        if (vkClientId.isBlank()) add("VKID_CLIENT_ID")
        if (vkClientSecret.isBlank()) add("VKID_CLIENT_SECRET")
        if (configuredValue("APP_API_BASE_URL").isBlank()) add("APP_API_BASE_URL")
        if (configuredValue("ACCOUNT_API_BASE_URL").isBlank()) add("ACCOUNT_API_BASE_URL")
    }
    if (missing.isNotEmpty()) {
        throw GradleException(
            "Real account-beta OAuth requires: ${missing.joinToString()}",
        )
    }
    if (!vkClientId.all { it.isDigit() }) {
        throw GradleException("VKID_CLIENT_ID must be a decimal application ID")
    }
    listOf("APP_API_BASE_URL", "ACCOUNT_API_BASE_URL").forEach { name ->
        val uri = runCatching { java.net.URI(configuredValue(name)) }.getOrNull()
        val valid = uri != null &&
            uri.scheme == "https" &&
            !uri.host.isNullOrBlank() &&
            uri.userInfo == null &&
            uri.query == null &&
            uri.fragment == null
        if (!valid) {
            throw GradleException("$name must be an absolute credential-free HTTPS URL")
        }
    }
}

android {
    namespace = "com.plantdiseases.app"
    // compileSdk 36 (Android 16) is required by the current AndroidX line —
    // androidx.core 1.17+, activity 1.13.0 and CameraX 1.6.x all declare
    // minCompileSdk=36 in their AAR metadata and fail the build below it.
    // AGP 8.13.2 already satisfies their minAgp=8.9.1, so no AGP/Kotlin bump
    // is needed to get there.
    compileSdk = 36

    defaultConfig {
        applicationId = "com.plantdiseases.app"
        minSdk = 29 // Android 10
        // Deliberately held one behind compileSdk. targetSdk is what opts the
        // app into a platform's *behaviour* changes; raising it to 36 would
        // turn on Android 16's mandatory edge-to-edge enforcement and the
        // stricter intent/foreground-service rules, which is a UI change to
        // make on its own terms — not a side effect of a dependency refresh.
        // Compiling against 36 while targeting 35 is the supported combination.
        targetSdk = 35
        versionCode = 18
        versionName = "1.8"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        // Public application traffic and private account traffic deliberately
        // have independent origins. Production defaults stay on the reviewed
        // public endpoint; the account UI is enabled only in the branch-only
        // accountBeta build type below.
        val configuredAppApiBaseUrl = configuredValue("APP_API_BASE_URL")
            .ifBlank { "https://pdis.arbuz.lol/" }
        val configuredAccountApiBaseUrl = configuredValue("ACCOUNT_API_BASE_URL")
            .ifBlank { "https://pdis.arbuz.lol/id/" }
        fun quotedUrl(raw: String): String {
            val normalized = if (raw.endsWith("/")) raw else "$raw/"
            return "\"${normalized.replace("\\", "\\\\").replace("\"", "\\\"")}\""
        }
        buildConfigField("String", "APP_API_BASE_URL", quotedUrl(configuredAppApiBaseUrl))
        buildConfigField("String", "ACCOUNT_API_BASE_URL", quotedUrl(configuredAccountApiBaseUrl))
        buildConfigField("boolean", "ACCOUNT_UI_ENABLED", "false")
        buildConfigField("boolean", "SYNTHETIC_AUTH_ENABLED", "false")

        // OAuth mobile application values are public/extractable client
        // configuration, but no real values are committed. Without explicit
        // Gradle properties/environment values the provider buttons fail closed
        // and no SDK flow is started. Package/signature registration remains an
        // operator step.
        buildConfigField("boolean", "YANDEX_OAUTH_CONFIGURED", yandexClientId.isNotBlank().toString())
        buildConfigField(
            "boolean",
            "VK_OAUTH_CONFIGURED",
            (vkClientId.isNotBlank() && vkClientSecret.isNotBlank()).toString(),
        )
        manifestPlaceholders += mapOf(
            "YANDEX_CLIENT_ID" to yandexClientId.ifBlank { "0" },
            "VKIDClientID" to vkClientId.ifBlank { "0" },
            "VKIDClientSecret" to vkClientSecret.ifBlank { "unconfigured" },
            "VKIDRedirectHost" to "vk.ru",
            "VKIDRedirectScheme" to "vk${vkClientId.ifBlank { "0" }}",
        )

        // Firebase Cloud Messaging client config. These four values are the
        // public client identifiers of a Firebase project (the same ones that
        // normally live in google-services.json). They are NOT injected with
        // any default here — they must be supplied at build time via a Gradle
        // property, a same-named environment variable, or (in CI) a GitHub
        // Actions secret. See DEPLOYMENT.md (Android release).
        //   ./gradlew :app:assembleRelease -PFCM_APP_ID=... -PFCM_API_KEY=...
        // When any of them is blank the app builds fine and push messaging
        // simply stays dormant (PushManager.isConfigured() returns false and
        // the client falls back to its WorkManager poll). We init Firebase
        // MANUALLY from these (no google-services plugin), so the build never
        // requires a google-services.json file.
        fun fcmProp(name: String): String =
            (providers.gradleProperty(name).orNull ?: System.getenv(name) ?: "")
                .replace("\\", "\\\\")
                .replace("\"", "\\\"")
        buildConfigField("String", "FCM_APP_ID", "\"${fcmProp("FCM_APP_ID")}\"")
        buildConfigField("String", "FCM_API_KEY", "\"${fcmProp("FCM_API_KEY")}\"")
        buildConfigField("String", "FCM_PROJECT_ID", "\"${fcmProp("FCM_PROJECT_ID")}\"")
        buildConfigField("String", "FCM_SENDER_ID", "\"${fcmProp("FCM_SENDER_ID")}\"")

        // The Aqua family runs a TFLite model on-device. Limit the bundled
        // native .so libs to the ABIs real phones use. x86_64 is dropped: it
        // exists only for emulators/Chromebooks and the LiteRT interpreter lib
        // for it is ~10 MB — on x86_64 the app simply degrades to server
        // inference (the documented Aqua-absent fallback). LiteRT ships no
        // armeabi-v7a lib at 2.1.3 either (one first appears in 2.1.5, which
        // the Kotlin ceiling above rules out), so 32-bit devices also degrade
        // to the server.
        // (The model itself is downloaded from the server into filesDir, not
        // bundled in the APK — see AquaModelManager.)
        ndk {
            abiFilters += listOf("arm64-v8a", "armeabi-v7a")
        }
    }

    signingConfigs {
        create("release") {
            // Each value comes from a Gradle property (-PRELEASE_STORE_FILE=…)
            // or a same-named environment variable — so CI can inject them as
            // plain env from GitHub Actions secrets. When RELEASE_STORE_FILE is
            // absent the release build falls back to the debug key (see below),
            // which keeps local/CI sideload builds installable without a
            // keystore. See DEPLOYMENT.md (Android release).
            fun signProp(name: String): String? =
                (project.findProperty(name) as String?)?.takeIf { it.isNotEmpty() }
                    ?: System.getenv(name)?.takeIf { it.isNotEmpty() }

            val keystorePath = signProp("RELEASE_STORE_FILE")
            if (keystorePath != null) {
                storeFile = file(keystorePath)
                storePassword = signProp("RELEASE_STORE_PASSWORD") ?: ""
                keyAlias = signProp("RELEASE_KEY_ALIAS") ?: ""
                keyPassword = signProp("RELEASE_KEY_PASSWORD") ?: ""
            }
        }
    }

    buildTypes {
        create("accountBeta") {
            initWith(getByName("debug"))
            matchingFallbacks += listOf("debug")
            applicationIdSuffix = ".accountbeta"
            versionNameSuffix = "-account-beta"
            resValue("string", "app_name", "PlantDiseases Account Beta")

            val appUrl = configuredValue("APP_API_BASE_URL")
                .ifBlank { "https://pdis.arbuz.lol/plantdisease-test/" }
            val accountUrl = configuredValue("ACCOUNT_API_BASE_URL")
                .ifBlank { "https://pdis.arbuz.lol/plantdisease-test/id/" }
            fun quotedUrl(raw: String): String {
                val normalized = if (raw.endsWith("/")) raw else "$raw/"
                return "\"${normalized.replace("\\", "\\\\").replace("\"", "\\\"")}\""
            }
            buildConfigField("String", "APP_API_BASE_URL", quotedUrl(appUrl))
            buildConfigField("String", "ACCOUNT_API_BASE_URL", quotedUrl(accountUrl))
            buildConfigField("boolean", "ACCOUNT_UI_ENABLED", "true")
            // Synthetic auth remains the safe default for the local/German
            // mock stack. ACCOUNT_BETA_REAL_OAUTH=true selects the already
            // integrated official Yandex/VK SDK flows, after the configuration
            // validation above has proved both clients and an explicit remote
            // account endpoint are present.
            buildConfigField(
                "boolean",
                "SYNTHETIC_AUTH_ENABLED",
                (!accountBetaRealOAuthEnabled).toString(),
            )
        }

        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
            val releaseConfig = signingConfigs.findByName("release")
            // Always sign the release APK so it is INSTALLABLE. A real
            // keystore (pass -PRELEASE_STORE_FILE=… for a store build) is used
            // when present; otherwise (CI, local sideload) fall back to the
            // auto-provisioned debug key. An UNSIGNED release APK fails to
            // install with "App not installed as package appears to be
            // invalid" — which is what packaging only the release variant in
            // CI (no keystore) exposed.
            signingConfig = if (releaseConfig?.storeFile != null) {
                releaseConfig
            } else {
                signingConfigs.getByName("debug")
            }
        }
    }

    buildFeatures {
        viewBinding = true
        buildConfig = true
    }

    packaging {
        jniLibs {
            // LiteRT ships a GPU-delegate accelerator lib alongside the
            // interpreter. AquaClassifier runs CPU-only
            // (Interpreter.Options().setNumThreads(2), no GpuDelegate), so the
            // accelerator is never dlopen'd — exclude it from the APK.
            // The artefact was renamed between LiteRT 2.1.0 and 2.1.3
            // (libLiteRtOpenClAccelerator.so → libLiteRtClGlAccelerator.so);
            // both patterns are listed so the exclude keeps matching across the
            // rename instead of silently shipping the lib again on a bump.
            excludes += "**/libLiteRtOpenClAccelerator.so"
            excludes += "**/libLiteRtClGlAccelerator.so"

            // These prebuilt .so libs (DataStore, CameraX JNI) can't be
            // stripped by the NDK strip tool, which logs a noisy "Unable to
            // strip the following libraries" warning every build. They are
            // packaged as-is either way, so declaring them here just keeps
            // their (already-present) symbols and silences the warning — the
            // APK size is unchanged.
            keepDebugSymbols += listOf(
                "**/libdatastore_shared_counter.so",
                "**/libimage_processing_util_jni.so",
                "**/libsurface_util_jni.so",
            )
        }
    }

    lint {
        // Android Lint is no longer the code-quality gate — ktlint (style) and
        // detekt (static analysis) replace it (see the ktlint/detekt blocks
        // below and ci.yml). Don't let it fail the build, and skip the
        // lintVitalRelease pass so assembleRelease doesn't re-run it.
        abortOnError = false
        checkReleaseBuilds = false
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    testOptions {
        unitTests {
            // Robolectric needs the merged Android resources + a real
            // android.jar rather than the all-throwing stub, so JVM unit
            // tests can inflate views, read strings and run Room in-memory.
            isIncludeAndroidResources = true
            isReturnDefaultValues = true

            // Put the actual assertion message + stack in the console log.
            // Nobody builds this project locally (CI owns builds), so a
            // failure that prints only "ComparisonFailureWithFacts at
            // FooTest.kt:42" costs a whole round-trip to diagnose — the HTML
            // report it points at lives on the runner, not in the log.
            all {
                it.testLogging {
                    events("failed")
                    exceptionFormat = org.gradle.api.tasks.testing.logging.TestExceptionFormat.FULL
                    showStackTraces = true
                    showCauses = true
                }
            }
        }
    }
}

kotlin {
    // Replaces the deprecated android.kotlinOptions block (Kotlin 2.x DSL).
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

// ─── Kotlin linting (replaces Android Lint) ───────────────────────────────────
// Gating: ktlint (formatting/style) + detekt (static analysis) fail the build
// on any NEW violation. The pre-existing tree is grandfathered via baselines
// (config/ktlint/baseline.xml, config/detekt/baseline.xml) generated once with
// `./gradlew ktlintGenerateBaseline detektBaseline` after a `./gradlew
// ktlintFormat` cleanup, so only newly-introduced issues break CI.
detekt {
    buildUponDefaultConfig = true
    config.setFrom(files("$rootDir/config/detekt/detekt.yml"))
    baseline = file("$rootDir/config/detekt/baseline.xml")
    source.setFrom("src/main/java", "src/test/java")
    parallel = true
    ignoreFailures = false
}

tasks.withType<io.gitlab.arturbosch.detekt.Detekt>().configureEach {
    jvmTarget = "17"
}

ktlint {
    version.set("1.3.1")
    android.set(true)
    ignoreFailures.set(false)
    baseline.set(file("config/ktlint/baseline.xml"))
    filter {
        // Never lint generated sources (view binding, KSP/Room, BuildConfig).
        exclude { it.file.path.contains("/build/") }
    }
}

dependencies {
    // AndroidX Core
    // core-ktx is capped at 1.18.x: 1.19.0 declares minCompileSdk=37 and
    // minAgp=9.1.0, i.e. it needs the AGP 9 migration, not just an SDK bump.
    implementation("androidx.core:core-ktx:1.18.0")
    implementation("androidx.core:core-splashscreen:1.2.0")
    implementation("androidx.appcompat:appcompat:1.7.1")
    implementation("androidx.activity:activity-ktx:1.13.0")
    implementation("androidx.fragment:fragment-ktx:1.8.9")
    implementation("androidx.constraintlayout:constraintlayout:2.2.2")
    implementation("androidx.recyclerview:recyclerview:1.4.0")
    implementation("androidx.swiperefreshlayout:swiperefreshlayout:1.2.0")

    // Material Design 3
    implementation("com.google.android.material:material:1.14.0")

    // Navigation
    implementation("androidx.navigation:navigation-fragment-ktx:2.9.8")
    implementation("androidx.navigation:navigation-ui-ktx:2.9.8")

    // CameraX
    val cameraxVersion = "1.6.1"
    implementation("androidx.camera:camera-core:$cameraxVersion")
    implementation("androidx.camera:camera-camera2:$cameraxVersion")
    implementation("androidx.camera:camera-lifecycle:$cameraxVersion")
    implementation("androidx.camera:camera-view:$cameraxVersion")

    // Room Database
    val roomVersion = "2.8.4"
    implementation("androidx.room:room-runtime:$roomVersion")
    implementation("androidx.room:room-ktx:$roomVersion")
    ksp("androidx.room:room-compiler:$roomVersion")

    // OkHttp — the engine the Ktor client runs on (shared RetryInterceptor).
    // Retrofit/converter-gson were removed once the last endpoint
    // moved to the Ktor client; Gson itself stays for Room field + cache JSON.
    implementation("com.squareup.okhttp3:okhttp:4.12.0")

    // Ktor client + kotlinx.serialization — incrementally replaces Retrofit.
    // Uses the OkHttp engine so the existing RetryInterceptor, logging and
    // timeouts (configured on the shared OkHttpClient) carry over unchanged.
    implementation(platform("io.ktor:ktor-bom:2.3.13"))
    implementation("io.ktor:ktor-client-core")
    implementation("io.ktor:ktor-client-okhttp")
    implementation("io.ktor:ktor-client-content-negotiation")
    implementation("io.ktor:ktor-serialization-kotlinx-json")
    // Pinned to match the Kotlin 2.1.21 serialization compiler plugin (Ktor
    // 2.3.13 bundles 1.6.3; overriding up keeps the generated serializers and
    // the runtime in lockstep). 1.8.1 is the newest release still built
    // against kotlin-stdlib 2.1.x — 1.9.0 moves to 2.2.0 and 1.11.0 to 2.3.20,
    // so taking those would mean bumping the Kotlin compiler in lockstep too.
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.8.1")

    // Firebase Cloud Messaging (push). No google-services plugin / json:
    // Firebase is initialised manually from BuildConfig.FCM_* at runtime, so
    // the build works with or without credentials.
    implementation(platform("com.google.firebase:firebase-bom:34.17.0"))
    implementation("com.google.firebase:firebase-messaging")

    // Official mobile OAuth SDKs. Both are inert until build-time client
    // configuration is supplied; tests use synthetic provider tokens and
    // never contact either provider.
    implementation("com.yandex.android:authsdk:3.1.3")
    implementation("com.vk.id:vkid:2.7.2")

    // RuStore in-app updates — installs that came from RuStore get offered new
    // builds in-place (flexible flow, util/InAppUpdateManager). Resolved from
    // VK's artifactory (see settings.gradle.kts). On every other install
    // (sideload, no RuStore app) the SDK fails into its failure listeners and
    // the app carries on — the usual optional-integration degradation.
    implementation("ru.rustore.sdk:appupdate:10.5.0")

    // Gson
    implementation("com.google.code.gson:gson:2.14.0")

    // Glide for images
    implementation("com.github.bumptech.glide:glide:4.16.0")
    ksp("com.github.bumptech.glide:ksp:4.16.0")

    // Hilt (Dagger) dependency injection. Replaces the hand-rolled `by lazy`
    // singletons on PlantDiseasesApp with a Hilt graph (di/AppModule.kt).
    // KSP (not kapt) generates the Dagger components, matching the rest of
    // the build (Room, Glide already use KSP). androidx.hilt:hilt-work is
    // intentionally NOT depended on: wiring a HiltWorkerFactory into
    // Configuration.Provider re-initialises WorkManager and collides with the
    // default androidx.startup initializer (crash on launch — see
    // PlantDiseasesApp.onCreate). Workers, the two push-package singleton
    // objects and SpeciesInfoCardView (a custom View instantiated in a plain
    // Robolectric host in tests, so it can't be `@AndroidEntryPoint`) instead
    // resolve their deps via EntryPointAccessors against the Application's
    // SingletonComponent — see di/AppEntryPoint.kt.
    implementation("com.google.dagger:hilt-android:2.57.2")
    ksp("com.google.dagger:hilt-android-compiler:2.57.2")

    // Coroutines
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")

    // Lifecycle 2.11's Compose artifact is compiled against API 37 and declares
    // AGP 9.1 as its minimum. Keep the atomic Lifecycle family on 2.10 while
    // this project intentionally remains on compileSdk 36 / AGP 8.13.2.
    val lifecycleVersion = "2.10.0"
    implementation("androidx.lifecycle:lifecycle-viewmodel-ktx:$lifecycleVersion")
    implementation("androidx.lifecycle:lifecycle-livedata-ktx:$lifecycleVersion")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:$lifecycleVersion")
    constraints {
        implementation("androidx.lifecycle:lifecycle-runtime-compose:$lifecycleVersion") {
            version { strictly(lifecycleVersion) }
            because("Lifecycle 2.11 requires compileSdk 37 and AGP 9.1+")
        }
    }

    // Lottie animations
    implementation("com.airbnb.android:lottie:6.7.1")

    // ViewPager2
    implementation("androidx.viewpager2:viewpager2:1.1.0")

    // osmdroid — OpenStreetMap renderer for the community scans map. Keyless,
    // no Play Services (mavenCentral). We draw the aggregated heat cells +
    // scan markers ourselves; OSM raster tiles need a descriptive User-Agent.
    implementation("org.osmdroid:osmdroid-android:6.1.20")

    // Preferences DataStore
    implementation("androidx.datastore:datastore-preferences:1.2.1")

    // Shimmer effect for skeleton loading
    implementation("com.facebook.shimmer:shimmer:0.5.0")

    // ExifInterface (used for orientation correction in ImageUtils)
    implementation("androidx.exifinterface:exifinterface:1.4.2")

    // WorkManager (plant reminders)
    implementation("androidx.work:work-runtime-ktx:2.11.2")

    // LiteRT (formerly TensorFlow Lite) — on-device inference for the Aqua
    // model family. Migrated off org.tensorflow:tensorflow-lite:2.16.1, whose
    // prebuilt .so libs aren't 16 KB page-aligned (Google Play's Nov 2025
    // requirement). LiteRT keeps the org.tensorflow.lite Interpreter API, so
    // AquaClassifier's code is unchanged; only the artefact coordinate moves.
    // The model artefact (assets/models/aqua_classifier.tflite) is provisioned
    // out of band like the server weights, never committed; AquaClassifier
    // gates on its presence and the app degrades to the server when absent.
    // Held at 2.1.3: the newest release the Kotlin 2.1.21 compiler above will
    // read. LiteRT ships Kotlin metadata inside its own classes.jar, and the
    // compiler only tolerates metadata one minor version ahead of itself —
    // 2.1.0 through 2.1.3 carry metadata 2.2.0 (accepted, which is why 2.1.0
    // built), while 2.1.4+ moved to 2.3.0 and fail the build with
    // "Module was compiled with an incompatible version of Kotlin".
    // Raising this needs the Kotlin bump, not just a version change.
    implementation("com.google.ai.edge.litert:litert:2.1.3")

    // ─── Local JVM unit tests (./gradlew testDebugUnitTest) ───────────────
    // Robolectric lets the image / ML-diagnosis pipeline run on the JVM
    // (Bitmap, Room in-memory DB, resources) without a device, so the
    // bulk of the pipeline is covered in CI-runnable unit tests.
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.robolectric:robolectric:4.16.1")
    testImplementation("androidx.test:core:1.7.0")
    testImplementation("androidx.test:core-ktx:1.7.0")
    testImplementation("androidx.test.ext:junit:1.3.0")
    testImplementation("androidx.test.ext:junit-ktx:1.3.0")
    testImplementation("com.google.truth:truth:1.4.5")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.10.2")
    // MockWebServer stands in for the diagnosis server so Ktor/OkHttp
    // request building + the RetryInterceptor are exercised for real.
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
    testImplementation("androidx.arch.core:core-testing:2.2.0")
    testImplementation("androidx.room:room-testing:2.8.4")

    // ─── Instrumented / Espresso UI tests (./gradlew connectedDebugAndroidTest,
    //     device or emulator required) ──────────────────────────────────────
    androidTestImplementation("androidx.test.ext:junit:1.3.0")
    androidTestImplementation("androidx.test:core:1.7.0")
    androidTestImplementation("androidx.test:core-ktx:1.7.0")
    androidTestImplementation("androidx.test:runner:1.7.0")
    androidTestImplementation("androidx.test:rules:1.7.0")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.7.0")
    androidTestImplementation("androidx.test.espresso:espresso-contrib:3.7.0")
    androidTestImplementation("androidx.test.espresso:espresso-intents:3.7.0")
    androidTestImplementation("androidx.fragment:fragment-testing:1.8.9")
    androidTestImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
    androidTestImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.10.2")
    // Provides the empty host Activity that launchFragmentInContainer needs,
    // merged into the debug app manifest.
    debugImplementation("androidx.fragment:fragment-testing-manifest:1.8.9")
}
