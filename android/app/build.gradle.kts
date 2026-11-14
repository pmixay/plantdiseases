import java.net.URI
import java.security.MessageDigest

plugins {
    id("com.android.application")
    // No org.jetbrains.kotlin.android — AGP 9's built-in Kotlin compiles this
    // module. The serialization compiler plugin still applies on top of it.
    id("org.jetbrains.kotlin.plugin.serialization")
    id("com.google.devtools.ksp")
    id("com.google.dagger.hilt.android")
    id("org.jlleitschuh.gradle.ktlint")
    id("io.gitlab.arturbosch.detekt")
}

// OAuth configuration can come from an environment variable or a Gradle
// property. Environment values deliberately win: the protected distribution
// workflow must not be shadowed by a committed or runner-local
// gradle.properties value. This also keeps extractable mobile client values
// out of command lines and lets CI inject them without committing local state.
fun configuredValue(name: String): String =
    providers.environmentVariable(name)
        .orElse(providers.gradleProperty(name))
        .orNull
        ?.trim()
        .orEmpty()

fun sha256(value: String): String =
    MessageDigest
        .getInstance("SHA-256")
        .digest(value.toByteArray(Charsets.UTF_8))
        .joinToString("") { byte -> "%02x".format(byte.toInt() and 0xff) }

// Yandex LoginSDK uses a public native client ID. Pin the registered
// com.plantdiseases.app value so every PlantDiseases 1.9 RuStore release has
// Yandex enabled without accepting or embedding the Yandex client secret.
val yandexClientId = "2b439944bb4c4e33b85e20290aca6c7f"
val vkClientId = configuredValue("VKID_CLIENT_ID")
val vkClientSecret = configuredValue("VKID_CLIENT_SECRET")
// These are public RuStore Console identifiers, not payment credentials.
// They remain blank in ordinary builds; protected distribution automation or
// a developer's local Gradle properties may supply them independently.
val rustoreConsoleAppId = configuredValue("RUSTORE_CONSOLE_APP_ID")
val rustoreProMonthlyProductId = configuredValue("RUSTORE_PRO_MONTHLY_PRODUCT_ID")
val rustoreProYearlyProductId = configuredValue("RUSTORE_PRO_YEARLY_PRODUCT_ID")
val rustoreConsoleAppIdValid = rustoreConsoleAppId.isNotBlank() &&
    rustoreConsoleAppId.all { it in '0'..'9' } &&
    rustoreConsoleAppId.any { it in '1'..'9' }
val rustorePayConfigured = rustoreConsoleAppIdValid &&
    (rustoreProMonthlyProductId.isNotBlank() || rustoreProYearlyProductId.isNotBlank())
val rustorePayScheme = "plantdiseases-rustore-pay"
val yandexOAuthConfigured = yandexClientId.isNotBlank()
val vkOAuthConfigured = vkClientId.isNotBlank() && vkClientSecret.isNotBlank()
// Every build talks to the production server. Override both URLs together
// only for a local mock stack (-PAPP_API_BASE_URL=… -PACCOUNT_API_BASE_URL=…).
// The recovery variant ignores the override: it always targets production.
val productionAppApiBaseUrl = "https://pdis.arbuz.lol/"
val productionAccountApiBaseUrl = "https://pdis.arbuz.lol/id/"
val appApiBaseUrl = configuredValue("APP_API_BASE_URL")
    .ifBlank { productionAppApiBaseUrl }
val accountApiBaseUrl = configuredValue("ACCOUNT_API_BASE_URL")
    .ifBlank { productionAccountApiBaseUrl }
