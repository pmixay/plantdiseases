// Top-level build file
plugins {
    id("com.android.application") version "8.13.2" apply false
    id("org.jetbrains.kotlin.android") version "2.1.21" apply false
    // Serialization compiler plugin — version locked to the Kotlin version so
    // the generated serializers match the compiler (Ktor + kotlinx migration).
    id("org.jetbrains.kotlin.plugin.serialization") version "2.4.10" apply false
    id("com.google.devtools.ksp") version "2.3.11" apply false
    // Hilt (Dagger) dependency injection. The Gradle plugin rewrites Hilt's
    // generated component classes into the app's bytecode; must be applied
    // here AND in app/build.gradle.kts per the standard Hilt setup.
    // Hilt is held at the 2.57.x line on purpose: 2.58+ depends on
    // kotlin-stdlib 2.2.20 and 2.60+ on 2.3.21, either of which would drag a
    // stdlib newer than the Kotlin 2.1.21 compiler above onto the classpath.
    // 2.57.2 is the newest release still built against stdlib 2.1.21.
    id("com.google.dagger.hilt.android") version "2.57.2" apply false
    // Kotlin linting replaces Android Lint as the code-quality gate:
    // ktlint (formatting/style) + detekt (static analysis / smells).
    id("org.jlleitschuh.gradle.ktlint") version "12.1.2" apply false
    id("io.gitlab.arturbosch.detekt") version "1.23.8" apply false
}

task("clean", Delete::class) {
    delete(rootProject.layout.buildDirectory)
}
