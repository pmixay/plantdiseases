package com.plantdiseases.app.config

import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.BuildConfig
import org.junit.Test

class ReleaseBuildIdentityTest {
    @Test
    fun releaseIsPlantDiseases19WithYandexOnlyAccountSystem() {
        assertThat(BuildConfig.APPLICATION_ID).isEqualTo("com.plantdiseases.app")
        assertThat(BuildConfig.VERSION_CODE).isEqualTo(19)
        assertThat(BuildConfig.VERSION_NAME).isEqualTo("1.9")
        assertThat(BuildConfig.ACCOUNT_UI_ENABLED).isTrue()
        assertThat(BuildConfig.SYNTHETIC_AUTH_ENABLED).isFalse()
        assertThat(BuildConfig.YANDEX_OAUTH_CONFIGURED).isTrue()
        assertThat(BuildConfig.VK_OAUTH_CONFIGURED).isFalse()
        assertThat(BuildConfig.APP_API_BASE_URL).isEqualTo("https://pdis.arbuz.lol/")
        assertThat(BuildConfig.ACCOUNT_API_BASE_URL).isEqualTo("https://pdis.arbuz.lol/id/")
    }
}
