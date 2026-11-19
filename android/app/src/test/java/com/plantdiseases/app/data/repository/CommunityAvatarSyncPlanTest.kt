package com.plantdiseases.app.data.repository

import com.google.common.truth.Truth.assertThat
import org.junit.Test

/** The privacy rules of [CommunityAvatarSync]: never publish without a "show", ask once per photo. */
class CommunityAvatarSyncPlanTest {

    private val plan = CommunityAvatarSync.Plan

    @Test
    fun asksOnFirstSightOfAPhotoOnly() {
        assertThat(plan.shouldAsk(choice = null, askedFingerprint = null, fingerprint = "a")).isTrue()
        assertThat(plan.shouldAsk(choice = null, askedFingerprint = null, fingerprint = null)).isFalse()
        assertThat(plan.shouldAsk(choice = true, askedFingerprint = "a", fingerprint = "a")).isFalse()
    }

    @Test
    fun notNowHoldsUntilThePhotoChanges() {
        assertThat(plan.shouldAsk(choice = false, askedFingerprint = "a", fingerprint = "a")).isFalse()
        assertThat(plan.shouldAsk(choice = false, askedFingerprint = "a", fingerprint = "b")).isTrue()
        // Turned off in Profile before any photo existed: the first photo asks.
        assertThat(plan.shouldAsk(choice = false, askedFingerprint = null, fingerprint = "a")).isTrue()
    }

    @Test
    fun aChosenPhotoIsKeptOnlyWhileShownAndNotRefused() {
        assertThat(plan.desired(choice = true, fingerprint = "a", rejectedFingerprint = null)).isEqualTo("a")
        // A changed photo is published again without asking.
        assertThat(plan.desired(choice = true, fingerprint = "b", rejectedFingerprint = "a")).isEqualTo("b")
        assertThat(plan.desired(choice = true, fingerprint = "a", rejectedFingerprint = "a")).isNull()
        assertThat(plan.desired(choice = false, fingerprint = "a", rejectedFingerprint = null)).isNull()
        assertThat(plan.desired(choice = null, fingerprint = "a", rejectedFingerprint = null)).isNull()
        assertThat(plan.desired(choice = true, fingerprint = null, rejectedFingerprint = null)).isNull()
    }

    @Test
    fun onlyLastingRefusalsStopRetries() {
        assertThat(plan.isRefusal(422)).isTrue()
        assertThat(plan.isRefusal(403)).isTrue()
        assertThat(plan.isRefusal(401)).isFalse()
        assertThat(plan.isRefusal(429)).isFalse()
        assertThat(plan.isRefusal(500)).isFalse()
    }
}
