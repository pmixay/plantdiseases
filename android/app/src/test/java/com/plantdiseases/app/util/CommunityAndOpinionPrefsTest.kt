package com.plantdiseases.app.util

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class CommunityAndOpinionPrefsTest {

    @Test
    fun communitySharingDefaultsOn() {
        assertThat(CommunityPrefs.DEFAULT_UPLOADS_ENABLED).isTrue()
    }

    @Test
    fun opinionNotificationsDefaultOff() {
        assertThat(OpinionPrefs.DEFAULT_NOTIFICATIONS_ENABLED).isFalse()
    }
}
