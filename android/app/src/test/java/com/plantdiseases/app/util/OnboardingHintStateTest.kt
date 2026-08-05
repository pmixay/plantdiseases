package com.plantdiseases.app.util

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class OnboardingHintStateTest {

    private val context: Context
        get() = ApplicationProvider.getApplicationContext()

    @Before
    fun clearStateBeforeTest() {
        preferences().edit().clear().commit()
    }

    @After
    fun clearStateAfterTest() {
        preferences().edit().clear().commit()
    }

    @Test
    fun scanHintCanBeClaimedOnlyOnce() {
        val state = OnboardingHintState(context)

        assertThat(state.claimScanTabHint()).isTrue()
        assertThat(state.claimScanTabHint()).isFalse()
    }

    @Test
    fun resultHintCanBeClaimedOnlyOnce() {
        val state = OnboardingHintState(context)

        assertThat(state.claimResultHint()).isTrue()
        assertThat(state.claimResultHint()).isFalse()
    }

    @Test
    fun hintStatesAreIndependent() {
        val state = OnboardingHintState(context)

        assertThat(state.claimScanTabHint()).isTrue()
        assertThat(state.claimResultHint()).isTrue()
    }

    @Test
    fun claimsSurviveANewStateInstance() {
        OnboardingHintState(context).claimScanTabHint()
        val recreated = OnboardingHintState(context)

        assertThat(recreated.claimScanTabHint()).isFalse()
        assertThat(recreated.claimResultHint()).isTrue()
    }

    private fun preferences() = context.getSharedPreferences(
        OnboardingHintState.PREFS_NAME,
        Context.MODE_PRIVATE,
    )
}
