// Top-level build file
plugins {
    id("com.android.application") version "8.13.2" apply false
    id("org.jetbrains.kotlin.android") version "2.1.21" apply false
    id("com.google.devtools.ksp") version "2.1.21-2.0.2" apply false
}

// Make dependency resolution reproducible: lock every configuration so the
// resolved versions (including transitive ones) are pinned in
// `gradle.lockfile`. Regenerate after changing dependencies with:
//   ./gradlew :app:dependencies --write-locks
allprojects {
    dependencyLocking {
        lockAllConfigurations()
    }
}

task("clean", Delete::class) {
    delete(rootProject.layout.buildDirectory)
}
