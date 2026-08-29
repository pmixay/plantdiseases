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
        // Current RuStore Pay SDK artefacts are published through the
        // exposed repository. Keep it content-filtered so it cannot affect
        // resolution of unrelated Android dependencies.
        maven("https://artifactory-external.vkpartner.ru/artifactory/maven-rustore-exposed/") {
            content { includeGroupByRegex("""ru\.rustore(\..*)?""") }
        }
        // RuStore SDK (in-app updates) lives on VK's artifactory, not Maven
        // Central. Content-filtered so every other dependency keeps resolving
        // from google()/mavenCentral() only.
        maven("https://artifactory-external.vkpartner.ru/artifactory/maven") {
            content {
                includeGroupByRegex("""ru\.rustore(\..*)?""")
                includeGroup("com.vk")
                includeGroup("com.vk.id")
            }
        }
        maven("https://artifactory-external.vkpartner.ru/artifactory/vkid-sdk-android/") {
            content { includeGroup("com.vk.id") }
        }
        maven("https://artifactory-external.vkpartner.ru/artifactory/vk-id-captcha/android/") {
            content { includeGroup("com.vk.id.captcha") }
        }
    }
}

rootProject.name = "PlantDiseases"
include(":app")
