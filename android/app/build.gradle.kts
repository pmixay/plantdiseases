plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.google.devtools.ksp")
}

android {
    namespace = "com.plantdiseases.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.plantdiseases.app"
        minSdk = 26 // Android 8.0
        targetSdk = 35
        versionCode = 11
        versionName = "1.1"

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

        // Firebase Cloud Messaging config. Defaults below are the public
        // client identifiers of the "plantdiseases-8ffeb" Firebase project
        // (from its google-services.json) — these are the same values shipped
        // inside every APK and are safe to commit; access is gated by app
        // signing + the server's service-account key, not their secrecy.
        // Override per-build with -PFCM_APP_ID=... (gradle property) or the
        // same-named env var to point at a different Firebase project.
        // We init Firebase MANUALLY from these (no google-services plugin), so
        // the build never hard-requires a google-services.json file.
        fun fcmProp(name: String, default: String): String =
            (providers.gradleProperty(name).orNull ?: System.getenv(name) ?: default)
                .replace("\\", "\\\\")
                .replace("\"", "\\\"")
        buildConfigField(
            "String", "FCM_APP_ID",
            "\"${fcmProp("FCM_APP_ID", "1:477261694434:android:ab17ca7387d4e5a390e5bb")}\"",
        )
        buildConfigField(
            "String", "FCM_API_KEY",
            "\"${fcmProp("FCM_API_KEY", "AIzaSyCaHSEXJDiEvPbh_O-T9GqhTg_9_E7oeqc")}\"",
        )
        buildConfigField(
            "String", "FCM_PROJECT_ID",
            "\"${fcmProp("FCM_PROJECT_ID", "plantdiseases-8ffeb")}\"",
        )
        buildConfigField(
            "String", "FCM_SENDER_ID",
            "\"${fcmProp("FCM_SENDER_ID", "477261694434")}\"",
        )

        // The Aqua family runs a TFLite model on-device. Limit the bundled
        // native .so libs to the ABIs real devices use — dropping x86/mips
        // keeps the APK from carrying interpreter libs no phone will load.
        // (The model itself is downloaded from the server into filesDir, not
        // bundled in the APK — see AquaModelManager.)
        ndk {
            abiFilters += listOf("arm64-v8a", "armeabi-v7a", "x86_64")
        }
    }

    signingConfigs {
        create("release") {
            val keystorePath = project.findProperty("RELEASE_STORE_FILE") as String?
            if (!keystorePath.isNullOrEmpty()) {
                storeFile = file(keystorePath)
                storePassword = project.findProperty("RELEASE_STORE_PASSWORD") as String? ?: ""
                keyAlias = project.findProperty("RELEASE_KEY_ALIAS") as String? ?: ""
                keyPassword = project.findProperty("RELEASE_KEY_PASSWORD") as String? ?: ""
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
            // These prebuilt .so libs (DataStore, CameraX JNI, TFLite) can't be
            // stripped by the NDK strip tool, which logs a noisy "Unable to
            // strip the following libraries" warning every build. They are
            // packaged as-is either way, so declaring them here just keeps
            // their (already-present) symbols and silences the warning — the
            // APK size is unchanged.
            keepDebugSymbols += listOf(
                "**/libdatastore_shared_counter.so",
                "**/libimage_processing_util_jni.so",
                "**/libsurface_util_jni.so",
                "**/libtensorflowlite_jni.so",
            )
        }
    }

    lint {
        // Fail the release build on lint errors so we catch regressions
        // in CI rather than on-device. Warnings are still non-fatal —
        // `warningsAsErrors = false` preserves that.
        abortOnError = true
        checkReleaseBuilds = true
        warningsAsErrors = false
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

    // Retrofit + OkHttp
    implementation("com.squareup.retrofit2:retrofit:2.11.0")
    implementation("com.squareup.retrofit2:converter-gson:2.11.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.squareup.okhttp3:logging-interceptor:4.12.0")

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

    // Preferences DataStore
    implementation("androidx.datastore:datastore-preferences:1.1.1")

    // Shimmer effect for skeleton loading
    implementation("com.facebook.shimmer:shimmer:0.5.0")

    // ExifInterface (used for orientation correction in ImageUtils)
    implementation("androidx.exifinterface:exifinterface:1.4.0")

    // WorkManager (plant reminders)
    implementation("androidx.work:work-runtime-ktx:2.10.0")

    // TensorFlow Lite — on-device inference for the Aqua model family.
    // The model artefact (assets/models/aqua_classifier.tflite) is provisioned
    // out of band like the server weights, never committed; AquaClassifier
    // gates on its presence and the app degrades to the server when absent.
    implementation("org.tensorflow:tensorflow-lite:2.16.1")

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
