plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.serialization")
    id("com.google.devtools.ksp")
    id("org.jlleitschuh.gradle.ktlint")
    id("io.gitlab.arturbosch.detekt")
}

android {
    namespace = "com.plantdiseases.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.plantdiseases.app"
        minSdk = 26 // Android 8.0
        targetSdk = 35
        versionCode = 16
        versionName = "1.6"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        // Default API base URL — HTTPS in production (TLS terminated in
        // front of the uvicorn process). Override at build time, e.g. to
        // point a debug build at the emulator host over cleartext (only
        // local/LAN hosts are allowed cleartext, see
        // res/xml/network_security_config.xml):
        //   ./gradlew :app:assembleDebug -PAPI_BASE_URL=http://10.0.2.2:8000/
        val configuredApiBaseUrl = providers.gradleProperty("API_BASE_URL")
            .orElse("https://pdis.arbuz.lol/")
            .get()
            .let { if (it.endsWith("/")) it else "$it/" }
            .replace("\\", "\\\\")
            .replace("\"", "\\\"")
        buildConfigField("String", "API_BASE_URL", "\"$configuredApiBaseUrl\"")

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
        // armeabi-v7a lib, so 32-bit devices also degrade to the server.
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
            // LiteRT ships an OpenCL GPU-delegate accelerator lib alongside the
            // interpreter (~2.7 MB arm64 / 3.3 MB x86_64). AquaClassifier runs
            // CPU-only (Interpreter.Options().setNumThreads(2), no GpuDelegate),
            // so the accelerator is never dlopen'd — exclude it from the APK.
            excludes += "**/libLiteRtOpenClAccelerator.so"

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

    kotlinOptions {
        jvmTarget = "17"
    }

    testOptions {
        unitTests {
            // Robolectric needs the merged Android resources + a real
            // android.jar rather than the all-throwing stub, so JVM unit
            // tests can inflate views, read strings and run Room in-memory.
            isIncludeAndroidResources = true
            isReturnDefaultValues = true
        }
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
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.core:core-splashscreen:1.2.0")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.activity:activity-ktx:1.9.3")
    implementation("androidx.fragment:fragment-ktx:1.8.5")
    implementation("androidx.constraintlayout:constraintlayout:2.2.0")
    implementation("androidx.recyclerview:recyclerview:1.4.0")
    implementation("androidx.swiperefreshlayout:swiperefreshlayout:1.1.0")

    // Material Design 3
    implementation("com.google.android.material:material:1.12.0")

    // Image cropper (vanniktech fork — on mavenCentral, classes live in
    // package com.canhub.cropper). Lets the user crop a captured shot or
    // gallery image before it is sent for analysis.
    implementation("com.vanniktech:android-image-cropper:4.7.0")

    // Navigation
    implementation("androidx.navigation:navigation-fragment-ktx:2.8.5")
    implementation("androidx.navigation:navigation-ui-ktx:2.8.5")

    // CameraX
    val cameraxVersion = "1.4.1"
    implementation("androidx.camera:camera-core:$cameraxVersion")
    implementation("androidx.camera:camera-camera2:$cameraxVersion")
    implementation("androidx.camera:camera-lifecycle:$cameraxVersion")
    implementation("androidx.camera:camera-view:$cameraxVersion")

    // Room Database
    val roomVersion = "2.7.0"
    implementation("androidx.room:room-runtime:$roomVersion")
    implementation("androidx.room:room-ktx:$roomVersion")
    ksp("androidx.room:room-compiler:$roomVersion")

    // OkHttp — the engine the Ktor client runs on (shared RetryInterceptor +
    // logging). Retrofit/converter-gson were removed once the last endpoint
    // moved to the Ktor client; Gson itself stays for Room field + cache JSON.
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.squareup.okhttp3:logging-interceptor:4.12.0")

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
    // the runtime in lockstep).
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.7.3")

    // Firebase Cloud Messaging (push). No google-services plugin / json:
    // Firebase is initialised manually from BuildConfig.FCM_* at runtime, so
    // the build works with or without credentials.
    implementation(platform("com.google.firebase:firebase-bom:33.7.0"))
    implementation("com.google.firebase:firebase-messaging")

    // Gson
    implementation("com.google.code.gson:gson:2.11.0")

    // Glide for images
    implementation("com.github.bumptech.glide:glide:4.16.0")
    ksp("com.github.bumptech.glide:ksp:4.16.0")

    // Koin dependency injection. Replaces the hand-rolled `by lazy` singletons
    // on PlantDiseasesApp with a Koin graph (di/AppModule.kt). The
    // koin-androidx-workmanager artefact (Koin WorkerFactory) is intentionally
    // NOT depended on: wiring workManagerFactory() re-initialises WorkManager
    // and collides with the default androidx.startup initializer (crash on
    // launch — see PlantDiseasesApp.onCreate). A future Koin-injected worker
    // would re-add it there and remove the startup initializer.
    implementation(platform("io.insert-koin:koin-bom:4.0.0"))
    implementation("io.insert-koin:koin-android")

    // Coroutines
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")

    // Lifecycle
    implementation("androidx.lifecycle:lifecycle-viewmodel-ktx:2.8.7")
    implementation("androidx.lifecycle:lifecycle-livedata-ktx:2.8.7")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")

    // Lottie animations
    implementation("com.airbnb.android:lottie:6.6.0")

    // ViewPager2
    implementation("androidx.viewpager2:viewpager2:1.1.0")

    // osmdroid — OpenStreetMap renderer for the community scans map. Keyless,
    // no Play Services (mavenCentral). We draw the aggregated heat cells +
    // scan markers ourselves; OSM raster tiles need a descriptive User-Agent.
    implementation("org.osmdroid:osmdroid-android:6.1.20")

    // Preferences DataStore
    implementation("androidx.datastore:datastore-preferences:1.1.1")

    // Shimmer effect for skeleton loading
    implementation("com.facebook.shimmer:shimmer:0.5.0")

    // ExifInterface (used for orientation correction in ImageUtils)
    implementation("androidx.exifinterface:exifinterface:1.4.0")

    // WorkManager (plant reminders)
    implementation("androidx.work:work-runtime-ktx:2.10.0")

    // LiteRT (formerly TensorFlow Lite) — on-device inference for the Aqua
    // model family. Migrated off org.tensorflow:tensorflow-lite:2.16.1, whose
    // prebuilt .so libs aren't 16 KB page-aligned (Google Play's Nov 2025
    // requirement). LiteRT keeps the org.tensorflow.lite Interpreter API, so
    // AquaClassifier's code is unchanged; only the artefact coordinate moves.
    // The model artefact (assets/models/aqua_classifier.tflite) is provisioned
    // out of band like the server weights, never committed; AquaClassifier
    // gates on its presence and the app degrades to the server when absent.
    implementation("com.google.ai.edge.litert:litert:2.1.0")

    // ─── Local JVM unit tests (./gradlew testDebugUnitTest) ───────────────
    // Robolectric lets the image / ML-diagnosis pipeline run on the JVM
    // (Bitmap, Room in-memory DB, resources) without a device, so the
    // bulk of the pipeline is covered in CI-runnable unit tests.
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.robolectric:robolectric:4.14.1")
    testImplementation("androidx.test:core:1.6.1")
    testImplementation("androidx.test:core-ktx:1.6.1")
    testImplementation("androidx.test.ext:junit:1.2.1")
    testImplementation("androidx.test.ext:junit-ktx:1.2.1")
    testImplementation("com.google.truth:truth:1.4.4")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.10.2")
    // MockWebServer stands in for the diagnosis server so Retrofit /
    // OkHttp request building + the RetryInterceptor are exercised for real.
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
    testImplementation("androidx.arch.core:core-testing:2.2.0")
    testImplementation("androidx.room:room-testing:2.7.0")

    // ─── Instrumented / Espresso UI tests (./gradlew connectedDebugAndroidTest,
    //     device or emulator required) ──────────────────────────────────────
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test:core:1.6.1")
    androidTestImplementation("androidx.test:core-ktx:1.6.1")
    androidTestImplementation("androidx.test:runner:1.6.2")
    androidTestImplementation("androidx.test:rules:1.6.1")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.6.1")
    androidTestImplementation("androidx.test.espresso:espresso-contrib:3.6.1")
    androidTestImplementation("androidx.test.espresso:espresso-intents:3.6.1")
    androidTestImplementation("androidx.fragment:fragment-testing:1.8.5")
    androidTestImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
    androidTestImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.10.2")
    // Provides the empty host Activity that launchFragmentInContainer needs,
    // merged into the debug app manifest.
    debugImplementation("androidx.fragment:fragment-testing-manifest:1.8.5")
}
