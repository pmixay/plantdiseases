package com.plantdiseases.app.config

import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.BuildConfig
import org.junit.Test

class AccountBetaBuildIdentityTest {
    @Test
    fun betaCanCoexistAndHasConfigurationForItsSelectedAuthMode() {
        assertThat(BuildConfig.ACCOUNT_UI_ENABLED).isTrue()
        assertThat(BuildConfig.APPLICATION_ID).isEqualTo("com.plantdiseases.app.accountbeta")
        if (BuildConfig.SYNTHETIC_AUTH_ENABLED) {
            assertThat(BuildConfig.APP_API_BASE_URL)
                .isEqualTo("https://pdis.arbuz.lol/plantdisease-test/")
            assertThat(BuildConfig.ACCOUNT_API_BASE_URL)
                .isEqualTo("https://pdis.arbuz.lol/plantdisease-test/id/")
        } else {
            assertThat(BuildConfig.YANDEX_OAUTH_CONFIGURED).isTrue()
            assertThat(BuildConfig.VK_OAUTH_CONFIGURED).isTrue()
            assertThat(BuildConfig.APP_API_BASE_URL).startsWith("https://")
            assertThat(BuildConfig.ACCOUNT_API_BASE_URL).startsWith("https://")
        }
    }
}
