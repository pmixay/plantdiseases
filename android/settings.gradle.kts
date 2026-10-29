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
        // RuStore SDKs (Pay, in-app updates, reviews) live on RuStore's own
        // Nexus, not Maven Central. RuStore retired the old VK artifactory
        // (artifactory-external.vkpartner.ru) on 2026-10-01; it now answers
        // 404 for every path. Content-filtered so every other dependency keeps
        // resolving from google()/mavenCentral() only.
        maven("https://nexus-external.rustore.ru/repository/maven-rustore-exposed") {
            content { includeGroupByRegex("""ru\.rustore(\..*)?""") }
        }
        // VK ID SDK (com.vk.id + its com.vk.id.captcha dependency) moved to the
        // same Nexus with the artifactory retirement. Its other transitive
        // groups (com.vk:android-sdk-id, ru.ok.tracer) resolve from Maven Central.
        maven("https://nexus-external.rustore.ru/repository/vkid-sdk-android") {
            content { includeGroupByRegex("""com\.vk\.id(\..*)?""") }
        }
    }
}

rootProject.name = "PlantDiseases"
include(":app")