val recoveryVersionCode = 20
val realOAuthEnabled = when (
    val raw = configuredValue("ACCOUNT_REAL_OAUTH").lowercase()
) {
    "", "true" -> true
    "false" -> false
    else -> throw GradleException("ACCOUNT_REAL_OAUTH must be true or false")
}
val unsignedDistributionBuild = when (
    val raw = configuredValue("ANDROID_UNSIGNED_DISTRIBUTION").lowercase()
) {
    "", "false" -> false
    "true" -> true
    else -> throw GradleException("ANDROID_UNSIGNED_DISTRIBUTION must be true or false")
}
val distributionChannel = configuredValue("ANDROID_DISTRIBUTION_CHANNEL").lowercase()
val distributionChannels = setOf("production", "recovery-production")
if (distributionChannel.isNotBlank() && distributionChannel !in distributionChannels) {
    throw GradleException(
        "ANDROID_DISTRIBUTION_CHANNEL must be production or recovery-production",
    )
}
if (unsignedDistributionBuild && distributionChannel.isBlank()) {
    throw GradleException("ANDROID_DISTRIBUTION_CHANNEL is required for an unsigned distribution build")
}
if (distributionChannel == "production") {
    val missingRuStoreValues = buildList {
        if (rustoreConsoleAppId.isBlank()) add("RUSTORE_CONSOLE_APP_ID")
        if (rustoreProMonthlyProductId.isBlank()) add("RUSTORE_PRO_MONTHLY_PRODUCT_ID")
        if (rustoreProYearlyProductId.isBlank()) add("RUSTORE_PRO_YEARLY_PRODUCT_ID")
    }
    if (missingRuStoreValues.isNotEmpty()) {
        throw GradleException(
            "PRO-enabled distribution requires: ${missingRuStoreValues.joinToString()}",
        )
    }
    if (!rustoreConsoleAppIdValid) {
        throw GradleException("RUSTORE_CONSOLE_APP_ID must be a non-zero decimal RuStore application ID")
    }
    if (rustoreProMonthlyProductId != "plantdiseases_pro_monthly" ||
        rustoreProYearlyProductId != "plantdiseases_pro_yearly"
    ) {
        throw GradleException(
            "RuStore PRO product IDs must match the account-server allowlist",
        )
    }
}
val emailPasswordAuthOverride = when (
    val raw = configuredValue("EMAIL_PASSWORD_AUTH_ENABLED").lowercase()
) {
    "" -> null
    "true" -> true
    "false" -> false
    else -> throw GradleException("EMAIL_PASSWORD_AUTH_ENABLED must be true or false")
}
// Email/password is off unless a local mock-stack build opts in with
// EMAIL_PASSWORD_AUTH_ENABLED=true: every ordinary build talks to the
// production account service, which rejects it. A production distribution
// cannot opt in at all.
if (distributionChannel == "production" && emailPasswordAuthOverride == true) {
    throw GradleException(
        "EMAIL_PASSWORD_AUTH_ENABLED is blocked for production until release gates close",
    )
}
val emailPasswordAuthEnabled = when (distributionChannel) {
    "production", "recovery-production" -> false
    else -> emailPasswordAuthOverride ?: false
}

fun validateHttpsEndpoint(name: String, raw: String): URI {
    val uri = runCatching { URI(raw) }.getOrNull()
    val valid = uri != null &&
        uri.scheme == "https" &&
        !uri.host.isNullOrBlank() &&
        uri.userInfo == null &&
        uri.query == null &&
        uri.fragment == null &&
        raw.endsWith("/") &&
        raw.none(Char::isWhitespace)
    if (!valid) {
        throw GradleException("$name must be an absolute credential-free HTTPS URL ending in /")
    }
    return checkNotNull(uri)
}

if (realOAuthEnabled) {
    if (vkClientId.isBlank() != vkClientSecret.isBlank()) {
        throw GradleException(
            "VK ID requires both VKID_CLIENT_ID and VKID_CLIENT_SECRET when enabled",
        )
    }
    if (!yandexOAuthConfigured && !vkOAuthConfigured) {
        throw GradleException(
            "Real OAuth requires a bundled Yandex client ID, or both " +
                "VKID_CLIENT_ID and VKID_CLIENT_SECRET",
        )
    }
    if (vkOAuthConfigured && !vkClientId.all { it.isDigit() }) {
        throw GradleException("VKID_CLIENT_ID must be a decimal application ID")
    }
    listOf(
        "APP_API_BASE_URL" to appApiBaseUrl,
        "ACCOUNT_API_BASE_URL" to accountApiBaseUrl,
    ).forEach { (name, raw) ->
        validateHttpsEndpoint(name, raw)
    }
}

