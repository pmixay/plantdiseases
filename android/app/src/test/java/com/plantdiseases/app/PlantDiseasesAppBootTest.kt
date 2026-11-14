package com.plantdiseases.app

import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The one test that boots the real application on purpose.
 *
 * Most Robolectric classes in this module declare
 * `@Config(application = Application::class)`: they exercise a store, a view or
 * a repository and gain nothing from constructing the Hilt graph and running
 * [PlantDiseasesApp.onCreate] before every single test method. That used to
 * happen implicitly everywhere, which also made every test an accidental smoke
 * test of app start-up. This class keeps that check, deliberately.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class PlantDiseasesAppBootTest {

    @Test
    fun realApplicationStartsAndResolvesItsHiltGraph() {
        val app = ApplicationProvider.getApplicationContext<PlantDiseasesApp>()

        // Hilt injects these inside super.onCreate(); reading them proves the
        // component was built and onCreate ran to the end without throwing.
        assertThat(app.scanRepository).isNotNull()
        assertThat(app.authRepository).isNotNull()
        assertThat(app.communityUploader).isNotNull()
        assertThat(app.notificationRepository).isNotNull()
        assertThat(app.privacyConsentPrefs).isNotNull()
    }
}
