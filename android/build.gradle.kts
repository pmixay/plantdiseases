// Top-level build file
plugins {
    // AGP 9. This is what lets compileSdk move to 37 and androidx.core to
    // 1.19.0 (its aar-metadata declares minCompileSdk=37, minAgp=9.1.0).
    //
    // 9.1.1 rather than the newest 9.3.1, on purpose: Kotlin 2.4.x lists AGP
    // 8.5.2–9.1.0 as its supported range, so 9.1.x is the newest AGP still
    // inside the Kotlin Gradle plugin's tested window. 9.2/9.3 want the next
    // Kotlin — move them together.
    //
    // Gradle: AGP 9.1.1 requires >= 9.3.1 and the wrapper is pinned at 9.5.0,
    // so no wrapper change is needed. JDK 17 and build-tools 36.0.0 are also
    // required and both are already what CI provisions.
    id("com.android.application") version "9.1.1" apply false
    // ── The Kotlin lockstep group ────────────────────────────────────────
    // These three moved together, from Kotlin 2.1.21. The serialization
    // compiler plugin must equal the Kotlin version exactly (it generates the
    // serializers the compiler then reads), which is why an automated bump of
    // only the plugin to 2.4.10 (#166) broke Gradle at configuration time —
    // the fix is to move the compiler with it, not to pin the plugin back.
    //
    // Kotlin 2.4.10 is the current stable and is what lifts the ceilings that
    // were pinning kotlinx-serialization, Ktor, OkHttp, Hilt and LiteRT to old
    // releases in app/build.gradle.kts. Gradle 9.5.0 is Kotlin 2.4.x's
    // maximum fully-supported Gradle, so the pinned wrapper matches exactly.
    id("org.jetbrains.kotlin.android") version "2.4.10" apply false
    id("org.jetbrains.kotlin.plugin.serialization") version "2.4.10" apply false
    // KSP is NOT part of that lockstep any more. As of KSP 2.3.0 the version
    // is independent of the Kotlin version (the old `<kotlin>-<ksp>` scheme is
    // gone) and a 2.3.x release supports Kotlin 2.2 and newer — so the bare
    // "2.3.11" proposed by the bot in #167 is a real, current release and is
    // correct against a 2.4.10 compiler. It was only broken in combination
    // with the 2.1.21 compiler it was merged against.
    id("com.google.devtools.ksp") version "2.3.11" apply false
    // Hilt (Dagger) dependency injection. The Gradle plugin rewrites Hilt's
    // generated component classes into the app's bytecode; must be applied
    // here AND in app/build.gradle.kts per the standard Hilt setup.
    // Was held at 2.57.2 because 2.60+ depends on kotlin-stdlib 2.3.21, which
    // the old 2.1.21 compiler could not read. With Kotlin 2.4.10 a 2.3.21
    // stdlib is simply an older metadata version, so the current line is fine.
    id("com.google.dagger.hilt.android") version "2.60.1" apply false
    // Kotlin linting replaces Android Lint as the code-quality gate:
    // ktlint (formatting/style) + detekt (static analysis / smells).
    // detekt 1.23.8 IS the current stable (the 2.x line is still pre-release).
    // The ktlint plugin is deliberately NOT on its current 14.x: both it and
    // the engine pin in app/build.gradle.kts feed baselines
    // (config/ktlint/baseline.xml) that only `./gradlew ktlintGenerateBaseline`
    // can regenerate, and nobody builds this project locally — so a rule-set
    // bump lands as a wall of CI failures with no way to re-baseline them in
    // the same change. Bump plugin + engine + baseline together, on their own.
    id("org.jlleitschuh.gradle.ktlint") version "12.1.2" apply false
    id("io.gitlab.arturbosch.detekt") version "1.23.8" apply false
}

task("clean", Delete::class) {
    delete(rootProject.layout.buildDirectory)
}
