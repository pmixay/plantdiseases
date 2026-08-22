package com.plantdiseases.app.ui.onboarding.guidance

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class OnboardingCatalogTest {

    @Test
    fun communityFeed_isConditionalAndSkipsWhenEmpty() {
        val scope = OnboardingCatalog.step(OnboardingStepId.COMMUNITY_SCOPE)
        val feed = OnboardingCatalog.step(OnboardingStepId.COMMUNITY_FEED)

        // An empty community still teaches the visible scope control, then
        // completes without retrying forever on the hidden RecyclerView.
        assertThat(scope.conditional).isFalse()
        assertThat(scope.skipWhenUnavailable).isFalse()
        assertThat(feed.conditional).isTrue()
        assertThat(feed.skipWhenUnavailable).isTrue()
    }
}
