package com.plantdiseases.app.worker

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class OpinionCheckWorkerPolicyTest {
    @Test
    fun accountDisabledBuildNeverRunsEvenWithInheritedPreference() {
        assertThat(
            shouldRunOpinionCheck(
                accountUiEnabled = false,
                notificationsEnabled = true,
            ),
        ).isFalse()
    }

    @Test
    fun enabledBuildRequiresNotificationOptIn() {
        assertThat(shouldRunOpinionCheck(accountUiEnabled = true, notificationsEnabled = false))
            .isFalse()
        assertThat(shouldRunOpinionCheck(accountUiEnabled = true, notificationsEnabled = true))
            .isTrue()
    }
}
