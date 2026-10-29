package com.plantdiseases.app

import com.google.common.truth.Truth.assertThat
import org.junit.Test

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
}
