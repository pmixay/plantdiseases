// Top-level build file
plugins {
    id("com.android.application") version "8.13.2" apply false
    id("org.jetbrains.kotlin.android") version "2.1.21" apply false
    // Serialization compiler plugin — version locked to the Kotlin version so
    // the generated serializers match the compiler (Ktor + kotlinx migration).
    id("org.jetbrains.kotlin.plugin.serialization") version "2.1.21" apply false
    id("com.google.devtools.ksp") version "2.1.21-2.0.2" apply false
    // Hilt (Dagger) dependency injection. The Gradle plugin rewrites Hilt's
    // generated component classes into the app's bytecode; must be applied
    // here AND in app/build.gradle.kts per the standard Hilt setup.
    id("com.google.dagger.hilt.android") version "2.56.2" apply false
    // Kotlin linting replaces Android Lint as the code-quality gate:
    // ktlint (formatting/style) + detekt (static analysis / smells).
    id("org.jlleitschuh.gradle.ktlint") version "12.1.2" apply false
    id("io.gitlab.arturbosch.detekt") version "1.23.8" apply false
}

task("clean", Delete::class) {
    delete(rootProject.layout.buildDirectory)
}