android {
    namespace = "com.plantdiseases.app"
    // compileSdk 37 is required by the current AndroidX line — androidx.core
    // 1.19.0 declares minCompileSdk=37 / minAgp=9.1.0 in its AAR metadata and
    // fails the build below either. AGP 9.1.1 (root build.gradle.kts) is the
    // other half of that requirement; the two only move together.
    compileSdk = 37

    // This module compiles no native code; the NDK is here for one tool.
    // AGP strips the prebuilt .so files that come in with AARs (CameraX,
    // LiteRT, DataStore, graphics-path) using llvm-strip from the NDK, looks
    // for it only at <sdk>/ndk/<this exact version>, and never downloads an
    // NDK for that task. With the version absent it packages every library
    // unstripped and logs "Unable to strip the following libraries".
    // Unpinned, "this version" is AGP's built-in default, which changes with
    // each AGP release and which the CI image does not ship. Pinned to the
    // NDK LTS the image does ship; the android job in ci.yml reads this line
    // and installs that version itself if the image ever stops carrying it.
    // A machine without it still builds, with that warning.
    ndkVersion = "27.3.13750724"

    defaultConfig {
        applicationId = "com.plantdiseases.app"
        minSdk = 31 // Android 12
        // Deliberately behind compileSdk, and stated explicitly (AGP 9
        // otherwise defaults targetSdk to compileSdk when unset). targetSdk is
        // what opts the app into a platform's *behaviour* changes; raising it
        // would turn on Android 16's mandatory edge-to-edge enforcement and
        // the stricter intent/foreground-service rules — a UI change to make
        // on its own terms, not a side effect of a dependency refresh.
        // Compiling against a newer SDK than you target is supported.
        targetSdk = 35
        versionCode = 19
        versionName = "1.9"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        // Public application traffic and private account traffic deliberately
        // have independent base URLs; both default to production.
        val configuredAppApiBaseUrl = appApiBaseUrl
        val configuredAccountApiBaseUrl = accountApiBaseUrl
        fun quotedUrl(raw: String): String {
            val normalized = if (raw.endsWith("/")) raw else "$raw/"
            return "\"${normalized.replace("\\", "\\\\").replace("\"", "\\\"")}\""
        }
        buildConfigField("String", "APP_API_BASE_URL", quotedUrl(configuredAppApiBaseUrl))
        buildConfigField("String", "ACCOUNT_API_BASE_URL", quotedUrl(configuredAccountApiBaseUrl))
        buildConfigField("boolean", "ACCOUNT_UI_ENABLED", "true")
        buildConfigField("boolean", "SYNTHETIC_AUTH_ENABLED", (!realOAuthEnabled).toString())
        buildConfigField(
            "boolean",
            "EMAIL_PASSWORD_AUTH_ENABLED",
            emailPasswordAuthEnabled.toString(),
        )
        buildConfigField(
            "String",
            "DISTRIBUTION_CHANNEL",
            "\"${distributionChannel.ifBlank { "local-beta" }}\"",
        )

        // OAuth mobile application values are public/extractable client
        // configuration. Yandex is pinned for the 1.9 RuStore release; VK still
        // fails closed unless its own complete configuration is supplied.
        // Package/signature registration remains an operator step.
        buildConfigField("boolean", "YANDEX_OAUTH_CONFIGURED", yandexOAuthConfigured.toString())
        buildConfigField("String", "YANDEX_CLIENT_ID_SHA256", "\"${sha256(yandexClientId)}\"")
        buildConfigField(
            "boolean",
            "VK_OAUTH_CONFIGURED",
            vkOAuthConfigured.toString(),
        )
        buildConfigField("String", "VKID_CLIENT_ID_SHA256", "\"${sha256(vkClientId)}\"")
        buildConfigField("String", "VKID_CLIENT_SECRET_SHA256", "\"${sha256(vkClientSecret)}\"")
        fun quotedPublicIdentifier(raw: String): String =
            "\"${raw.replace("\\", "\\\\").replace("\"", "\\\"")}\""
        buildConfigField(
            "String",
            "RUSTORE_CONSOLE_APP_ID",
            quotedPublicIdentifier(rustoreConsoleAppId),
        )
        buildConfigField(
            "String",
            "RUSTORE_PRO_MONTHLY_PRODUCT_ID",
            quotedPublicIdentifier(rustoreProMonthlyProductId),
        )
        buildConfigField(
            "String",
            "RUSTORE_PRO_YEARLY_PRODUCT_ID",
            quotedPublicIdentifier(rustoreProYearlyProductId),
        )
        buildConfigField("boolean", "RUSTORE_PAY_CONFIGURED", rustorePayConfigured.toString())
        manifestPlaceholders += mapOf(
            "YANDEX_CLIENT_ID" to yandexClientId.ifBlank { "0" },
            "YANDEX_OAUTH_ENABLED" to yandexOAuthConfigured.toString(),
            "VKIDClientID" to vkClientId.ifBlank { "0" },
            "VKIDClientSecret" to vkClientSecret.ifBlank { "unconfigured" },
            "VKIDRedirectHost" to "vk.ru",
            "VKIDRedirectScheme" to "vk${vkClientId.ifBlank { "0" }}",
            "VKID_OAUTH_ENABLED" to vkOAuthConfigured.toString(),
            // A disabled provider must still receive a syntactically valid
            // manifest value. BuildConfig retains the real blank so the app
            // can render its explicit "not configured" state.
            "RUSTORE_CONSOLE_APP_ID" to rustoreConsoleAppId.ifBlank { "0" },
            "RUSTORE_PAY_ENABLED" to rustorePayConfigured.toString(),
            "RUSTORE_PAY_SCHEME" to rustorePayScheme,
        )

        // Firebase Cloud Messaging client config. These four values are the
        // public client identifiers of a Firebase project (the same ones that
        // normally live in google-services.json). They are NOT injected with
        // any default here — they must be supplied at build time via a Gradle
        // property, a same-named environment variable, or (in CI) a GitHub
        // Actions variable. See DEPLOYMENT.md (Android release).
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
        debug {
            // When the operator has configured the permanent key outside the
            // repository, Android Studio's Run button uses it too, matching
            // the certificate registered with Yandex. A fresh contributor
            // checkout still falls back to Gradle's ordinary debug key.
            signingConfigs.findByName("release")
                ?.takeIf { it.storeFile != null }
                ?.let { signingConfig = it }
        }

        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
            val releaseConfig = signingConfigs.findByName("release")
            // A configured store key wins (CI supplies the permanent key);
            // without one the APK stays installable on the debug key.
            // ANDROID_UNSIGNED_DISTRIBUTION=true leaves it unsigned for a
            // separate signing step.
            signingConfig = when {
                unsignedDistributionBuild -> null
                releaseConfig?.storeFile != null -> releaseConfig
                else -> signingConfigs.getByName("debug")
            }
            buildConfigField("boolean", "ACCOUNT_UI_ENABLED", "true")
            buildConfigField("boolean", "SYNTHETIC_AUTH_ENABLED", "false")
        }

        // Same-package, same-certificate emergency rollback target: versionCode
        // 20 with the account UI disabled, always bound to the production
        // endpoints so its encrypted vault and queued remote revocations stay
        // with the service that issued them.
        create("recoveryProduction") {
            initWith(getByName("release"))
            matchingFallbacks += listOf("release")
            versionNameSuffix = "-recovery-production"
            buildConfigField("String", "APP_API_BASE_URL", "\"$productionAppApiBaseUrl\"")
            buildConfigField("String", "ACCOUNT_API_BASE_URL", "\"$productionAccountApiBaseUrl\"")
            buildConfigField("boolean", "ACCOUNT_UI_ENABLED", "false")
            buildConfigField("boolean", "SYNTHETIC_AUTH_ENABLED", "false")
            buildConfigField("boolean", "EMAIL_PASSWORD_AUTH_ENABLED", "false")
            buildConfigField("boolean", "YANDEX_OAUTH_CONFIGURED", "false")
            buildConfigField("boolean", "VK_OAUTH_CONFIGURED", "false")
            buildConfigField("boolean", "RUSTORE_PAY_CONFIGURED", "false")
            manifestPlaceholders["YANDEX_OAUTH_ENABLED"] = "false"
            manifestPlaceholders["VKID_OAUTH_ENABLED"] = "false"
            manifestPlaceholders["RUSTORE_PAY_ENABLED"] = "false"
            buildConfigField("int", "RECOVERY_APK_VERSION_CODE", recoveryVersionCode.toString())
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

            // No keepDebugSymbols entries, on purpose. Two CameraX libs used
            // to be listed here as "unstrippable"; they strip cleanly. The
            // "Unable to strip" warning they were hiding meant AGP had found
            // no NDK at all (see ndkVersion above), so nothing was stripped.
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
                // Fan the test classes out over the machine's cores: ~58
                // Robolectric classes, each CPU-bound instrumenting
                // android-all into its own sandbox classloader, and one fork
                // means one core. Forks are separate JVMs, so this makes the
                // tests MORE isolated than the shared-JVM default, not less.
                // Capped at 4 because the fork count multiplies peak heap and
                // the GitHub runner this has to fit in is 2 cores / 7 GB.
                //
                // What this is and is not worth, measured on a --profile run:
                // in a full CI invocation the test task overlaps
                // minifyReleaseWithR8 (Gradle runs both as async workers —
                // task time summed to 15m44 against a 9m33 build), and R8 at
                // 5m08 is the longer of the two, so forking buys little wall
                // clock there and does compete with R8 for the two cores.
                // It pays when the test task IS the critical path: a run whose
                // release variant comes from the build cache, and
                // `./gradlew testDebugUnitTest` on its own, where the suite
                // goes from minutes to ~20-30s.
                it.maxParallelForks = Runtime.getRuntime().availableProcessors().coerceAtMost(4)

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

androidComponents {
    // AGP 9 creates only the debug unit-test component by default for this
    // legacy-DSL project. Release signing depends on identity assertions for
    // release and the recovery variant, so keep those components
    // explicit instead of allowing the workflow tasks to disappear silently.
    beforeVariants(selector().all()) { variantBuilder ->
        (variantBuilder as com.android.build.api.variant.ApplicationVariantBuilder)
            .hostTests[com.android.build.api.variant.HostTestBuilder.UNIT_TEST_TYPE]
            ?.enable = true
    }

    onVariants(selector().withBuildType("recoveryProduction")) { variant ->
        variant.outputs.forEach { output ->
            output.versionCode.set(recoveryVersionCode)
        }
    }
}

kotlin {
    // Still the right home for compiler options under built-in Kotlin — it is
    // `android.kotlinOptions` that AGP 9 dropped, not this block. jvmTarget
    // would default to compileOptions.targetCompatibility; stated explicitly
    // so the two can't drift apart silently.
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

// ─── Kotlin linting (replaces Android Lint) ───────────────────────────────────
// Gating: ktlint (formatting/style) + detekt (static analysis) fail the build
// on any violation. The baselines (config/ktlint/baseline.xml,
// config/detekt/baseline.xml) once grandfathered a backlog; that backlog is now
// fixed and both files are empty, so the gate is zero-tolerance. Keep them empty
// — a violation is meant to be fixed, not baselined. They stay wired up only for
// a rule-set bump, where `./gradlew ktlintGenerateBaseline detektBaseline`
// regenerates them so the bump and the cleanup can land separately.
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
    version.set("1.5.0")
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

    // OkHttp — the engine the Ktor client runs on (shared RetryInterceptor).
    // Retrofit/converter-gson were removed once the last endpoint
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

    // Official mobile OAuth SDKs. Each remains inert until its own build-time
    // client configuration is supplied; Yandex-only, VK-only, and combined
    // builds are all supported.
    implementation("com.yandex.android:authsdk:3.2.1")
    implementation("com.vk.id:vkid:2.7.2")

    // RuStore in-app updates — installs that came from RuStore get offered new
    // builds in-place (flexible flow, util/InAppUpdateManager). Resolved from
    // RuStore's Nexus (see settings.gradle.kts). On every other install
    // (sideload, no RuStore app) the SDK fails into its failure listeners and
    // the app carries on — the usual optional-integration degradation.
    implementation("ru.rustore.sdk:appupdate:10.5.0")

    // RuStore in-app reviews — on a few genuine engaged-user moments on the
    // Result screen (confirmed-correct diagnosis, scan attached to a garden
    // plant, care plan turned into reminders), offers RuStore's native
    // rate/review dialog (util/InAppReviewManager). Same artifactory as
    // appupdate above; RuStore caps this at one launch a day
    // (RuStoreRequestLimitReached) and we throttle further locally.
    implementation("ru.rustore.sdk:review:10.0.0")

    // Current RuStore Pay SDK. This is intentionally the Pay API, not the
    // retired billingClient API. Its public Product.amountLabel is the sole
    // source of displayed subscription prices.
    implementation("ru.rustore.sdk:pay:11.1.0")

    // Gson
    implementation("com.google.code.gson:gson:2.14.0")

    // Glide for images. On 5.x — the app only uses the plain
    // Glide.with(...).load(...) surface (no @GlideModule / generated GlideApp),
    // which is the part Glide 5 kept source-compatible. The Glide KSP
    // annotation processor is deliberately NOT declared: with no
    // @GlideModule/@AppGlideModule/@GlideExtension anywhere it generates
    // nothing. Re-add `ksp("com.github.bumptech.glide:ksp:5.0.9")` (and the
    // matching keeps in proguard-rules.pro) the day one is introduced.
    implementation("com.github.bumptech.glide:glide:5.0.9")

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

    // Lifecycle. Runtime only — this app has no ViewModel and no LiveData
    // (activity + controller + repository, see HomeFragment's note), so the
    // viewmodel/livedata artifacts would only be dead weight.
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.11.0")

    // Lottie animations
    implementation("com.airbnb.android:lottie:6.7.1")

    // ViewPager2
    implementation("androidx.viewpager2:viewpager2:1.1.0")

    // osmdroid — OpenStreetMap renderer for the community scans map. Keyless,
    // no Play Services (mavenCentral). We draw the aggregated heat cells +
    // scan markers ourselves; OSM raster tiles need a descriptive User-Agent.
    implementation("org.osmdroid:osmdroid-android:6.1.20")

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
    // Pure-Java FlatBuffer metadata reader used to validate a downloaded
    // Aqua graph's tensor contract before its bundle pointer is activated.
    // This inspects shapes/dtypes only; it does not execute the model.
    implementation("org.tensorflow:tensorflow-lite-metadata:0.2.0")

    // ─── Local JVM unit tests (./gradlew testDebugUnitTest) ───────────────
    // Robolectric lets the image / ML-diagnosis pipeline run on the JVM
    // (Bitmap, Room in-memory DB, resources) without a device, so the
    // bulk of the pipeline is covered in CI-runnable unit tests.
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.robolectric:robolectric:4.16.1")
    testImplementation("androidx.test:core:1.7.0")
    testImplementation("com.google.truth:truth:1.4.5")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.10.2")
    // MockWebServer stands in for the diagnosis server so Ktor/OkHttp
    // request building + the RetryInterceptor are exercised for real.
    testImplementation("com.squareup.okhttp3:mockwebserver:5.4.0")

    // ─── Instrumented / Espresso UI tests (./gradlew connectedDebugAndroidTest,
    //     device or emulator required) ──────────────────────────────────────
    androidTestImplementation("androidx.test.ext:junit:1.3.0")
    androidTestImplementation("androidx.test:core:1.7.0")
    androidTestImplementation("androidx.test:runner:1.7.0")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.7.0")
    androidTestImplementation("androidx.test.uiautomator:uiautomator:2.4.0")
}
