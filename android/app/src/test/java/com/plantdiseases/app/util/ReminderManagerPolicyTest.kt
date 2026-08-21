package com.plantdiseases.app.util

import androidx.work.ExistingPeriodicWorkPolicy
import com.google.common.truth.Truth.assertThat
import org.junit.Test

class ReminderManagerPolicyTest {

    @Test
    fun cadenceChangesCancelAndReenqueuePeriodicWork() {
        assertThat(ReminderManager.REANCHOR_POLICY)
            .isEqualTo(ExistingPeriodicWorkPolicy.CANCEL_AND_REENQUEUE)
    }
}
