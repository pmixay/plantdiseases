package com.plantdiseases.app.util

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class CommunityPrefsTest {

    @Test
    fun communitySharingDefaultsOff() {
        // Nothing is shared until the user joins the community.
        assertThat(CommunityPrefs.DEFAULT_UPLOADS_ENABLED).isFalse()
    }
}
