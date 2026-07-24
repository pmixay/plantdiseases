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
        // RuStore SDK (in-app updates) lives on VK's artifactory, not Maven
        // Central. Content-filtered so every other dependency keeps resolving
        // from google()/mavenCentral() only.
        maven("https://artifactory-external.vkpartner.ru/artifactory/maven") {
            content { includeGroupByRegex("""ru\.rustore(\..*)?""") }
        }
    }
}

rootProject.name = "PlantDiseases"
include(":app")
