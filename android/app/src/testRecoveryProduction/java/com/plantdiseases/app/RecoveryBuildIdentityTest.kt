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
        assertThat(BuildConfig.APP_API_BASE_URL).isEqualTo("https://pdis.arbuz.lol/")
        assertThat(BuildConfig.ACCOUNT_API_BASE_URL).isEqualTo("https://pdis.arbuz.lol/id/")
        assertThat(BuildConfig.ACCOUNT_UI_ENABLED).isFalse()
        assertThat(BuildConfig.SYNTHETIC_AUTH_ENABLED).isFalse()
    }
}
