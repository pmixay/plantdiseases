package com.plantdiseases.app

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class RecoveryBuildIdentityTest {
    @Test
    fun recoveryArtifactCanReplaceV19AndKeepsAccountsFailClosed() {
        assertThat(BuildConfig.APPLICATION_ID).isEqualTo("com.plantdiseases.app")
        assertThat(BuildConfig.RECOVERY_APK_VERSION_CODE).isEqualTo(20)
        assertThat(BuildConfig.VERSION_CODE).isEqualTo(20)
        assertThat(BuildConfig.VERSION_NAME).contains("recovery")
        when (BuildConfig.RECOVERY_ENDPOINT_SCOPE) {
            "production" -> {
                if (BuildConfig.RECOVERY_ENDPOINTS_CONFIGURED) {
                    assertThat(BuildConfig.APP_API_BASE_URL).startsWith("https://")
                    assertThat(BuildConfig.APP_API_BASE_URL).endsWith("/")
                    assertThat(BuildConfig.ACCOUNT_API_BASE_URL)
                        .isEqualTo("${BuildConfig.APP_API_BASE_URL}id/")
                    assertThat(BuildConfig.APP_API_BASE_URL).doesNotContain("/plantdisease-test/")
                } else {
                    // Credential-free ordinary CI has no production configuration.
                    // Its production-recovery artifact is inert, never silently staging.
                    assertThat(BuildConfig.APP_API_BASE_URL).isEqualTo("https://invalid.invalid/")
                    assertThat(BuildConfig.ACCOUNT_API_BASE_URL)
                        .isEqualTo("https://invalid.invalid/id/")
                }
            }

            "staging" -> {
                assertThat(BuildConfig.RECOVERY_ENDPOINTS_CONFIGURED).isTrue()
                assertThat(BuildConfig.APP_API_BASE_URL)
                    .isEqualTo("https://pdis.arbuz.lol/plantdisease-test/")
                assertThat(BuildConfig.ACCOUNT_API_BASE_URL)
                    .isEqualTo("https://pdis.arbuz.lol/plantdisease-test/id/")
            }

            else -> throw AssertionError("unexpected recovery endpoint scope")
        }
        assertThat(BuildConfig.ACCOUNT_UI_ENABLED).isFalse()
        assertThat(BuildConfig.SYNTHETIC_AUTH_ENABLED).isFalse()
    }
}
