package com.plantdiseases.app.util

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class CommunityAndOpinionPrefsTest {

    @Test
    fun communitySharingDefaultsOff() {
        // Opt-in: nothing is shared until the user enables community in context.
        assertThat(CommunityPrefs.DEFAULT_UPLOADS_ENABLED).isFalse()
    }

    @Test
    fun opinionNotificationsDefaultOff() {
        assertThat(OpinionPrefs.DEFAULT_NOTIFICATIONS_ENABLED).isFalse()
    }
}
