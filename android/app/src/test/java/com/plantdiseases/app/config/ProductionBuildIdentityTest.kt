package com.plantdiseases.app.config

import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.BuildConfig
import org.junit.Test

class ProductionBuildIdentityTest {
    @Test
    fun ordinaryDebugBuildKeepsAccountUiDisabled() {
        assertThat(BuildConfig.ACCOUNT_UI_ENABLED).isFalse()
        assertThat(BuildConfig.SYNTHETIC_AUTH_ENABLED).isFalse()
        assertThat(BuildConfig.APPLICATION_ID).isEqualTo("com.plantdiseases.app")
        assertThat(BuildConfig.APP_API_BASE_URL).doesNotContain("/plantdisease-test/")
    }
}
