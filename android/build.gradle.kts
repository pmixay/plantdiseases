// Top-level build file
plugins {
    id("com.android.application") version "8.13.2" apply false
    id("org.jetbrains.kotlin.android") version "2.1.21" apply false
    // Serialization compiler plugin — version locked to the Kotlin version so
    // the generated serializers match the compiler (Ktor + kotlinx migration).
    id("org.jetbrains.kotlin.plugin.serialization") version "2.1.21" apply false
    id("com.google.devtools.ksp") version "2.1.21-2.0.2" apply false
}

task("clean", Delete::class) {
    delete(rootProject.layout.buildDirectory)
}
