plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.serialization")
    id("com.google.devtools.ksp")
    id("com.google.dagger.hilt.android")
    id("org.jlleitschuh.gradle.ktlint")
    id("io.gitlab.arturbosch.detekt")
}

android {
    namespace = "com.plantdiseases.app"
    // compileSdk 37 is required by the current AndroidX line — androidx.core
    // 1.19.0 declares minCompileSdk=37 / minAgp=9.1.0 in its AAR metadata and
    // fails the build below either. AGP 9.1.1 (root build.gradle.kts) is the
    // other half of that requirement; the two only move together.
    compileSdk = 37

    defaultConfig {
        applicationId = "com.plantdiseases.app"
        minSdk = 29 // Android 10
        // Deliberately behind compileSdk, and stated explicitly (AGP 9
        // otherwise defaults targetSdk to compileSdk when unset). targetSdk is
        // what opts the app into a platform's *behaviour* changes; raising it
        // would turn on Android 16's mandatory edge-to-edge enforcement and
        // the stricter intent/foreground-service rules — a UI change to make
        // on its own terms, not a side effect of a dependency refresh.
        // Compiling against a newer SDK than you target is supported.
        targetSdk = 35
        versionCode = 18
        versionName = "1.8"

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
        // for it is ~7.6 MB — on x86_64 the app simply degrades to server
        // inference (the documented Aqua-absent fallback). armeabi-v7a is now
        // real: LiteRT shipped no 32-bit lib at 2.1.3, but 2.1.5 (below) does,
        // so this filter keeps it rather than silently matching nothing.
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
    // AndroidX Core. 1.19.0 needs compileSdk 37 + AGP 9.1.0, both of which
    // this build now has.
    implementation("androidx.core:core-ktx:1.19.0")
    implementation("androidx.core:core-splashscreen:1.2.0")
    implementation("androidx.appcompat:appcompat:1.8.0")
    implementation("androidx.activity:activity-ktx:1.13.0")
    implementation("androidx.fragment:fragment-ktx:1.9.0")
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

    // OkHttp — the engine the Ktor client runs on (shared RetryInterceptor +
    // logging). Retrofit/converter-gson were removed once the last endpoint
    // moved to the Ktor client; Gson itself stays for Room field + cache JSON.
    // OkHttp 5 keeps the OkHttpClient/Interceptor API this project uses, and
    // com.squareup.okhttp3:mockwebserver still ships the legacy
    // okhttp3.mockwebserver package, so the network tests below are unchanged.
    implementation("com.squareup.okhttp3:okhttp:5.4.0")
    implementation("com.squareup.okhttp3:logging-interceptor:5.4.0")

    // Ktor client + kotlinx.serialization — incrementally replaces Retrofit.
    // Uses the OkHttp engine so the existing RetryInterceptor, logging and
    // timeouts (configured on the shared OkHttpClient) carry over unchanged.
    // Ktor 3's breaking changes are in the IO/multipart internals and the
    // server artefacts; the client surface used here (HttpClient(OkHttp),
    // ContentNegotiation, defaultRequest, get/post/setBody, and
    // MultiPartFormDataContent + formData over ByteArrays in ScanRepository)
    // is source-compatible. It needed Kotlin >= 2.2 on the compiler side,
    // which is what blocked it before — 3.5.2 pulls a 2.3.x stdlib that the
    // old 2.1.21 compiler refused to read.
    implementation(platform("io.ktor:ktor-bom:3.5.2"))
    implementation("io.ktor:ktor-client-core")
    implementation("io.ktor:ktor-client-okhttp")
    implementation("io.ktor:ktor-client-content-negotiation")
    implementation("io.ktor:ktor-serialization-kotlinx-json")
    // Still declared explicitly (rather than inherited from the Ktor BOM) so
    // the runtime and the serialization compiler plugin stay in lockstep.
    // 1.11.0 is built against kotlin-stdlib 2.3.20 — older than the 2.4.10
    // compiler now in use, so it reads fine; it was 1.8.1 only because the
    // 2.1.21 compiler could not.
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.11.0")

    // Firebase Cloud Messaging (push). No google-services plugin / json:
    // Firebase is initialised manually from BuildConfig.FCM_* at runtime, so
    // the build works with or without credentials.
    implementation(platform("com.google.firebase:firebase-bom:34.17.0"))
    implementation("com.google.firebase:firebase-messaging")

    // RuStore in-app updates — installs that came from RuStore get offered new
    // builds in-place (flexible flow, util/InAppUpdateManager). Resolved from
    // VK's artifactory (see settings.gradle.kts). On every other install
    // (sideload, no RuStore app) the SDK fails into its failure listeners and
    // the app carries on — the usual optional-integration degradation.
    implementation("ru.rustore.sdk:appupdate:10.5.0")

    // Gson
    implementation("com.google.code.gson:gson:2.14.0")

    // Glide for images. On 5.x — the app only uses the plain
    // Glide.with(...).load(...) surface (no @GlideModule / generated GlideApp),
    // which is the part Glide 5 kept source-compatible.
    implementation("com.github.bumptech.glide:glide:5.0.9")
    ksp("com.github.bumptech.glide:ksp:5.0.9")

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
    implementation("com.google.dagger:hilt-android:2.60.1")
    ksp("com.google.dagger:hilt-android-compiler:2.60.1")

    // Coroutines
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.11.0")

    // Lifecycle
    implementation("androidx.lifecycle:lifecycle-viewmodel-ktx:2.11.0")
    implementation("androidx.lifecycle:lifecycle-livedata-ktx:2.11.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.11.0")

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
    // Was held at 2.1.3 — the newest release the old Kotlin 2.1.21 compiler
    // could read, since 2.1.4+ moved its bundled Kotlin metadata to 2.3.0 and
    // failed with "Module was compiled with an incompatible version of
    // Kotlin". The Kotlin bump cleared that, so this moves to 2.1.5, which
    // keeps the same org.tensorflow.lite Interpreter +
    // Interpreter.Options(setNumThreads) surface AquaClassifier uses and adds
    // an armeabi-v7a libLiteRt.so (2.1.3 was arm64-only) — so 32-bit devices
    // now run Aqua on-device instead of degrading to server inference, which
    // is what finally makes armeabi-v7a in the abiFilters above do something.
    //
    // NOT 2.1.6+ / 2.2.0, and this is an upstream packaging defect rather
    // than a version ceiling of ours: 2.1.6 split the artefact into
    // `litert` + `litert-api`, and BOTH AARs declare the same namespace
    // `com.google.ai.edge.litert`. AGP 9 flipped android.uniquePackageNames
    // to true by default, so the merger now rejects it outright:
    //   Namespace 'com.google.ai.edge.litert' is used in multiple modules
    //   and/or libraries: litert:2.2.0, litert-api:2.2.0
    // The alternative is android.uniquePackageNames=false, i.e. switching a
    // project-wide integrity check off to accommodate one vendor's mistake —
    // not worth one minor version. Revisit when litert-api gets its own
    // namespace.
    implementation("com.google.ai.edge.litert:litert:2.1.5")

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
    testImplementation("com.squareup.okhttp3:mockwebserver:5.4.0")
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
    androidTestImplementation("androidx.fragment:fragment-testing:1.9.0")
    androidTestImplementation("com.squareup.okhttp3:mockwebserver:5.4.0")
    androidTestImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.10.2")
    // Provides the empty host Activity that launchFragmentInContainer needs,
    // merged into the debug app manifest.
    debugImplementation("androidx.fragment:fragment-testing-manifest:1.9.0")
}
