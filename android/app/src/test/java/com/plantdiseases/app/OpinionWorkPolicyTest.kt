package com.plantdiseases.app

import com.google.common.truth.Truth.assertThat
import com.plantdiseases.app.worker.shouldRunOpinionCheck
import org.junit.Test

/**
 * The two gates of the second-opinion poll: whether MainActivity schedules or
 * cancels the periodic work, and whether a run that fires does anything.
 */
class OpinionWorkPolicyTest {
    @Test
    fun disabledAccountBuildCancelsInheritedWorkRegardlessOfPreference() {
        assertThat(opinionWorkAction(accountUiEnabled = false, notificationsEnabled = false))
            .isEqualTo(OpinionWorkAction.CANCEL)
        assertThat(opinionWorkAction(accountUiEnabled = false, notificationsEnabled = true))
            .isEqualTo(OpinionWorkAction.CANCEL)
    }

    @Test
    fun enabledBuildSchedulesOnlyWhenUserOptedIn() {
        assertThat(opinionWorkAction(accountUiEnabled = true, notificationsEnabled = true))
            .isEqualTo(OpinionWorkAction.SCHEDULE)
        assertThat(opinionWorkAction(accountUiEnabled = true, notificationsEnabled = false))
            .isEqualTo(OpinionWorkAction.NONE)
    }

    @Test
    fun workerRunsOnEnabledBuildsOnlyAndIgnoresTheNotificationOptIn() {
        // An account-disabled build never runs, even with an inherited opt-in.
        assertThat(shouldRunOpinionCheck(accountUiEnabled = false, notificationsEnabled = true))
            .isFalse()
        // An enabled build polls for the in-app inbox whatever the opt-in says.
        assertThat(shouldRunOpinionCheck(accountUiEnabled = true, notificationsEnabled = false))
            .isTrue()
        assertThat(shouldRunOpinionCheck(accountUiEnabled = true, notificationsEnabled = true))
            .isTrue()
    }
}
