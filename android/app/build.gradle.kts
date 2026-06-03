plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.google.devtools.ksp")
}

android {
    namespace = "com.plantdiseases.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.plantdiseases.app"
        minSdk = 26 // Android 8.0
        targetSdk = 34
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
            if (releaseConfig?.storeFile != null) {
                signingConfig = releaseConfig
            }
        }
    }

    buildFeatures {
        viewBinding = true
        buildConfig = true
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
    implementation("androidx.core:core-ktx:1.12.0")
    implementation("androidx.core:core-splashscreen:1.0.1")
    implementation("androidx.appcompat:appcompat:1.6.1")
    implementation("androidx.activity:activity-ktx:1.8.2")
    implementation("androidx.fragment:fragment-ktx:1.6.2")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")
    implementation("androidx.recyclerview:recyclerview:1.3.2")
    implementation("androidx.swiperefreshlayout:swiperefreshlayout:1.1.0")

    // Material Design 3
    implementation("com.google.android.material:material:1.11.0")

    // Image cropper (vanniktech fork — on mavenCentral, classes live in
    // package com.canhub.cropper). Lets the user crop a captured shot or
    // gallery image before it is sent for analysis.
    implementation("com.vanniktech:android-image-cropper:4.6.0")

    // Navigation
    implementation("androidx.navigation:navigation-fragment-ktx:2.7.6")
    implementation("androidx.navigation:navigation-ui-ktx:2.7.6")

    // CameraX
    val cameraxVersion = "1.3.1"
    implementation("androidx.camera:camera-core:$cameraxVersion")
    implementation("androidx.camera:camera-camera2:$cameraxVersion")
    implementation("androidx.camera:camera-lifecycle:$cameraxVersion")
    implementation("androidx.camera:camera-view:$cameraxVersion")

    // Room Database
    val roomVersion = "2.6.1"
    implementation("androidx.room:room-runtime:$roomVersion")
    implementation("androidx.room:room-ktx:$roomVersion")
    ksp("androidx.room:room-compiler:$roomVersion")

    // Retrofit + OkHttp
    implementation("com.squareup.retrofit2:retrofit:2.9.0")
    implementation("com.squareup.retrofit2:converter-gson:2.9.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.squareup.okhttp3:logging-interceptor:4.12.0")

    // Gson
    implementation("com.google.code.gson:gson:2.10.1")

    // Glide for images
    implementation("com.github.bumptech.glide:glide:4.16.0")
    ksp("com.github.bumptech.glide:ksp:4.16.0")

    // Coroutines
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.7.3")

    // Lifecycle
    implementation("androidx.lifecycle:lifecycle-viewmodel-ktx:2.7.0")
    implementation("androidx.lifecycle:lifecycle-livedata-ktx:2.7.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.7.0")

    // Lottie animations
    implementation("com.airbnb.android:lottie:6.3.0")

    // ViewPager2
    implementation("androidx.viewpager2:viewpager2:1.0.0")

    // Preferences DataStore
    implementation("androidx.datastore:datastore-preferences:1.0.0")

    // Shimmer effect for skeleton loading
    implementation("com.facebook.shimmer:shimmer:0.5.0")

    // ExifInterface (used for orientation correction in ImageUtils)
    implementation("androidx.exifinterface:exifinterface:1.3.7")

    // WorkManager (plant reminders)
    implementation("androidx.work:work-runtime-ktx:2.9.0")

    // ─── Local JVM unit tests (./gradlew testDebugUnitTest) ───────────────
    // Robolectric lets the image / ML-diagnosis pipeline run on the JVM
    // (Bitmap, Room in-memory DB, resources) without a device, so the
    // bulk of the pipeline is covered in CI-runnable unit tests.
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.robolectric:robolectric:4.11.1")
    testImplementation("androidx.test:core:1.5.0")
    testImplementation("androidx.test:core-ktx:1.5.0")
    testImplementation("androidx.test.ext:junit:1.1.5")
    testImplementation("androidx.test.ext:junit-ktx:1.1.5")
    testImplementation("com.google.truth:truth:1.1.5")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.7.3")
    // MockWebServer stands in for the diagnosis server so Retrofit /
    // OkHttp request building + the RetryInterceptor are exercised for real.
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
    testImplementation("androidx.arch.core:core-testing:2.2.0")
    testImplementation("androidx.room:room-testing:2.6.1")

    // ─── Instrumented / Espresso UI tests (./gradlew connectedDebugAndroidTest,
    //     device or emulator required) ──────────────────────────────────────
    androidTestImplementation("androidx.test.ext:junit:1.1.5")
    androidTestImplementation("androidx.test:core:1.5.0")
    androidTestImplementation("androidx.test:core-ktx:1.5.0")
    androidTestImplementation("androidx.test:runner:1.5.2")
    androidTestImplementation("androidx.test:rules:1.5.0")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.5.1")
    androidTestImplementation("androidx.test.espresso:espresso-contrib:3.5.1")
    androidTestImplementation("androidx.test.espresso:espresso-intents:3.5.1")
    androidTestImplementation("androidx.fragment:fragment-testing:1.6.2")
    androidTestImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
    androidTestImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.7.3")
    // Provides the empty host Activity that launchFragmentInContainer needs,
    // merged into the debug app manifest.
    debugImplementation("androidx.fragment:fragment-testing-manifest:1.6.2")
}
