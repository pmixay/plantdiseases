pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    @Suppress("UnstableApiUsage")
    repositories {
        google()
        mavenCentral()
        // RuStore SDK (in-app updates, reviews) lives on RuStore's own Nexus,
        // not Maven Central. RuStore retired the old VK artifactory
        // (artifactory-external.vkpartner.ru) on 2026-10-01; it now answers
        // 404 for every path. Content-filtered so every other dependency keeps
        // resolving from google()/mavenCentral() only.
        maven("https://nexus-external.rustore.ru/repository/maven-rustore-exposed") {
            content { includeGroupByRegex("""ru\.rustore(\..*)?""") }
        }
    }
}

rootProject.name = "PlantDiseases"
include(":app")
