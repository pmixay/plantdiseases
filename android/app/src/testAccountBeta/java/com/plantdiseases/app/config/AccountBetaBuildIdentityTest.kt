package com.plantdiseases.app.config

import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.BuildConfig
import org.junit.Test

class AccountBetaBuildIdentityTest {
    @Test
    fun betaCanCoexistAndUsesOnlyIsolatedPrefixDefaults() {
        assertThat(BuildConfig.ACCOUNT_UI_ENABLED).isTrue()
        assertThat(BuildConfig.SYNTHETIC_AUTH_ENABLED).isTrue()
        assertThat(BuildConfig.APPLICATION_ID).isEqualTo("com.plantdiseases.app.accountbeta")
        assertThat(BuildConfig.APP_API_BASE_URL)
            .isEqualTo("https://pdis.arbuz.lol/plantdisease-test/")
        assertThat(BuildConfig.ACCOUNT_API_BASE_URL)
            .isEqualTo("https://pdis.arbuz.lol/plantdisease-test/id/")
    }
}
