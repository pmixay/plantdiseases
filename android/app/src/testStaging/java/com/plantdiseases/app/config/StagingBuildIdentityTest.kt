package com.plantdiseases.app.config

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.BuildConfig
import com.plantdiseases.app.R
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class StagingBuildIdentityTest {
    @Test
    fun stagingIsPlantDiseases19WithAccountSystem() {
        assertThat(BuildConfig.ACCOUNT_UI_ENABLED).isTrue()
        assertThat(BuildConfig.APPLICATION_ID).isEqualTo("com.plantdiseases.app")
        assertThat(BuildConfig.VERSION_CODE).isEqualTo(19)
        assertThat(BuildConfig.VERSION_NAME).isEqualTo("1.9")
        val context = ApplicationProvider.getApplicationContext<Context>()
        assertThat(context.getString(R.string.app_name)).isEqualTo("PlantDiseases")
        assertThat(BuildConfig.SYNTHETIC_AUTH_ENABLED).isFalse()
        assertThat(BuildConfig.YANDEX_OAUTH_CONFIGURED).isTrue()
        assertThat(BuildConfig.VK_OAUTH_CONFIGURED).isFalse()
        assertThat(BuildConfig.DISTRIBUTION_CHANNEL).isAnyOf("local-beta", "staging")
        assertThat(BuildConfig.APP_API_BASE_URL)
            .isEqualTo("https://pdis.arbuz.lol/plantdisease-test/")
        assertThat(BuildConfig.ACCOUNT_API_BASE_URL)
            .isEqualTo("https://pdis.arbuz.lol/plantdisease-test/id/")
    }
}
